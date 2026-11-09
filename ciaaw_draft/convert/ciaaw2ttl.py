#!/usr/bin/env python3
"""Convert the CIAAW tables into Turtle that follows the draft vocabulary in model/ontology.ttl.

Inputs (saved from the CIAAW website, used as they are):
  --weights     https://ciaaw.org/atomic-weights.htm        (standard atomic weights)
  --abundances  https://ciaaw.org/isotopic-abundances.htm   (isotopic compositions)
Optional:
  --supplement  CSV with what the tables do not contain: subject,role,year,doi,chebi
                role = standard | abundance | match
                subject = element symbol (H) or isotope (H-1); an element symbol with role
                "abundance" applies to all isotopes of that element.
                role "match" uses the chebi column (number only) -> skos:closeMatch.
  --previous    Turtle file written by an earlier run. Declarations whose value changed are kept with the status
                "superseded" and linked to the new declaration with ciaaw:hasNextDeclaration and
                ciaaw:hasPreviousDeclaration.

Example:
  python -m ciaaw_draft.convert.ciaaw2ttl --weights atomic-weights.htm --abundances isotopic-abundances.htm \\
      --supplement data/supplement.csv --previous ciaaw-2022.ttl -o ciaaw-2024.ttl

Modelling, after the SI Digital Framework of the BIPM (see README.md and issue #11):
  * Each kind of resource has its own namespace: elements:Hydrogen, isotopes:H-1, weights:H-2009,
    abundances:H-1, reports:PAC-REP-10-09-14, footnotes:g, bodies:CIAAW.
  * Element and isotope nodes keep a stable IRI. They hold their identifying facts, a pointer to the current
    declaration (ciaaw:hasCurrentStandardAtomicWeight, ciaaw:hasCurrentAbundance) and the full history
    (ciaaw:hasDeclaration).
  * Every published value is a declaration of its own, holding its numbers directly (no blank nodes): an interval,
    a value with its standard uncertainty, or a value alone, and always the value as CIAAW prints it
    (ciaaw:hasValueAsString), which keeps the trailing zeros. Numbers are xsd:decimal text, never floats.
  * Declarations have a status (current or superseded), the year they were recommended, the report they come
    from, and the declaration before and after them.
  * prov:wasDerivedFrom names the document every element, isotope and declaration was read from. Each namespace
    has a header (owl:Ontology) with the version, the sources and the rights statement.
  * The year of a declaration is part of its IRI when it is known (weights:H-2009). If a previous run used the
    same IRI for a DIFFERENT value, a short hash of the new value is appended so the old declaration stays
    addressable. --iri-style hash always appends the hash instead (pure content addressing).
  * Not in these two tables, therefore not generated: atomic masses, conventional/abridged values.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup
from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef, XSD
from rdflib.namespace import DCTERMS, SKOS

from ciaaw_draft import namespaces as n
from ciaaw_draft.namespaces import (ABUNDANCES_PAGE, CHEBI, PROV, SITE_URL, STATUS_CURRENT, STATUS_SUPERSEDED,
                                    SUPPLEMENT_FILE, UNIT_ONE, WEIGHTS_PAGE, Namespaces)

NONE_MARKS = {"", "-", "—", "–", "−"}

# Wording of the footnotes as printed below both CIAAW tables.
FOOTNOTES = {
    "g": "Geological and biological materials are known in which the element has an isotopic "
         "composition outside the limits for normal material. The difference between the atomic "
         "weight of the element in such materials and that given in the table may exceed the "
         "stated uncertainty.",
    "m": "Modified isotopic compositions may be found in commercially available material because "
         "the material has been subjected to some undisclosed or inadvertent isotopic "
         "fractionation. Substantial deviations in atomic weight of the element from that given "
         "in the table can occur.",
    "r": "Range in isotopic composition of normal terrestrial material prevents a more precise "
         "standard atomic weight being given; the tabulated value and uncertainty should be "
         "applicable to normal material.",
}
BODIES = {"CIAAW": ("Commission on Isotopic Abundances and Atomic Weights", "https://ciaaw.org", "IUPAC"),
          "IUPAC": ("International Union of Pure and Applied Chemistry", "https://iupac.org", None)}


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------
_INTERVAL = re.compile(r"^\[(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\]$")
_VALUE_UNC = re.compile(r"^(\d+(?:\.\d+)?)\((\d+)\)$")
_PLAIN = re.compile(r"^\d+(?:\.\d+)?$")
_MASS_NUMBER = re.compile(r"^\[(\d+)\]$")


def squeeze(text: str | None) -> str:
    """Remove all whitespace (regular, no-break, thin spaces used as digit separators)."""
    return re.sub(r"\s+", "", text or "")


def scaled_uncertainty(value: str, uncertainty_digits: str) -> str:
    places = len(value.split(".")[1]) if "." in value else 0
    return format(Decimal(uncertainty_digits).scaleb(-places), "f")


def group_digits(number: str, space: str = " ") -> str:
    """Group decimals in threes as CIAAW prints them (1.007 84). A lone last digit joins the group before it
    (9.012 1831)."""
    if "." not in number:
        return number
    whole, decimals = number.split(".")
    groups = [decimals[i:i + 3] for i in range(0, len(decimals), 3)]
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2:] = [groups[-2] + groups[-1]]
    return f"{whole}.{space.join(groups)}"


@dataclass
class Parsed:
    kind: str                      # interval | value | exact | massno | none
    raw: str                       # whitespace-free cell text
    lower: str | None = None
    upper: str | None = None
    value: str | None = None
    uncertainty: str | None = None
    uncertainty_digits: str | None = None
    mass_number: int | None = None

    @property
    def printed(self) -> str:
        """The value as CIAAW prints it, with every digit of the table."""
        if self.kind == "interval":
            return f"[{group_digits(self.lower)}, {group_digits(self.upper)}]"
        if self.kind == "value":
            return f"{group_digits(self.value)}({self.uncertainty_digits})"
        return group_digits(self.value)


def parse_quantity(cell: str) -> Parsed:
    s = squeeze(cell)
    if s in NONE_MARKS:
        return Parsed("none", s)
    if m := _INTERVAL.match(s):
        return Parsed("interval", s, lower=m.group(1), upper=m.group(2))
    if m := _VALUE_UNC.match(s):
        return Parsed("value", s, value=m.group(1), uncertainty=scaled_uncertainty(m.group(1), m.group(2)),
                      uncertainty_digits=m.group(2))
    if _PLAIN.match(s):
        return Parsed("exact", s, value=s)
    if m := _MASS_NUMBER.match(s):
        return Parsed("massno", s, mass_number=int(m.group(1)))
    raise ValueError(f"unrecognised value format: {cell!r}")


def parse_notes(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b([gmr])\b", text or "")))


# ---------------------------------------------------------------------------
# Table readers
# ---------------------------------------------------------------------------
def table_rows(html: str):
    # html5lib repairs broken markup the way browsers do. ciaaw.org leaves out <tr> on many rows,
    # and html.parser would silently drop those cells.
    soup = BeautifulSoup(html, "html5lib")
    for tr in soup.find_all("tr"):
        yield [" ".join(td.get_text(" ").split()) for td in tr.find_all(["td", "th"])]


@dataclass
class WeightRow:
    z: int
    symbol: str
    name: str
    weight: Parsed
    notes: list[str]


@dataclass
class AbundanceElement:
    z: int
    symbol: str
    name: str
    notes: set[str] = field(default_factory=set)
    isotopes: list[tuple[int, Parsed]] = field(default_factory=list)
    longest_lived: int | None = None


def read_weights(html: str, problems: list[str]) -> list[WeightRow]:
    rows = []
    for cells in table_rows(html):
        if len(cells) < 4 or not cells[0].isdigit():
            continue                      # header rows, notes, navigation
        try:
            weight = parse_quantity(cells[3])
        except ValueError as exc:
            problems.append(f"weights Z={cells[0]}: {exc}")
            continue
        rows.append(WeightRow(int(cells[0]), cells[1], cells[2], weight,
                              parse_notes(cells[4] if len(cells) > 4 else "")))
    return rows


def read_abundances(html: str, problems: list[str]) -> list[AbundanceElement]:
    elements: list[AbundanceElement] = []
    current: AbundanceElement | None = None
    for cells in table_rows(html):
        if len(cells) >= 5 and cells[0].isdigit() and re.fullmatch(r"[A-Z][a-z]{0,2}", cells[1]):
            current = AbundanceElement(int(cells[0]), cells[1], cells[2])
            elements.append(current)
            rest = cells[3:]
        elif current and len(cells) >= 2 and (cells[0].isdigit() or _MASS_NUMBER.match(squeeze(cells[0]))):
            rest = cells                  # continuation row (Z/E/Element cells spanned from above)
        else:
            continue
        mass_cell, comp_cell = rest[0], rest[1]
        current.notes |= set(parse_notes(rest[2] if len(rest) > 2 else ""))
        try:
            comp = parse_quantity(comp_cell)
            mass = parse_quantity(mass_cell)
        except ValueError as exc:
            problems.append(f"abundances {current.symbol} A={mass_cell}: {exc}")
            continue
        if mass.kind == "massno":
            current.longest_lived = mass.mass_number
        elif mass.kind == "exact" and comp.kind != "none":
            current.isotopes.append((int(mass.value), comp))
    return elements


# ---------------------------------------------------------------------------
# The parts that historical2ttl.py shares
# ---------------------------------------------------------------------------
def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def iri_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).capitalize()


def value_triples(ns: Namespaces, decl, parsed: Parsed) -> list[tuple]:
    """The numbers of a declaration in one of three forms, the value as printed, and its unit."""
    ciaaw, dec = ns.ciaaw, (lambda s: Literal(s, datatype=XSD.decimal))
    triples = [(decl, ciaaw.hasValueAsString, Literal(parsed.printed)), (decl, ciaaw.hasUnit, UNIT_ONE)]
    if parsed.kind == "interval":
        triples += [(decl, ciaaw.hasLowerBound, dec(parsed.lower)), (decl, ciaaw.hasUpperBound, dec(parsed.upper))]
    elif parsed.kind == "value":
        triples += [(decl, ciaaw.hasValue, dec(parsed.value)),
                    (decl, ciaaw.hasStandardUncertainty, dec(parsed.uncertainty))]
    else:
        triples.append((decl, ciaaw.hasValue, dec(parsed.value)))
    return triples


def numbers(parsed: Parsed) -> frozenset:
    """The numbers of a value, as numbers, so that 107.8682 and 107.868200 compare equal. rdflib also writes
    "1"^^xsd:decimal as 1.0, so the text of an unchanged value can differ after a round trip."""
    if parsed.kind == "interval":
        items = [("hasLowerBound", parsed.lower), ("hasUpperBound", parsed.upper)]
    elif parsed.kind == "value":
        items = [("hasValue", parsed.value), ("hasStandardUncertainty", parsed.uncertainty)]
    else:
        items = [("hasValue", parsed.value)]
    return frozenset((name, Decimal(text)) for name, text in items)


def graph_numbers(ns: Namespaces, g: Graph, decl) -> frozenset:
    found = set()
    for name in ("hasLowerBound", "hasUpperBound", "hasValue", "hasStandardUncertainty"):
        if (v := g.value(decl, ns.ciaaw[name])) is not None:
            found.add((name, Decimal(str(v))))
    return frozenset(found)


def report_triples(ns: Namespaces, doi: str) -> tuple[URIRef, list[tuple]]:
    """The report with this DOI, named after the part of the DOI after the registrant (reports:PAC-REP-10-09-14)."""
    report = ns.reports[re.sub(r"[^A-Za-z0-9._-]", "-", doi.split("/", 1)[-1])]
    return report, [(report, RDF.type, ns.ciaaw.Report), (report, RDFS.label, Literal(f"doi:{doi}")),
                    (report, DCTERMS.identifier, Literal(doi)), (report, RDFS.seeAlso, URIRef(f"https://doi.org/{doi}"))]


def source_triples(ns: Namespaces, source: URIRef) -> list[tuple]:
    label, publisher = n.SOURCE_LABELS[source]
    triples = [(source, RDF.type, PROV.Entity), (source, RDFS.label, Literal(label, lang="en"))]
    if publisher:
        triples.append((source, DCTERMS.publisher, ns.bodies[publisher]))
    return triples


def header_triples(ns: Namespaces, prefix: str, sources: list[URIRef]) -> list[tuple]:
    """The header of one namespace of the data, as the SI Reference Point gives each of its files."""
    head = URIRef(str(getattr(ns, prefix)))
    triples = [(head, RDF.type, OWL.Ontology), (head, RDFS.label, Literal(f"CIAAW draft: {n.TITLES[prefix]}", lang="en")),
               (head, OWL.versionInfo, Literal(n.VERSION)),
               (head, OWL.versionIRI, URIRef(f"{ns.base}{n.VERSION}/{n.FOLDERS[prefix]}")),
               (head, OWL.imports, ns.ontology), (head, DCTERMS.rights, Literal(n.DATA_RIGHTS, lang="en")),
               (head, DCTERMS.rightsHolder, ns.bodies.CIAAW), (head, DCTERMS.rightsHolder, ns.bodies.IUPAC)]
    return triples + [(head, PROV.wasDerivedFrom, s) for s in sources]


def body_triples(ns: Namespaces) -> list[tuple]:
    triples = []
    for key, (label, page, part_of) in BODIES.items():
        body = ns.bodies[key]
        triples += [(body, RDF.type, ns.ciaaw.Body), (body, RDFS.label, Literal(label, lang="en")),
                    (body, SKOS.altLabel, Literal(key)), (body, RDFS.seeAlso, URIRef(page))]
        if part_of:
            triples.append((body, DCTERMS.isPartOf, ns.bodies[part_of]))
    return triples


def load_supplement(path: str | None) -> dict[tuple[str, str], dict[str, str]]:
    """(subject, role) -> {"year": ..., "doi": ..., "chebi": ...}"""
    sup: dict[tuple[str, str], dict[str, str]] = {}
    if not path:
        return sup
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            subject = (row.get("subject") or "").strip()
            if not subject or subject.startswith("#"):
                continue
            doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", (row.get("doi") or "").strip())
            chebi = re.sub(r"^(?:CHEBI[:_])", "", (row.get("chebi") or "").strip(), flags=re.I)
            sup[(subject, (row.get("role") or "").strip().lower())] = {
                "year": (row.get("year") or "").strip(), "doi": doi, "chebi": chebi}
    return sup


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_graph(weights: list[WeightRow], abundances: list[AbundanceElement],
                supplement: dict | None = None, base: str = SITE_URL, headers: bool = True,
                iri_style: str = "readable", previous: Graph | None = None):
    ns = Namespaces(base)
    CIAAW = ns.ciaaw
    supplement = supplement or {}
    warnings: list[str] = []
    g = Graph()
    ns.bind(g)

    def add(triples):
        for triple in triples:
            g.add(triple)

    def declaration_iri(namespace, local: str, raw: str, year: str | None, parsed: Parsed):
        base_name = local
        if iri_style == "hash":
            local += "-" + short_hash(base_name, raw)
        elif year:
            local += f"-{year}"
        iri = namespace[local]
        if (previous is not None and (iri, RDF.type, None) in previous
                and graph_numbers(ns, previous, iri) != numbers(parsed)):
            iri = namespace[f"{local}-{short_hash(base_name, raw)}"]        # same name, new value
        return iri

    # --- supplement ------------------------------------------------------------------------
    def meta(role: str, *subjects: str) -> dict:
        for s in subjects:
            if (s, role) in supplement:
                return supplement[(s, role)]
        return {}

    def add_provenance(decl, info: dict, source: URIRef):
        g.add((decl, PROV.wasDerivedFrom, source))
        if info:
            g.add((decl, PROV.wasDerivedFrom, SUPPLEMENT_FILE))
        if info.get("year"):
            g.add((decl, CIAAW.hasStartValidity, Literal(info["year"], datatype=XSD.gYear)))
        if info.get("doi"):
            report, triples = report_triples(ns, info["doi"])
            add(triples)
            g.add((decl, CIAAW.hasDefiningReport, report))

    def add_match(node, key: str):
        if (chebi_id := supplement.get((key, "match"), {}).get("chebi")):
            g.add((node, SKOS.closeMatch, CHEBI[chebi_id]))

    def add_declaration(decl, kind, label: str, subject, current_pointer, parsed: Parsed, notes, info, source):
        g.add((decl, RDF.type, kind))
        g.add((decl, RDFS.label, Literal(f"{label}, {info['year']}" if info.get("year") else label, lang="en")))
        g.add((decl, CIAAW.isDeclarationOf, subject))
        g.add((decl, CIAAW.hasStatus, Literal(STATUS_CURRENT)))
        add(value_triples(ns, decl, parsed))
        for letter in notes:
            g.add((decl, CIAAW.hasFootnote, ns.footnotes[letter]))
        add_provenance(decl, info, source)
        g.add((subject, CIAAW.hasDeclaration, decl))
        g.add((subject, current_pointer, decl))
        if previous is not None:
            carry_history(subject, current_pointer, decl, numbers(parsed))

    # --- revisions across runs -------------------------------------------------------------
    def copy_from_previous(node):
        for p, o in previous.predicate_objects(node):
            g.add((node, p, o))
            if p == CIAAW.hasDefiningReport:
                for triple in previous.triples((o, None, None)):
                    g.add(triple)

    def carry_history(subject, pointer, new_decl, new_numbers: frozenset):
        """Keep the declarations of the previous run, and chain the one that this run supersedes."""
        old = previous.value(subject, pointer)
        skip = {new_decl}
        if old is not None and old != new_decl and graph_numbers(ns, previous, old) == new_numbers:
            skip.add(old)                 # same value, only the IRI differs (e.g. year added later)
        history = set(previous.objects(subject, CIAAW.hasDeclaration))
        if old is not None:
            history.add(old)
        for d in sorted(history - skip, key=str):
            copy_from_previous(d)
            g.add((subject, CIAAW.hasDeclaration, d))
            if d == old:
                g.set((d, CIAAW.hasStatus, Literal(STATUS_SUPERSEDED)))
                g.add((d, CIAAW.hasNextDeclaration, new_decl))
                g.add((new_decl, CIAAW.hasPreviousDeclaration, d))
                if (start := g.value(new_decl, CIAAW.hasStartValidity)) is not None:
                    g.set((d, CIAAW.hasEndValidity, start))

    # --- main loop -------------------------------------------------------------------------
    ab_by_symbol = {e.symbol: e for e in abundances}
    for row in weights:
        el = ns.elements[iri_name(row.name)]
        g.add((el, RDF.type, CIAAW.ChemicalElement))
        g.add((el, RDFS.label, Literal(row.name, lang="en")))
        g.add((el, CIAAW.hasSymbol, Literal(row.symbol)))
        g.add((el, CIAAW.hasAtomicNumber, Literal(row.z)))
        g.add((el, PROV.wasDerivedFrom, WEIGHTS_PAGE))
        add_match(el, row.symbol)

        if row.weight.kind in ("interval", "value"):
            info = meta("standard", row.symbol)
            decl = declaration_iri(ns.weights, row.symbol, row.weight.raw, info.get("year"), row.weight)
            add_declaration(decl, CIAAW.StandardAtomicWeightDeclaration, f"standard atomic weight of {row.name}",
                            el, CIAAW.hasCurrentStandardAtomicWeight, row.weight, row.notes, info, WEIGHTS_PAGE)
        elif row.weight.kind != "none":
            warnings.append(f"{row.symbol}: unexpected standard atomic weight format {row.weight.raw!r}")

        ab = ab_by_symbol.get(row.symbol)
        if ab is None:
            warnings.append(f"{row.symbol}: not found in the abundance table")
            continue
        g.add((el, PROV.wasDerivedFrom, ABUNDANCES_PAGE))
        if (ab.z, ab.name.lower()) != (row.z, row.name.lower()):
            warnings.append(f"{row.symbol}: Z/name differ between tables ({row.z}/{row.name} vs {ab.z}/{ab.name})")
        if ab.longest_lived is not None:
            g.add((el, CIAAW.hasLongestLivedMassNumber, Literal(ab.longest_lived)))
        for mass_number, comp in ab.isotopes:
            key = f"{row.symbol}-{mass_number}"
            iso = ns.isotopes[key]
            g.add((iso, RDF.type, CIAAW.Isotope))
            g.add((iso, RDFS.label, Literal(f"{row.name}-{mass_number}", lang="en")))
            g.add((iso, CIAAW.hasMassNumber, Literal(mass_number)))
            g.add((iso, PROV.wasDerivedFrom, ABUNDANCES_PAGE))
            add_match(iso, key)
            g.add((el, CIAAW.hasIsotope, iso))
            info = meta("abundance", key, row.symbol)
            decl = declaration_iri(ns.abundances, key, comp.raw, info.get("year"), comp)
            add_declaration(decl, CIAAW.IsotopicAbundanceDeclaration, f"isotopic abundance of {row.name}-{mass_number}",
                            iso, CIAAW.hasCurrentAbundance, comp, sorted(ab.notes), info, ABUNDANCES_PAGE)
        singles = [c for _, c in ab.isotopes]
        if singles and all(c.kind in ("value", "exact") for c in singles):
            total = sum(Decimal(c.value) for c in singles)
            if abs(total - 1) > Decimal("0.0005"):
                warnings.append(f"{row.symbol}: abundances sum to {total}, expected 1")

    if sorted(r.z for r in weights) != list(range(1, 119)):
        warnings.append(f"weights table has {len(weights)} elements; expected Z = 1..118 without gaps")
    for letter in sorted({str(o).rsplit("/", 1)[-1] for o in g.objects(None, CIAAW.hasFootnote)} & set(FOOTNOTES)):
        note = ns.footnotes[letter]
        add([(note, RDF.type, CIAAW.Footnote), (note, RDFS.label, Literal(f"footnote {letter}", lang="en")),
             (note, SKOS.notation, Literal(letter)), (note, SKOS.definition, Literal(FOOTNOTES[letter], lang="en")),
             (note, PROV.wasDerivedFrom, WEIGHTS_PAGE)])
    add(body_triples(ns))
    if headers:
        add_headers(ns, g)
    return g, warnings


def add_headers(ns: Namespaces, g: Graph) -> None:
    """A header for each namespace that holds resources, naming the documents its resources come from."""
    for prefix in n.FOLDERS:
        namespace = str(getattr(ns, prefix))
        subjects = {s for s in g.subjects() if str(s).startswith(namespace) and str(s) != namespace}
        if not subjects:
            continue
        sources = sorted({o for s in subjects for o in g.objects(s, PROV.wasDerivedFrom)}, key=str)
        for triple in header_triples(ns, prefix, sources):
            g.add(triple)
        for source in sources:
            for triple in source_triples(ns, source):
                g.add(triple)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", required=True, type=Path)
    ap.add_argument("--abundances", required=True, type=Path)
    ap.add_argument("--supplement", help="CSV: subject,role,year,doi,chebi")
    ap.add_argument("--previous", type=Path, help="Turtle from an earlier run; enables the chain of revisions")
    ap.add_argument("--iri-style", choices=("readable", "hash"), default="readable")
    ap.add_argument("-o", "--output", required=True, type=Path)
    ap.add_argument("--base", default=SITE_URL, help="address under which all namespaces of the data lie")
    ap.add_argument("--no-headers", action="store_true", help="omit the header of each namespace")
    ap.add_argument("--lenient", action="store_true", help="exit 0 even if cells could not be parsed")
    args = ap.parse_args(argv)

    problems: list[str] = []
    weights = read_weights(args.weights.read_text(encoding="utf-8"), problems)
    abundances = read_abundances(args.abundances.read_text(encoding="utf-8"), problems)
    previous = None
    if args.previous:
        previous = Graph()
        previous.parse(args.previous, format="turtle")
    graph, warnings = build_graph(weights, abundances, load_supplement(args.supplement), args.base,
                                  not args.no_headers, args.iri_style, previous)

    header = (f"# Generated by ciaaw2ttl.py\n"
              f"# weights:    {args.weights.name} sha256={sha256(args.weights)}\n"
              f"# abundances: {args.abundances.name} sha256={sha256(args.abundances)}\n")
    if args.previous:
        header += f"# previous:   {args.previous.name} sha256={sha256(args.previous)}\n"
    args.output.write_text(header + "\n" + graph.serialize(format="turtle"), encoding="utf-8")

    ciaaw = Namespaces(args.base).ciaaw
    n_superseded = len(set(graph.subjects(ciaaw.hasStatus, Literal(STATUS_SUPERSEDED))))
    print(f"{len(weights)} elements, {sum(len(e.isotopes) for e in abundances)} isotopes, "
          f"{len(graph)} triples, {n_superseded} superseded declarations -> {args.output}", file=sys.stderr)
    for msg in warnings:
        print(f"WARNING: {msg}", file=sys.stderr)
    for msg in problems:
        print(f"ERROR: {msg}", file=sys.stderr)
    return 0 if (not problems or args.lenient) else 2


if __name__ == "__main__":
    sys.exit(main())
