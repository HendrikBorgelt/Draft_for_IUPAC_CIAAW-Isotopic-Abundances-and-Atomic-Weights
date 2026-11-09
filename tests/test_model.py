"""Tests for the DCTAP model, the generated SHACL shapes and the restored fixtures.

Run with:  python -m pytest tests/test_model.py

Every rule is tested by breaking valid data on purpose. Passing valid data alone would also
succeed if a rule never fired.
"""
from pathlib import Path

import pytest
from dctap.config import get_config
from dctap.csvreader import csvreader
from rdflib import Graph, Literal, RDF, URIRef, XSD
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDFS, SKOS

from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.namespaces import NS, PROV, SI_UNITS, UNIT_DALTON, UNIT_ONE, WEIGHTS_PAGE
from ciaaw_draft.paths import DATA_DIR, ROOT
from ciaaw_draft.schema import tap2model as t

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
CIAAW, ELEMENTS, ISOTOPES, WEIGHTS, ABUNDANCES = NS.ciaaw, NS.elements, NS.isotopes, NS.weights, NS.abundances
REPORTS, FOOTNOTES = NS.reports, NS.footnotes
SH = t.SH
H, HE = ELEMENTS.Hydrogen, ELEMENTS.Helium
H_WEIGHT, HE_WEIGHT = WEIGHTS["H-2009"], WEIGHTS.He


@pytest.fixture(scope="module")
def shapes():
    return t.load_shapes()


@pytest.fixture
def data():
    return Graph().parse(DATA_DIR / "example-output.ttl", format="turtle")


@pytest.fixture
def revision():
    return Graph().parse(DATA_DIR / "example-output-next.ttl", format="turtle")


def dec(text: str) -> Literal:
    return Literal(text, datatype=XSD.decimal)


def remove(g, *triple):
    """Remove what a test relies on. Graph.remove() is silent when nothing matches, which hides a stale IRI."""
    assert triple in g, triple
    g.remove(triple)


