"""Tests for the DCTAP model, the generated SHACL shapes and the restored fixtures.

Run with:  python -m pytest tests/test_model.py

Every rule is tested by breaking valid data on purpose. Passing valid data alone would also
succeed if a rule never fired.
"""
from pathlib import Path

import pytest
from dctap.config import get_config
from dctap.csvreader import csvreader
from rdflib import BNode, Graph, Literal, Namespace, RDF, XSD
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, OWL, SKOS

from ciaaw_draft.paths import DATA_DIR, ROOT
from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.schema import tap2model as t

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
CIAAW = Namespace(c.DEFAULT_VOCAB_NS)
EX = Namespace(c.DEFAULT_DATA_NS)
ELEMENT = Namespace(c.DEFAULT_ELEMENT_NS)
QUDT, CHEMROF = c.QUDT, c.CHEMROF
SH = t.SH


@pytest.fixture(scope="module")
def shapes():
    return t.load_shapes()


@pytest.fixture
def data():
    return Graph().parse(DATA_DIR / "example-output.ttl", format="turtle")


@pytest.fixture
def revision():
    return Graph().parse(DATA_DIR / "example-output-next.ttl", format="turtle")


def quantity(g, decl):
    return g.value(decl, CIAAW.value)


def remove(g, *triple):
    """Remove what a test relies on. Graph.remove() is silent when nothing matches, which hides a stale IRI."""
    assert triple in g, triple
    g.remove(triple)


def replace(g, subject, predicate, new):
    g.remove((subject, predicate, None))
    g.add((subject, predicate, new))


def results(report):
    """(severity, source shape, message) of every result in a validation report."""
    return [(r.value(n, SH.resultSeverity), r.value(n, SH.sourceShape), str(r.value(n, SH.resultMessage)))
            for r in [report] for n in r.subjects(RDF.type, SH.ValidationResult)]


# --- the DCTAP itself ---------------------------------------------------------------------
def test_tap_passes_the_dctap_reader_without_warnings():
    assert t.load_tap().warnings == []


def test_tap_uses_all_12_standard_dctap_columns():
    header = (ROOT / "model" / "model_dctap.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert {"shapeID", "shapeLabel", "propertyID", "propertyLabel", "mandatory", "repeatable", "valueNodeType",
            "valueDataType", "valueConstraint", "valueConstraintType", "valueShape", "note"} <= set(header)


def test_extra_columns_are_declared_in_the_config():
    config = get_config(nondefault_configfile_name=str(t.TAP_CONFIG))
    parsed = csvreader(csvfile_str=t.TAP_CSV.read_text(encoding="utf-8"), config_dict=config)
    assert "csv" not in parsed["warnings"]              # dctap reports undeclared columns here


def test_consistency_check_finds_broken_references(tmp_path):
    csv = t.TAP_CSV.read_text(encoding="utf-8").replace(",shape:Isotope,identity abundance,", ",shape:Nope,bogus,")
    path = tmp_path / "broken.csv"
    path.write_text(csv, encoding="utf-8")
    warnings = t.load_tap(path).warnings
    assert any("unknown shape shape:Nope" in w for w in warnings)
    assert any("unknown aspect bogus" in w for w in warnings)


def test_every_property_used_in_the_data_is_modelled(revision):
    """Otherwise the shapes would silently ignore part of the data."""
    tap = t.load_tap()
    modelled = {tap.iri(st.property) for s in tap.shapes.values() for st in s.statements}
    instances = {s for cls in (CHEMROF.ChemicalElement, CHEMROF.Isotope, CIAAW.Footnote, QUDT.QuantityValue,
                               CIAAW.StandardAtomicWeightDeclaration, CIAAW.IsotopicAbundanceDeclaration)
                 for s in revision.subjects(RDF.type, cls)}
    used = {p for s in instances for p in revision.predicates(s)}
    assert used - modelled == set()


# --- generated files ----------------------------------------------------------------------
def test_generated_files_are_up_to_date():
    assert t.main(["--check"]) == 0


def test_generation_is_deterministic():
    tap = t.load_tap()
    assert t.shacl_text(tap) == t.shacl_text(t.load_tap())
    assert t.diagrams_text(tap) == t.diagrams_text(t.load_tap())


