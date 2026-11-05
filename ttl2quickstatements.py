#!/usr/bin/env python3
"""Convert the CIAAW Turtle (see ciaaw2ttl.py) to QuickStatements v2 (CSV) for Wikidata.

One new Wikidata item is created per isotope (one CSV row each), carrying:

    Len     label, e.g. "hydrogen-1"
    P31     instance of: isotope (Q25276)
    Paaa    mass number (placeholder: the property does not exist in Wikidata yet)
    P2374   natural abundance, in percent, from the current abundance declaration
              - value with standard uncertainty  ->  96.941~0.156
              - interval (lower/upper bound)     ->  midpoint[lower,upper]
    qal585  point in time, from dcterms:issued of the declaration (year precision)
    S356    reference: DOI, from dcterms:source of the declaration

Declarations that are owl:deprecated are skipped; only ciaaw:currentAbundance
is exported. The Turtle has no Wikidata identifiers for isotopes, so every row
is a CREATE (empty qid column). Atomic weights and half-lives (see
quickstatements_like.txt) are not in the Turtle yet and are not exported.

Usage:
    ttl2quickstatements.py example-output-next.ttl > quickstatements.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS

CIAAW = Namespace("https://example.org/ciaaw-draft#")
CHEMROF = Namespace("https://w3id.org/chemrof/")
QUDT = Namespace("http://qudt.org/schema/qudt/")

Q_ISOTOPE = "Q25276"
P_INSTANCE_OF = "P31"
P_MASS_NUMBER = "Paaa"  # placeholder, see module docstring
P_ABUNDANCE = "P2374"
P_POINT_IN_TIME = "P585"
P_DOI = "P356"

COLUMNS = [
    "qid",
    "Len",
    P_INSTANCE_OF,
    P_MASS_NUMBER,
    P_ABUNDANCE,
    f"qal{P_POINT_IN_TIME[1:]}",
    f"S{P_DOI[1:]}",
]

DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")


def plain(d: Decimal) -> str:
    """Decimal as a string without exponent notation."""
    return format(d, "f")


def percent(fraction: Decimal) -> Decimal:
    return fraction.scaleb(2)


def quantity(g: Graph, value) -> str | None:
    """QuickStatements quantity (in percent) for a qudt:QuantityValue node."""
    num = g.value(value, QUDT.numericValue)
    lower = g.value(value, QUDT.lowerBound)
    upper = g.value(value, QUDT.upperBound)
    if num is not None:
        amount = percent(num.toPython())
        unc = g.value(value, QUDT.standardUncertainty)
        if unc is not None:
            return f"{plain(amount)}~{plain(percent(unc.toPython()))}"
        return plain(amount)
    if lower is not None and upper is not None:
        lo, hi = percent(lower.toPython()), percent(upper.toPython())
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


def element_labels(g: Graph) -> dict[URIRef, str]:
    """Isotope -> label of the element it belongs to."""
    labels = {}
    for element in g.subjects(RDF.type, CHEMROF.ChemicalElement):
        label = g.value(element, RDFS.label)
        if label is None:
            continue
        for isotope in g.objects(element, CIAAW.hasIsotope):
            labels[isotope] = str(label)
    return labels


def rows(g: Graph):
    labels = element_labels(g)
    for isotope in sorted(g.subjects(RDF.type, CHEMROF.Isotope), key=str):
        mass_number = g.value(isotope, CIAAW.massNumber)
        decl = g.value(isotope, CIAAW.currentAbundance)
        if mass_number is None or decl is None:
            continue
        if g.value(decl, URIRef("http://www.w3.org/2002/07/owl#deprecated")) == Literal(True):
            continue
        value = g.value(decl, CIAAW.value)
        amount = quantity(g, value) if value is not None else None
        if amount is None:
            continue
        element = labels.get(isotope)
        yield {
            "qid": "",
            "Len": f"{element}-{mass_number}" if element else "",
            P_INSTANCE_OF: Q_ISOTOPE,
            P_MASS_NUMBER: f'"{mass_number}"',
            P_ABUNDANCE: amount,
            COLUMNS[5]: year(g, decl),
            COLUMNS[6]: doi(g, decl),
        }


def convert(ttl: Path, out) -> int:
    g = Graph()
    g.parse(ttl, format="turtle")
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    n = 0
    for row in rows(g):
        writer.writerow(row)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("-o", "--output", type=Path, help="CSV output (default: stdout)")
    args = ap.parse_args(argv)
    if args.output:
        with args.output.open("w", newline="", encoding="utf-8") as f:
            n = convert(args.ttl, f)
    else:
        n = convert(args.ttl, sys.stdout)
    print(f"{n} isotopes written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
