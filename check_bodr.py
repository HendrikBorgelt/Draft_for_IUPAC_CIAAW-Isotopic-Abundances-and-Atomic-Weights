#!/usr/bin/env python3
"""Compare the isotopic abundances in the CIAAW Turtle with those in the Blue Obelisk Data Repository (BODR).

BODR (https://github.com/egonw/bodr, Debian package "bodr", https://doi.org/10.1186/1758-2946-3-37) has
isotopes.xml with a relative abundance in percent for the naturally occurring isotopes. It gives no
uncertainty. Isotopes are matched on atomic number and mass number. Abundances are compared in percent:

  * local value with uncertainty (96.941~0.156): BODR must have the same value;
  * local interval ([0.00, 2.07]): the BODR value must fall within it.

Differences are written to STDOUT in the format of check_wikidata.py, and optionally as JSON.

Usage:
    check_bodr.py build/ciaaw.ttl
    check_bodr.py build/ciaaw.ttl --bodr /usr/share/bodr/isotopes.xml --json site/bodr-comparison.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

from rdflib import Graph

from check_wikidata import local_isotopes, plain, row, show

DEFAULT_BODR = Path("/usr/share/bodr/isotopes.xml")
CML = "{http://www.xml-cml.org/schema}"


def read_bodr(path: Path) -> dict[tuple[int, int], dict]:
    """(Z, A) -> {id, amount: Decimal in percent or None} for every isotope in isotopes.xml."""
    isotopes = {}
    for isotope in ET.parse(path).getroot().iter(f"{CML}isotope"):
        values = {s.get("dictRef"): (s.text or "").strip() for s in isotope.findall(f"{CML}scalar")}
        if "bo:atomicNumber" not in values:
            continue
        abundance = values.get("bo:relativeAbundance")
        isotopes[(int(values["bo:atomicNumber"]), int(isotope.get("number")))] = {
            "id": isotope.get("id"), "amount": Decimal(abundance) if abundance else None}
    return isotopes


def agrees(entry: dict, amount: Decimal) -> bool:
    if "lower" in entry:
        return entry["lower"] <= amount <= entry["upper"]
    return amount == entry["amount"]


def compare(local: list[dict], bodr: dict[tuple[int, int], dict]):
    matched = set()
    for entry in local:
        z, n = entry["zn"]
        key = (z, z + n)
        item = bodr.get(key)
        if item is None:
            yield row("NOT-FOUND", entry["name"], f"local {show(entry)}  no BODR isotope", local=show(entry))
            continue
        matched.add(key)
        if item["amount"] is None:
            yield row("NO-VALUE", entry["name"], f"local {show(entry)}  {item['id']} has no abundance", item["id"], show(entry))
        elif not agrees(entry, item["amount"]):
            yield row("DIFFERENT", entry["name"], f"local {show(entry)}  {item['id']} bodr {plain(item['amount'])}",
                      item["id"], show(entry), plain(item["amount"]))
    for key, item in sorted(bodr.items()):
        if item["amount"] is not None and key not in matched:
            yield row("ONLY-RESOURCE", item["id"], f"{item['id']} bodr {plain(item['amount'])}  not in local data",
                      item["id"], "", plain(item["amount"]))


def package_version() -> str:
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "bodr"], capture_output=True, text=True, check=True)
        return f"Debian package bodr {out.stdout}"
    except (OSError, subprocess.CalledProcessError):
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("--bodr", type=Path, default=DEFAULT_BODR, help=f"isotopes.xml of BODR (default {DEFAULT_BODR})")
    ap.add_argument("--json", type=Path, help="also write the differences and counts to this JSON file")
    ap.add_argument("--strict", action="store_true", help="exit 1 if there are differences")
    args = ap.parse_args(argv)

    g = Graph()
    g.parse(args.ttl, format="turtle")
    local = local_isotopes(g)
    bodr = read_bodr(args.bodr)

    counts: dict[str, int] = {}
    differences = list(compare(local, bodr))
    for d in differences:
        counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        print(f"{d['kind']:<14}{d['name']:<16}{d['text']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "generated": date.today().isoformat(), "source": args.ttl.name, "local_isotopes": len(local),
            "resource_items": len(bodr), "resource_version": package_version(), "counts": dict(sorted(counts.items())),
            "differences": differences,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"# {len(local)} local isotopes, {len(bodr)} BODR isotopes, "
          f"{sum(counts.values())} differences {dict(sorted(counts.items()))}", file=sys.stderr)
    return 1 if args.strict and counts else 0


if __name__ == "__main__":
    sys.exit(main())