def test_cardinality_mapping(shapes):
    p = t.load_tap().iri("shape:Element-chemrof-symbol")
    assert (p, SH.minCount, Literal(1)) in shapes and (p, SH.maxCount, Literal(1)) in shapes
    repeatable = t.load_tap().iri("shape:Element-skos-closeMatch")
    assert (repeatable, SH.minCount, None) not in shapes and (repeatable, SH.maxCount, None) not in shapes


def test_every_aspect_has_a_diagram_that_names_its_properties():
    tap = t.load_tap()
    text = t.diagrams_text(tap)
    assert text.count("```mermaid") == len(tap.aspects) + 1          # plus the overview
    for key in tap.aspects:
        diagram = t.mermaid(tap, key)
        assert diagram.startswith("classDiagram")
        shown = diagram.replace("#58;", ":")
        for shape in tap.shapes.values():
            for st in shape.statements:
                if key in st.aspects and st.property != "rdf:type":
                    assert st.property in shown, (key, st.property)


def test_relation_labels_contain_no_raw_colon():
    """A second colon on a relation line is a Mermaid syntax error, and the whole diagram fails."""
    tap = t.load_tap()
    for key in [None, *tap.aspects]:
        relations = [line for line in t.mermaid(tap, key).splitlines() if " --> " in line]
        assert relations
        assert all(line.count(":") == 1 for line in relations), key


def test_diagrams_show_generalisation():
    assert "Declaration <|-- StandardAtomicWeightDeclaration" in t.mermaid(t.load_tap())


# --- valid data ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["example-output.ttl", "example-output-next.ttl"])
def test_example_data_conforms(name, shapes):
    conforms, report, text = t.validate_graph(Graph().parse(DATA_DIR / name, format="turtle"), shapes)
    assert conforms, text
    assert results(report) == []


def test_data_without_the_vocabulary_block_conforms(shapes):
    """The shapes must not rely on rdfs:subClassOf triples being present in the data."""
    problems = []
    w = c.read_weights((FIX / "atomic-weights.htm").read_text(encoding="utf-8"), problems)
    a = c.read_abundances((FIX / "isotopic-abundances.htm").read_text(encoding="utf-8"), problems)
    g, _ = c.build_graph(w, a, c.load_supplement(str(FIX / "supplement.csv")), include_vocab=False)
    conforms, _, text = t.validate_graph(g, shapes)
    assert conforms, text


def test_planned_declaration_types_conform(data, shapes):
    conventional, mass = EX["AW-H-conventional"], EX["Mass-H1"]
    for decl, kind, subject, about, pointer, unit in (
            (conventional, CIAAW.ConventionalAtomicWeightDeclaration, ELEMENT["Hydrogen"], CIAAW.ofElement,
             CIAAW.currentConventionalAtomicWeight, c.UNIT.UNITLESS),
            (mass, CIAAW.AtomicMassDeclaration, EX["H-1"], CIAAW.ofIsotope, CIAAW.currentAtomicMass, c.UNIT.DA)):
        q = BNode()
        data.add((decl, RDF.type, kind))
        data.add((decl, about, subject))
        data.add((decl, CIAAW.value, q))
        data.add((subject, pointer, decl))
        data.add((subject, CIAAW.hasDeclaration, decl))
        data.add((q, RDF.type, QUDT.QuantityValue))
        data.add((q, QUDT.hasUnit, unit))
        data.add((q, QUDT.numericValue, Literal("1.008", datatype=XSD.decimal)))
        if decl == mass:
            data.add((q, QUDT.standardUncertainty, Literal("0.001", datatype=XSD.decimal)))
            data.add((q, CIAAW.significantFigures, Literal(4)))
            data.add((q, CIAAW.significantFiguresUncertainty, Literal(1)))
    conforms, _, text = t.validate_graph(data, shapes)
    assert conforms, text


# --- broken data: every rule must fire ----------------------------------------------------
def missing_symbol(g):
    remove(g, ELEMENT["Hydrogen"], CHEMROF.symbol, None)


def two_symbols(g):
    g.add((ELEMENT["Hydrogen"], CHEMROF.symbol, Literal("Hy")))


def bad_symbol(g):
    replace(g, ELEMENT["Hydrogen"], CHEMROF.symbol, Literal("hydrogen"))


def zero_atomic_number(g):
    replace(g, ELEMENT["Hydrogen"], CHEMROF.atomic_number, Literal(0))


def wrong_chebi_namespace(g):
    g.add((ELEMENT["Hydrogen"], SKOS.closeMatch, EX["somewhere-else"]))


