"""Tests for the Wikidata query and the sources (stated in, P248) of the values in check_wikidata.py."""
import json
from decimal import Decimal

from ciaaw_draft.compare import check_wikidata as cw

WD = "http://www.wikidata.org/entity/"


def binding(item="Q1", statement="s1", amount="0.0115", **more):
    b = {"item": WD + item, "label": "deuterium", "z": "1", "n": "1", "statement": WD + "statement/" + statement,
         "amount": amount, "unit": WD + "Q11229"}
    return {**b, **more}


def test_query_skips_deprecated_statements_and_asks_for_the_stated_in_and_its_doi():
    assert "FILTER(?rank != wikibase:DeprecatedRank)" in cw.QUERY
    assert "prov:wasDerivedFrom/pr:P248 ?source" in cw.QUERY
    assert "wdt:P356 ?doi" in cw.QUERY


def test_a_statement_with_several_sources_is_one_value_with_several_sources():
    items = cw.group_wikidata([
        binding(source=WD + "Q10", sourceLabel="NuDat"),
        binding(source=WD + "Q11", sourceLabel="Article", doi="10.1/abc"),
        binding(statement="s2", amount="0.02")])
    item = items[WD + "Q1"]
    assert [v[0] for v in item["values"]] == [Decimal("0.0115"), Decimal("0.02")]
    assert item["sources"] == [[{"qid": "Q10", "label": "NuDat", "doi": ""},
                                {"qid": "Q11", "label": "Article", "doi": "10.1/abc"}], []]


def test_a_different_value_reports_the_sources_of_the_wikidata_value():
    items = cw.group_wikidata([binding(source=WD + "Q11", sourceLabel="Article", doi="10.1/abc")])
    local = [{"name": "hydrogen-2", "zn": (1, 1), "amount": Decimal("0.5"), "uncertainty": None}]
    [difference] = cw.compare(local, items)
    assert difference["kind"] == "DIFFERENT"
    assert difference["sources"] == [{"qid": "Q11", "label": "Article", "doi": "10.1/abc"}]
    json.dumps(difference)
