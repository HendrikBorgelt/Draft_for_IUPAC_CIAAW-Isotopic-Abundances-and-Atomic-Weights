"""Tests for historical2ttl.py: the historical standard atomic weights, chained to the current ones.

Run with:  python -m pytest tests/test_historical.py
"""
import re
from decimal import Decimal

import pytest
from rdflib import Graph, Literal, RDF, URIRef, XSD
from rdflib.namespace import DCTERMS, RDFS

from ciaaw_draft.convert import historical2ttl as h
from ciaaw_draft.namespaces import HISTORY_TABLE, NS, PROV
from ciaaw_draft.paths import DATA_DIR
from ciaaw_draft.schema import tap2model as t

CIAAW_TTL = DATA_DIR / "ciaaw.ttl"
HISTORY_CSV = DATA_DIR / "historical" / "IUPAC-TSAW.csv"
CIAAW, ELEMENTS, WEIGHTS = NS.ciaaw, NS.elements, NS.weights


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("history") / "ciaaw.ttl"
    assert h.main(["--current", str(CIAAW_TTL), "--history", str(HISTORY_CSV), "-o", str(out)]) == 0
    return Graph().parse(out, format="turtle")


def declarations(g, element):
    return sorted(g.objects(element, CIAAW.hasDeclaration), key=lambda d: str(g.value(d, CIAAW.hasStartValidity)))


def test_silver_history_ends_in_the_current_value_of_1985(graph):
    silver = ELEMENTS.Silver
    current = graph.value(silver, CIAAW.hasCurrentStandardAtomicWeight)
    assert current == WEIGHTS["Ag-1985"]
    assert graph.value(current, CIAAW.hasStartValidity) == Literal("1985", datatype=XSD.gYear)
    assert graph.value(current, CIAAW.hasStatus) == Literal("current")
    assert graph.value(current, CIAAW.hasNextDeclaration) is None
    assert [str(graph.value(d, CIAAW.hasStartValidity)) for d in declarations(graph, silver)] == [
        "1902", "1909", "1925", "1961", "1965", "1981", "1985"]


def test_each_superseded_declaration_points_to_the_next_one(graph):
    for element in graph.subjects(RDF.type, CIAAW.ChemicalElement):
        ds = declarations(graph, element)
        current = graph.value(element, CIAAW.hasCurrentStandardAtomicWeight)
        if current is None:
            continue
        assert ds[-1] == current
        for older, newer in zip(ds, ds[1:]):
            assert graph.value(older, CIAAW.hasStatus) == Literal("superseded")
            assert graph.value(older, CIAAW.hasNextDeclaration) == newer
            assert graph.value(newer, CIAAW.hasPreviousDeclaration) == older
            assert graph.value(older, CIAAW.hasEndValidity) == graph.value(newer, CIAAW.hasStartValidity)


def test_one_current_declaration_per_element(graph):
    for element in graph.subjects(RDF.type, CIAAW.ChemicalElement):
        currents = set(graph.objects(element, CIAAW.hasCurrentStandardAtomicWeight))
        assert len(currents) <= 1
        others = set(graph.objects(element, CIAAW.hasDeclaration)) - currents
        assert all(graph.value(d, CIAAW.hasStatus) == Literal("current") for d in currents)
        assert all(graph.value(d, CIAAW.hasStatus) == Literal("superseded") for d in others)


def test_the_year_of_every_weight_is_part_of_its_iri(graph):
    for decl in graph.subjects(RDF.type, CIAAW.StandardAtomicWeightDeclaration):
        year = str(graph.value(decl, CIAAW.hasStartValidity))
        assert str(decl).endswith(f"-{year}"), decl
        assert str(graph.value(decl, RDFS.label)).endswith(f", {year}"), decl


def test_the_exact_value_of_oxygen_in_1902_is_kept_without_an_uncertainty(graph):
    oxygen_1902 = WEIGHTS["O-1902"]
    assert Decimal(str(graph.value(oxygen_1902, CIAAW.hasValue))) == 16
    assert graph.value(oxygen_1902, CIAAW.hasStandardUncertainty) is None
    assert graph.value(oxygen_1902, CIAAW.hasValueAsString) == Literal("16")


def test_the_history_names_its_source(graph):
    for decl in graph.subjects(RDF.type, CIAAW.StandardAtomicWeightDeclaration):
        assert HISTORY_TABLE in set(graph.objects(decl, PROV.wasDerivedFrom)), decl
    assert HISTORY_TABLE in set(graph.objects(URIRef(str(WEIGHTS)), PROV.wasDerivedFrom))
    assert (HISTORY_TABLE, RDF.type, PROV.Entity) in graph


def test_the_history_conforms_to_the_shapes(graph):
    conforms = t.validate_graph(graph, t.load_shapes())[0]
    assert conforms


def test_running_it_again_adds_nothing(tmp_path, graph):
    first = tmp_path / "first.ttl"
    second = tmp_path / "second.ttl"
    assert h.main(["--current", str(CIAAW_TTL), "--history", str(HISTORY_CSV), "-o", str(first)]) == 0
    assert h.main(["--current", str(first), "--history", str(HISTORY_CSV), "-o", str(second)]) == 0
    assert len(Graph().parse(first)) == len(Graph().parse(second)) == len(graph)


def test_every_historical_declaration_has_a_value(graph):
    for decl in graph.subjects(RDF.type, CIAAW.StandardAtomicWeightDeclaration):
        assert graph.value(decl, CIAAW.hasValueAsString) is not None, decl
        assert (graph.value(decl, CIAAW.hasValue) or graph.value(decl, CIAAW.hasLowerBound)) is not None, decl


def test_a_newest_value_that_differs_from_the_current_value_stops_the_script(tmp_path):
    csv_text = HISTORY_CSV.read_text(encoding="utf-8")
    # The newest recommendation of silver, 107.8682(2), changed to another value.
    changed = csv_text.replace('"107.8682(2)"', '"107.9999(2)"', 1)
    assert changed != csv_text
    history = tmp_path / "history.csv"
    history.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="not the current value"):
        h.main(["--current", str(CIAAW_TTL), "--history", str(history), "-o", str(tmp_path / "out.ttl")])


def test_every_report_is_a_doi_and_n_a_is_not_turned_into_one(graph):
    reports = set(graph.subjects(RDF.type, CIAAW.Report))
    assert reports
    for report in reports:
        doi = str(graph.value(report, DCTERMS.identifier))
        assert re.match(r"10\.\d", doi), report
        assert graph.value(report, RDFS.seeAlso) == URIRef(f"https://doi.org/{doi}")
