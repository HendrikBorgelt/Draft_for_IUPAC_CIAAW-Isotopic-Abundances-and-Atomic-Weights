"""Run with:  python -m pytest tests/test_ciaaw2ttl.py   (or: python test_ciaaw2ttl.py)

The fixtures in ./fixtures are SMALL HAND-BUILT HTML files that mimic the two CIAAW tables
(rowspan layout, no-break/thin spaces in numbers, footnote links). They were built from the
table text as rendered by the website, NOT from the raw HTML of ciaaw.org, which could not be
downloaded in the build environment. The real pages also have broken markup that the fixtures
lack, see test_rows_without_an_opening_tr_are_read. On the real pages the script should report
118 elements, 289 isotopes, and 26 warnings for elements 93 to 118, which the abundance table
does not list. Any other warning needs looking at.
"""
from decimal import Decimal
from pathlib import Path

import pytest
from rdflib import BNode, Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.namespace import DCTERMS, RDFS, SKOS

from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.namespaces import (ABUNDANCES_PAGE, CHEBI, NS, PROV, SUPPLEMENT_FILE, UNIT_ONE, WEIGHTS_PAGE,
                                    Namespaces)

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
CIAAW, ELEMENTS, ISOTOPES, WEIGHTS, ABUNDANCES = NS.ciaaw, NS.elements, NS.isotopes, NS.weights, NS.abundances
REPORTS, FOOTNOTES, BODIES = NS.reports, NS.footnotes, NS.bodies


def dec(text: str) -> Literal:
    return Literal(text, datatype=XSD.decimal)


def build(supplement=True, weights_html=None, sup=None, previous=None, iri_style="readable"):
    problems = []
    html = weights_html or (FIX / "atomic-weights.htm").read_text(encoding="utf-8")
    w = c.read_weights(html, problems)
    a = c.read_abundances((FIX / "isotopic-abundances.htm").read_text(encoding="utf-8"), problems)
    if sup is None:
        sup = c.load_supplement(str(FIX / "supplement.csv")) if supplement else {}
    g, warnings = c.build_graph(w, a, sup, iri_style=iri_style, previous=previous)
    return g, warnings, problems


def roundtrip(g):
    """Serialize and re-parse, like a real --previous file."""
    out = Graph()
    out.parse(data=g.serialize(format="turtle"), format="turtle")
    return out


def changed_weights_html():
    """Hydrogen interval changed (hypothetical revision) to exercise the chain of revisions."""
    html = (FIX / "atomic-weights.htm").read_text(encoding="utf-8")
    assert "[1.007 84, 1.008 11]" in html
    return html.replace("[1.007 84, 1.008 11]", "[1.007 85, 1.008 12]")


def declarations(g):
    return set(g.subjects(CIAAW.isDeclarationOf, None))


def superseded(g):
    return set(g.subjects(CIAAW.hasStatus, Literal("superseded")))


# --- number parsing --------------------------------------------------------------------
@pytest.mark.parametrize("cell, value, unc, unc_digits, printed", [
    ("4.002 602(2)", "4.002602", "0.000002", "2", "4.002 602(2)"),
    ("0.969 41(156)", "0.96941", "0.00156", "156", "0.969 41(156)"),
    ("162.500(1)", "162.500", "0.001", "1", "162.500(1)"),          # trailing zeros are significant
    ("173.045(10)", "173.045", "0.010", "10", "173.045(10)"),
    ("9.012 1831(5)", "9.0121831", "0.0000005", "5", "9.012 1831(5)"),   # no-break space as separator
    ("0.000 8881(71)", "0.0008881", "0.0000071", "71", "0.000 8881(71)"),   # thin space
])
def test_value_with_uncertainty(cell, value, unc, unc_digits, printed):
    p = c.parse_quantity(cell)
    assert (p.kind, p.value, p.uncertainty, p.uncertainty_digits) == ("value", value, unc, unc_digits)
    assert p.printed == printed


