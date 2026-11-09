"""Tests for the charts of the Visualization page: the marks of each kind of value, and the details on hover and focus.

Run with:  python -m pytest tests/test_visualization.py
"""
import re

import pytest

from ciaaw_draft.paths import DATA_DIR
from ciaaw_draft.website import build_site as b
from ciaaw_draft.website import visualization as v


@pytest.fixture(scope="module")
def graph():
    return b.Graph().parse(DATA_DIR / "ciaaw.ttl", format="turtle")


@pytest.fixture(scope="module")
def items(graph):
    return {i.symbol: i for i in (v.Item(e, graph) for e in b.read_elements(graph))}


def tips(chart):
    """(value, other lines) of every tip, in order."""
    return [(value, re.findall(r"<span>(.*?)</span>", rest))
            for value, rest in re.findall(r'<div class="tip \w+" style="left:[\d.]+%"><b>(.*?)</b>(.*?)</div>', chart)]


def test_each_kind_of_value_has_its_own_mark(items):
    """Hydrogen has all three: five values without an uncertainty, four with one, and the interval of 2009."""
    chart = v.history_chart(items["H"])
    assert chart.count('class="exact"') == 5
    assert chart.count('class="value"') == 4
    assert chart.count('class="range"') == 1


def test_the_line_stops_where_an_interval_starts(items):
    """A line through the middle of an interval would read as a value that CIAAW never gave."""
    chart = v.history_chart(items["H"])
    line_ends = [float(x) for x in re.findall(r"H([\d.]+)", re.search(r'class="line" d="([^"]+)"', chart)[1])]
    range_x = float(re.search(r'class="range" x="([\d.]+)"', chart)[1]) + 4
    assert max(line_ends) == pytest.approx(range_x)


def test_an_interval_is_a_band_until_the_end(items):
    chart = v.history_chart(items["H"])
    x, width = re.findall(r'class="band" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', chart)[-1]
    assert float(x) + float(width) == pytest.approx(640 - 24)


def test_every_value_has_a_focusable_area_and_details(items):
    for symbol in ("H", "O", "Fe", "U"):
        chart = v.history_chart(items[symbol])
        history = items[symbol].history
        assert chart.count('<g class="hit" tabindex="0"') == len(history), symbol
        assert [value for value, _ in tips(chart)] == [h["text"] for h in history], symbol


def test_the_details_give_the_kind_the_years_and_the_report(items):
    details = dict(tips(v.history_chart(items["H"])))
    assert details["[1.007 84, 1.008 11]"] == [
        "Interval", "Recommended in 2009, still current", "Report doi:10.1351/PAC-REP-10-09-14"]
    assert details["1.007 94(7)"][:2] == ["Value with a standard uncertainty of 0.000 07", "Recommended in 1981, replaced in 2009"]
    assert details["1.01"][:2] == ["Value given without an uncertainty", "Recommended in 1902, replaced in 1903"]


def test_the_hit_areas_are_labelled_for_screen_readers(items):
    chart = v.history_chart(items["H"])
    assert 'aria-label="2009: [1.007 84, 1.008 11]. Interval. Recommended in 2009, still current.' in chart
    assert '<div class="tips" aria-hidden="true">' in chart


def test_the_key_names_the_kinds_of_the_element(items):
    key = v.history_key(items["H"])
    assert [label for label in re.findall(r"</svg>([^<]+)</li>", key)] == list(v.KINDS.values())


def test_the_changes_chart_has_details_for_every_year(items):
    chart = v.changes_chart(list(items.values()))
    years = {h["year"] for i in items.values() for h in i.history}
    assert chart.count('<g class="hit" tabindex="0"') == len(years)
    in_1902 = sum(h["year"] == 1902 for i in items.values() for h in i.history)
    assert (str(in_1902), ["values recommended in 1902"]) in tips(chart)


def test_one_css_rule_per_position_in_the_longest_chart(graph, items):
    body, _ = v.page(b.read_elements(graph), graph, b.footnotes(graph), "")
    positions = [int(k) for k in re.findall(r":nth-child\((\d+)\):hover", body)]
    years = len({h["year"] for i in items.values() for h in i.history})
    assert positions == list(range(1, years + 1))
    assert "<script" not in body


def test_a_hovered_mark_wins_over_a_focused_one():
    rule = v.tip_rules(2)[1]
    assert ".chart-box:has(.hits>:nth-child(2):hover) .tips>:nth-child(2)" in rule
    assert ".chart-box:not(:has(.hit:hover)):has(.hits>:nth-child(2):focus) .tips>:nth-child(2)" in rule
