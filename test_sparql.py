"""Tests for the example SPARQL queries, the SPARQL page and the endpoint setup.

Run with:  python -m pytest test_sparql.py

These tests use Oxigraph, the engine behind the endpoint, at the version in the worker's lockfile.
They do not run the Cloudflare Worker itself.
"""
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyoxigraph as ox
import pytest
from bs4 import BeautifulSoup
from rdflib import Graph
from ruamel.yaml import YAML

import build_site as b

HERE = Path(__file__).parent
DATA = HERE / "example-output.ttl"
QUERY_FILES = sorted((HERE / "sparql" / "queries").glob("*.rq"))
ENDPOINT = "https://another-endpoint.example/sparql"
PREFIXES = """PREFIX ciaaw: <https://example.org/ciaaw-draft#>
PREFIX ex: <https://example.org/data/>
PREFIX qudt: <http://qudt.org/schema/qudt/>
"""


@pytest.fixture(scope="module")
def store():
    s = ox.Store()
    s.load(DATA.read_bytes(), format=ox.RdfFormat.TURTLE)
    return s


@pytest.fixture(scope="module")
def graph():
    return Graph().parse(DATA, format="turtle")


@pytest.fixture(scope="module")
def examples():
    return {e.title: e for e in b.read_examples(DATA)}


def build(tmp_path_factory, endpoint):
    out = tmp_path_factory.mktemp("site") / "_site"
    assert b.main(["--out", str(out), "--sparql-endpoint", endpoint]) == 0
    return out


@pytest.fixture(scope="module")
def plain_site(tmp_path_factory):
    return build(tmp_path_factory, "")


@pytest.fixture(scope="module")
def live_site(tmp_path_factory):
    return build(tmp_path_factory, ENDPOINT)


def comparable(value):
    """The engines write numbers differently (162.500 and 162.5), so compare them as numbers."""
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return str(value)


def text_of(path):
    return path.read_text(encoding="utf-8")


# --- the data on the engine of the endpoint -----------------------------------------------
@pytest.mark.parametrize("name", ["example-output.ttl", "example-output-next.ttl"])
def test_oxigraph_loads_every_triple(name):
    s = ox.Store()
    s.load((HERE / name).read_bytes(), format=ox.RdfFormat.TURTLE)
    assert len(s) == len(Graph().parse(HERE / name, format="turtle"))


def test_oxigraph_version_matches_the_worker_lockfile():
    assert ox.__version__ == "0.5.11"


def test_oxigraph_normalises_decimals_but_significant_figures_survive(store):
    """Why the SPARQL page warns about trailing zeros, and why significantFigures is in the model."""
    query = PREFIXES + """SELECT ?value ?figures WHERE {
        ex:Dysprosium ciaaw:currentStandardAtomicWeight/ciaaw:value ?q .
        ?q qudt:numericValue ?value ; ciaaw:significantFigures ?figures }"""
    row = next(iter(store.query(query)))
    assert row["value"].value == "162.5"
    assert row["figures"].value == "6"


def test_zero_bounds_lose_their_printed_form_on_the_endpoint(store):
    """A known gap: 0.0000 and 0.000 both become 0, and zero has no significant figures recorded."""
    query = PREFIXES + "SELECT ?lower WHERE { ex:Abundance-Ar36 ciaaw:value/qudt:lowerBound ?lower }"
    assert next(iter(store.query(query)))["lower"].value == "0"
    assert "qudt:lowerBound 0.0000" in text_of(DATA)              # the Turtle keeps the digits


# --- the example queries ------------------------------------------------------------------
def test_there_are_example_queries():
    assert len(QUERY_FILES) >= 5


@pytest.mark.parametrize("path", QUERY_FILES, ids=lambda p: p.stem)
def test_query_starts_with_a_title_and_a_description(path):
    title, description = path.read_text(encoding="utf-8").splitlines()[:2]
    assert re.match(r"# \S", title) and re.match(r"# \S", description)


