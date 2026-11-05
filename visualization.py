"""The Visualization page of the site: the standard atomic weights as a periodic table, their history since 1902,
and the isotopic compositions as segmented bars. Built by build_site.py, which calls page().

The page is HTML and CSS without JavaScript. The periodic table colours each element by the relative uncertainty of
its standard atomic weight (the width of the interval, or twice the standard uncertainty, divided by the value), by
the year of the current value, or by the number of values recommended since 1902; radio inputs switch the colouring
and choose the element whose details and history show below the table. The history comes from
site/historical-atomic-weights.csv, CIAAW's table of every standard atomic weight recommended since 1902
(https://ciaaw.org/historical-atomic-weights.htm, refreshed with `make history`); without the file the page shows the
current values only. The styles are in site/static/style.css under .page-visualization.
"""
from __future__ import annotations

import csv
import html
import math
import re
from collections import Counter, defaultdict
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from pathlib import Path

from rdflib import Graph

import build_site as b

HISTORY_URL = "https://ciaaw.org/historical-atomic-weights.htm"
HISTORY_CREDIT_HTML = (f' Historical values: <a href="{HISTORY_URL}">Historical changes of standard atomic weights</a>, '
                       "CIAAW, ciaaw.org, © CIAAW 2016–2024, reproduced with the acknowledgement its terms ask for.")
HISTORY_CREDIT_MD = (f"Historical values: [Historical changes of standard atomic weights]({HISTORY_URL}), CIAAW, ciaaw.org, "
                     "© CIAAW 2016–2024, reproduced with the acknowledgement its terms ask for.")
FIRST_YEAR, LAST_YEAR = 1900, 2026
BASIS_YEAR = 1961
VIEWS = [("uncertainty", "Relative uncertainty", "Relative uncertainty of the standard atomic weight",
          [(1e-8, "below 10⁻⁸"), (1e-7, "10⁻⁸ to 10⁻⁷"), (1e-6, "10⁻⁷ to 10⁻⁶"), (1e-5, "10⁻⁶ to 10⁻⁵"),
           (1e-4, "10⁻⁵ to 10⁻⁴"), (math.inf, "10⁻⁴ and above")]),
         ("year", "Year of the current value", "Year in which the current value was recommended",
          [(1970, "1969 and before"), (1990, "1970 to 1989"), (2005, "1990 to 2004"), (2013, "2005 to 2012"),
           (2019, "2013 to 2018"), (math.inf, "2019 to 2024")]),
         ("revisions", "Values since 1902", "Number of values recommended since 1902",
          [(5, "1 to 4"), (7, "5 or 6"), (8, "7"), (9, "8"), (11, "9 or 10"), (math.inf, "11 to 13")])]
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#2a78d6", "#1c5cab", "#0d366b"]
INK = ["#0b0b0b", "#0b0b0b", "#0b0b0b", "#ffffff", "#ffffff", "#ffffff"]
ORDINAL = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
PART_INK = ["#0b0b0b", "#0b0b0b", "#ffffff", "#ffffff", "#ffffff"]
SERIES, GRID, AXIS, MUTED, TEXT = "#2a78d6", "#e1e0d9", "#c3c2b7", "#898781", "#222222"
SUPERSCRIPTS = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def plural(n: int, noun: str) -> str:
    return f"{n} {noun if n == 1 else noun + 's'}"


def plain(d: Decimal) -> str:
    return format(d, "f")


def significant(d: Decimal, figures: int, rounding: str = ROUND_HALF_EVEN) -> str:
    if d == 0:
        return "0"
    figures = min(figures, len(d.as_tuple().digits))
    quantum = Decimal(1).scaleb(d.adjusted() - figures + 1)
    return plain(d.quantize(quantum, rounding=rounding))


def compact_text(q: dict) -> str:
    """The value for a cell of the periodic table: five significant figures, an interval with its bounds rounded outward."""
    if q["kind"] == "interval":
        return f"{significant(q['lower'], 5, ROUND_FLOOR)}–{significant(q['upper'], 5, ROUND_CEILING)}"
    return significant(q["value"], 5)


def sci(x: float, markdown: bool = False) -> str:
    exponent = math.floor(math.log10(x))
    mantissa = f"{x / 10 ** exponent:.1f}"
    if markdown:
        return f"{mantissa} × 10{str(exponent).translate(SUPERSCRIPTS)}"
    return f"{mantissa}{b.NBSP}×{b.NBSP}10<sup>{exponent}</sup>"


