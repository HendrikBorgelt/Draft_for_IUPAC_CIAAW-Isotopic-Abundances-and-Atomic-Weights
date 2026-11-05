"""Tests for the static site.  Run with:  python -m pytest test_site.py"""
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup
from rdflib import Graph
from rdflib.compare import isomorphic

import build_site as b
import ciaaw2ttl as c

HERE = Path(__file__).parent
NB = b.NBSP
SAMPLE = ["--data", str(HERE / "example-output.ttl")]            # small and fast; test_published_data.py has the rest


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "_site"
    assert b.main([*SAMPLE, "--out", str(out)]) == 0
    return out


def pages(site, suffix):
    return sorted(site.rglob(f"*.{suffix}"))


def text_of(path):
    return path.read_text(encoding="utf-8")


# --- notation -----------------------------------------------------------------------------
@pytest.mark.parametrize("number, expected", [
    ("1.00784", f"1.007{NB}84"),
    ("4.002602", f"4.002{NB}602"),
    ("9.0121831", f"9.012{NB}1831"),        # a lone last digit joins the group before it
    ("0.0008881", f"0.000{NB}8881"),
    ("0.0000", "0.0000"),
    ("39.792", "39.792"),
    ("162.500", "162.500"),
    ("1", "1"),
])
def test_digit_grouping_follows_the_ciaaw_tables(number, expected):
    assert b.group_digits(number) == expected


def cell_texts(html):
    return [" ".join(td.get_text().split()) for td in BeautifulSoup(html, "html.parser").find_all("td")]


def test_weights_table_uses_ciaaw_notation(site):
    cells = cell_texts(text_of(site / "atomic-weights.html"))
    for expected in ("[1.007 84, 1.008 11]", "4.002 602(2)", "9.012 1831(5)", "162.500(1)", "[39.792, 39.963]"):
        assert expected in cells


def test_abundance_table_uses_ciaaw_notation(site):
    cells = cell_texts(text_of(site / "isotopic-abundances.html"))
    for expected in ("[0.999 72, 0.999 99]", "0.969 41(156)", "[0.0000, 0.0207]", "[97]"):
        assert expected in cells
    assert "1" in cells and "1.0" not in cells             # beryllium is printed as in the table


def test_elements_missing_from_the_abundance_table_get_no_row():
    """The CIAAW table stops at uranium, so elements 93 to 118 should not appear as empty rows."""
    empty = b.Element(b.URIRef(f"{c.DEFAULT_ELEMENT_NS}Neptunium"), "neptunium", "Np", 93, None, [], [], None, [])
    assert "neptunium" not in b.page_isotopic_abundances([empty], {})


def test_element_without_standard_atomic_weight(site):
    assert "gives no standard atomic weight" in text_of(site / "elements" / "Technetium.md")


# --- nobody may mistake this for the official site ----------------------------------------
def test_every_html_page_has_the_watermark_and_the_banner(site):
    found = pages(site, "html")
    assert len(found) >= 14
    for path in found:
        soup = BeautifulSoup(text_of(path), "html.parser")
        assert soup.find(class_="watermark") is not None, path
        banner = soup.find(class_="prototype-banner")
        assert banner is not None and "not an official" in banner.get_text(), path
        assert "Unofficial" in soup.title.get_text(), path
        assert soup.find("meta", attrs={"name": "robots", "content": "noindex"}) is not None, path


def test_watermark_image_is_published_and_used(site):
    assert "NOT AN OFFICIAL CIAAW PAGE" in text_of(site / "static" / "watermark.svg")
    css = text_of(site / "static" / "style.css")
    assert 'url("watermark.svg")' in css and "pointer-events: none" in css


def test_every_generated_markdown_page_starts_with_the_notice(site):
    for html in pages(site, "html"):
        assert text_of(html.with_suffix(".md")).startswith("> **PROTOTYPE:**"), html


def test_no_official_artwork_is_used(site):
    for html in pages(site, "html"):
        assert BeautifulSoup(text_of(html), "html.parser").find("img") is None, html


