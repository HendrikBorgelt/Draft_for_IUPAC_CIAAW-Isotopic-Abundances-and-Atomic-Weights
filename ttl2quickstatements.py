#!/usr/bin/env python3
"""Convert the CIAAW Turtle (see ciaaw2ttl.py) to QuickStatements v2 (CSV) for Wikidata.

The abundance comes from ciaaw:currentAbundance of an isotope (declarations that are owl:deprecated are
skipped), as a fraction in the unit "1", which is how the existing P2374 statements in Wikidata are stored:

    P2374   natural abundance
              - value with standard uncertainty  ->  0.96941~0.00156
              - interval (lower/upper bound)     ->  midpoint[lower,upper]
    qal585  point in time, from dcterms:issued of the declaration (year precision)
    S356    reference: DOI, from dcterms:source of the declaration

Isotopes that Wikidata already has (site/wikidata.csv, written by wikidata_links.py) are NOT created again.
Without --comparison they are skipped. With --comparison FILE, the JSON of `check_wikidata.py --json`, the
isotopes for which Wikidata has an item but no abundance (kind NO-VALUE) get the statement added to that item
(the qid column). Isotopes that are different in Wikidata are left for a person to decide.

An isotope without a Wikidata item becomes a new item (empty qid column), with:

    Len     label, e.g. "hydrogen-1"
    P31     instance of: isotope (Q25276)
    P1086   atomic number, from chemrof:atomic_number of the element
    P1148   number of neutrons, the mass number (ciaaw:massNumber) minus the atomic number

Atomic weights and half-lives (see quickstatements_like.txt) are not exported, although the Turtle has the
atomic weights now: Wikidata has no agreed property for an interval of standard atomic weights.

Usage:
    ttl2quickstatements.py example-output-next.ttl > quickstatements.csv
    ttl2quickstatements.py build/ciaaw.ttl --comparison site/wikidata-comparison.json -o quickstatements.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS, OWL

CIAAW = Namespace("https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/ciaaw-draft#")
CHEMROF = Namespace("https://w3id.org/chemrof/")
QUDT = Namespace("http://qudt.org/schema/qudt/")

Q_ISOTOPE = "Q25276"
P_INSTANCE_OF = "P31"
P_ATOMIC_NUMBER = "P1086"
P_NEUTRONS = "P1148"
P_ABUNDANCE = "P2374"
P_POINT_IN_TIME = "P585"
P_DOI = "P356"

COLUMN_TIME = f"qal{P_POINT_IN_TIME[1:]}"
COLUMN_DOI = f"S{P_DOI[1:]}"
COLUMNS = ["qid", "Len", P_INSTANCE_OF, P_ATOMIC_NUMBER, P_NEUTRONS, P_ABUNDANCE, COLUMN_TIME, COLUMN_DOI]
WIKIDATA_CSV = Path(__file__).parent / "site" / "wikidata.csv"

DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")


def plain(d: Decimal) -> str:
    """Decimal as a string without exponent notation."""
    return format(d, "f")


def quantity(g: Graph, value) -> str | None:
    """QuickStatements quantity (a fraction, no unit) for a qudt:QuantityValue node."""
    num = g.value(value, QUDT.numericValue)
    lower = g.value(value, QUDT.lowerBound)
    upper = g.value(value, QUDT.upperBound)
    if num is not None:
        unc = g.value(value, QUDT.standardUncertainty)
        if unc is not None:
            return f"{plain(num.toPython())}~{plain(unc.toPython())}"
        return plain(num.toPython())
    if lower is not None and upper is not None:
        lo, hi = lower.toPython(), upper.toPython()
        return f"{plain((lo + hi) / 2)}[{plain(lo)},{plain(hi)}]"
    return None


def year(g: Graph, decl) -> str:
    issued = g.value(decl, DCTERMS.issued)
    if issued is None:
        return ""
    return f"+{str(issued)[:4]}-00-00T00:00:00Z/9"


def doi(g: Graph, decl) -> str:
    src = g.value(decl, DCTERMS.source)
    if src is None:
        return ""
    s = str(src)
    for prefix in DOI_PREFIXES:
        if s.startswith(prefix):
            return f'"{s[len(prefix):].upper()}"'
    return ""


def read_known(path: Path) -> set[tuple[int, int]]:
    """(Z, A) of the isotopes that Wikidata already has, from site/wikidata.csv; empty without the file."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {(int(r["z"]), int(r["a"])) for r in csv.DictReader(f) if r["kind"] == "isotope"}


def read_comparison(path: Path | None) -> dict[str, str]:
    """Isotope name -> Q-number of the Wikidata item without an abundance, from check_wikidata.py --json."""
    if path is None:
        return {}
    result = json.loads(path.read_text(encoding="utf-8"))
    return {d["name"]: d["qid"] for d in result["differences"] if d["kind"] == "NO-VALUE" and "/" not in d["qid"]}


def rows(g: Graph, known: set[tuple[int, int]] = frozenset(), no_value: dict[str, str] | None = None):
    no_value = no_value or {}
    element_of = {i: e for e in g.subjects(RDF.type, CHEMROF.ChemicalElement) for i in g.objects(e, CIAAW.hasIsotope)}
    for isotope in sorted(g.subjects(RDF.type, CHEMROF.Isotope), key=str):
        mass_number = g.value(isotope, CIAAW.massNumber)
        decl = g.value(isotope, CIAAW.currentAbundance)
        element = element_of.get(isotope)
        if mass_number is None or decl is None or element is None:
            continue
        if g.value(decl, OWL.deprecated) == Literal(True):
            continue
        value = g.value(decl, CIAAW.value)
        amount = quantity(g, value) if value is not None else None
        z = g.value(element, CHEMROF.atomic_number)
        if amount is None or z is None:
            continue
        name = f"{g.value(element, RDFS.label)}-{mass_number}"
        row = {P_ABUNDANCE: amount, COLUMN_TIME: year(g, decl), COLUMN_DOI: doi(g, decl)}
        if (int(z), int(mass_number)) in known:
            if name in no_value:     # the item exists, only the statement is missing
                yield {"qid": no_value[name], **row}
            continue
        yield {"qid": "", "Len": name, P_INSTANCE_OF: Q_ISOTOPE, P_ATOMIC_NUMBER: z.toPython(),
               P_NEUTRONS: int(mass_number) - int(z), **row}


def convert(ttl: Path, out, known: set[tuple[int, int]] = frozenset(), no_value: dict[str, str] | None = None) -> int:
    g = Graph()
    g.parse(ttl, format="turtle")
    writer = csv.DictWriter(out, fieldnames=COLUMNS, restval="", lineterminator="\n")
    writer.writeheader()
    n = 0
    for row in rows(g, known, no_value):
        writer.writerow(row)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("-o", "--output", type=Path, help="CSV output (default: stdout)")
    ap.add_argument("--wikidata", type=Path, default=WIKIDATA_CSV,
                    help="CSV of the isotopes Wikidata has (wikidata_links.py); these are not created again")
    ap.add_argument("--comparison", type=Path,
                    help="JSON of check_wikidata.py --json: add the abundance to items that have none")
    args = ap.parse_args(argv)
    known, no_value = read_known(args.wikidata), read_comparison(args.comparison)
    if args.output:
        with args.output.open("w", newline="", encoding="utf-8") as f:
            n = convert(args.ttl, f, known, no_value)
    else:
        n = convert(args.ttl, sys.stdout, known, no_value)
    print(f"{n} rows written ({len(known)} isotopes known in Wikidata are not created again)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
