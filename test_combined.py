"""Tests for combined.ttl: the data, the shared sparql-examples prefixes and every example in one file.

Run with:  python -m pytest test_combined.py
"""
import re
from pathlib import Path

import pyoxigraph as ox
import pytest
from rdflib import Graph, Namespace
from rdflib.namespace import RDF

import combine_sparql_examples as combine

HERE = Path(__file__).parent
DATA = HERE / "example-output.ttl"
EXAMPLES = HERE / "sparql" / "examples"
QUERY_FILES = sorted((HERE / "sparql" / "queries").glob("*.rq"))
SH = Namespace("http://www.w3.org/ns/shacl#")


@pytest.fixture(scope="module")
def combined(tmp_path_factory):
    path = tmp_path_factory.mktemp("combined") / "combined.ttl"
    assert combine.main(["--data", str(DATA), "--examples", str(EXAMPLES), "-o", str(path)]) == 0
    return path


@pytest.fixture(scope="module")
def graph(combined):
    return Graph().parse(combined, format="turtle")


def parts():
    example_files = sorted(p for p in EXAMPLES.glob("*.ttl") if p.name != "prefixes.ttl")
    return [EXAMPLES / "prefixes.ttl", combine.PREFIXES_TO_ADD, DATA, *example_files]


def test_combined_file_parses_and_loads_in_oxigraph(combined):
    store = ox.Store()
    store.load(combined.read_bytes(), format=ox.RdfFormat.TURTLE)
    assert len(store) == len(Graph().parse(combined, format="turtle"))


def test_every_example_is_in_the_combined_file(graph):
    executables = set(graph.subjects(RDF.type, SH.SPARQLSelectExecutable))
    assert len(executables) == len(QUERY_FILES)


def test_examples_share_the_one_prefix_node_that_declares_the_prefixes(graph):
    used = set(graph.objects(None, SH.prefixes))
    declaring = set(graph.subjects(SH.declare, None))
    assert len(used) == 1 and used == declaring


def test_every_prefix_the_data_declares_is_declared_for_the_examples(graph):
    data_prefixes = set(re.findall(r"^@prefix (\w+):", DATA.read_text(encoding="utf-8"), flags=re.M))
    declared = set(graph.objects(None, SH.prefix))
    assert data_prefixes <= {str(p) for p in declared}


def test_no_triple_is_lost_when_the_parts_are_joined(graph):
    """Joining the text keeps every triple. Triples are counted per part, since a set would merge repeats."""
    assert len(graph) == sum(len(Graph().parse(p, format="turtle")) for p in parts())