def test_interval_and_special_cells():
    p = c.parse_quantity("[1.007 84, 1.008 11]")
    assert (p.kind, p.lower, p.upper) == ("interval", "1.00784", "1.00811")
    assert p.printed == "[1.007 84, 1.008 11]"
    assert c.parse_quantity("[0.0000, 0.0207]").printed == "[0.0000, 0.0207]"     # zeros as printed
    assert c.parse_quantity("1").kind == "exact" and c.parse_quantity("1").printed == "1"
    assert c.parse_quantity("—").kind == "none"
    assert c.parse_quantity("-").kind == "none"
    assert c.parse_quantity("[97]").mass_number == 97
    with pytest.raises(ValueError):
        c.parse_quantity("about 3")


# --- reading the tables ----------------------------------------------------------------
def test_tables_are_read_completely():
    g, warnings, problems = build()
    assert problems == []
    assert len(list(g.subjects(RDF.type, CIAAW.ChemicalElement))) == 8
    assert len(list(g.subjects(RDF.type, CIAAW.Isotope))) == 24      # Tc has none
    # the only warning is the expected one for a fixture subset
    assert len(warnings) == 1 and "Z = 1..118" in warnings[0]


def test_rows_without_an_opening_tr_are_read():
    # regression: ciaaw.org leaves out <tr> on many rows. Browsers repair that, but html.parser
    # dropped those cells, which merged each such element into the element before it.
    html = """<table><tbody>
<tr><td rowspan="2">22</td><td rowspan="2">Ti</td><td rowspan="2">titanium</td><td>46</td><td>0.0825(3)</td><td rowspan="2"></td></tr>
<tr><td>47</td><td>0.9175(3)</td></tr>
<td rowspan="2">23</td><td rowspan="2">V</td><td rowspan="2">vanadium</td><td>50</td><td>0.002 50(10)</td><td rowspan="2"></td></tr>
<tr><td>51</td><td>0.997 50(10)</td></tr>
</tbody></table>"""
    problems = []
    elements = c.read_abundances(html, problems)
    assert problems == []
    assert [(e.symbol, [a for a, _ in e.isotopes]) for e in elements] == [("Ti", [46, 47]), ("V", [50, 51])]


# --- the shape of the data ------------------------------------------------------------
def test_hydrogen_follows_the_model():
    g, _, _ = build()
    h = ELEMENTS.Hydrogen
    assert (h, CIAAW.hasAtomicNumber, Literal(1)) in g and (h, CIAAW.hasSymbol, Literal("H")) in g
    decl = g.value(h, CIAAW.hasCurrentStandardAtomicWeight)
    assert decl == WEIGHTS["H-2009"]
    assert (decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration) in g
    assert (decl, CIAAW.isDeclarationOf, h) in g and (h, CIAAW.hasDeclaration, decl) in g
    assert (decl, CIAAW.hasFootnote, FOOTNOTES.m) in g
    assert g.value(decl, CIAAW.hasStartValidity) == Literal("2009", datatype=XSD.gYear)    # from supplement.csv
    assert g.value(decl, CIAAW.hasStatus) == Literal("current")
    assert (g.value(decl, CIAAW.hasLowerBound), g.value(decl, CIAAW.hasUpperBound)) == (dec("1.00784"), dec("1.00811"))
    assert g.value(decl, CIAAW.hasValueAsString) == Literal("[1.007 84, 1.008 11]")
    assert g.value(decl, CIAAW.hasUnit) == UNIT_ONE
    for iso, lo, hi in ((ISOTOPES["H-1"], "0.99972", "0.99999"), (ISOTOPES["H-2"], "0.00001", "0.00028")):
        ab = g.value(iso, CIAAW.hasCurrentAbundance)
        assert (ab, CIAAW.isDeclarationOf, iso) in g
        assert (ab, CIAAW.hasFootnote, FOOTNOTES.m) in g          # element-level footnote
        assert (g.value(ab, CIAAW.hasLowerBound), g.value(ab, CIAAW.hasUpperBound)) == (dec(lo), dec(hi))
    assert (h, CIAAW.hasIsotope, ISOTOPES["H-1"]) in g
    assert g.value(ISOTOPES["H-1"], RDFS.label) == Literal("hydrogen-1", lang="en")