@pytest.mark.parametrize("path", QUERY_FILES, ids=lambda p: p.stem)
def test_query_returns_results_and_both_engines_agree(path, store, graph):
    query = path.read_text(encoding="utf-8")
    solutions = store.query(query)
    names = [v.value for v in solutions.variables]
    oxigraph = [[None if row[n] is None else comparable(row[n].value) for n in names] for row in solutions]
    rdflib = [[None if value is None else comparable(value) for value in row] for row in graph.query(query)]
    assert oxigraph, path.name
    assert oxigraph == rdflib


def test_weights_query_gives_both_forms_and_skips_technetium(examples):
    rows = {row[0]: row for row in examples["Standard atomic weights"].rows}
    assert len(rows) == 7 and "Tc" not in rows
    assert rows["H"][1:3] == ["1.00784", "1.00811"]
    assert rows["He"][3:5] == ["4.002602", "0.000002"]


def test_abundance_totals_are_close_to_one(examples):
    totals = {row[0]: Decimal(row[1]) for row in examples["Total abundance per element"].rows}
    assert "H" not in totals and "Be" in totals                  # hydrogen is given as intervals
    assert all(abs(total - 1) <= Decimal("0.0005") for total in totals.values())


def test_terms_are_shortened(examples):
    subjects = [row[0] for row in examples["Matches to ChEBI"].rows]
    assert "ex:H-1" in subjects
    assert all(row[1].startswith("chebi:") for row in examples["Matches to ChEBI"].rows)


# --- the page without an endpoint ---------------------------------------------------------
def test_page_says_when_a_build_has_no_endpoint(plain_site):
    assert "has no live endpoint" in text_of(plain_site / "sparql.md")


def test_no_form_or_script_without_an_endpoint(plain_site):
    html = text_of(plain_site / "sparql.html")
    assert 'id="sparql-form"' not in html and "sparql.js" not in html and b.SPARQL_FORM_MARK not in html


def test_every_query_and_its_results_are_on_the_page(plain_site, examples):
    soup = BeautifulSoup(text_of(plain_site / "sparql.html"), "html.parser")
    assert len(soup.select("code.language-sparql")) == len(QUERY_FILES)
    headings = [h.get_text() for h in soup.find_all("h3")]
    cells = {td.get_text() for td in soup.find_all("td")}
    for example in examples.values():
        assert example.title in headings
        assert all(value in cells for row in example.rows for value in row if value)


def test_page_shows_what_the_endpoint_returns_and_explains_it(plain_site):
    text = text_of(plain_site / "sparql.md")
    assert "| Dy |  |  | 162.5 | 0.001 |" in text
    assert "`162.500` comes back as `162.5`" in text


def test_invented_value_is_not_in_the_results(plain_site):
    assert "1.00785" not in text_of(plain_site / "sparql.md")


def test_sparql_page_is_reachable(plain_site):
    soup = BeautifulSoup(text_of(plain_site / "downloads.html"), "html.parser")
    assert soup.select_one('#breadcrumb a[href="sparql.html"]') is not None
    active = BeautifulSoup(text_of(plain_site / "sparql.html"), "html.parser")
    assert active.select_one("#main-navigation .selected").get_text() == "Downloads"
    assert active.select_one("#breadcrumb .selected").get_text() == "SPARQL"


# --- the page with an endpoint ------------------------------------------------------------
def test_form_and_script_appear_with_an_endpoint(live_site):
    soup = BeautifulSoup(text_of(live_site / "sparql.html"), "html.parser")
    assert soup.find(id="sparql-form")["data-endpoint"] == ENDPOINT
    assert soup.find("script", src="static/sparql.js") is not None
    assert (live_site / "static" / "sparql.js").exists()
    assert "has no live endpoint" not in soup.get_text()


