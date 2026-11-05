"""Tests for ciaaw.ttl, the full data that the site publishes, and for the site built from it.

Run with:  python -m pytest test_published_data.py

The other test files use the small example-output.ttl. These tests would have caught the parser bug
that lost isotopes on the real CIAAW pages, and they notice when generated pages go out of date.
"""
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

import pyoxigraph as ox
import pytest
from bs4 import BeautifulSoup
from rdflib import Graph, RDF
from rdflib.compare import isomorphic

import build_site as b
import combine_sparql_examples as combine
import tap2model as t

HERE = Path(__file__).parent
DATA = HERE / "ciaaw.ttl"
CIAAW = b.CIAAW
CHEMROF = b.CHEMROF
ELEMENTS, ISOTOPES, WEIGHTS = 118, 289, 84


@pytest.fixture(scope="module")
def graph():
    return Graph().parse(DATA, format="turtle")


@pytest.fixture(scope="module")
def elements(graph):
    return b.read_elements(graph)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "_site"
    assert b.main(["--out", str(out)]) == 0                       # the defaults, as in the published site
    return out


def text_of(path):
    return path.read_text(encoding="utf-8")


# --- the data -----------------------------------------------------------------------------
def test_the_data_is_complete(graph):
    assert len(set(graph.subjects(RDF.type, CHEMROF.ChemicalElement))) == ELEMENTS
    assert len(set(graph.subjects(RDF.type, CHEMROF.Isotope))) == ISOTOPES
    assert len(set(graph.subject_objects(CIAAW.currentStandardAtomicWeight))) == WEIGHTS


def test_every_atomic_number_is_there_once(elements):
    assert [e.z for e in elements] == list(range(1, ELEMENTS + 1))


def test_no_element_lost_its_isotopes_to_a_neighbour(elements):
    """The symptom of the parser bug: vanadium's isotopes ended up under titanium."""
    by_symbol = {e.symbol: e for e in elements}
    assert [i.mass_number for i in by_symbol["Ti"].isotopes] == [46, 47, 48, 49, 50]
    assert [i.mass_number for i in by_symbol["V"].isotopes] == [50, 51]
    assert [i.mass_number for i in by_symbol["U"].isotopes] == [234, 235, 238]


def test_the_abundance_table_stops_at_uranium(elements):
    assert max(e.z for e in elements if e.isotopes) == 92
    assert not any(e.isotopes or e.longest_lived for e in elements if e.z > 92)


def test_the_data_conforms_without_warnings(graph):
    conforms, report, text = t.validate_graph(graph)
    assert conforms, text
    assert list(report.subjects(RDF.type, t.SH.ValidationResult)) == []


def test_abundances_given_as_values_add_up_to_one(elements):
    for e in elements:
        values = [i.abundance.text.replace(b.NBSP, "") for i in e.isotopes]
        if values and not any(v.startswith("[") for v in values):
            total = sum(Decimal(v.split("(")[0]) for v in values)
            assert abs(total - 1) <= Decimal("0.0005"), e.symbol


def test_no_invented_value_in_the_published_data():
    assert "1.00785" not in text_of(DATA)


# --- the example queries ------------------------------------------------------------------
def test_example_queries_stay_cheap_on_the_full_data():
    """The endpoint has a small CPU budget. An early version of query 7 took over 200 ms."""
    store = ox.Store()
    store.load(DATA.read_bytes(), format=ox.RdfFormat.TURTLE)
    for path in sorted(b.QUERIES.glob("*.rq")):
        query = path.read_text(encoding="utf-8")
        best = 1000.0
        for _ in range(3):
            start = time.perf_counter()
            rows = store.query(query).serialize(format=ox.QueryResultsFormat.JSON)
            best = min(best, (time.perf_counter() - start) * 1000)
        assert rows and best < 100, f"{path.name} took {best:.0f} ms"


# --- the site built from it ---------------------------------------------------------------
def test_site_defaults_to_the_full_data(site):
    assert (site / "data" / "ciaaw.ttl").read_bytes() == DATA.read_bytes()
    make = text_of(HERE / "Makefile")
    assert re.search(r"^TTL\s*\?= ciaaw\.ttl$", make, flags=re.M)
    assert re.search(r"^SITE_DATA\s*\?= \$\(TTL\)$", make, flags=re.M)


def test_no_page_calls_it_a_sample(site):
    for name in ("index", "atomic-weights", "isotopic-abundances", "downloads", "sparql", "comparison",
                 "wikidata-comparison", "bodr-comparison"):
        assert "sample of" not in text_of(site / f"{name}.md").replace("A small sample after", ""), name
        assert "Only a sample" not in text_of(site / f"{name}.md"), name


def test_the_tables_are_complete(site):
    weights = BeautifulSoup(text_of(site / "atomic-weights.html"), "html.parser")
    abundances = BeautifulSoup(text_of(site / "isotopic-abundances.html"), "html.parser")
    assert len(weights.select("tbody tr")) == ELEMENTS
    assert len(abundances.select("tbody tr")) == ISOTOPES + 8      # plus 8 elements with only a longest-lived isotope
    assert "neptunium" not in abundances.get_text()


def test_every_element_has_a_page(site):
    assert len(list((site / "elements").glob("*.html"))) == ELEMENTS


def test_all_internal_links_resolve(site):
    for page in site.rglob("*.html"):
        for link in BeautifulSoup(text_of(page), "html.parser").find_all("a", href=True):
            target = link["href"].partition("#")[0]
            if target and not re.match(r"[a-z]+:", target):
                assert (page.parent / unquote(target)).exists(), f"{page.name}: {target}"


@pytest.mark.parametrize("label, extension, rdf_format, media_type", b.FORMATS)
def test_every_format_holds_the_same_triples(site, graph, label, extension, rdf_format, media_type):
    assert isomorphic(graph, Graph().parse(site / "data" / f"ciaaw.{extension}", format=rdf_format)), label


@pytest.mark.parametrize("resource", b.RESOURCES, ids=lambda r: r.key)
def test_comparisons_are_up_to_date_with_the_data(resource):
    result = json.loads(text_of(resource.json))
    assert result["source"] == DATA.name
    assert result["local_isotopes"] == ISOTOPES


def test_combined_file_holds_the_full_data_and_the_examples(tmp_path, graph):
    combined = tmp_path / "combined.ttl"
    assert combine.main(["-o", str(combined)]) == 0               # the defaults, as in make site
    store = ox.Store()
    store.load(combined.read_bytes(), format=ox.RdfFormat.TURTLE)
    assert len(store) > len(graph)
    count = "SELECT (COUNT(DISTINCT ?e) AS ?n) WHERE { ?e a <https://w3id.org/chemrof/ChemicalElement> }"
    assert next(iter(store.query(count)))["n"].value == str(ELEMENTS)


def test_every_cited_doi_has_a_formatted_reference(graph):
    cached = b.read_references(b.REFERENCES_JSON)
    dois = {str(o).removeprefix("https://doi.org/").upper() for o in graph.objects(None, b.DCTERMS.source)}
    assert dois and dois <= set(cached)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