def test_there_are_no_blank_nodes():
    """Every node has an IRI, and so a page, as in the SI Reference Point."""
    g, _, _ = build()
    assert not [t for t in g if any(isinstance(x, BNode) for x in t)]


def test_every_declaration_gives_exactly_one_form_of_value():
    g, _, _ = build()
    forms = {"interval": {"hasLowerBound", "hasUpperBound"}, "value": {"hasValue", "hasStandardUncertainty"},
             "exact": {"hasValue"}}
    for decl in declarations(g):
        numbers = {p.split("#")[-1] for p in map(str, g.predicates(decl, None))} & \
                  {"hasLowerBound", "hasUpperBound", "hasValue", "hasStandardUncertainty"}
        assert numbers in forms.values(), decl
        assert g.value(decl, CIAAW.hasValueAsString) is not None and g.value(decl, CIAAW.hasUnit) == UNIT_ONE


def test_value_with_uncertainty_is_stored_exactly():
    g, _, _ = build()
    dy = g.value(ELEMENTS.Dysprosium, CIAAW.hasCurrentStandardAtomicWeight)
    assert g.value(dy, CIAAW.hasValue) == dec("162.500")
    assert str(g.value(dy, CIAAW.hasValue)) == "162.500"                 # lexical form keeps the zeros
    assert g.value(dy, CIAAW.hasStandardUncertainty) == dec("0.001")
    assert g.value(dy, CIAAW.hasValueAsString) == Literal("162.500(1)")
    ca44 = g.value(ISOTOPES["Ca-44"], CIAAW.hasCurrentAbundance)
    assert g.value(ca44, CIAAW.hasStandardUncertainty) == dec("0.00110")
    assert str(g.value(ca44, CIAAW.hasValueAsString)).endswith("(110)")


def test_exact_values_and_elements_without_stable_isotopes():
    g, _, _ = build()
    be = g.value(ISOTOPES["Be-9"], CIAAW.hasCurrentAbundance)
    assert g.value(be, CIAAW.hasValue) == dec("1")
    assert g.value(be, CIAAW.hasStandardUncertainty) is None
    assert g.value(be, CIAAW.hasValueAsString) == Literal("1")
    tc = ELEMENTS.Technetium
    assert g.value(tc, CIAAW.hasCurrentStandardAtomicWeight) is None   # "—" in the table: no declaration
    assert g.value(tc, CIAAW.hasLongestLivedMassNumber) == Literal(97)
    assert g.value(tc, CIAAW.hasIsotope) is None


def test_zero_lower_bound_and_footnotes():
    g, _, _ = build()
    ar36 = g.value(ISOTOPES["Ar-36"], CIAAW.hasCurrentAbundance)
    assert str(g.value(ar36, CIAAW.hasLowerBound)) == "0.0000"
    assert str(g.value(ar36, CIAAW.hasValueAsString)).startswith("[0.0000, ")
    for a in (36, 38, 40):                                          # g, r given on the first row only
        decl = g.value(ISOTOPES[f"Ar-{a}"], CIAAW.hasCurrentAbundance)
        assert set(g.objects(decl, CIAAW.hasFootnote)) == {FOOTNOTES.g, FOOTNOTES.r}
    for letter in ("g", "m", "r"):
        note = FOOTNOTES[letter]
        assert (note, RDF.type, CIAAW.Footnote) in g and (note, SKOS.notation, Literal(letter)) in g
        assert g.value(note, SKOS.definition) is not None and (note, PROV.wasDerivedFrom, WEIGHTS_PAGE) in g


def test_abundances_sum_to_one_where_values_are_given():
    g, _, _ = build()
    for name in ("Neon", "Calcium", "Dysprosium", "Helium"):
        total = sum(Decimal(str(g.value(g.value(iso, CIAAW.hasCurrentAbundance), CIAAW.hasValue)))
                    for iso in g.objects(ELEMENTS[name], CIAAW.hasIsotope))
        assert abs(total - 1) <= Decimal("0.0005"), name