def test_page_explains_how_to_call_the_endpoint(live_site):
    text = text_of(live_site / "sparql.md")
    assert 'curl -H "Accept: text/turtle" --data-urlencode "query=CONSTRUCT { ?s a ?o } WHERE { ?s a ?o }"' in text
    assert "need an RDF format in the `Accept` header" in text


def test_page_says_the_endpoint_holds_a_sample(live_site):
    assert "sample of 8 of the 118 elements" in text_of(live_site / "sparql.md")


def test_default_build_uses_the_running_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("SPARQL_ENDPOINT", raising=False)
    out = tmp_path / "_site"
    assert b.main(["--out", str(out)]) == 0
    form = BeautifulSoup(text_of(out / "sparql.html"), "html.parser").find(id="sparql-form")
    assert form["data-endpoint"] == b.SPARQL_ENDPOINT
    assert b.SPARQL_ENDPOINT.endswith("/sparql")                  # the root of the worker answers with 404


def test_empty_environment_variable_keeps_the_running_endpoint(tmp_path, monkeypatch):
    """GitHub Actions turns an unset repository variable into an empty value."""
    monkeypatch.setenv("SPARQL_ENDPOINT", "")
    out = tmp_path / "_site"
    assert b.main(["--out", str(out)]) == 0
    assert b.SPARQL_ENDPOINT in text_of(out / "sparql.md")


def test_markdown_page_has_no_form_markup(live_site):
    text = text_of(live_site / "sparql.md")
    assert ENDPOINT in text and "sparql-form" not in text


def test_endpoint_url_is_escaped(tmp_path):
    out = tmp_path / "_site"
    assert b.main(["--out", str(out), "--sparql-endpoint", 'https://x.test/"><script>alert(1)</script>']) == 0
    form = BeautifulSoup(text_of(out / "sparql.html"), "html.parser").find(id="sparql-form")
    assert form["data-endpoint"] == 'https://x.test/"><script>alert(1)</script>'
    assert form.find("script") is None


def test_endpoint_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARQL_ENDPOINT", ENDPOINT)
    out = tmp_path / "_site"
    assert b.main(["--out", str(out)]) == 0
    assert ENDPOINT in text_of(out / "sparql.md")


# --- the deployment setup -----------------------------------------------------------------
def make_variable(name):
    return re.search(rf"^{name}\s*\?=\s*(.+)$", text_of(HERE / "Makefile"), flags=re.M).group(1).strip()


def test_worker_is_pinned_to_a_full_commit():
    assert re.fullmatch(r"[0-9a-f]{40}", make_variable("WORKER_REF"))


def test_worker_name_matches_the_running_endpoint():
    assert f"//{make_variable('WORKER_NAME')}." in b.SPARQL_ENDPOINT


def test_endpoint_loads_the_published_current_data():
    assert make_variable("SPARQL_DATA").endswith("/data/ciaaw.ttl")


def test_deploy_job_is_optional_and_runs_after_the_site():
    workflow = YAML(typ="safe").load(text_of(HERE / ".github" / "workflows" / "pages.yml"))
    job = workflow["jobs"]["deploy-sparql"]
    assert job["needs"] == "deploy"                               # the worker must load the new data
    assert "vars.SPARQL_DEPLOY == 'true'" in job["if"] and "pull_request" in job["if"]
    assert job["steps"][-1]["run"] == "make sparql-deploy"
    assert "secrets.CLOUDFLARE_API_TOKEN" in job["steps"][-1]["env"]["CLOUDFLARE_API_TOKEN"]


def test_workflow_does_not_override_the_endpoint():
    """An unset repository variable would reach the build as an empty value."""
    workflow = YAML(typ="safe").load(text_of(HERE / ".github" / "workflows" / "pages.yml"))
    step = next(s for s in workflow["jobs"]["build"]["steps"] if "make site" in s.get("run", ""))
    assert "SPARQL_ENDPOINT" not in step.get("env", {})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