def isotope_without_abundance(g):
    remove(g, EX["H-1"], CIAAW.currentAbundance, None)


def wrong_kind_of_declaration(g):
    replace(g, ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight, EX["Abundance-H1"])


def declaration_without_value(g):
    remove(g, EX["AW-He-standard"], CIAAW.value, None)


def weight_as_a_single_value(g):
    q = quantity(g, EX["AW-He-standard"])
    for p in (QUDT.standardUncertainty, CIAAW.significantFigures, CIAAW.significantFiguresUncertainty):
        remove(g, q, p, None)


def standard_value_without_a_number(g):
    q = quantity(g, EX["AW-He-standard"])
    for p in (QUDT.numericValue, QUDT.standardUncertainty):
        remove(g, q, p, None)


def bounds_and_value_mixed(g):
    g.add((quantity(g, EX["AW-H-standard-2009"]), QUDT.numericValue, Literal("1.008", datatype=XSD.decimal)))


def interval_missing_upper_bound(g):
    remove(g, quantity(g, EX["AW-H-standard-2009"]), QUDT.upperBound, None)


def float_instead_of_decimal(g):
    replace(g, quantity(g, EX["AW-He-standard"]), QUDT.numericValue, Literal(4.002602, datatype=XSD.double))


def wrong_unit(g):
    replace(g, quantity(g, EX["AW-He-standard"]), QUDT.hasUnit, c.UNIT.DA)


def unknown_footnote_letter(g):
    replace(g, CIAAW["note-g"], SKOS.notation, Literal("x"))


def undefined_footnote(g):
    g.add((EX["AW-He-standard"], CIAAW.footnote, CIAAW["note-z"]))


def year_as_integer(g):
    replace(g, EX["AW-H-standard-2009"], DCTERMS.issued, Literal(2009))


def source_is_not_a_doi(g):
    g.add((EX["AW-He-standard"], DCTERMS.source, EX["report"]))


def lower_above_upper(g):
    q = quantity(g, EX["AW-H-standard-2009"])
    replace(g, q, QUDT.lowerBound, Literal("1.1", datatype=XSD.decimal))


def zero_uncertainty(g):
    replace(g, quantity(g, EX["AW-He-standard"]), QUDT.standardUncertainty, Literal("0", datatype=XSD.decimal))


def abundance_above_one(g):
    q = quantity(g, EX["Abundance-H1"])
    replace(g, q, QUDT.upperBound, Literal("1.2", datatype=XSD.decimal))


def abundance_below_zero(g):
    q = quantity(g, EX["Abundance-H1"])
    replace(g, q, QUDT.lowerBound, Literal("-0.1", datatype=XSD.decimal))


def isotope_without_element(g):
    remove(g, None, CIAAW.hasIsotope, EX["H-1"])


def isotope_in_two_elements(g):
    g.add((ELEMENT["Helium"], CIAAW.hasIsotope, EX["H-1"]))


def current_not_in_history(g):
    remove(g, ELEMENT["Hydrogen"], CIAAW.hasDeclaration, EX["AW-H-standard-2009"])


def declaration_points_elsewhere(g):
    replace(g, EX["AW-He-standard"], CIAAW.ofElement, ELEMENT["Hydrogen"])


def shortcut_differs(g):
    replace(g, ELEMENT["Hydrogen"], CIAAW.standardAtomicWeightLower, Literal("1.5", datatype=XSD.decimal))