# --- model invariants ------------------------------------------------------------------
def test_exactly_one_current_pointer_and_it_is_in_the_history():
    g, _, _ = build()
    for el in g.subjects(RDF.type, CIAAW.ChemicalElement):
        cur = list(g.objects(el, CIAAW.hasCurrentStandardAtomicWeight))
        assert len(cur) <= 1
        for d in cur:
            assert (el, CIAAW.hasDeclaration, d) in g
    for iso in g.subjects(RDF.type, CIAAW.Isotope):
        cur = list(g.objects(iso, CIAAW.hasCurrentAbundance))
        assert len(cur) == 1 and (iso, CIAAW.hasDeclaration, cur[0]) in g
    assert {str(o) for o in g.objects(None, CIAAW.hasStatus)} == {"current"}


def test_hash_iri_style_depends_on_value_only():
    g1, _, _ = build(supplement=False, iri_style="hash")
    g2, _, _ = build(supplement=True, iri_style="hash")
    d1 = g1.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight)
    d2 = g2.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight)
    assert d1 == d2                                                 # supplement does not change the IRI
    assert str(d1).endswith(c.short_hash("H", "[1.00784,1.00811]"))
    assert not str(d1).endswith(c.short_hash("H", "[1.00784,1.00812]"))


# --- IRIs, provenance, headers -------------------------------------------------------------
def test_each_kind_of_resource_has_its_own_namespace():
    g, _, _ = build()
    assert g.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight) == WEIGHTS["H-2009"]
    assert g.value(ISOTOPES["H-1"], CIAAW.hasCurrentAbundance) == ABUNDANCES["H-1"]       # no year known
    assert g.value(ISOTOPES["H-2"], CIAAW.hasCurrentAbundance) == ABUNDANCES["H-2-2016"]  # year from supplement
    assert g.value(ELEMENTS.Calcium, CIAAW.hasCurrentStandardAtomicWeight) == WEIGHTS.Ca


def test_chebi_matches_come_from_the_supplement():
    g, _, _ = build()
    assert (ELEMENTS.Hydrogen, SKOS.closeMatch, CHEBI["49637"]) in g
    assert (ISOTOPES["H-1"], SKOS.closeMatch, CHEBI["29236"]) in g
    assert (ISOTOPES["H-2"], SKOS.closeMatch, CHEBI["29237"]) in g
    assert g.value(ELEMENTS.Helium, SKOS.closeMatch) is None               # nothing invented


def test_every_element_isotope_and_declaration_names_its_source():
    g, _, _ = build()
    for element in g.subjects(RDF.type, CIAAW.ChemicalElement):
        assert WEIGHTS_PAGE in set(g.objects(element, PROV.wasDerivedFrom)), element
    for isotope in g.subjects(RDF.type, CIAAW.Isotope):
        assert set(g.objects(isotope, PROV.wasDerivedFrom)) == {ABUNDANCES_PAGE}, isotope
    for decl in declarations(g):
        page = WEIGHTS_PAGE if (decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration) in g else ABUNDANCES_PAGE
        assert page in set(g.objects(decl, PROV.wasDerivedFrom)), decl
    # what came from the supplement says so
    assert SUPPLEMENT_FILE in set(g.objects(WEIGHTS["H-2009"], PROV.wasDerivedFrom))
    assert SUPPLEMENT_FILE in set(g.objects(ABUNDANCES["H-2-2016"], PROV.wasDerivedFrom))
    assert SUPPLEMENT_FILE not in set(g.objects(WEIGHTS.Ca, PROV.wasDerivedFrom))
    for source in (WEIGHTS_PAGE, ABUNDANCES_PAGE, SUPPLEMENT_FILE):
        assert (source, RDF.type, PROV.Entity) in g and g.value(source, RDFS.label) is not None
    assert g.value(WEIGHTS_PAGE, DCTERMS.publisher) == BODIES.CIAAW


