#!/usr/bin/env python3
"""Look up the Wikidata items of the chemical elements and isotopes, for the links on the site.

  python -m ciaaw_draft.compare.wikidata_links [-o site/wikidata.csv]

Writes a CSV with the columns kind,z,a,qid,label. kind is "element" (a is empty) or "isotope"
(a = mass number = atomic number P1086 + number of neutrons P1148). The file is committed, so that
`make site` and the tests work offline. Run `make wikidata-links` to refresh it.

Several items can have the same Z and N (for example the deuteron next to deuterium). Only items that
are an isotope (a subclass of Q25276) are used; if there are still several, the one with a natural
abundance (P2374) wins, then the lowest Q-number. These cases are reported on STDERR.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from ciaaw_draft.paths import SITE_SRC

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "ciaaw-draft-wikidata-links/0.1 (https://github.com/HendrikBorgelt/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights)"
DEFAULT_OUT = SITE_SRC / "wikidata.csv"

ELEMENTS = """
SELECT ?item ?label ?z WHERE {
  ?item wdt:P31 wd:Q11344 ; wdt:P1086 ?z .
  ?item rdfs:label ?label . FILTER(LANG(?label) = "en")
}"""

ISOTOPES = """
SELECT ?item ?label ?z ?n (EXISTS { ?item wdt:P2374 [] } AS ?abundance) WHERE {
  ?item wdt:P31/wdt:P279* wd:Q25276 ; wdt:P1086 ?z ; wdt:P1148 ?n .
  ?item rdfs:label ?label . FILTER(LANG(?label) = "en")
}"""

MAX_Z = 118   # Wikidata also has items for elements that were never observed
COLUMNS = ["kind", "z", "a", "qid", "label"]


def sparql(query: str) -> list[dict]:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return [{k: v["value"] for k, v in b.items()} for b in json.load(resp)["results"]["bindings"]]


def qnumber(item: str) -> int:
    return int(item.rsplit("/", 1)[-1][1:])


def pick(candidates: list[dict], what: str) -> dict:
    ranked = sorted(candidates, key=lambda b: (b.get("abundance") != "true", qnumber(b["item"])))
    if len(ranked) > 1:
        print(f"{what}: {len(ranked)} items ({', '.join(b['item'].rsplit('/', 1)[-1] for b in ranked)}), "
              f"using {ranked[0]['item'].rsplit('/', 1)[-1]}", file=sys.stderr)
    return ranked[0]


def collect() -> list[dict]:
    rows = []
    elements: dict[int, list[dict]] = {}
    for b in sparql(ELEMENTS):
        elements.setdefault(int(float(b["z"])), []).append(b)
    for z, found in sorted(elements.items()):
        if z > MAX_Z:
            continue
        b = pick(found, f"element Z={z}")
        rows.append({"kind": "element", "z": z, "a": "", "qid": b["item"].rsplit("/", 1)[-1], "label": b["label"]})
    isotopes: dict[tuple[int, int], list[dict]] = {}
    for b in sparql(ISOTOPES):
        z, a = int(float(b["z"])), int(float(b["z"])) + int(float(b["n"]))
        if z > MAX_Z:
            continue
        isotopes.setdefault((z, a), []).append(b)
    for (z, a), found in sorted(isotopes.items()):
        b = pick(found, f"isotope Z={z} A={a}")
        rows.append({"kind": "isotope", "z": z, "a": a, "qid": b["item"].rsplit("/", 1)[-1], "label": b["label"]})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    rows = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    kinds = [r["kind"] for r in rows]
    print(f"{kinds.count('element')} elements and {kinds.count('isotope')} isotopes written to {args.output}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
