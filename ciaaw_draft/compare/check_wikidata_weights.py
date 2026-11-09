#!/usr/bin/env python3
"""Compare the standard atomic weights in the CIAAW Turtle with the mass of the elements in Wikidata.

Wikidata is queried with SPARQL for the chemical elements (Q11344) with an atomic number (P1086) and their mass
(P2067), which Wikidata gives in dalton (Q483261) and which is the atomic weight of the element. Deprecated
statements are skipped. Elements are matched on the atomic number. A local weight and a Wikidata mass agree if
 * the local weight is an interval and the Wikidata value is inside it,
 * the local weight has an uncertainty and the Wikidata value is within it,
 * the local weight has none and the values are equal,
 * or the Wikidata value has bounds (a rounded value, 15.999 with [15.998, 16]) that contain the local weight.
Each Wikidata value comes with the sources of its references: the "stated in" (P248) items, with the DOI (P356)
of the item if it has one. Differences are written to STDOUT, one per line, in the kinds of check_wikidata.py:

    DIFFERENT       hydrogen   local [1.00784, 1.00811]  Q556 wikidata 1.1  source 10.1351/...
    NO-VALUE        ...        the Wikidata item has no mass
    NOT-FOUND       ...        no Wikidata item matches the local element
    UNIT            ...        the mass is not in dalton
    ONLY-RESOURCE   ...        Wikidata has a mass for an element that is not in the local data

Only the current, non-deprecated standard atomic weight declaration is checked.

Usage:
    python -m ciaaw_draft.compare.check_wikidata_weights data/ciaaw.ttl
    python -m ciaaw_draft.compare.check_wikidata_weights data/ciaaw.ttl --sparql-json saved-results.json      # no network
    python -m ciaaw_draft.compare.check_wikidata_weights data/ciaaw.ttl --json site/wikidata-weights-comparison.json

The JSON has the same keys as that of check_wikidata.py (generated, source, local_isotopes, which here counts the
local elements, resource_items, counts, differences).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS

from ciaaw_draft.compare.check_wikidata import (CHEMROF, CIAAW, OWL_DEPRECATED, P_ATOMIC_NUMBER, P_DOI, P_STATED_IN, QUDT, WD,
                            fetch_wikidata, row, show, show_wd, unique_sources)

P_MASS = "P2067"
Q_DALTON = WD + "Q483261"
Q_CHEMICAL_ELEMENT = "Q11344"

QUERY = f"""
SELECT ?item ?label ?z ?statement ?amount ?lower ?upper ?unit ?source ?sourceLabel ?doi WHERE {{
  ?item wdt:P31 wd:{Q_CHEMICAL_ELEMENT} ; wdt:{P_ATOMIC_NUMBER} ?z .
  ?item rdfs:label ?label . FILTER(LANG(?label) = "en")
  OPTIONAL {{
    ?item p:{P_MASS} ?statement .
    ?statement wikibase:rank ?rank ; psv:{P_MASS} ?v .
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


def group_wikidata(bindings: list[dict]) -> dict[int, dict]:
    """Atomic number -> {qid, label, values: [(amount, lower, upper)], sources: one list per value, odd_units}.
    An atomic number with several items (rare) is pooled. A statement with several sources comes in several rows."""
    elements: dict[int, dict] = {}
    seen: dict[str, dict] = {}                      # statement -> its sources, so that its value is added once
    for b in bindings:
        z = int(Decimal(b["z"]))
        qid = b["item"].rsplit("/", 1)[-1]
        element = elements.setdefault(z, {"qid": qid, "label": b["label"], "values": [], "sources": [], "odd_units": set()})
        if qid not in element["qid"].split("/"):
            element["qid"] += "/" + qid
        if "amount" not in b:
            continue
        statement = b.get("statement") or f"{b['item']}#{len(element['values'])}"
        if statement not in seen:
            if b.get("unit") not in (None, Q_DALTON):
                element["odd_units"].add(b["unit"])
            lower, upper = b.get("lower"), b.get("upper")
            element["values"].append((Decimal(b["amount"]), Decimal(lower) if lower else None,
                                      Decimal(upper) if upper else None))
            seen[statement] = {}
            element["sources"].append(seen[statement])
        if "source" in b:
            source_qid = b["source"].rsplit("/", 1)[-1]
            source = seen[statement].setdefault(source_qid, {"qid": source_qid, "label": b.get("sourceLabel", ""), "doi": ""})
            source["doi"] = source["doi"] or b.get("doi", "")
    for element in elements.values():
        element["sources"] = [list(s.values()) for s in element["sources"]]
    return elements


def local_weights(g: Graph) -> list[dict]:
    """The current standard atomic weight of every element: name, Z and either amount ~ uncertainty or lower/upper."""
    out = []
    for element in sorted(g.subjects(RDF.type, CHEMROF.ChemicalElement), key=str):
        z, label = g.value(element, CHEMROF.atomic_number), g.value(element, RDFS.label)
        decl = g.value(element, CIAAW.currentStandardAtomicWeight)
        if z is None or label is None or decl is None or g.value(decl, OWL_DEPRECATED) == Literal(True):
            continue
        value = g.value(decl, CIAAW.value)
        if value is None:
            continue
        num, lo, hi = (g.value(value, QUDT[p]) for p in ("numericValue", "lowerBound", "upperBound"))
        entry = {"name": str(label), "z": int(z)}
        if num is not None:
            unc = g.value(value, QUDT.standardUncertainty)
            entry["amount"], entry["uncertainty"] = num.toPython(), unc.toPython() if unc is not None else None
        elif lo is not None and hi is not None:
            entry["lower"], entry["upper"] = lo.toPython(), hi.toPython()
            entry["amount"] = (entry["lower"] + entry["upper"]) / 2
        else:
            continue
        out.append(entry)
    return out


def agrees(entry: dict, value: tuple) -> bool:
    amount, lower, upper = value
    local_lower, local_upper = entry.get("lower", entry["amount"]), entry.get("upper", entry["amount"])
    if lower is not None and upper is not None and lower <= local_lower and local_upper <= upper:
        return True
    if "lower" in entry:
        return entry["lower"] <= amount <= entry["upper"]
    unc = entry.get("uncertainty")
    return abs(amount - entry["amount"]) <= unc if unc is not None else amount == entry["amount"]


def compare(local: list[dict], wikidata: dict[int, dict]):
    for entry in local:
        element = wikidata.get(entry["z"])
        if element is None:
            yield row("NOT-FOUND", entry["name"], f"local {show(entry)}  no Wikidata item", local=show(entry))
        elif not element["values"]:
            yield row("NO-VALUE", entry["name"], f"local {show(entry)}  {element['qid']} has no mass",
                      element["qid"], show(entry))
        elif not any(agrees(entry, v) for v in element["values"]):
            theirs = " | ".join(show_wd(v) for v in element["values"])
            yield row("DIFFERENT", entry["name"], f"local {show(entry)}  {element['qid']} wikidata {theirs}",
                      element["qid"], show(entry), theirs, unique_sources(element["sources"]))
        for unit in sorted(element["odd_units"] if element else ()):
            yield row("UNIT", entry["name"], f"{element['qid']} mass has unexpected unit {unit}", element["qid"],
                      show(entry), unit, unique_sources(element["sources"]))
    known = {e["z"] for e in local}
    for z, element in sorted(wikidata.items()):
        if element["values"] and z not in known:
            yield row("ONLY-RESOURCE", element["label"], f"{element['qid']} wikidata {show_wd(element['values'][0])}  "
                      "not in local data", element["qid"], "", show_wd(element["values"][0]), element["sources"][0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("--sparql-json", type=Path, help="read saved SPARQL JSON results instead of querying Wikidata")
    ap.add_argument("--json", type=Path, help="also write the differences and counts to this JSON file")
    ap.add_argument("--strict", action="store_true", help="exit 1 if there are differences")
    args = ap.parse_args(argv)

    g = Graph()
    g.parse(args.ttl, format="turtle")
    local = local_weights(g)
    wikidata = group_wikidata(fetch_wikidata(args.sparql_json, QUERY))

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
        }, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"# {len(local)} local elements, {len(wikidata)} Wikidata elements, "
          f"{sum(counts.values())} differences {dict(sorted(counts.items()))}", file=sys.stderr)
    return 1 if args.strict and counts else 0


if __name__ == "__main__":
    sys.exit(main())