def test_reports_are_resources_named_after_their_doi():
    g, _, _ = build()
    report = REPORTS["pac-2015-0503"]
    assert (report, RDF.type, CIAAW.Report) in g
    assert g.value(report, DCTERMS.identifier) == Literal("10.1515/pac-2015-0503")
    assert g.value(report, RDFS.seeAlso) == URIRef("https://doi.org/10.1515/pac-2015-0503")
    assert (ABUNDANCES["H-1"], CIAAW.hasDefiningReport, report) in g
    assert g.value(WEIGHTS["H-2009"], CIAAW.hasDefiningReport) is None       # the supplement gives no DOI for it


def test_every_namespace_has_a_header_with_version_sources_and_rights():
    g, _, _ = build()
    for prefix in ("elements", "isotopes", "weights", "abundances", "reports", "footnotes", "bodies"):
        head = URIRef(str(getattr(NS, prefix)))
        assert (head, RDF.type, OWL.Ontology) in g, prefix
        assert g.value(head, OWL.versionInfo) is not None and g.value(head, OWL.imports) == NS.ontology
        assert g.value(head, DCTERMS.rights) is not None and g.value(head, DCTERMS.license) is None
        assert set(g.objects(head, DCTERMS.rightsHolder)) == {BODIES.CIAAW, BODIES.IUPAC}
        members = {s for s in g.subjects() if str(s).startswith(str(head)) and s != head}
        member_sources = {o for s in members for o in g.objects(s, PROV.wasDerivedFrom)}
        assert member_sources <= set(g.objects(head, PROV.wasDerivedFrom)), prefix


def test_bodies_are_described():
    g, _, _ = build()
    assert g.value(BODIES.CIAAW, DCTERMS.isPartOf) == BODIES.IUPAC
    assert g.value(BODIES.CIAAW, SKOS.altLabel) == Literal("CIAAW")


# --- the chain of revisions (--previous) -------------------------------------------------
def test_changed_value_supersedes_the_old_declaration():
    first, _, _ = build()
    prev = roundtrip(first)
    sup = {**c.load_supplement(str(FIX / "supplement.csv")), ("H", "standard"): {"year": "2027", "doi": "", "chebi": ""}}
    g, _, _ = build(weights_html=changed_weights_html(), sup=sup, previous=prev)
    h, old, new = ELEMENTS.Hydrogen, WEIGHTS["H-2009"], WEIGHTS["H-2027"]
    assert g.value(h, CIAAW.hasCurrentStandardAtomicWeight) == new
    assert set(g.objects(h, CIAAW.hasDeclaration)) == {old, new}
    assert g.value(old, CIAAW.hasStatus) == Literal("superseded")
    assert g.value(new, CIAAW.hasStatus) == Literal("current")
    assert (old, CIAAW.hasNextDeclaration, new) in g and (new, CIAAW.hasPreviousDeclaration, old) in g
    assert g.value(old, CIAAW.hasEndValidity) == Literal("2027", datatype=XSD.gYear)
    # the old declaration is complete and still carries its original value and year
    assert str(g.value(old, CIAAW.hasStartValidity)) == "2009"
    assert g.value(old, CIAAW.hasLowerBound) == dec("1.00784")
    assert g.value(new, CIAAW.hasLowerBound) == dec("1.00785")
    # footnote text of copied declarations is still defined
    assert (FOOTNOTES.m, SKOS.notation, Literal("m")) in g


def test_unchanged_values_are_not_superseded():
    first, _, _ = build()
    g, _, _ = build(weights_html=changed_weights_html(), previous=roundtrip(first))
    assert superseded(g) == {WEIGHTS["H-2009"]}                          # only hydrogen changed
    assert len(set(g.objects(ELEMENTS.Helium, CIAAW.hasDeclaration))) == 1
    assert len(set(g.objects(ISOTOPES["H-1"], CIAAW.hasDeclaration))) == 1


def test_exact_values_survive_the_turtle_roundtrip_without_a_false_revision():
    # regression: rdflib writes "1"^^xsd:decimal as 1.0, which once made monoisotopic
    # elements (abundance exactly 1) look like a changed value on every --previous run
    first, _, _ = build()
    prev = roundtrip(first)
    assert prev.value(ABUNDANCES["Be-9"], CIAAW.hasValue).toPython() == Decimal(1)
    g, _, _ = build(previous=prev)
    assert g.value(ABUNDANCES["Be-9"], CIAAW.hasStatus) == Literal("current")
    assert g.value(ISOTOPES["Be-9"], CIAAW.hasCurrentAbundance) == ABUNDANCES["Be-9"]