def bin_index(x: float, bins: list) -> int:
    for i, (upper, _) in enumerate(bins):
        if x < upper:
            return i
    return len(bins) - 1


def quantity(g: Graph, declaration) -> dict:
    """The numbers of a declaration: kind, bounds or value and uncertainty, midpoint and relative uncertainty."""
    node = g.value(declaration, b.CIAAW.value)
    lower, upper = g.value(node, b.QUDT.lowerBound), g.value(node, b.QUDT.upperBound)
    if lower is not None:
        lower, upper = Decimal(str(lower)), Decimal(str(upper))
        mid = (lower + upper) / 2
        return {"kind": "interval", "lower": lower, "upper": upper, "mid": mid, "relative": float((upper - lower) / mid)}
    value = Decimal(str(g.value(node, b.QUDT.numericValue)))
    uncertainty = g.value(node, b.QUDT.standardUncertainty)
    if uncertainty is None:
        return {"kind": "exact", "value": value, "mid": value, "relative": 0.0}
    uncertainty = Decimal(str(uncertainty))
    return {"kind": "value", "value": value, "uncertainty": uncertainty, "mid": value, "relative": float(2 * uncertainty / value)}


def parse_history_value(text: str) -> dict | None:
    """The value of a history row: interval, value with uncertainty or exact; None for a withdrawn value (n/a)."""
    compact = re.sub(r"\s+", "", text.replace("(exact)", ""))
    if compact.lower() in ("", "n/a"):
        return None
    if m := re.fullmatch(r"\[([\d.]+),([\d.]+)\]", compact):
        lower, upper = Decimal(m[1]), Decimal(m[2])
        return {"kind": "interval", "low": lower, "high": upper, "mid": (lower + upper) / 2}
    if m := re.fullmatch(r"([\d.]+)\((\d+)\)", compact):
        value = Decimal(m[1])
        uncertainty = Decimal(m[2]).scaleb(-(len(m[1].split(".")[1]) if "." in m[1] else 0))
        return {"kind": "value", "low": value - uncertainty, "high": value + uncertainty, "mid": value}
    if re.fullmatch(r"[\d.]+", compact):
        value = Decimal(compact)
        return {"kind": "exact", "low": value, "high": value, "mid": value}
    raise ValueError(f"unrecognised historical value {text!r}")


