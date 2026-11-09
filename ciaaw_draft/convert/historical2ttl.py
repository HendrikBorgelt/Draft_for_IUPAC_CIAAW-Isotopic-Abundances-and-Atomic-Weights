#!/usr/bin/env python3
"""Add the historical standard atomic weights to a CIAAW Turtle file.

  python -m ciaaw_draft.convert.historical2ttl --current data/ciaaw.ttl --history data/historical/IUPAC-TSAW.csv -o data/ciaaw.ttl

The history is the table "Historical Atomic Weights" of ciaaw.org (data/historical/IUPAC-TSAW.csv): every standard
atomic weight the Commission recommended, with its year and DOI. Each element with a current standard atomic
weight gets one declaration per earlier recommendation, named weights:<symbol>-<year>. The newest recommendation
is the current declaration, which is already in the Turtle. It gets its year and report, and its year becomes part
of its IRI. The script stops if its value differs from the current value.

The declarations are chained as in the SI Reference Point: every earlier one has the status "superseded", the year
in which the next one replaced it, and ciaaw:hasNextDeclaration and ciaaw:hasPreviousDeclaration link the two.
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
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef, XSD

from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.namespaces import HISTORY_TABLE, PROV, STATUS_SUPERSEDED, Namespaces, SITE_URL
from ciaaw_draft.paths import DATA_DIR


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


def rename(g: Graph, old: URIRef, new: URIRef) -> None:
    """Give a resource a new IRI wherever it occurs."""
    for p, o in list(g.predicate_objects(old)):
        g.remove((old, p, o))
        g.add((new, p, o))
    for s, p in list(g.subject_predicates(old)):
        g.remove((s, p, old))
        g.add((s, p, new))


def add_report(ns: Namespaces, g: Graph, decl, doi: str) -> None:
    report, triples = c.report_triples(ns, doi)
    for triple in triples:
        g.add(triple)
    g.add((decl, ns.ciaaw.hasDefiningReport, report))


def add_history(g: Graph, rows_by_symbol: dict[str, list[tuple[int, str, str]]], base: str = SITE_URL) -> list[str]:
    """Adds the declarations to g. Returns the symbols that were left out."""
    ns = Namespaces(base)
    CIAAW = ns.ciaaw
    left_out = []
    for element in sorted(g.subjects(RDF.type, CIAAW.ChemicalElement), key=str):
        symbol = str(g.value(element, CIAAW.hasSymbol))
        name = str(g.value(element, RDFS.label))
        rows = rows_by_symbol.get(symbol)
        if not rows:
            continue
        current = g.value(element, CIAAW.hasCurrentStandardAtomicWeight)
        if current is None:
            left_out.append(symbol)
            continue
        latest_year, latest_printed, latest_doi = rows[-1]
        if c.graph_numbers(ns, g, current) != c.numbers(c.parse_quantity(latest_printed)):
            raise ValueError(f"{symbol}: the newest recommendation {latest_printed!r} is not the current value")
        if g.value(current, CIAAW.hasStartValidity) is None:
            g.add((current, CIAAW.hasStartValidity, Literal(str(latest_year), datatype=XSD.gYear)))
            g.set((current, RDFS.label, Literal(f"standard atomic weight of {name}, {latest_year}", lang="en")))
        if g.value(current, CIAAW.hasDefiningReport) is None and latest_doi:
            add_report(ns, g, current, latest_doi)
        g.add((current, PROV.wasDerivedFrom, HISTORY_TABLE))
        dated = ns.weights[f"{symbol}-{g.value(current, CIAAW.hasStartValidity)}"]
        if current == ns.weights[symbol] and (dated, None, None) not in g:
            rename(g, current, dated)
            current = dated

        chain = []
        for year, printed, doi in rows[:-1]:
            decl = ns.weights[f"{symbol}-{year}"]
            if (decl, RDF.type, None) not in g:
                g.add((decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration))
                g.add((decl, RDFS.label, Literal(f"standard atomic weight of {name}, {year}", lang="en")))
                g.add((decl, CIAAW.isDeclarationOf, element))
                for triple in c.value_triples(ns, decl, c.parse_quantity(printed)):
                    g.add(triple)
                g.add((decl, CIAAW.hasStartValidity, Literal(str(year), datatype=XSD.gYear)))
                g.add((decl, PROV.wasDerivedFrom, HISTORY_TABLE))
                if doi:
                    add_report(ns, g, decl, doi)
                g.add((element, CIAAW.hasDeclaration, decl))
            chain.append(decl)
        chain.append(current)

        for older, newer in zip(chain, chain[1:]):
            g.set((older, CIAAW.hasStatus, Literal(STATUS_SUPERSEDED)))
            g.set((older, CIAAW.hasEndValidity, g.value(newer, CIAAW.hasStartValidity)))
            g.set((older, CIAAW.hasNextDeclaration, newer))
            g.set((newer, CIAAW.hasPreviousDeclaration, older))
    if (URIRef(str(ns.weights)), None, None) in g:
        g.add((URIRef(str(ns.weights)), PROV.wasDerivedFrom, HISTORY_TABLE))
        for triple in c.source_triples(ns, HISTORY_TABLE):
            g.add(triple)
    if (URIRef(str(ns.reports)), None, None) not in g and any(g.subjects(RDF.type, CIAAW.Report)):
        for triple in c.header_triples(ns, "reports", [HISTORY_TABLE]):
            g.add(triple)
    elif (URIRef(str(ns.reports)), None, None) in g:
        g.add((URIRef(str(ns.reports)), PROV.wasDerivedFrom, HISTORY_TABLE))
    return left_out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--current", required=True, type=Path, help="Turtle with the current data, from ciaaw2ttl.py")
    ap.add_argument("--history", type=Path, default=DATA_DIR / "historical" / "IUPAC-TSAW.csv")
    ap.add_argument("--base", default=SITE_URL, help="address under which all namespaces of the data lie")
    ap.add_argument("-o", "--output", required=True, type=Path)
    args = ap.parse_args(argv)

    current_text = args.current.read_text(encoding="utf-8")
    g = Graph()
    g.parse(data=current_text, format="turtle")
    Namespaces(args.base).bind(g)
    left_out = add_history(g, read_history(args.history), args.base)

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