def test_same_name_new_value_gets_a_hash_suffix():
    first, _, _ = build()                                                   # supplement keeps year 2009
    g, _, _ = build(weights_html=changed_weights_html(), previous=roundtrip(first))
    new = g.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight)
    assert new != WEIGHTS["H-2009"] and str(new).startswith(str(WEIGHTS["H-2009-"]))
    assert (WEIGHTS["H-2009"], CIAAW.hasNextDeclaration, new) in g


def test_year_added_later_is_not_a_new_declaration():
    first, _, _ = build(supplement=False)                                   # IRI without year
    assert first.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight) == WEIGHTS.H
    g, _, _ = build(previous=roundtrip(first))                              # same values, year 2009 now known
    assert g.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight) == WEIGHTS["H-2009"]
    assert set(g.objects(ELEMENTS.Hydrogen, CIAAW.hasDeclaration)) == {WEIGHTS["H-2009"]}
    assert superseded(g) == set()


def test_history_survives_several_generations():
    sup0 = c.load_supplement(str(FIX / "supplement.csv"))

    def with_year(year):
        return {**sup0, ("H", "standard"): {"year": year, "doi": "", "chebi": ""}}
    html2 = changed_weights_html()
    html3 = html2.replace("[1.007 85, 1.008 12]", "[1.007 86, 1.008 13]")
    g1, _, _ = build(sup=with_year("2009"))
    g2, _, _ = build(weights_html=html2, sup=with_year("2027"), previous=roundtrip(g1))
    g3, _, _ = build(weights_html=html3, sup=with_year("2029"), previous=roundtrip(g2))
    d09, d27, d29 = (WEIGHTS[f"H-{y}"] for y in ("2009", "2027", "2029"))
    assert set(g3.objects(ELEMENTS.Hydrogen, CIAAW.hasDeclaration)) == {d09, d27, d29}
    assert g3.value(ELEMENTS.Hydrogen, CIAAW.hasCurrentStandardAtomicWeight) == d29
    assert (d09, CIAAW.hasNextDeclaration, d27) in g3                       # first link kept
    assert (d27, CIAAW.hasNextDeclaration, d29) in g3                       # new link added
    assert superseded(g3) == {d09, d27}
    assert [str(g3.value(d, CIAAW.hasEndValidity)) for d in (d09, d27)] == ["2027", "2029"]


def test_output_is_deterministic_and_parses(tmp_path):
    args = ["--weights", str(FIX / "atomic-weights.htm"), "--abundances", str(FIX / "isotopic-abundances.htm"),
            "--supplement", str(FIX / "supplement.csv"), "--lenient"]
    out1, out2 = tmp_path / "a.ttl", tmp_path / "b.ttl"
    assert c.main(args + ["-o", str(out1)]) == 0
    assert c.main(args + ["-o", str(out2)]) == 0
    assert out1.read_text() == out2.read_text()
    g = Graph()
    g.parse(out1, format="turtle")
    assert len(g) > 500
    assert (ISOTOPES["H-1"], RDF.type, CIAAW.Isotope) in g


def test_base_moves_every_namespace_of_the_data(tmp_path):
    base = "https://example.test/ciaaw/"
    out = tmp_path / "moved.ttl"
    assert c.main(["--weights", str(FIX / "atomic-weights.htm"), "--abundances", str(FIX / "isotopic-abundances.htm"),
                   "--supplement", str(FIX / "supplement.csv"), "--lenient", "--base", base, "-o", str(out)]) == 0
    g = Graph().parse(out, format="turtle")
    moved = Namespaces(base)
    assert (moved.elements.Hydrogen, moved.ciaaw.hasCurrentStandardAtomicWeight, moved.weights["H-2009"]) in g
    ours = {s for s in g.subjects() if "hendrikborgelt" in str(s)}
    assert ours == set()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
