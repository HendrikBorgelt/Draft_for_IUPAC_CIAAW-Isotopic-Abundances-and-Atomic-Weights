"""Tests for the static site.  Run with:  python -m pytest test_site.py"""
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup
from rdflib import Graph
from rdflib.compare import isomorphic

import build_site as b

HERE = Path(__file__).parent
NB = b.NBSP


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "_site"
    assert b.main(["--out", str(out)]) == 0
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
    empty = b.Element(b.URIRef("https://example.org/data/Neptunium"), "neptunium", "Np", 93, None, [], [], None, [])
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
    assert b.main(["--out", str(out), "--wikidata", str(tmp_path / "missing.csv")]) == 0
    assert "wikidata.org" not in text_of(out / "elements" / "Hydrogen.md")


# --- Bibliography ---------------------------------------------------------------------------
def test_element_page_has_a_bibliography_of_its_sources(site):
    html = text_of(site / "elements" / "Hydrogen.html")
    assert ">Bibliography</h2>" in html
    assert "Isotopic compositions of the elements 2013" in html      # formatted by citation.js
    assert 'href="https://doi.org/10.1515/pac-2015-0503"' in html


def test_no_bibliography_without_sources(site):
    assert "Bibliography" not in text_of(site / "elements" / "Technetium.html")


def test_bibliography_falls_back_to_the_doi_when_not_cached(tmp_path):
    h = next(e for e in b.read_elements(b.Graph().parse(HERE / "example-output.ttl", format="turtle")) if e.symbol == "H")
    assert "Isotopic compositions" in b.bibliography(h, b.read_references(b.REFERENCES_JSON))
    assert "10.1515/pac-2015-0503</div>" not in b.bibliography(h, {}) and "10.1515/pac-2015-0503 <a" in b.bibliography(h, {})