def read_history(path: Path) -> dict[str, list[dict]]:
    """Symbol -> the values recommended since 1902, oldest first: year, text as published, doi, numbers (q)."""
    if not path.exists():
        return {}
    history: dict[str, list[dict]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if len(row) != 5 or row[1] == "no changes":
                continue
            year = re.sub(r"<[^>]+>", "", row[0]).strip()
            if not year.isdigit():
                continue
            doi = row[4].strip()
            history[row[1]].append({"year": int(year), "text": row[3].strip(), "doi": doi if doi.startswith("10.") else "",
                                    "q": parse_history_value(row[3])})
    for entries in history.values():
        entries.sort(key=lambda h: h["year"])
    return dict(history)


def position(z: int) -> tuple[int, int]:
    """Grid row and column of an element: periods 1 to 7 in rows 1 to 7, the f-blocks in rows 9 and 10."""
    if z == 1:
        return 1, 1
    if z == 2:
        return 1, 18
    if z <= 18:
        period = 2 if z <= 10 else 3
        offset = z - (3 if period == 2 else 11)
        return period, offset + 1 if offset < 2 else offset + 11
    if z <= 54:
        period = 4 if z <= 36 else 5
        return period, z - (19 if period == 4 else 37) + 1
    if z <= 118:
        period = 6 if z <= 86 else 7
        start = 55 if period == 6 else 87
        if z - start < 2:
            return period, z - start + 1
        if z - start < 17:
            return period + 3, z - start + 1
        return period, z - start - 13
    raise ValueError(z)


def sup(a: int, symbol: str) -> str:
    return f"<sup>{a}</sup>{symbol}"


def doi_link(doi: str, markdown: bool = False) -> str:
    if not doi:
        return ""
    return f"[doi:{doi}](https://doi.org/{doi})" if markdown else f'<a href="https://doi.org/{html.escape(doi)}">doi:{html.escape(doi)}</a>'


class Item:
    """One element with the numbers the page needs beside the Element of build_site."""

    def __init__(self, e: b.Element, g: Graph, history: list[dict]):
        self.e = e
        self.symbol, self.z, self.name = e.symbol, e.z, e.name
        self.weight = quantity(g, e.weight.iri) if e.weight else None
        self.history = history
        self.values = [h for h in history if h["q"]]
        self.isotopes = [(i, quantity(g, i.abundance.iri)) for i in e.isotopes]

    def bins(self) -> dict[str, int | None]:
        if self.weight is None:
            return {key: None for key, *_ in VIEWS}
        return {"uncertainty": bin_index(self.weight["relative"], VIEWS[0][3]),
                "year": bin_index(self.values[-1]["year"], VIEWS[1][3]) if self.values else None,
                "revisions": bin_index(len(self.values), VIEWS[2][3]) if self.values else None}


def composition_bar(item: Item) -> str:
    if not item.isotopes:
        return ""
    n = len(item.isotopes)
    last = len(ORDINAL) - 1
    steps = [round(i * last / (n - 1)) for i in range(n)] if n > 1 else [last // 2]
    parts = []
    for (iso, q), step in zip(item.isotopes, steps):
        parts.append(f'<span class="part" style="flex-grow:{float(q["mid"]):.6f};background:{ORDINAL[step]};color:{PART_INK[step]}">'
                     f'<span class="part-label">{sup(iso.mass_number, item.symbol)}</span></span>')
    summary = "; ".join(f"{item.symbol}-{iso.mass_number} {iso.abundance.text}" for iso, _ in item.isotopes)
    return f'<span class="bar" role="img" aria-label="{html.escape(summary)}">{"".join(parts)}</span>'


def cell(item: Item, checked: bool) -> str:
    row, col = position(item.z)
    colours = "".join(f"--{k[0]}:{RAMP[i]};--{k[0]}i:{INK[i]};" for k, i in item.bins().items() if i is not None)
    if item.weight is None:
        klass, value = "cell none", f"[{item.e.longest_lived}]" if item.e.longest_lived else ""
    else:
        klass, value = "cell", compact_text(item.weight).replace("–", "–<br>")
    return (f'<label class="{klass}" style="grid-row:{row};grid-column:{col};{colours}">'
            f'<input type="radio" name="element" value="{item.symbol}"{" checked" if checked else ""}>'
            f'<span class="z">{item.z}</span><span class="symbol">{item.symbol}</span><span class="value">{value}</span></label>')


def svg_text(x: float, y: float, text: str, anchor: str = "start", fill: str = MUTED, size: int = 11) -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{fill}" font-size="{size}">{text}</text>'


def history_chart(item: Item) -> str:
    """Timeline of the values recommended for one element: a step line, uncertainty bars, intervals as bands."""
    pts = item.values
    W, H, L, R, T, B = 640, 220, 84, 24, 20, 30
    lo, hi = min(float(p["q"]["low"]) for p in pts), max(float(p["q"]["high"]) for p in pts)
    if hi == lo:
        half = abs(hi) * 0.0025 or 0.5
        lo, hi = lo - half, hi + half
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    decimals = min(7, max(0, math.ceil(-math.log10(span / 4)) + 1))
    X = lambda year: L + (year - FIRST_YEAR) / (LAST_YEAR - FIRST_YEAR) * (W - L - R)
    Y = lambda v: T + (hi - float(v)) / span * (H - T - B)
    fmt = lambda v: b.group_digits(f"{v:.{decimals}f}")
    out = []
    for k in (1, 2, 3):
        v = lo + span * k / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{GRID}"/>')
        out.append(svg_text(L - 8, Y(v) + 4, fmt(v), "end"))
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" stroke="{AXIS}"/>')
    for year in range(1925, LAST_YEAR, 25):
        out.append(svg_text(X(year), H - B + 16, str(year), "middle"))
    out.append(f'<line x1="{X(BASIS_YEAR):.1f}" x2="{X(BASIS_YEAR):.1f}" y1="{T - 4}" y2="{H - B}" stroke="{AXIS}"/>')
    out.append(svg_text(X(BASIS_YEAR) + 4, T + 4, "¹²C = 12", "start", MUTED, 10))
    path = []
    for i, p in enumerate(pts):
        x, y = X(p["year"]), Y(p["q"]["mid"])
        path.append(f"M{x:.1f} {y:.1f}" if i == 0 else f"H{x:.1f} V{y:.1f}")
    path.append(f"H{W - R}")
    out.append(f'<path d="{" ".join(path)}" fill="none" stroke="{SERIES}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for p in pts:
        x, q = X(p["year"]), p["q"]
        top, bottom = Y(q["high"]), Y(q["low"])
        if q["kind"] == "interval":
            out.append(f'<rect x="{x - 4:.1f}" y="{top:.1f}" width="8" height="{max(bottom - top, 3):.1f}" rx="2" fill="{SERIES}"/>')
        elif q["kind"] == "value" and bottom - top >= 1:
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top:.1f}" y2="{bottom:.1f}" stroke="{SERIES}" stroke-width="2" stroke-linecap="round"/>')
    for p in pts:
        out.append(f'<circle cx="{X(p["year"]):.1f}" cy="{Y(p["q"]["mid"]):.1f}" r="4" fill="{SERIES}" stroke="#fff" stroke-width="2"/>')
    first, last = pts[0], pts[-1]
    out.append(svg_text(X(first["year"]), Y(first["q"]["mid"]) + 17, f'{first["year"]}: {html.escape(first["text"])}', "start", TEXT))
    out.append(svg_text(W - R, Y(last["q"]["mid"]) - 9, f'{last["year"]}: {html.escape(last["text"])}', "end", TEXT))
    label = (f"Standard atomic weight of {item.name}: {plural(len(pts), 'value')} recommended between "
             f"{first['year']} and {last['year']}")
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(label)}">{"".join(out)}</svg>'


def changes_chart(items: list[Item]) -> str:
    """Column chart of how many standard atomic weights the Commission changed in each year."""
    counts = Counter(h["year"] for item in items for h in item.history)
    W, H, L, R, T, B = 960, 220, 44, 16, 24, 30
    ymax = max(25, math.ceil(max(counts.values()) / 25) * 25)
    slot = (W - L - R) / (LAST_YEAR - FIRST_YEAR)
    width = max(3, min(8, slot - 2))
    X = lambda year: L + (year - FIRST_YEAR) * slot
    Y = lambda n: T + (ymax - n) / ymax * (H - T - B)
    out = []
    for n in range(0, ymax + 1, 25):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(n):.1f}" y2="{Y(n):.1f}" stroke="{GRID if n else AXIS}"/>')
        out.append(svg_text(L - 8, Y(n) + 4, str(n), "end"))
    out.append(f'<line x1="{X(BASIS_YEAR) + width / 2:.1f}" x2="{X(BASIS_YEAR) + width / 2:.1f}" y1="{T - 6}" y2="{H - B}" stroke="{AXIS}"/>')
    out.append(svg_text(X(BASIS_YEAR) + width / 2 + 5, T, "¹²C = 12", "start", MUTED, 10))
    labelled = {year for year, _ in counts.most_common(6)}
    for year in range(1925, LAST_YEAR, 25):
        if year not in labelled:
            out.append(svg_text(X(year) + width / 2, H - B + 16, str(year), "middle"))
    for year, n in sorted(counts.items()):
        x, y, base = X(year), Y(n), H - B
        out.append(f'<path d="M{x:.1f} {base} V{y + 2:.1f} Q{x:.1f} {y:.1f} {x + 2:.1f} {y:.1f} H{x + width - 2:.1f} '
                   f'Q{x + width:.1f} {y:.1f} {x + width:.1f} {y + 2:.1f} V{base} Z" fill="{SERIES}"/>')
        if year in labelled:
            out.append(svg_text(x + width / 2, y - 6, str(n), "middle", TEXT))
            out.append(svg_text(x + width / 2, H - B + 16, str(year), "middle", TEXT))
    label = f"{plural(sum(counts.values()), 'standard atomic weight')} recommended between {min(counts)} and {max(counts)}, by year"
    return f'<svg class="chart wide-chart" viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(label)}">{"".join(out)}</svg>'


