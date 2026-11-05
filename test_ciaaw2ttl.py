"""Run with:  python -m pytest test_ciaaw2ttl.py   (or: python test_ciaaw2ttl.py)

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
from rdflib import Graph, Literal, Namespace, RDF, XSD

import ciaaw2ttl as c

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
CIAAW = Namespace(c.DEFAULT_VOCAB_NS)
EX = Namespace(c.DEFAULT_DATA_NS)
ELEMENT = Namespace(c.DEFAULT_ELEMENT_NS)
QUDT = c.QUDT


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
    """Hydrogen interval changed (hypothetical revision) to exercise the deprecation chain."""
    html = (FIX / "atomic-weights.htm").read_text(encoding="utf-8")
    assert "[1.007 84, 1.008 11]" in html
    return html.replace("[1.007 84, 1.008 11]", "[1.007 85, 1.008 12]")


def value_node(g, decl):
    return g.value(decl, CIAAW.value)


def decl_of(g, subject, pointer):
    return g.value(subject, pointer)


# --- number parsing --------------------------------------------------------------------
@pytest.mark.parametrize("cell, value, unc, sig, unc_digits", [
    ("4.002 602(2)", "4.002602", "0.000002", 7, "2"),
    ("0.969 41(156)", "0.96941", "0.00156", 5, "156"),
    ("162.500(1)", "162.500", "0.001", 6, "1"),            # trailing zeros are significant
    ("173.045(10)", "173.045", "0.010", 6, "10"),
    ("9.012 1831(5)", "9.0121831", "0.0000005", 8, "5"),   # no-break space as separator
    ("0.000 8881(71)", "0.0008881", "0.0000071", 4, "71"),   # thin space
])
def test_value_with_uncertainty(cell, value, unc, sig, unc_digits):
    p = c.parse_quantity(cell)
    assert (p.kind, p.value, p.uncertainty, p.uncertainty_digits) == ("value", value, unc, unc_digits)
    assert c.sig_figs(p.value) == sig


def test_interval_and_special_cells():
    p = c.parse_quantity("[1.007 84, 1.008 11]")
    assert (p.kind, p.lower, p.upper) == ("interval", "1.00784", "1.00811")
    assert c.sig_figs("1.00784") == 6          # six significant figures, five decimal places
    assert c.sig_figs("1.008") == 4
    assert c.sig_figs("0.0000") is None         # zero: not countable
    assert c.parse_quantity("1").kind == "exact"
    assert c.parse_quantity("—").kind == "none"
    assert c.parse_quantity("-").kind == "none"
    assert c.parse_quantity("[97]").mass_number == 97
    with pytest.raises(ValueError):
        c.parse_quantity("about 3")


# --- reading the tables ----------------------------------------------------------------
def test_tables_are_read_completely():
    g, warnings, problems = build()
    assert problems == []
    assert len(list(g.subjects(RDF.type, c.CHEMROF.ChemicalElement))) == 8
    assert len(list(g.subjects(RDF.type, c.CHEMROF.Isotope))) == 24      # Tc has none
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


def test_hydrogen_matches_the_draft_model():
    g, _, _ = build()
    h = ELEMENT["Hydrogen"]
    assert (h, c.CHEMROF.atomic_number, Literal(1)) in g
    assert g.value(h, CIAAW.standardAtomicWeightLower) == Literal("1.00784", datatype=XSD.decimal)
    assert g.value(h, CIAAW.standardAtomicWeightUpper) == Literal("1.00811", datatype=XSD.decimal)
    decl = decl_of(g, h, CIAAW.currentStandardAtomicWeight)
    assert (decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration) in g
    assert (decl, CIAAW.footnote, CIAAW["note-m"]) in g
    assert str(g.value(decl, c.DCTERMS.issued)) == "2009"          # from supplement.csv
    q = value_node(g, decl)
    assert g.value(q, QUDT.lowerBound) == Literal("1.00784", datatype=XSD.decimal)
    for iso, lo, hi in ((EX["H-1"], "0.99972", "0.99999"), (EX["H-2"], "0.00001", "0.00028")):
        ab = decl_of(g, iso, CIAAW.currentAbundance)
        assert (ab, CIAAW.ofIsotope, iso) in g
        assert (ab, CIAAW.footnote, CIAAW["note-m"]) in g          # element-level footnote
        q = value_node(g, ab)
        assert (g.value(q, QUDT.lowerBound), g.value(q, QUDT.upperBound)) == (
            Literal(lo, datatype=XSD.decimal), Literal(hi, datatype=XSD.decimal))
    assert (ELEMENT["Hydrogen"], CIAAW.hasIsotope, EX["H-1"]) in g


def test_value_with_uncertainty_is_stored_exactly():
    g, _, _ = build()
    q = value_node(g, decl_of(g, ELEMENT["Dysprosium"], CIAAW.currentStandardAtomicWeight))
    assert g.value(q, QUDT.numericValue) == Literal("162.500", datatype=XSD.decimal)
    assert str(g.value(q, QUDT.numericValue)) == "162.500"          # lexical form keeps the zeros
    assert g.value(q, QUDT.standardUncertainty) == Literal("0.001", datatype=XSD.decimal)
    assert g.value(q, CIAAW.significantFigures) == Literal(6)
    ca44 = value_node(g, decl_of(g, EX["Ca-44"], CIAAW.currentAbundance))
    assert g.value(ca44, QUDT.standardUncertainty) == Literal("0.00110", datatype=XSD.decimal)
    assert g.value(ca44, CIAAW.significantFiguresUncertainty) == Literal(3)


def test_exact_values_and_elements_without_stable_isotopes():
    g, _, _ = build()
    be = value_node(g, decl_of(g, EX["Be-9"], CIAAW.currentAbundance))
    assert g.value(be, QUDT.numericValue) == Literal("1", datatype=XSD.decimal)
    assert g.value(be, QUDT.standardUncertainty) is None
    tc = ELEMENT["Technetium"]
    assert g.value(tc, CIAAW.currentStandardAtomicWeight) is None   # "—" in the table: no declaration
    assert g.value(tc, CIAAW.longestLivedMassNumber) == Literal(97)
    assert g.value(tc, CIAAW.hasIsotope) is None


def test_zero_lower_bound_and_footnote_propagation():
    g, _, _ = build()
    ar36 = value_node(g, decl_of(g, EX["Ar-36"], CIAAW.currentAbundance))
    assert g.value(ar36, QUDT.lowerBound) == Literal("0.0000", datatype=XSD.decimal)
    assert g.value(ar36, CIAAW.significantFiguresLower) is None
    for a in (36, 38, 40):                                          # g, r given on the first row only
        decl = decl_of(g, EX[f"Ar-{a}"], CIAAW.currentAbundance)
        assert {str(o) for o in g.objects(decl, CIAAW.footnote)} == {str(CIAAW["note-g"]), str(CIAAW["note-r"])}
    assert (CIAAW["note-m"], c.SKOS.notation, Literal("m")) in g
    assert (CIAAW["note-g"], c.SKOS.notation, Literal("g")) in g


def test_abundances_sum_to_one_where_values_are_given():
    g, _, _ = build()
    for symbol, name in (("Ne", "Neon"), ("Ca", "Calcium"), ("Dy", "Dysprosium"), ("He", "Helium")):
        total = Decimal(0)
        for iso in g.objects(ELEMENT[name], CIAAW.hasIsotope):
            q = value_node(g, decl_of(g, iso, CIAAW.currentAbundance))
            total += Decimal(str(g.value(q, QUDT.numericValue)))
        assert abs(total - 1) <= Decimal("0.0005"), symbol


# --- model invariants ------------------------------------------------------------------
def test_exactly_one_current_pointer_and_it_is_in_the_history():
    g, _, _ = build()
    for el in g.subjects(RDF.type, c.CHEMROF.ChemicalElement):
        cur = list(g.objects(el, CIAAW.currentStandardAtomicWeight))
        assert len(cur) <= 1
        for d in cur:
            assert (el, CIAAW.hasDeclaration, d) in g
    for iso in g.subjects(RDF.type, c.CHEMROF.Isotope):
        cur = list(g.objects(iso, CIAAW.currentAbundance))
        assert len(cur) == 1 and (iso, CIAAW.hasDeclaration, cur[0]) in g


def test_hash_iri_style_depends_on_value_only():
    g1, _, _ = build(supplement=False, iri_style="hash")
    g2, _, _ = build(supplement=True, iri_style="hash")
    d1 = g1.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight)
    d2 = g2.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight)
    assert d1 == d2                                                 # supplement does not change the IRI
    assert str(d1).endswith(c.short_hash("AW-H-standard", "[1.00784,1.00811]"))
    assert not str(d1).endswith(c.short_hash("AW-H-standard", "[1.00784,1.00812]"))


# --- alignment with the draft in issue 20 -------------------------------------------------
def test_readable_iris_follow_the_draft():
    g, _, _ = build()
    assert g.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight) == EX["AW-H-standard-2009"]
    assert g.value(EX["H-1"], CIAAW.currentAbundance) == EX["Abundance-H1"]      # no year known
    assert g.value(EX["H-2"], CIAAW.currentAbundance) == EX["Abundance-H2-2016"]  # year from supplement
    assert g.value(ELEMENT["Calcium"], CIAAW.currentStandardAtomicWeight) == EX["AW-Ca-standard"]


def test_chebi_matches_come_from_the_supplement():
    g, _, _ = build()
    chebi = Namespace("http://purl.obolibrary.org/obo/CHEBI_")
    assert (ELEMENT["Hydrogen"], c.SKOS.closeMatch, chebi["49637"]) in g
    assert (EX["H-1"], c.SKOS.closeMatch, chebi["29236"]) in g
    assert (EX["H-2"], c.SKOS.closeMatch, chebi["29237"]) in g
    assert g.value(ELEMENT["Helium"], c.SKOS.closeMatch) is None               # nothing invented


def test_vocabulary_block_and_publication_nodes_follow_the_draft():
    g, _, _ = build()
    assert (CIAAW.value, c.RDFS.range, QUDT.QuantityValue) in g
    assert (CIAAW.footnote, c.RDFS.range, CIAAW.Footnote) in g
    assert (CIAAW.StandardAtomicWeightDeclaration, c.RDFS.subClassOf, CIAAW.Declaration) in g
    doi = c.URIRef("https://doi.org/10.1515/pac-2015-0503")
    assert (EX["Abundance-H1"], c.DCTERMS.source, doi) in g
    assert list(g.predicate_objects(doi)) == []                          # plain IRI, as in the draft


# --- deprecation chain (--previous) -------------------------------------------------------
def test_changed_value_deprecates_the_old_declaration():
    first, _, _ = build()
    prev = roundtrip(first)
    sup = {**c.load_supplement(str(FIX / "supplement.csv")), ("H", "standard"): {"year": "2027", "doi": "", "chebi": ""}}
    g, _, _ = build(weights_html=changed_weights_html(), sup=sup, previous=prev)
    h, old, new = ELEMENT["Hydrogen"], EX["AW-H-standard-2009"], EX["AW-H-standard-2027"]
    assert g.value(h, CIAAW.currentStandardAtomicWeight) == new
    assert set(g.objects(h, CIAAW.hasDeclaration)) == {old, new}
    assert (old, c.OWL.deprecated, c.Literal(True)) in g
    assert (old, c.ADMS.status, c.ADMS_DEPRECATED) in g
    assert (old, c.DCTERMS.isReplacedBy, new) in g
    assert (new, c.DCTERMS.replaces, old) in g
    assert (new, c.OWL.deprecated, None) not in g
    # old declaration is complete and still carries its original value and year
    assert str(g.value(old, c.DCTERMS.issued)) == "2009"
    assert g.value(value_node(g, old), QUDT.lowerBound) == Literal("1.00784", datatype=XSD.decimal)
    assert g.value(value_node(g, new), QUDT.lowerBound) == Literal("1.00785", datatype=XSD.decimal)
    # the derived shortcut shows only the new value
    assert g.value(h, CIAAW.standardAtomicWeightLower) == Literal("1.00785", datatype=XSD.decimal)
    # footnote text of copied declarations is still defined
    assert (CIAAW["note-m"], c.SKOS.notation, Literal("m")) in g


def test_unchanged_values_are_not_deprecated():
    first, _, _ = build()
    g, _, _ = build(weights_html=changed_weights_html(), previous=roundtrip(first))
    deprecated = set(g.subjects(c.OWL.deprecated, Literal(True)))
    assert deprecated == {EX["AW-H-standard-2009"]}                       # only hydrogen changed
    assert len(set(g.objects(ELEMENT["Helium"], CIAAW.hasDeclaration))) == 1
    assert len(set(g.objects(EX["H-1"], CIAAW.hasDeclaration))) == 1


def test_exact_values_survive_the_turtle_roundtrip_without_false_deprecation():
    # regression: rdflib writes "1"^^xsd:decimal as 1.0, which once made monoisotopic
    # elements (abundance exactly 1) look like a changed value on every --previous run
    first, _, _ = build()
    prev = roundtrip(first)
    be = value_node(prev, EX["Abundance-Be9"])
    assert prev.value(be, QUDT.numericValue).toPython() == Decimal(1)
    g, _, _ = build(previous=prev)
    assert (EX["Abundance-Be9"], c.OWL.deprecated, None) not in g
    assert g.value(EX["Be-9"], CIAAW.currentAbundance) == EX["Abundance-Be9"]


def test_same_name_new_value_gets_a_hash_suffix():
    first, _, _ = build()                                                   # supplement keeps year 2009
    g, _, _ = build(weights_html=changed_weights_html(), previous=roundtrip(first))
    new = g.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight)
    assert new != EX["AW-H-standard-2009"] and str(new).startswith(str(EX["AW-H-standard-2009-"]))
    assert (EX["AW-H-standard-2009"], c.DCTERMS.isReplacedBy, new) in g


def test_year_added_later_is_not_a_new_declaration():
    first, _, _ = build(supplement=False)                                   # IRI without year
    g, _, _ = build(previous=roundtrip(first))                              # same values, year 2009 now known
    assert g.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight) == EX["AW-H-standard-2009"]
    assert set(g.objects(ELEMENT["Hydrogen"], CIAAW.hasDeclaration)) == {EX["AW-H-standard-2009"]}
    assert list(g.subjects(c.OWL.deprecated, None)) == []


def test_history_survives_several_generations():
    sup0 = c.load_supplement(str(FIX / "supplement.csv"))
    def with_year(year):
        return {**sup0, ("H", "standard"): {"year": year, "doi": "", "chebi": ""}}
    html2 = changed_weights_html()
    html3 = html2.replace("[1.007 85, 1.008 12]", "[1.007 86, 1.008 13]")
    g1, _, _ = build(sup=with_year("2009"))
    g2, _, _ = build(weights_html=html2, sup=with_year("2027"), previous=roundtrip(g1))
    g3, _, _ = build(weights_html=html3, sup=with_year("2029"), previous=roundtrip(g2))
    d09, d27, d29 = (EX[f"AW-H-standard-{y}"] for y in ("2009", "2027", "2029"))
    assert set(g3.objects(ELEMENT["Hydrogen"], CIAAW.hasDeclaration)) == {d09, d27, d29}
    assert g3.value(ELEMENT["Hydrogen"], CIAAW.currentStandardAtomicWeight) == d29
    assert (d09, c.DCTERMS.isReplacedBy, d27) in g3                        # first link kept
    assert (d27, c.DCTERMS.isReplacedBy, d29) in g3                        # new link added
    assert set(g3.subjects(c.OWL.deprecated, Literal(True))) == {d09, d27}


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
    assert (EX["H-1"], RDF.type, c.CHEMROF.Isotope) in g


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