def replace(g, subject, predicate, new):
    remove(g, subject, predicate, None)
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
    """Otherwise the shapes would silently ignore part of the data. Only the headers of the namespaces are left to
    test_ciaaw2ttl.py, because the ontology, which validation mixes in, is an owl:Ontology too."""
    tap = t.load_tap()
    modelled = {tap.iri(st.property) for s in tap.shapes.values() for st in s.statements}
    instances = {s for cls in (CIAAW.ChemicalElement, CIAAW.Isotope, CIAAW.Footnote, CIAAW.Report, CIAAW.Body,
                               CIAAW.StandardAtomicWeightDeclaration, CIAAW.IsotopicAbundanceDeclaration, PROV.Entity)
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
    p = t.load_tap().iri("shape:Element-ciaaw-hasSymbol")
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
        assert relations or key == "value"
        assert all(line.count(":") == 1 for line in relations), key


def test_diagrams_show_generalisation():
    assert "Declaration <|-- StandardAtomicWeightDeclaration" in t.mermaid(t.load_tap())


# --- valid data ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["example-output.ttl", "example-output-next.ttl"])
def test_example_data_conforms(name, shapes):
    conforms, report, text = t.validate_graph(Graph().parse(DATA_DIR / name, format="turtle"), shapes)
    assert conforms, text
    assert results(report) == []


def test_data_without_headers_conforms(shapes):
    """The shapes rely on the ontology, which validation mixes in, not on anything else in the data."""
    problems = []
    w = c.read_weights((FIX / "atomic-weights.htm").read_text(encoding="utf-8"), problems)
    a = c.read_abundances((FIX / "isotopic-abundances.htm").read_text(encoding="utf-8"), problems)
    g, _ = c.build_graph(w, a, c.load_supplement(str(FIX / "supplement.csv")), headers=False)
    conforms, _, text = t.validate_graph(g, shapes)
    assert conforms, text


def test_planned_declaration_types_conform(data, shapes):
    conventional = URIRef(f"{NS.base}conventional-atomic-weights/H")
    mass = URIRef(f"{NS.base}atomic-masses/H-1")
    for decl, kind, subject, pointer, unit in (
            (conventional, CIAAW.ConventionalAtomicWeightDeclaration, H, CIAAW.hasCurrentConventionalAtomicWeight, UNIT_ONE),
            (mass, CIAAW.AtomicMassDeclaration, ISOTOPES["H-1"], CIAAW.hasCurrentAtomicMass, UNIT_DALTON)):
        for triple in ((decl, RDF.type, kind), (decl, RDFS.label, Literal("planned", lang="en")),
                       (decl, CIAAW.isDeclarationOf, subject), (decl, CIAAW.hasStatus, Literal("current")),
                       (decl, CIAAW.hasValueAsString, Literal("1.008")), (decl, CIAAW.hasUnit, unit),
                       (decl, CIAAW.hasValue, dec("1.008")), (decl, PROV.wasDerivedFrom, WEIGHTS_PAGE),
                       (subject, pointer, decl), (subject, CIAAW.hasDeclaration, decl)):
            data.add(triple)
        if decl == mass:
            data.add((decl, CIAAW.hasStandardUncertainty, dec("0.001")))
    conforms, _, text = t.validate_graph(data, shapes)
    assert conforms, text


# --- broken data: every rule must fire ----------------------------------------------------
def missing_symbol(g):
    remove(g, H, CIAAW.hasSymbol, None)


def two_symbols(g):
    g.add((H, CIAAW.hasSymbol, Literal("Hy")))


def bad_symbol(g):
    replace(g, H, CIAAW.hasSymbol, Literal("hydrogen"))


def zero_atomic_number(g):
    replace(g, H, CIAAW.hasAtomicNumber, Literal(0))


def wrong_chebi_namespace(g):
    g.add((H, SKOS.closeMatch, ELEMENTS["somewhere-else"]))


def element_without_source(g):
    remove(g, H, PROV.wasDerivedFrom, None)


def isotope_without_abundance(g):
    remove(g, ISOTOPES["H-1"], CIAAW.hasCurrentAbundance, None)


def wrong_kind_of_declaration(g):
    replace(g, H, CIAAW.hasCurrentStandardAtomicWeight, ABUNDANCES["H-1"])


def declaration_without_numbers(g):
    remove(g, HE_WEIGHT, CIAAW.hasValue, None)
    remove(g, HE_WEIGHT, CIAAW.hasStandardUncertainty, None)


def uncertainty_without_value(g):
    remove(g, HE_WEIGHT, CIAAW.hasValue, None)


def bounds_and_value_mixed(g):
    g.add((H_WEIGHT, CIAAW.hasValue, dec("1.008")))


def interval_missing_upper_bound(g):
    remove(g, H_WEIGHT, CIAAW.hasUpperBound, None)


def without_the_printed_value(g):
    remove(g, HE_WEIGHT, CIAAW.hasValueAsString, None)


def float_instead_of_decimal(g):
    replace(g, HE_WEIGHT, CIAAW.hasValue, Literal(4.002602, datatype=XSD.double))


def wrong_unit(g):
    replace(g, HE_WEIGHT, CIAAW.hasUnit, UNIT_DALTON)


def unit_outside_the_si_framework(g):
    replace(g, HE_WEIGHT, CIAAW.hasUnit, URIRef("http://qudt.org/vocab/unit/UNITLESS"))


def unknown_status(g):
    replace(g, HE_WEIGHT, CIAAW.hasStatus, Literal("deprecated"))


def declaration_without_source(g):
    remove(g, HE_WEIGHT, PROV.wasDerivedFrom, None)


def unknown_footnote_letter(g):
    replace(g, FOOTNOTES.g, SKOS.notation, Literal("x"))


def undefined_footnote(g):
    g.add((HE_WEIGHT, CIAAW.hasFootnote, FOOTNOTES.z))


def year_as_integer(g):
    replace(g, H_WEIGHT, CIAAW.hasStartValidity, Literal(2009))


def report_is_not_a_report(g):
    g.add((HE_WEIGHT, CIAAW.hasDefiningReport, H))


def report_without_a_doi(g):
    replace(g, REPORTS["pac-2015-0503"], DCTERMS.identifier, Literal("pac-2015-0503"))


def lower_above_upper(g):
    replace(g, H_WEIGHT, CIAAW.hasLowerBound, dec("1.1"))


def zero_uncertainty(g):
    replace(g, HE_WEIGHT, CIAAW.hasStandardUncertainty, dec("0"))


def abundance_above_one(g):
    replace(g, ABUNDANCES["H-1"], CIAAW.hasUpperBound, dec("1.2"))


def abundance_below_zero(g):
    replace(g, ABUNDANCES["H-1"], CIAAW.hasLowerBound, dec("-0.1"))


def isotope_without_element(g):
    remove(g, None, CIAAW.hasIsotope, ISOTOPES["H-1"])


def isotope_in_two_elements(g):
    g.add((HE, CIAAW.hasIsotope, ISOTOPES["H-1"]))


def current_not_in_history(g):
    remove(g, H, CIAAW.hasDeclaration, H_WEIGHT)


def declaration_points_elsewhere(g):
    replace(g, HE_WEIGHT, CIAAW.isDeclarationOf, H)


@pytest.mark.parametrize("break_it, expected", [
    (missing_symbol, "Element-ciaaw-hasSymbol"),
    (two_symbols, "Element-ciaaw-hasSymbol"),
    (bad_symbol, "Element-ciaaw-hasSymbol"),
    (zero_atomic_number, "Element-ciaaw-hasAtomicNumber"),
    (wrong_chebi_namespace, "Element-skos-closeMatch"),
    (element_without_source, "Element-prov-wasDerivedFrom"),
    (isotope_without_abundance, "Isotope-ciaaw-hasCurrentAbundance"),
    (wrong_kind_of_declaration, "Element-ciaaw-hasCurrentStandardAtomicWeight"),
    (declaration_without_numbers, "OneValueForm"),
    (uncertainty_without_value, "OneValueForm"),
    (bounds_and_value_mixed, "OneValueForm"),
    (interval_missing_upper_bound, "OneValueForm"),
    (without_the_printed_value, "Declaration-ciaaw-hasValueAsString"),
    (float_instead_of_decimal, "Declaration-ciaaw-hasValue"),
    (wrong_unit, "StandardAtomicWeightDeclaration-ciaaw-hasUnit"),
    (unit_outside_the_si_framework, "StandardAtomicWeightDeclaration-ciaaw-hasUnit"),
    (unknown_status, "Declaration-ciaaw-hasStatus"),
    (declaration_without_source, "Declaration-prov-wasDerivedFrom"),
    (unknown_footnote_letter, "Footnote-skos-notation"),
    (undefined_footnote, "Declaration-ciaaw-hasFootnote"),
    (year_as_integer, "Declaration-ciaaw-hasStartValidity"),
    (report_is_not_a_report, "Declaration-ciaaw-hasDefiningReport"),
    (report_without_a_doi, "Report-dcterms-identifier"),
    (lower_above_upper, "BoundsInOrder"),
    (zero_uncertainty, "UncertaintyIsPositive"),
    (abundance_above_one, "AbundanceIsAFraction"),
    (abundance_below_zero, "AbundanceIsAFraction"),
    (isotope_without_element, "IsotopeBelongsToOneElement"),
    (isotope_in_two_elements, "IsotopeBelongsToOneElement"),
    (current_not_in_history, "CurrentIsInHistory"),
    (declaration_points_elsewhere, "DeclarationPointsBack"),
])
def test_broken_data_is_rejected_by_the_expected_shape(break_it, expected, data, shapes):
    break_it(data)
    conforms, report, text = t.validate_graph(data, shapes)
    assert not conforms
    assert expected in text, text


def test_a_standard_value_may_be_given_without_an_uncertainty_as_in_the_older_tables(data, shapes):
    """The historical tables give some values without an uncertainty, for example oxygen in 1902 as 16."""
    remove(data, HE_WEIGHT, CIAAW.hasStandardUncertainty, None)
    conforms, _, text = t.validate_graph(data, shapes)
    assert conforms, text


OLD, NEW = WEIGHTS["H-2009"], WEIGHTS["H-2027"]


def pointer_to_superseded(g):
    replace(g, H, CIAAW.hasCurrentStandardAtomicWeight, OLD)


def superseded_without_next(g):
    remove(g, OLD, CIAAW.hasNextDeclaration, None)


def current_with_a_next(g):
    g.add((NEW, CIAAW.hasNextDeclaration, OLD))


def superseded_marked_current(g):
    replace(g, OLD, CIAAW.hasStatus, Literal("current"))


def chain_one_way(g):
    remove(g, NEW, CIAAW.hasPreviousDeclaration, None)


def next_is_not_a_declaration(g):
    replace(g, OLD, CIAAW.hasNextDeclaration, H)


def chain_changes_element(g):
    replace(g, OLD, CIAAW.hasNextDeclaration, HE_WEIGHT)
    g.add((HE_WEIGHT, CIAAW.hasPreviousDeclaration, OLD))


def ends_before_it_starts(g):
    replace(g, OLD, CIAAW.hasEndValidity, Literal("1999", datatype=XSD.gYear))


def ends_after_the_next_starts(g):
    replace(g, OLD, CIAAW.hasEndValidity, Literal("2030", datatype=XSD.gYear))


@pytest.mark.parametrize("break_it, expected", [
    (pointer_to_superseded, "Element-ciaaw-hasCurrentStandardAtomicWeight-2"),
    (superseded_without_next, "SupersededHasNext"),
    (current_with_a_next, "SupersededHasNext"),
    (superseded_marked_current, "CurrentIsPointedTo"),
    (chain_one_way, "ChainIsMutual"),
    (next_is_not_a_declaration, "Declaration-ciaaw-hasNextDeclaration"),
    (chain_changes_element, "ChainStaysWithSubject"),
    (ends_before_it_starts, "ValidityInOrder"),
    (ends_after_the_next_starts, "ValidityInOrder"),
])
def test_broken_history_is_rejected(break_it, expected, revision, shapes):
    break_it(revision)
    conforms, _, text = t.validate_graph(revision, shapes)
    assert not conforms
    assert expected in text, text


def test_wrong_abundance_sum_is_only_a_warning(data, shapes):
    replace(data, ABUNDANCES["He-4"], CIAAW.hasValue, dec("0.9"))
    conforms, report, _ = t.validate_graph(data, shapes)
    assert conforms
    found = results(report)
    assert len(found) == 1 and found[0][0] == SH.Warning
    assert "sum to" in found[0][2]


def test_abundance_sum_skips_elements_given_as_intervals(data, shapes):
    """Hydrogen is given as intervals, so there is no meaningful sum to check."""
    assert results(t.validate_graph(data, shapes)[1]) == []


def test_units_come_from_the_si_digital_framework(shapes):
    units = {o for o in shapes.objects(None, SH.hasValue) if str(o).startswith(str(SI_UNITS))}
    assert units == {UNIT_ONE, UNIT_DALTON}


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
    assert (OLD, CIAAW.hasStatus, Literal("superseded")) in built


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