def panel(item: Item, notes: dict[str, str]) -> str:
    e, w = item.e, item.weight
    lines = [f'<h3>{html.escape(e.name.capitalize())} <span class="symbol-z">{e.symbol}, Z{b.NBSP}={b.NBSP}{e.z}</span></h3>']
    if w is None:
        lines.append(f'<p>No standard atomic weight: the element has no stable isotope. '
                     f'The longest-lived isotope has mass number {e.longest_lived}.</p>' if e.longest_lived
                     else "<p>No standard atomic weight.</p>")
    else:
        kind = {"interval": "an interval", "value": "a value with its standard uncertainty in brackets", "exact": "an exact value"}[w["kind"]]
        relative = f' Relative uncertainty {sci(w["relative"])}.' if w["relative"] else ""
        lines.append(f'<p class="weight"><span class="figure">{e.weight.text}</span></p>')
        lines.append(f'<p>Standard atomic weight, given as {kind}.{relative}</p>')
        if e.weight.notes:
            lines.append('<ul class="notes">' + "".join(f"<li><b>{n}</b> {html.escape(notes[n])}</li>" for n in e.weight.notes) + "</ul>")
        parts = [f"issued {e.weight.issued}" if e.weight.issued else "",
                 ", ".join(f'<a href="{html.escape(s)}">{html.escape(s.removeprefix("https://doi.org/"))}</a>' for s in e.weight.sources)]
        if any(parts):
            lines.append("<p>" + ", ".join(p for p in parts if p) + "</p>")
    if item.history:
        lines.append(f'<h4>{plural(len(item.history), "value")} recommended since {item.history[0]["year"]}</h4>')
        if item.values:
            lines.append(history_chart(item))
        rows = "".join(f'<tr><td class="num">{h["year"]}</td><td class="num">{html.escape(h["text"])}</td><td>{doi_link(h["doi"])}</td></tr>'
                       for h in reversed(item.history))
        lines.append(f'<table><thead><tr><th class="num">Year</th><th class="num">Value</th><th>Report</th></tr></thead><tbody>{rows}</tbody></table>')
    if item.isotopes:
        lines.append(f'<h4>{plural(len(item.isotopes), "isotope")} with an abundance</h4>')
        lines.append(composition_bar(item))
        rows = "".join(f'<tr><td>{sup(iso.mass_number, e.symbol)}</td><td class="num">{iso.abundance.text}</td></tr>' for iso, _ in item.isotopes)
        lines.append(f'<table><thead><tr><th>Isotope</th><th class="num">Amount fraction</th></tr></thead><tbody>{rows}</tbody></table>')
    return f'<section class="panel panel-{e.symbol}" aria-label="{html.escape(e.name)}">{"".join(lines)}</section>'


