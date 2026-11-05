"""Tests for the example SPARQL queries, the SPARQL page and the endpoint setup.

Run with:  python -m pytest test_sparql.py

These tests use Oxigraph, the engine behind the endpoint, at the version in the worker's lockfile.
They do not run the Cloudflare Worker itself.
"""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyoxigraph as ox
import pytest
from bs4 import BeautifulSoup
from rdflib import Graph, Namespace
from ruamel.yaml import YAML

import build_site as b

HERE = Path(__file__).parent
DATA = HERE / "example-output.ttl"
QUERY_FILES = sorted((HERE / "sparql" / "queries").glob("*.rq"))
EXAMPLES = HERE / "sparql" / "examples"
SH = Namespace("http://www.w3.org/ns/shacl#")
ENDPOINT = "https://ciaaw-sparql-prototype.example.workers.dev/sparql"
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
def test_query_has_a_sparql_examples_turtle_file(path):
    assert (EXAMPLES / f"{path.stem}.ttl").exists()
    assert (EXAMPLES / "prefixes.ttl").exists()


def sparql_examples_select(path):
    """The sh:select of a file made by sparql-examples-utils, which must answer like the .rq file."""
    graph = Graph().parse(EXAMPLES / f"{path.stem}.ttl", format="turtle")
    return str(next(graph.objects(None, SH.select)))


@pytest.mark.parametrize("path", QUERY_FILES, ids=lambda p: p.stem)
def test_sparql_examples_turtle_runs_the_same_query(path, store):
    def rows(query):
        solutions = store.query(query)
        names = [v.value for v in solutions.variables]
        return names, [[None if row[n] is None else comparable(row[n].value) for n in names] for row in solutions]

    assert rows(sparql_examples_select(path)) == rows(path.read_text(encoding="utf-8"))


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
def test_page_says_the_endpoint_is_not_running(plain_site):
    text = text_of(plain_site / "sparql.md")
    assert "not running yet" in text and "/issues/1" in text


def test_no_editor_without_an_endpoint(plain_site):
    """The editor throws when it has no endpoint, so the page leaves it out."""
    html = text_of(plain_site / "sparql.html")
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find("sparql-editor") is None
    assert soup.find("script", src=b.EDITOR_URL) is None
    assert b.SPARQL_EDITOR_MARK not in html


def test_query_files_are_published(plain_site):
    for path in QUERY_FILES:
        assert (plain_site / "sparql" / "queries" / path.name).read_bytes() == path.read_bytes()


def test_sparql_examples_are_published(plain_site):
    for path in EXAMPLES.glob("*.ttl"):
        assert (plain_site / "sparql" / "examples" / path.name).read_bytes() == path.read_bytes()


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


def test_sparql_is_a_tab_of_its_own(plain_site):
    soup = BeautifulSoup(text_of(plain_site / "sparql.html"), "html.parser")
    assert soup.select_one("#main-navigation .selected").get_text() == "SPARQL"
    assert soup.select_one('#main-navigation a[href="sparql.html"]') is not None
    assert soup.select_one("#breadcrumb") is None


# --- the page with an endpoint ------------------------------------------------------------
def test_editor_runs_against_the_endpoint(live_site):
    soup = BeautifulSoup(text_of(live_site / "sparql.html"), "html.parser")
    assert soup.find("sparql-editor")["endpoint"] == ENDPOINT
    assert soup.find("sparql-editor")["default-method"] == "POST"
    assert soup.find("script", src=b.EDITOR_URL) is not None
    assert soup.find("script", src="static/sparql-editor.js") is not None
    assert (live_site / "static" / "sparql-editor.js").exists()
    assert "not running yet" not in soup.get_text()


def test_each_example_is_a_tab_of_the_editor(live_site):
    soup = BeautifulSoup(text_of(live_site / "sparql.html"), "html.parser")
    tabs = json.loads(soup.find("sparql-editor")["data-examples"])
    assert [t["id"] for t in tabs] == [f"example-{path.stem}" for path in QUERY_FILES]
    assert [t["query"] for t in tabs] == [path.read_text(encoding="utf-8") for path in QUERY_FILES]
    assert all(t["name"] for t in tabs)


def test_markdown_page_has_no_editor_markup(live_site):
    text = text_of(live_site / "sparql.md")
    assert ENDPOINT in text and "sparql-editor" not in text


def test_endpoint_url_is_escaped(tmp_path):
    out = tmp_path / "_site"
    assert b.main(["--out", str(out), "--sparql-endpoint", 'https://x.test/"><script>alert(1)</script>']) == 0
    editor = BeautifulSoup(text_of(out / "sparql.html"), "html.parser").find("sparql-editor")
    assert editor["endpoint"] == 'https://x.test/"><script>alert(1)</script>'
    assert editor.find("script") is None


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


def test_worker_name_says_it_is_a_prototype():
    assert "prototype" in make_variable("WORKER_NAME")


def test_endpoint_loads_the_published_current_data():
    assert make_variable("SPARQL_DATA").endswith("/data/ciaaw.ttl")


def test_deploy_job_is_optional_and_runs_after_the_site():
    workflow = YAML(typ="safe").load(text_of(HERE / ".github" / "workflows" / "pages.yml"))
    job = workflow["jobs"]["deploy-sparql"]
    assert job["needs"] == "deploy"                               # the worker must load the new data
    assert "vars.SPARQL_ENDPOINT != ''" in job["if"] and "pull_request" in job["if"]
    assert job["steps"][-1]["run"] == "make sparql-deploy"
    assert "secrets.CLOUDFLARE_API_TOKEN" in job["steps"][-1]["env"]["CLOUDFLARE_API_TOKEN"]


def test_sparql_examples_utils_is_pinned_and_cached():
    assert re.fullmatch(r"\d+\.\d+\.\d+", make_variable("SPARQL_EXAMPLES_UTILS_VERSION"))
    workflow = YAML(typ="safe").load(text_of(HERE / ".github" / "workflows" / "pages.yml"))
    steps = workflow["jobs"]["build"]["steps"]
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert "sparql-examples-utils" in cache["with"]["key"]
    assert any(s.get("run") == "make sparql-examples" for s in steps)


def test_site_build_receives_the_endpoint():
    workflow = YAML(typ="safe").load(text_of(HERE / ".github" / "workflows" / "pages.yml"))
    step = next(s for s in workflow["jobs"]["build"]["steps"] if "make site" in s.get("run", ""))
    assert "vars.SPARQL_ENDPOINT" in step["env"]["SPARQL_ENDPOINT"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