@pytest.mark.parametrize("break_it, expected", [
    (missing_symbol, "Element-chemrof-symbol"),
    (two_symbols, "Element-chemrof-symbol"),
    (bad_symbol, "Element-chemrof-symbol"),
    (zero_atomic_number, "Element-chemrof-atomic_number"),
    (wrong_chebi_namespace, "Element-skos-closeMatch"),
    (isotope_without_abundance, "Isotope-ciaaw-currentAbundance"),
    (wrong_kind_of_declaration, "Element-ciaaw-currentStandardAtomicWeight"),
    (declaration_without_value, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (standard_value_without_a_number, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (bounds_and_value_mixed, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (interval_missing_upper_bound, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (float_instead_of_decimal, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (wrong_unit, "StandardAtomicWeightDeclaration-ciaaw-value"),
    (unknown_footnote_letter, "Footnote-skos-notation"),
    (undefined_footnote, "Declaration-ciaaw-footnote"),
    (year_as_integer, "Declaration-dcterms-issued"),
    (source_is_not_a_doi, "Declaration-dcterms-source"),
    (lower_above_upper, "BoundsInOrder"),
    (zero_uncertainty, "UncertaintyIsPositive"),
    (abundance_above_one, "AbundanceIsAFraction"),
    (abundance_below_zero, "AbundanceIsAFraction"),
    (isotope_without_element, "IsotopeBelongsToOneElement"),
    (isotope_in_two_elements, "IsotopeBelongsToOneElement"),
    (current_not_in_history, "CurrentIsInHistory"),
    (declaration_points_elsewhere, "DeclarationPointsBack"),
    (shortcut_differs, "ShortcutsMatchCurrentDeclaration"),
])
def test_broken_data_is_rejected_by_the_expected_shape(break_it, expected, data, shapes):
    break_it(data)
    conforms, report, text = t.validate_graph(data, shapes)
    assert not conforms
    assert expected in text, text


def test_a_standard_value_may_be_exact_as_in_the_older_tables(data, shapes):
    """The historical tables give some values without an uncertainty, for example oxygen in 1902 as 16."""
    weight_as_a_single_value(data)
    remove(data, ELEMENT["Helium"], CIAAW.standardAtomicWeightUncertainty, None)   # derived from the uncertainty
    conforms, _, text = t.validate_graph(data, shapes)
    assert conforms, text


OLD, NEW = EX["AW-H-standard-2009"], EX["AW-H-standard-2027"]


def pointer_to_deprecated(g):
    replace(g, ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight, OLD)


def deprecated_without_replacement(g):
    remove(g, OLD, DCTERMS.isReplacedBy, None)


def deprecated_without_status(g):
    remove(g, OLD, c.ADMS.status, None)


def replacement_one_way(g):
    remove(g, NEW, DCTERMS.replaces, None)


def replaced_by_a_non_declaration(g):
    replace(g, OLD, DCTERMS.isReplacedBy, ELEMENT["Hydrogen"])


@pytest.mark.parametrize("break_it, expected", [
    (pointer_to_deprecated, "Element-ciaaw-currentStandardAtomicWeight-2"),
    (deprecated_without_replacement, "DeprecationIsComplete"),
    (deprecated_without_status, "DeprecationIsComplete"),
    (replacement_one_way, "ReplacementIsMutual"),
    (replaced_by_a_non_declaration, "Declaration-dcterms-isReplacedBy"),
])
def test_broken_history_is_rejected(break_it, expected, revision, shapes):
    break_it(revision)
    conforms, _, text = t.validate_graph(revision, shapes)
    assert not conforms
    assert expected in text, text


def test_wrong_abundance_sum_is_only_a_warning(data, shapes):
    replace(data, quantity(data, EX["Abundance-He4"]), QUDT.numericValue, Literal("0.9", datatype=XSD.decimal))
    conforms, report, _ = t.validate_graph(data, shapes)
    assert conforms
    found = results(report)
    assert len(found) == 1 and found[0][0] == SH.Warning
    assert "sum to" in found[0][2]


def test_abundance_sum_skips_elements_given_as_intervals(data, shapes):
    """Hydrogen is given as intervals, so there is no meaningful sum to check."""
    assert results(t.validate_graph(data, shapes)[1]) == []


# --- the restored fixtures ----------------------------------------------------------------
def build_from_fixtures(tmp_path, weights, supplement, previous=None):
    out = tmp_path / "out.ttl"
    args = ["--weights", str(FIX / weights), "--abundances", str(FIX / "isotopic-abundances.htm"),
            "--supplement", str(FIX / supplement), "--lenient", "-o", str(out)]
    if previous:
        args += ["--previous", str(previous)]
    assert c.main(args) == 0
    return Graph().parse(out, format="turtle")


def test_fixtures_reproduce_the_committed_example(tmp_path, data):
    assert isomorphic(build_from_fixtures(tmp_path, "atomic-weights.htm", "supplement.csv"), data)


def test_fixtures_reproduce_the_committed_revision_example(tmp_path, revision):
    built = build_from_fixtures(tmp_path, "atomic-weights-next.htm", "supplement-next.csv",
                                DATA_DIR / "example-output.ttl")
    assert isomorphic(built, revision)
    assert (OLD, OWL.deprecated, Literal(True)) in built


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