def legend_block(title: str, bins: list, view: str = "") -> str:
    swatches = "".join(f'<li><span class="swatch" style="background:{RAMP[i]}"></span>{label}</li>' for i, (_, label) in enumerate(bins))
    attr = f' data-view="{view}"' if view else ""
    return (f'<div class="legend"{attr}><span class="legend-title">{title}</span><ul>{swatches}'
            f'<li><span class="swatch none"></span>no standard atomic weight</li></ul></div>')


def switch_rules(items: list[Item], views: list) -> str:
    rules = [f'#content:has(input[value="{i.symbol}"]:checked) .panel-{i.symbol}{{visibility:visible;height:auto;overflow:visible}}' for i in items]
    for key, *_ in views:
        rules.append(f'#content:has(input[name="view"][value="{key}"]:checked) .cell{{background:var(--{key[0]},var(--zebra));color:var(--{key[0]}i,var(--heading))}}')
        rules.append(f'#content:has(input[name="view"][value="{key}"]:checked) .legend[data-view="{key}"]{{visibility:visible;height:auto;margin-top:12px}}')
    return "\n".join(rules)


def markdown_twin(items: list[Item], coverage: str, counts: list[tuple[int, str]], with_history: bool) -> str:
    lines = ["# Visualization", ""] + ([coverage.rstrip(), ""] if coverage else [])
    lines += [f"- {n} {label}" for n, label in counts]
    if with_history:
        lines += ["", HISTORY_CREDIT_MD]
    lines += ["", "## Standard atomic weights", "",
              "| Z | Symbol | Element | Standard atomic weight | Relative uncertainty | Since | Values since 1902 | Isotopes |",
              "|--:|---|---|--:|--:|--:|--:|--:|"]
    for i in items:
        w = i.weight
        weight = i.e.weight.text if w else (f"[{i.e.longest_lived}]" if i.e.longest_lived else "")
        lines.append(f"| {i.z} | {i.symbol} | {i.name} | {weight} | {sci(w['relative'], True) if w and w['relative'] else ''} | "
                     f"{i.values[-1]['year'] if w and i.values else ''} | {len(i.history) or ''} | {len(i.isotopes) or ''} |")
    if any(i.history for i in items):
        lines += ["", "## Values recommended since 1902", "", "| Z | Symbol | Element | Year | Value | Report |", "|--:|---|---|--:|--:|---|"]
        for i in items:
            for h in reversed(i.history):
                lines.append(f"| {i.z} | {i.symbol} | {i.name} | {h['year']} | {h['text']} | {doi_link(h['doi'], True)} |")
    lines += ["", "## Isotopic abundances", "", "| Z | Isotope | Element | Amount fraction |", "|--:|---|---|--:|"]
    for i in items:
        for iso, _ in i.isotopes:
            lines.append(f"| {i.z} | {i.symbol}-{iso.mass_number} | {i.name} | {iso.abundance.text} |")
    return "\n".join(line for line in lines if line is not None) + "\n"


