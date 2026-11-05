#!/usr/bin/env python3
"""Add the historical standard atomic weights to a CIAAW Turtle file.

  historical2ttl.py --current ciaaw.ttl --history historical/IUPAC-TSAW.csv -o ciaaw.ttl

The history is the table "Historical Atomic Weights" of ciaaw.org (historical/IUPAC-TSAW.csv): every standard
atomic weight the Commission recommended, with its year and DOI. Each element with a current standard atomic
weight gets one declaration per earlier recommendation, named ciaaw_draft:AW-<symbol>-standard-<year>. The newest
recommendation is the current declaration, which is already in the Turtle. It only gets its year and DOI, and
the script stops if its value differs from the current value.

Declarations are chained as ciaaw2ttl.py chains them after a new release: an older declaration is flagged
owl:deprecated and adms:status deprecated, and dcterms:isReplacedBy and dcterms:replaces link it to the next one.
Running the script again adds nothing new.

Rows with "n/a" are not declarations: they record that no value was recommended (protactinium in 1987, for
example), and the model has nothing to store for that. Elements without a current standard atomic weight (Np, Ra,
Rn, Tc) are left out: without a current declaration there is nothing to chain their history to. They are listed
on stderr.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from decimal import Decimal
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef, XSD
from rdflib.namespace import DCTERMS, OWL

import ciaaw2ttl as c

HERE = Path(__file__).parent
ADMS = Namespace("http://www.w3.org/ns/adms#")
ADMS_DEPRECATED = URIRef("http://purl.org/adms/status/Deprecated")
CIAAW = Namespace(c.DEFAULT_VOCAB_NS)
EX = Namespace(c.DEFAULT_DATA_NS)


def doi_of(cell: str) -> str | None:
    """Some rows have no DOI, written as "n/a"."""
    cell = cell.strip()
    return cell if re.match(r"10\.\d", cell) else None


def year_of(cell: str) -> int | None:
    """The year is the text of the link on ciaaw.org, or the cell itself when it has no link."""
    m = re.search(r">(\d{4})</a>", cell) or re.fullmatch(r"\s*(\d{4})\s*", cell)
    return int(m.group(1)) if m else None


def read_history(path: Path) -> dict[str, list[tuple[int, str, str]]]:
    """Symbol -> [(year, value as printed, DOI)], oldest first. Repeated header lines, 'no changes' and 'n/a' are
    skipped."""
    history: dict[str, list[tuple[int, str, str]]] = {}
    for cells in csv.reader(path.read_text(encoding="utf-8").splitlines()):
        if len(cells) < 5 or cells[1] in ("", "Symbol", "no changes") or cells[3].strip() == "n/a":
            continue
        year = year_of(cells[0])
        if year is None:
            raise ValueError(f"no year in {cells!r}")
        # "16(exact)" is the exact value 16, as in the 1902 table of oxygen.
        history.setdefault(cells[1], []).append((year, cells[3].replace("(exact)", ""), doi_of(cells[4])))
    for rows in history.values():
        rows.sort()
    return history


def add_value(g: Graph, decl, parsed: c.Parsed) -> None:
    """The same value encoding as ciaaw2ttl.py, so a historical value compares with a current one."""
    q = BNode()
    g.add((decl, CIAAW.value, q))
    g.add((q, RDF.type, c.QUDT.QuantityValue))
    g.add((q, c.QUDT.hasUnit, c.UNIT.UNITLESS))

    def dec(s: str) -> Literal:
        return Literal(s, datatype=XSD.decimal)

    if parsed.kind == "interval":
        g.add((q, c.QUDT.lowerBound, dec(parsed.lower)))
        g.add((q, c.QUDT.upperBound, dec(parsed.upper)))
        for prop, num in ((CIAAW.significantFiguresLower, parsed.lower), (CIAAW.significantFiguresUpper, parsed.upper)):
            if (n := c.sig_figs(num)) is not None:
                g.add((q, prop, Literal(n)))
    elif parsed.kind == "value":
        g.add((q, c.QUDT.numericValue, dec(parsed.value)))
        g.add((q, c.QUDT.standardUncertainty, dec(parsed.uncertainty)))
        g.add((q, CIAAW.significantFigures, Literal(c.sig_figs(parsed.value))))
        g.add((q, CIAAW.significantFiguresUncertainty, Literal(len(parsed.uncertainty_digits))))
    elif parsed.kind == "exact":
        g.add((q, c.QUDT.numericValue, dec(parsed.value)))


def value_numbers(parsed: c.Parsed) -> frozenset:
    """The numbers of a value, as numbers, so that 107.8682 and 107.868200 compare equal."""
    if parsed.kind == "interval":
        items = [("lowerBound", parsed.lower), ("upperBound", parsed.upper)]
    elif parsed.kind == "value":
        items = [("numericValue", parsed.value), ("standardUncertainty", parsed.uncertainty)]
    else:
        items = [("numericValue", parsed.value)]
    return frozenset((name, Decimal(text)) for name, text in items)


def graph_numbers(g: Graph, decl) -> frozenset:
    q = g.value(decl, CIAAW.value)
    numbers = set()
    for name in ("lowerBound", "upperBound", "numericValue", "standardUncertainty"):
        v = g.value(q, c.QUDT[name]) if q is not None else None
        if v is not None:
            numbers.add((name, Decimal(str(v))))
    return frozenset(numbers)


def add_history(g: Graph, rows_by_symbol: dict[str, list[tuple[int, str, str]]]) -> list[str]:
    """Adds the declarations to g. Returns the symbols that were left out."""
    left_out = []
    for element in sorted(g.subjects(RDF.type, c.CHEMROF.ChemicalElement), key=str):
        symbol = str(g.value(element, c.CHEMROF.symbol))
        rows = rows_by_symbol.get(symbol)
        if not rows:
            continue
        current = g.value(element, CIAAW.currentStandardAtomicWeight)
        if current is None:
            left_out.append(symbol)
            continue
        latest_year, latest_printed, latest_doi = rows[-1]
        if graph_numbers(g, current) != value_numbers(c.parse_quantity(latest_printed)):
            raise ValueError(f"{symbol}: the newest recommendation {latest_printed!r} is not the current value")
        if g.value(current, DCTERMS.issued) is None:
            g.add((current, DCTERMS.issued, Literal(str(latest_year), datatype=XSD.gYear)))
        if g.value(current, DCTERMS.source) is None and latest_doi:
            g.add((current, DCTERMS.source, URIRef(f"https://doi.org/{latest_doi}")))

        chain = []
        for year, printed, doi in rows[:-1]:
            decl = EX[f"AW-{symbol}-standard-{year}"]
            if (decl, RDF.type, None) not in g:
                g.add((decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration))
                g.add((decl, CIAAW.ofElement, element))
                add_value(g, decl, c.parse_quantity(printed))
                g.add((decl, DCTERMS.issued, Literal(str(year), datatype=XSD.gYear)))
                if doi:
                    g.add((decl, DCTERMS.source, URIRef(f"https://doi.org/{doi}")))
                g.add((element, CIAAW.hasDeclaration, decl))
            chain.append(decl)
        chain.append(current)

        for older, newer in zip(chain, chain[1:]):
            g.add((older, OWL.deprecated, Literal(True)))
            g.add((older, ADMS.status, ADMS_DEPRECATED))
            g.add((older, DCTERMS.isReplacedBy, newer))
            g.add((newer, DCTERMS.replaces, older))
    return left_out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--current", required=True, type=Path, help="Turtle with the current data, from ciaaw2ttl.py")
    ap.add_argument("--history", type=Path, default=HERE / "historical" / "IUPAC-TSAW.csv")
    ap.add_argument("-o", "--output", required=True, type=Path)
    args = ap.parse_args(argv)

    current_text = args.current.read_text(encoding="utf-8")
    g = Graph()
    g.parse(data=current_text, format="turtle")
    g.bind("adms", ADMS)
    left_out = add_history(g, read_history(args.history))

    comments = [line for line in current_text.splitlines() if line.startswith("#") and not line.startswith("# history:")]
    digest = hashlib.sha256(args.history.read_bytes()).hexdigest()
    comments.append(f"# history:    {args.history.name} sha256={digest}")
    args.output.write_text("\n".join(comments) + "\n\n" + g.serialize(format="turtle"), encoding="utf-8", newline="\n")
    print(f"history added to {args.output}", file=sys.stderr)
    if left_out:
        print(f"no current standard atomic weight, history not added: {', '.join(left_out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