def test_invented_revision_value_is_labelled_and_stays_off_the_tables(site):
    assert "is invented" in text_of(site / "revision-example.md")
    assert f"1.007{NB}85" in text_of(site / "revision-example.md")
    for name in ("atomic-weights.md", "elements/Hydrogen.md"):
        assert f"1.007{NB}85" not in text_of(site / name)


def test_sample_is_labelled_as_a_sample(site):
    for name in ("index.md", "atomic-weights.md", "isotopic-abundances.md", "downloads.md"):
        assert "sample of 8 of the 118 elements" in text_of(site / name), name


# --- links --------------------------------------------------------------------------------
def local_targets(path):
    soup = BeautifulSoup(text_of(path), "html.parser")
    urls = [a["href"] for a in soup.find_all("a", href=True)] + [l["href"] for l in soup.find_all("link", href=True)]
    return [u for u in urls if not re.match(r"[a-z]+:", u)]


def test_all_internal_links_and_anchors_resolve(site):
    checked = 0
    for page in pages(site, "html"):
        for url in local_targets(page):
            target, _, anchor = url.partition("#")
            file = (page.parent / unquote(target)).resolve() if target else page
            assert file.exists(), f"{page.name}: {url}"
            if anchor:
                soup = BeautifulSoup(text_of(file), "html.parser")
                assert soup.find(id=anchor) is not None, f"{page.name}: {url}"
            checked += 1
    assert checked > 100


def test_markdown_links_stay_in_markdown(site):
    text = text_of(site / "atomic-weights.md")
    assert "(elements/Hydrogen.md)" in text and ".html" not in text


def test_html_links_stay_in_html(site):
    assert 'href="elements/Hydrogen.html"' in text_of(site / "atomic-weights.html")


def test_nav_marks_the_active_section(site):
    soup = BeautifulSoup(text_of(site / "elements" / "Hydrogen.html"), "html.parser")
    assert soup.select_one("#main-navigation .selected").get_text() == "Atomic Weights"


# --- RDF ----------------------------------------------------------------------------------
def test_turtle_is_published_unchanged(site):
    assert (site / "data" / "ciaaw.ttl").read_bytes() == (HERE / "example-output.ttl").read_bytes()
    assert (site / "data" / "ciaaw-revision-example.ttl").read_bytes() == (HERE / "example-output-next.ttl").read_bytes()


@pytest.mark.parametrize("stem, source", [("data/ciaaw", "example-output.ttl"),
                                          ("data/ciaaw-revision-example", "example-output-next.ttl")])
@pytest.mark.parametrize("label, extension, rdf_format, media_type", b.FORMATS)
def test_every_format_holds_the_same_triples(site, stem, source, label, extension, rdf_format, media_type):
    original = Graph().parse(HERE / source, format="turtle")
    translated = Graph().parse(site / f"{stem}.{extension}", format=rdf_format)
    assert isomorphic(original, translated), label


def test_shapes_are_published_in_every_format(site):
    original = Graph().parse(HERE / "model" / "model_shacl.ttl", format="turtle")
    for _, extension, rdf_format, _ in b.FORMATS:
        assert isomorphic(original, Graph().parse(site / "model" / f"model_shacl.{extension}", format=rdf_format)), extension


def test_trailing_zeros_survive_translation(site):
    assert '"162.500"' in text_of(site / "data" / "ciaaw.nt")
    assert "162.500" in text_of(site / "data" / "ciaaw.jsonld")
    assert "162.500" in text_of(site / "data" / "ciaaw.rdf")


def test_model_files_are_published(site):
    for name in ("model_dctap.csv", "dctap.yaml", "rules.ttl", "diagrams.md"):
        assert (site / "model" / name).exists(), name


