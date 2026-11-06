#!/usr/bin/env python3
"""Compare the isotopic abundances in the CIAAW Turtle with those in Wikidata.

Wikidata is queried with SPARQL for all items with an atomic number (P1086) and a number of
neutrons (P1148), i.e. the isotopes, with their natural abundance (P2374). (Wikidata items are
not instances of "isotope" Q25276 but of "stable isotope", "isotope of carbon" etc., so the class
is not used.) Local isotopes are matched to Wikidata items by atomic number Z and neutron number
N; the mass number is A = Z + N.
Abundances are compared in percent. Differences are written to STDOUT, one per line:

    DIFFERENT   H-2  local 0.0115~0.0070  Q16143  wikidata 0.0156
    NO-VALUE    ...  the Wikidata item has no abundance
    NOT-FOUND   ...  no Wikidata item matches the local isotope

Only the current, non-deprecated abundance declaration is checked (as in ttl2quickstatements.py).

Usage:
    python -m ciaaw_draft.compare.check_wikidata data/ciaaw.ttl
    python -m ciaaw_draft.compare.check_wikidata data/ciaaw.ttl --sparql-json saved-results.json    # no network
    python -m ciaaw_draft.compare.check_wikidata data/ciaaw.ttl --json site/wikidata-comparison.json  # also write JSON

The JSON (used by the "Wikidata Comparison" page of the site) has the keys generated, source,
local_isotopes, resource_items, counts and differences; each difference has kind, name, qid, local,
wikidata, text and sources. The sources are the "stated in" (P248) of the references of the Wikidata value,
each with qid, label and doi (the DOI, P356, of the source item, empty if it has none).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from ciaaw_draft.namespaces import CHEMROF, CIAAW, QUDT

P_ABUNDANCE = "P2374"

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "ciaaw-draft-check-wikidata/0.1 (https://github.com/; egon.willighagen@gmail.com)"
WD = "http://www.wikidata.org/entity/"
Q_PERCENT = WD + "Q11229"
Q_ONE = WD + "Q199"
P_ATOMIC_NUMBER = "P1086"
P_NEUTRONS = "P1148"
P_STATED_IN = "P248"
P_DOI = "P356"
OWL_DEPRECATED = URIRef("http://www.w3.org/2002/07/owl#deprecated")

# One row per statement and source. Deprecated statements are skipped: Wikidata marks them as not to be used.
# The source is the "stated in" (P248) of the reference; its DOI is the item's DOI (P356), if it has one.
QUERY = f"""
SELECT ?item ?label ?z ?n ?statement ?amount ?lower ?upper ?unit ?source ?sourceLabel ?doi WHERE {{
  ?item wdt:{P_ATOMIC_NUMBER} ?z ; wdt:{P_NEUTRONS} ?n .
  ?item rdfs:label ?label . FILTER(LANG(?label) = "en")
  OPTIONAL {{
    ?item p:{P_ABUNDANCE} ?statement .
    ?statement wikibase:rank ?rank ; psv:{P_ABUNDANCE} ?v .
    FILTER(?rank != wikibase:DeprecatedRank)
    ?v wikibase:quantityAmount ?amount .
    OPTIONAL {{ ?v wikibase:quantityLowerBound ?lower }}
    OPTIONAL {{ ?v wikibase:quantityUpperBound ?upper }}
    OPTIONAL {{ ?v wikibase:quantityUnit ?unit }}
    OPTIONAL {{
      ?statement prov:wasDerivedFrom/pr:{P_STATED_IN} ?source .
      OPTIONAL {{ ?source rdfs:label ?sourceLabel . FILTER(LANG(?sourceLabel) = "en") }}
      OPTIONAL {{ ?source wdt:{P_DOI} ?doi }}
    }}
  }}
}}
"""


def plain(d: Decimal) -> str:
    """Decimal as a string without exponent notation."""
    return format(d, "f")


def percent(fraction: Decimal) -> Decimal:
    return fraction.scaleb(2)


def fetch_wikidata(path: Path | None, query: str = QUERY) -> list[dict]:
    if path:
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    return [{k: v["value"] for k, v in b.items()} for b in data["results"]["bindings"]]


def group_wikidata(bindings: list[dict]) -> dict[str, dict]:
    """Wikidata item IRI -> {qid, label, zn: {(Z, N)}, values: [(amount, lower, upper)] in percent,
    sources: one list per value of {qid, label, doi}}. A statement with several sources comes in several rows."""
    items: dict[str, dict] = {}
    seen: dict[str, dict] = {}                      # statement -> its sources, so that its value is added once
    for b in bindings:
        item = items.setdefault(b["item"], {"qid": b["item"].rsplit("/", 1)[-1], "label": b["label"], "zn": set(),
                                            "values": [], "sources": []})
        item["zn"].add((int(Decimal(b["z"])), int(Decimal(b["n"]))))
        if "amount" not in b:
            continue
        statement = b.get("statement") or f"{b['item']}#{len(item['values'])}"
        if statement not in seen:
            scale = Decimal(100) if b.get("unit") == Q_ONE else Decimal(1)
            if b.get("unit") not in (None, Q_PERCENT, Q_ONE):
                item.setdefault("odd_units", set()).add(b["unit"])
            lower, upper = b.get("lower"), b.get("upper")
            item["values"].append((
                Decimal(b["amount"]) * scale,
                Decimal(lower) * scale if lower is not None else None,
                Decimal(upper) * scale if upper is not None else None,
            ))
            seen[statement] = {}
            item["sources"].append(seen[statement])
        if "source" in b:
            qid = b["source"].rsplit("/", 1)[-1]
            source = seen[statement].setdefault(qid, {"qid": qid, "label": b.get("sourceLabel", ""), "doi": ""})
            source["doi"] = source["doi"] or b.get("doi", "")
    for item in items.values():
        item["sources"] = [list(s.values()) for s in item["sources"]]
    return items


def local_isotopes(g: Graph) -> list[dict]:
    """Current abundance of every isotope, in percent: label, (Z, N), and the expected value."""
    element_of = {i: e for e in g.subjects(RDF.type, CHEMROF.ChemicalElement) for i in g.objects(e, CIAAW.hasIsotope)}
    out = []
    for isotope in sorted(g.subjects(RDF.type, CHEMROF.Isotope), key=str):
        mass_number = g.value(isotope, CIAAW.massNumber)
        decl = g.value(isotope, CIAAW.currentAbundance)
        label = g.value(element_of[isotope], RDFS.label) if isotope in element_of else None
        element = str(label) if label is not None else None
        if mass_number is None or decl is None or element is None or isotope not in element_of:
            continue
        if g.value(decl, OWL_DEPRECATED) == Literal(True):
            continue
        value = g.value(decl, CIAAW.value)
        if value is None:
            continue
        num = g.value(value, QUDT.numericValue)
        lo = g.value(value, QUDT.lowerBound)
        hi = g.value(value, QUDT.upperBound)
        z = g.value(element_of[isotope], CHEMROF.atomic_number)
        a = int(mass_number)
        entry = {
            "name": f"{element}-{mass_number}",
            "zn": (int(z), a - int(z)) if z is not None else None,
        }
        if num is not None:
            amount = percent(num.toPython())
            unc = g.value(value, QUDT.standardUncertainty)
            entry["amount"] = amount
            entry["uncertainty"] = percent(unc.toPython()) if unc is not None else None
        elif lo is not None and hi is not None:
            entry["lower"], entry["upper"] = percent(lo.toPython()), percent(hi.toPython())
            entry["amount"] = (entry["lower"] + entry["upper"]) / 2
        else:
            continue
        out.append(entry)
    return out


def show(entry: dict) -> str:
    if "lower" in entry:
        return f"[{plain(entry['lower'])}, {plain(entry['upper'])}]"
    unc = entry.get("uncertainty")
    return plain(entry["amount"]) + (f"~{plain(unc)}" if unc is not None else "")


def show_wd(value: tuple) -> str:
    amount, lower, upper = value
    if lower is None or upper is None:
        return plain(amount)
    return f"{plain(amount)} [{plain(lower)}, {plain(upper)}]"


def same(entry: dict, value: tuple) -> bool:
    amount, lower, upper = value
    if amount != entry["amount"]:
        return False
    if "lower" in entry:
        return (lower, upper) == (entry["lower"], entry["upper"])
    unc = entry.get("uncertainty")
    if unc is None:
        return True
    return lower is not None and upper is not None and (amount - lower, upper - amount) == (unc, unc)


def row(kind: str, name: str, text: str, qid: str = "", local: str = "", wikidata: str = "",
        sources: list[dict] | None = None) -> dict:
    """A difference; sources are the references (qid, label, doi) of the value in the resource."""
    return {"kind": kind, "name": name, "qid": qid, "local": local, "wikidata": wikidata, "text": text,
            "sources": sources or []}


def unique_sources(lists: list[list[dict]]) -> list[dict]:
    """The sources of several values, each source once."""
    return list({s["qid"]: s for sources in lists for s in sources}.values())


def compare(local: list[dict], wikidata: dict[str, dict]):
    by_zn = {}
    for item in wikidata.values():
        for zn in item["zn"]:
            by_zn.setdefault(zn, []).append(item)
    matched = set()
    for entry in local:
        # several items can share Z and N (isotope, ion, hypernucleus): use those with an abundance
        candidates = by_zn.get(entry["zn"], [])
        with_values = [c for c in candidates if c["values"]]
        item = None
        if candidates:
            used = with_values or candidates
            item = {"qid": "/".join(c["qid"] for c in used), "values": [v for c in used for v in c["values"]],
                    "odd_units": {u for c in used for u in c.get("odd_units", ())},
                    "sources": [s for c in used for s in c["sources"]]}
            matched.update(c["qid"] for c in used)
        if item is None:
            yield row("NOT-FOUND", entry["name"], f"local {show(entry)}  no Wikidata item", local=show(entry))
            continue
        where = f"{item['qid']}"
        if not item["values"]:
            yield row("NO-VALUE", entry["name"], f"local {show(entry)}  {where} has no abundance", where, show(entry))
        elif not any(same(entry, v) for v in item["values"]):
            theirs = " | ".join(show_wd(v) for v in item["values"])
            yield row("DIFFERENT", entry["name"], f"local {show(entry)}  {where} wikidata {theirs}", where, show(entry), theirs,
                      unique_sources(item["sources"]))
        for unit in sorted(item.get("odd_units", ())):
            yield row("UNIT", entry["name"], f"{where} abundance has unexpected unit {unit}", where, show(entry), unit, unique_sources(item["sources"]))
    for item in wikidata.values():
        if item["values"] and item["qid"] not in matched:
            yield row("ONLY-RESOURCE", item["label"], f"{item['qid']} wikidata {show_wd(item['values'][0])}  not in local data",
                      item["qid"], "", show_wd(item["values"][0]), item["sources"][0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("--sparql-json", type=Path, help="read saved SPARQL JSON results instead of querying Wikidata")
    ap.add_argument("--json", type=Path, help="also write the differences and counts to this JSON file")
    ap.add_argument("--strict", action="store_true", help="exit 1 if there are differences")
    args = ap.parse_args(argv)

    g = Graph()
    g.parse(args.ttl, format="turtle")
    local = local_isotopes(g)
    wikidata = group_wikidata(fetch_wikidata(args.sparql_json))

    counts: dict[str, int] = {}
    differences = list(compare(local, wikidata))
    for d in differences:
        counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        cited = ", ".join(s["doi"] or f"{s['label']} ({s['qid']})" for s in d["sources"])
        print(f"{d['kind']:<14}{d['name']:<16}{d['text']}{'  source ' + cited if cited else ''}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "generated": date.today().isoformat(), "source": args.ttl.name, "local_isotopes": len(local),
            "resource_items": len(wikidata), "counts": dict(sorted(counts.items())), "differences": differences,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"# {len(local)} local isotopes, {len(wikidata)} Wikidata isotopes, "
          f"{sum(counts.values())} differences {dict(sorted(counts.items()))}", file=sys.stderr)
    return 1 if args.strict and counts else 0


if __name__ == "__main__":
    sys.exit(main())
