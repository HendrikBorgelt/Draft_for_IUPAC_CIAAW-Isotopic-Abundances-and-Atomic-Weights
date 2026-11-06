"""Tests for historical2ttl.py: the historical standard atomic weights, chained to the current ones.

Run with:  python -m pytest tests/test_historical.py
"""
import re
from decimal import Decimal

import pytest
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL

from ciaaw_draft.paths import DATA_DIR
from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.convert import historical2ttl as h
from ciaaw_draft.schema import tap2model as t

CIAAW_TTL = DATA_DIR / "ciaaw.ttl"
HISTORY_CSV = DATA_DIR / "historical" / "IUPAC-TSAW.csv"
ID = Namespace(c.DEFAULT_DATA_NS)
ELEMENT = Namespace(c.DEFAULT_ELEMENT_NS)
CIAAW = Namespace(c.DEFAULT_VOCAB_NS)
QUDT, CHEMROF = c.QUDT, c.CHEMROF


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("history") / "ciaaw.ttl"
    assert h.main(["--current", str(CIAAW_TTL), "--history", str(HISTORY_CSV), "-o", str(out)]) == 0
    return Graph().parse(out, format="turtle")


def declarations(g, element):
    return sorted(g.objects(element, CIAAW.hasDeclaration), key=lambda d: str(g.value(d, DCTERMS.issued)))


def test_silver_history_ends_in_the_current_value_of_1985(graph):
    silver = ELEMENT.Silver
    current = graph.value(silver, CIAAW.currentStandardAtomicWeight)
    assert current == ID["AW-Ag-standard"]
    assert str(graph.value(current, DCTERMS.issued)) == "1985"
    assert graph.value(current, OWL.deprecated) is None
    assert [str(graph.value(d, DCTERMS.issued)) for d in declarations(graph, silver)] == [
        "1902", "1909", "1925", "1961", "1965", "1981", "1985"]


def test_each_superseded_declaration_points_to_the_next_one(graph):
    for element in graph.subjects(RDF_TYPE, CHEMROF.ChemicalElement):
        ds = declarations(graph, element)
        current = graph.value(element, CIAAW.currentStandardAtomicWeight)
        if current is None:
            continue
        assert ds[-1] == current
        for older, newer in zip(ds, ds[1:]):
            assert graph.value(older, OWL.deprecated) is not None
            assert newer in set(graph.objects(older, DCTERMS.isReplacedBy))
            assert older in set(graph.objects(newer, DCTERMS.replaces))


RDF_TYPE = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#").type


def test_one_current_declaration_per_element_and_none_of_them_deprecated(graph):
    for element in graph.subjects(RDF_TYPE, CHEMROF.ChemicalElement):
        currents = set(graph.objects(element, CIAAW.currentStandardAtomicWeight))
        assert len(currents) <= 1
        assert all(graph.value(c, OWL.deprecated) is None for c in currents)


def test_the_exact_value_of_oxygen_in_1902_is_kept_without_an_uncertainty(graph):
    oxygen_1902 = ID["AW-O-standard-1902"]
    value = graph.value(oxygen_1902, CIAAW.value)
    assert Decimal(str(graph.value(value, QUDT.numericValue))) == 16
    assert graph.value(value, QUDT.standardUncertainty) is None


def test_the_history_conforms_to_the_shapes(graph, tmp_path):
    conforms = t.validate_graph(graph, t.load_shapes())[0]
    assert conforms


def test_running_it_again_adds_nothing(tmp_path, graph):
    first = tmp_path / "first.ttl"
    second = tmp_path / "second.ttl"
    assert h.main(["--current", str(CIAAW_TTL), "--history", str(HISTORY_CSV), "-o", str(first)]) == 0
    assert h.main(["--current", str(first), "--history", str(HISTORY_CSV), "-o", str(second)]) == 0
    assert len(Graph().parse(first)) == len(Graph().parse(second)) == len(graph)


def test_every_historical_declaration_has_a_value(graph):
    for decl in graph.subjects(RDF_TYPE, CIAAW.StandardAtomicWeightDeclaration):
        assert graph.value(decl, CIAAW.value) is not None, decl


def test_a_newest_value_that_differs_from_the_current_value_stops_the_script(tmp_path):
    csv_text = HISTORY_CSV.read_text(encoding="utf-8")
    # The newest recommendation of silver, 107.8682(2), changed to another value.
    changed = csv_text.replace('"107.8682(2)"', '"107.9999(2)"', 1)
    assert changed != csv_text
    history = tmp_path / "history.csv"
    history.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="not the current value"):
        h.main(["--current", str(CIAAW_TTL), "--history", str(history), "-o", str(tmp_path / "out.ttl")])


def test_every_source_is_a_doi_and_n_a_is_not_turned_into_one(graph):
    sources = [str(o) for o in graph.objects(None, DCTERMS.source)]
    assert sources and all(re.match(r"https://doi\.org/10\.\d", s) for s in sources)