# --- model page ---------------------------------------------------------------------------
def test_model_page_has_a_diagram_for_every_aspect(site):
    tap = b.t.load_tap()
    soup = BeautifulSoup(text_of(site / "model.html"), "html.parser")
    assert len(soup.select("pre.mermaid")) == len(tap.aspects) + 1
    headings = [h.get_text() for h in soup.find_all("h2")]
    for aspect in tap.aspects.values():
        assert aspect["title"] in headings


def test_model_page_reports_validation(site):
    assert "`example-output.ttl`: conforms" in text_of(site / "model.md")


def test_mermaid_is_only_loaded_where_needed(site):
    assert b.MERMAID_URL in text_of(site / "model.html")
    assert b.MERMAID_URL in text_of(site / "revision-example.html")
    assert b.MERMAID_URL not in text_of(site / "atomic-weights.html")


def test_build_fails_when_data_does_not_conform(tmp_path):
    broken = tmp_path / "broken.ttl"
    broken.write_text(text_of(HERE / "example-output.ttl").replace('chemrof:symbol "He"', 'chemrof:symbol "he"'), encoding="utf-8")
    assert b.main(["--data", str(broken), "--out", str(tmp_path / "out")]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- Subpages -------------------------------------------------------------------------------
def test_isotope_page_shows_its_facts_and_links_to_its_turtle(site):
    html = text_of(site / "id" / "H-1.html")
    assert 'href="../elements/Hydrogen.html"' in html
    assert 'href="H-1.ttl"' in html
    assert "ciaaw_draft:Abundance-H1" in html                       # the Turtle is shown on the page
    assert 'href="Abundance-H1.html"' in html
    assert (site / "id" / "H-1.ttl").exists()


def test_declaration_page_shows_the_value_and_what_it_is_about(site):
    html = text_of(site / "id" / "AW-H-standard-2009.html")
    assert "Standard atomic weight of hydrogen" in html
    assert f"[1.007{NB}84,{NB}1.008{NB}11]" in html
    assert 'href="../elements/Hydrogen.html"' in html


def test_declaration_page_explains_its_footnotes(site):
    html = text_of(site / "id" / "Abundance-Ar36.html")
    for letter in ("g", "r"):
        assert f'id="note-{letter}"' in html


def test_resource_pages_tell_software_where_the_turtle_is(site):
    for path, name in (("id/H-1", "H-1"), ("id/Abundance-H1", "Abundance-H1"), ("elements/Hydrogen", "Hydrogen")):
        link = BeautifulSoup(text_of(site / f"{path}.html"), "html.parser").find("link", rel="alternate")
        assert link["type"] == "text/turtle" and link["href"] == f"{name}.ttl"


def test_other_pages_have_no_turtle_link(site):
    assert BeautifulSoup(text_of(site / "atomic-weights.html"), "html.parser").find("link", rel="alternate") is None


def test_tables_link_to_the_subpages(site):
    assert 'href="id/H-1.html"' in text_of(site / "isotopic-abundances.html")
    element = text_of(site / "elements" / "Hydrogen.html")
    assert 'href="../id/H-1.html"' in element
    assert 'href="../id/AW-H-standard-2009.html"' in element
    assert 'href="../id/Abundance-H1.html"' in element


def test_query_results_link_to_the_subpages(site):
    html = text_of(site / "sparql.html")
    assert '<a href="id/H-1.html">ciaaw_draft:H-1</a>' in html
    assert '<a href="elements/Hydrogen.html">element:Hydrogen</a>' in html
    assert '<a href="id/Abundance-H1.html">ciaaw_draft:Abundance-H1</a>' in html


def test_invented_declaration_gets_no_page(site):
    """The revision example is not in the current data, so its made-up value must not look like a real resource."""
    assert not (site / "id" / "AW-H-standard-2027.html").exists()


def test_revision_diagram_uses_the_prefixes(site):
    text = text_of(site / "revision-example.md")
    assert "element:Hydrogen" in text and "ciaaw_draft:AW-H-standard-2009" in text
    assert '"ex:' not in text


# --- Wikidata links -------------------------------------------------------------------------
def test_element_page_links_to_wikidata_for_the_element_and_each_isotope(site):
    html = text_of(site / "elements" / "Hydrogen.html")
    assert 'href="https://www.wikidata.org/wiki/Q556"' in html        # hydrogen
    assert 'href="https://www.wikidata.org/wiki/Q102296"' in html     # deuterium, A = 2


def test_wikidata_csv_covers_all_elements_and_has_the_isotopes_of_the_site():
    links = b.read_wikidata(b.WIKIDATA_CSV)
    assert all((z, None) in links for z in range(1, b.ELEMENT_COUNT + 1))
    assert (1, 2) in links and (6, 13) in links


def test_site_builds_without_the_wikidata_csv(tmp_path):
    out = tmp_path / "_site"
    assert b.main([*SAMPLE, "--out", str(out), "--wikidata", str(tmp_path / "missing.csv")]) == 0
    assert "wikidata.org" not in text_of(out / "elements" / "Hydrogen.md")


# --- Bibliography ---------------------------------------------------------------------------
def test_element_page_has_a_bibliography_of_its_sources(site):
    html = text_of(site / "elements" / "Hydrogen.html")
    assert ">Bibliography</h2>" in html
    assert "Isotopic compositions of the elements 2013" in html      # formatted by citation.js
    assert 'href="https://doi.org/10.1515/pac-2015-0503"' in html


def test_isotope_and_abundance_pages_have_a_bibliography_of_their_sources(site):
    for name in ("H-1", "Abundance-H1"):
        html = text_of(site / "id" / f"{name}.html")
        assert ">Bibliography</h2>" in html, name
        assert "Isotopic compositions of the elements 2013" in html, name      # formatted by citation.js
        assert 'href="https://doi.org/10.1515/pac-2015-0503"' in html, name


def test_weight_declaration_pages_have_no_bibliography(site):
    assert all("Bibliography" not in text_of(p) for p in (site / "id").glob("AW-*.html"))


def test_no_bibliography_without_sources(site):
    assert "Bibliography" not in text_of(site / "elements" / "Technetium.html")


def test_bibliography_falls_back_to_the_doi_when_not_cached(tmp_path):
    h = next(e for e in b.read_elements(b.Graph().parse(HERE / "example-output.ttl", format="turtle")) if e.symbol == "H")
    assert "Isotopic compositions" in b.bibliography(h, b.read_references(b.REFERENCES_JSON))
    assert "10.1515/pac-2015-0503</div>" not in b.bibliography(h, {}) and "10.1515/pac-2015-0503 <a" in b.bibliography(h, {})


# --- Comparison -----------------------------------------------------------------------------
def test_comparison_overview_lists_the_resources_and_is_in_the_navigation(site):
    html = text_of(site / "comparison.html")
    soup = BeautifulSoup(html, "html.parser")
    assert "Comparison" in [a.get_text() for a in soup.select("#main-navigation a")]
    assert soup.select_one("#main-navigation .selected").get_text() == "Comparison"
    assert 'href="wikidata-comparison.html"' in html and "Wikidata" in html


def test_comparison_page_for_a_resource_lists_the_differences(site):
    html = text_of(site / "wikidata-comparison.html")
    assert "Comparison: Wikidata abundances" in html and "Different values" in html
    assert "argon-36" in html and 'href="https://www.wikidata.org/wiki/Q2019992"' in html
    assert [a.get_text() for a in BeautifulSoup(html, "html.parser").select("#breadcrumb a")] == ["Overview", "Wikidata abundances", "Wikidata atomic weights", "BODR"]


def test_wikidata_weights_comparison_page_lists_elements_with_their_sources(site):
    html = text_of(site / "wikidata-weights-comparison.html")
    assert "Comparison: Wikidata atomic weights" in html and "Only in the resource" in html
    assert "<th>Element</th>" in html and "<th>Source</th>" in html
    assert 'href="https://www.wikidata.org/wiki/Q1054"' in html and "PubChem" in html


def test_bodr_comparison_page_cites_the_article(site):
    html = text_of(site / "bodr-comparison.html")
    assert "Comparison with BODR" in html and "Blue Obelisk" in html
    assert 'href="https://doi.org/10.1186/1758-2946-3-37"' in html and "dysprosium-156" in html
    assert [a.get_text() for a in BeautifulSoup(html, "html.parser").select("#breadcrumb a")] == ["Overview", "Wikidata abundances", "Wikidata atomic weights", "BODR"]


def test_comparison_page_without_the_json(tmp_path):
    resource = b.Resource("x", "Example", "https://example.org", tmp_path / "missing.json", "example", "Text.")
    assert "has not been generated yet" in b.page_resource_comparison(resource)
    assert "not generated" in b.page_comparison([resource])


def test_resources_without_item_url_are_not_linked():
    resource = b.Resource("x", "Example", "https://example.org", Path("none.json"), "example", "Text.")
    assert b.item_links(resource, "A1/A2") == "A1 A2"


def test_about_page_lists_the_authors_with_their_orcid(site):
    html = text_of(site / "about.html")
    for line in (HERE / "AUTHORS").read_text(encoding="utf-8").splitlines():
        name, orcid = line.rsplit(" <", 1)
        assert name in html and f'href="{orcid.rstrip(">")}"' in html


# --- SSSOM ----------------------------------------------------------------------------------
def test_sssom_is_published_where_its_mapping_set_id_says(site):
    text = text_of(site / "data" / "ciaaw.sssom.tsv")
    set_id = re.search(r"^# mapping_set_id: (\S+)$", text, re.M).group(1)
    assert set_id.endswith("/data/ciaaw.sssom.tsv")
    assert "\nsubject_id\tsubject_label" in text and "skos:exactMatch\twd:Q556" in text


def test_downloads_page_links_to_the_sssom_file(site):
    html = text_of(site / "downloads.html")
    assert 'href="data/ciaaw.sssom.tsv"' in html and "SSSOM" in html


def test_comparison_lists_the_sources_of_the_resource_values_and_cites_their_dois(tmp_path, monkeypatch):
    references = tmp_path / "references.json"
    references.write_text(json.dumps({"10.1/ABC": "Author, A. (2020). A paper."}), encoding="utf-8")
    monkeypatch.setattr(b, "REFERENCES_JSON", references)
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"generated": "2026-01-01", "source": "x.ttl", "local_isotopes": 1, "resource_items": 1,
        "counts": {"DIFFERENT": 2}, "differences": [
            {"kind": "DIFFERENT", "name": "a-1", "qid": "Q1", "local": "1", "wikidata": "2", "text": "",
             "sources": [{"qid": "Q11", "label": "Article", "doi": "10.1/abc"}]},
            {"kind": "DIFFERENT", "name": "b-1", "qid": "Q2", "local": "1", "wikidata": "3", "text": "",
             "sources": [{"qid": "Q10", "label": "NuDat", "doi": ""}]}]}), encoding="utf-8")
    resource = b.Resource("x", "Example", "https://example.org", result, "example", "Text.", "https://example.org/")
    md = b.page_resource_comparison(resource)
    assert "| Source |" in md
    assert "[10.1/abc](https://doi.org/10.1/abc)" in md and "[NuDat](https://example.org/Q10)" in md
    assert "## Bibliography" in md and "Author, A. (2020). A paper." in md


def test_comparison_without_sources_has_no_source_column_or_bibliography(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"generated": "2026-01-01", "source": "x.ttl", "local_isotopes": 1, "resource_items": 1,
        "counts": {"DIFFERENT": 1}, "differences": [
            {"kind": "DIFFERENT", "name": "a-1", "qid": "Q1", "local": "1", "wikidata": "2", "text": ""}]}), encoding="utf-8")
    md = b.page_resource_comparison(b.Resource("x", "Example", "https://example.org", result, "example", "Text."))
    assert "Source" not in md and "Bibliography" not in md