def page(elements: list[b.Element], g: Graph, notes: dict[str, str], history_path: Path, coverage: str) -> tuple[str, str]:
    """The HTML body of the Visualization page and its Markdown twin."""
    history = read_history(history_path)
    items = [Item(e, g, history.get(e.symbol, [])) for e in elements]
    with_history = any(i.history for i in items)
    views = VIEWS if with_history else VIEWS[:1]
    with_weight = [i for i in items if i.weight]
    intervals = [i for i in with_weight if i.weight["kind"] == "interval"]
    isotopes = sum(len(i.isotopes) for i in items)
    counts = [(len(with_weight), f"{'element' if len(with_weight) == 1 else 'elements'} with a standard atomic weight"),
              (len(intervals), "given as an interval"), (len(items) - len(with_weight), "without a standard atomic weight")]
    if with_history:
        recommendations = sum(len(i.history) for i in items)
        counts.append((recommendations, f"{'value' if recommendations == 1 else 'values'} recommended since 1902"))
    counts.append((isotopes, f"{'isotope' if isotopes == 1 else 'isotopes'} with an abundance"))
    tiles = "".join(f'<li class="count"><b>{n}</b><span>{label}</span></li>' for n, label in counts)
    switch = ""
    if with_history:
        options = "".join(f'<li><label><input type="radio" name="view" value="{key}"{" checked" if n == 0 else ""}><span>{short}</span></label></li>'
                          for n, (key, short, _, _) in enumerate(views))
        switch = f'<ul class="views" role="radiogroup" aria-label="Colour of the table">{options}</ul>'
    legends = "".join(legend_block(title, bins, key) for key, _, title, bins in views)
    cells = "".join(cell(i, n == 0) for n, i in enumerate(items))
    panels = "".join(panel(i, notes) for i in items)
    changes = (f"<h2>Changes since 1902</h2>"
               f'<div class="legend"><span class="legend-title">Standard atomic weights changed per year</span></div>'
               f'<div class="wide">{changes_chart(items)}</div>') if with_history else ""
    rows = "".join(f'<div class="row"><span class="row-label"><b>{i.symbol}</b> {i.z}</span>{composition_bar(i)}</div>' for i in items if i.isotopes)
    body = f"""<style>
{switch_rules(items, views)}
</style>
<h1>Visualization</h1>
{b.render_body(coverage) if coverage else ""}
<ul class="counts">{tiles}</ul>
<h2>Standard atomic weights</h2>
{switch}
<div class="wide"><div class="scroll"><div class="table" role="radiogroup" aria-label="Periodic table">{cells}</div></div></div>
<div class="legends">{legends}</div>
<div class="panels">{panels}</div>
{changes}
<h2>Isotopic composition</h2>
<div class="legend"><span class="legend-title">Amount fraction of each isotope, in order of mass number</span><ul><li><span class="swatch" style="background:{ORDINAL[0]}"></span>lightest isotope</li><li><span class="swatch" style="background:{ORDINAL[-1]}"></span>heaviest isotope</li></ul></div>
<div class="rows">{rows}</div>
<p>Current values from the <a href="atomic-weights.html">table of standard atomic weights</a> and the
<a href="isotopic-abundances.html">isotopic abundances</a>.{HISTORY_CREDIT_HTML if with_history else ""}</p>
"""
    return body, markdown_twin(items, coverage, counts, with_history)
