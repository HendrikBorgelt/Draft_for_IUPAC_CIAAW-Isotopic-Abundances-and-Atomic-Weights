#!/usr/bin/env python3
"""Convert the CIAAW tables into Turtle that follows the draft declaration model (issue 20).

Inputs (saved from the CIAAW website, used as they are):
  --weights     https://ciaaw.org/atomic-weights.htm        (standard atomic weights)
  --abundances  https://ciaaw.org/isotopic-abundances.htm   (isotopic compositions)
Optional:
  --supplement  CSV with what the tables do not contain: subject,role,year,doi,chebi
                role = standard | abundance | match
                subject = element symbol (H) or isotope (H-1); an element symbol with role
                "abundance" applies to all isotopes of that element.
                role "match" uses the chebi column (number only) -> skos:closeMatch.
  --previous    Turtle file written by an earlier run. Declarations whose value changed are kept,
                flagged owl:deprecated and linked with dcterms:isReplacedBy / dcterms:replaces.

Example:
  python ciaaw2ttl.py --weights atomic-weights.htm --abundances isotopic-abundances.htm \
      --supplement supplement.csv --previous ciaaw-2022.ttl -o ciaaw-2024.ttl

Modelling (see hydrogen-example-v2.ttl and the draft in issue 20):
  * Element and isotope nodes keep a stable IRI and only carry pointers
    (ciaaw:currentStandardAtomicWeight, ciaaw:currentAbundance), the full history
    (ciaaw:hasDeclaration) and derived shortcut values.
  * Every published value is its own ciaaw:*Declaration with a qudt:QuantityValue.
  * Declaration IRIs are readable: ex:AW-H-standard-2009, ex:Abundance-H1. The year is taken
    from the supplement; without a year the IRI has none. If a previous run used the same IRI for
    a DIFFERENT value, a short hash of the new value is appended so the old declaration stays
    addressable. --iri-style hash always appends the hash instead (pure content addressing).
  * Numbers are kept as xsd:decimal text, never as float. Uncertainty digits in "x(u)" notation
    are scaled to the last digits of x. Significant figures are counted from the cell text,
    because xsd:decimal drops trailing zeros.
  * Not in these two tables, therefore not generated: atomic masses, conventional/abridged
    values, German labels.
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
from rdflib import BNode, Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from rdflib.namespace import DCTERMS, SKOS

DEFAULT_DATA_NS = "https://example.org/data/"
DEFAULT_VOCAB_NS = "https://example.org/ciaaw-draft#"
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")
CHEMROF = Namespace("https://w3id.org/chemrof/")
CHEBI = Namespace("http://purl.obolibrary.org/obo/CHEBI_")
ADMS = Namespace("http://www.w3.org/ns/adms#")
ADMS_DEPRECATED = URIRef("http://purl.org/adms/status/Deprecated")   # TODO verify this IRI

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

VOCAB_TTL = """
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix ciaaw: <@@VOCAB@@> .

ciaaw:Declaration a rdfs:Class ;
    rdfs:comment "One immutable, citable assignment of a value by CIAAW. Never edited after publication, only deprecated." .
ciaaw:StandardAtomicWeightDeclaration rdfs:subClassOf ciaaw:Declaration ;
    rdfs:comment "Ar°(E): either an interval (lowerBound/upperBound) or value with standardUncertainty." .
ciaaw:ConventionalAtomicWeightDeclaration rdfs:subClassOf ciaaw:Declaration ;
    rdfs:comment "Single abridged/conventional value for applications that do not need the interval." .
ciaaw:Footnote a rdfs:Class .
ciaaw:IsotopicAbundanceDeclaration rdfs:subClassOf ciaaw:Declaration ;
    rdfs:comment "Representative isotopic composition (amount fraction) of one isotope." .
ciaaw:AtomicMassDeclaration rdfs:subClassOf ciaaw:Declaration ;
    rdfs:comment "Atomic mass of one isotope in Da, with standard uncertainty." .

ciaaw:hasIsotope a rdf:Property ; rdfs:comment "Element -> isotope." .
ciaaw:massNumber a rdf:Property .
ciaaw:longestLivedMassNumber a rdf:Property ;
    rdfs:comment "Element without stable isotopes: mass number of the longest-lived isotope as bracketed in the CIAAW table." .
ciaaw:ofIsotope a rdf:Property ; rdfs:comment "Isotope declaration -> isotope it is about." .
ciaaw:currentAbundance a rdf:Property ; rdfs:comment "Isotope -> the one non-deprecated abundance declaration." .
ciaaw:currentAtomicMass a rdf:Property ; rdfs:comment "Isotope -> the one non-deprecated atomic mass declaration." .

ciaaw:ofElement a rdf:Property ; rdfs:comment "Declaration -> element it is about." .
ciaaw:hasDeclaration a rdf:Property ; rdfs:comment "Element or isotope -> ALL its declarations incl. deprecated (history)." .
ciaaw:currentStandardAtomicWeight a rdf:Property ;
    rdfs:comment "Element -> the one non-deprecated standard declaration. Modelled after dcat:hasCurrentVersion." .
ciaaw:currentConventionalAtomicWeight a rdf:Property ;
    rdfs:comment "Element -> the one non-deprecated conventional declaration." .
ciaaw:value a rdf:Property ; rdfs:range qudt:QuantityValue .
ciaaw:significantFigures a rdf:Property ;
    rdfs:comment "Needed because xsd:decimal drops trailing zeros (1.0080 = 1.008)." .
ciaaw:significantFiguresLower a rdf:Property .
ciaaw:significantFiguresUpper a rdf:Property .
ciaaw:significantFiguresUncertainty a rdf:Property ;
    rdfs:comment "Number of digits in the bracketed uncertainty, e.g. 3 for 0.969 41(156)." .
ciaaw:footnote a rdf:Property ; rdfs:range ciaaw:Footnote .
ciaaw:standardAtomicWeightLower a rdf:Property ;
    rdfs:comment "Derived shortcut copied from the current declaration; never edited by hand." .
ciaaw:standardAtomicWeightUpper a rdf:Property .
ciaaw:standardAtomicWeightValue a rdf:Property .
ciaaw:standardAtomicWeightUncertainty a rdf:Property .
"""


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


def sig_figs(number: str) -> int | None:
    """Significant figures of a plain decimal string; None if the value is zero."""
    digits = number.replace(".", "").lstrip("0")
    return len(digits) or None


def scaled_uncertainty(value: str, uncertainty_digits: str) -> str:
    places = len(value.split(".")[1]) if "." in value else 0
    return format(Decimal(uncertainty_digits).scaleb(-places), "f")


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
# Graph construction
# ---------------------------------------------------------------------------
def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def iri_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).capitalize()


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


def build_graph(weights: list[WeightRow], abundances: list[AbundanceElement],
                supplement: dict | None = None, data_ns: str = DEFAULT_DATA_NS,
                vocab_ns: str = DEFAULT_VOCAB_NS, include_vocab: bool = True,
                iri_style: str = "readable", previous: Graph | None = None):
    EX, CIAAW = Namespace(data_ns), Namespace(vocab_ns)
    supplement = supplement or {}
    warnings: list[str] = []
    g = Graph()
    if include_vocab:
        g.parse(data=VOCAB_TTL.replace("@@VOCAB@@", vocab_ns), format="turtle")
    for prefix, ns in [("dcterms", DCTERMS), ("skos", SKOS), ("qudt", QUDT), ("unit", UNIT), ("owl", OWL),
                       ("adms", ADMS), ("chemrof", CHEMROF), ("chebi", CHEBI), ("ciaaw", CIAAW), ("ex", EX)]:
        g.bind(prefix, ns)

    def dec(s: str) -> Literal:
        return Literal(s, datatype=XSD.decimal)

    # --- value signatures: decide whether a value changed between two runs ------------------
    # Compared as numbers, not as text: rdflib writes "1"^^xsd:decimal as 1.0 in Turtle, so the
    # lexical form of an unchanged value can differ after a round trip.
    def signature(parsed: Parsed) -> frozenset:
        if parsed.kind == "interval":
            items = [("lowerBound", parsed.lower), ("upperBound", parsed.upper)]
        elif parsed.kind == "value":
            items = [("numericValue", parsed.value), ("standardUncertainty", parsed.uncertainty)]
        else:
            items = [("numericValue", parsed.value)]
        return frozenset((name, Decimal(text)) for name, text in items)

    def graph_signature(graph: Graph, decl) -> frozenset:
        q = graph.value(decl, CIAAW.value)
        sig = set()
        for name in ("lowerBound", "upperBound", "numericValue", "standardUncertainty"):
            v = graph.value(q, QUDT[name]) if q is not None else None
            if v is not None:
                sig.add((name, Decimal(str(v))))
        return frozenset(sig)

    def declaration_iri(base: str, raw: str, year: str | None, parsed: Parsed):
        local = base
        if iri_style == "hash":
            local += "-" + short_hash(base, raw)
        elif year:
            local += f"-{year}"
        iri = EX[local]
        if (previous is not None and (iri, RDF.type, None) in previous
                and graph_signature(previous, iri) != signature(parsed)):
            iri = EX[f"{local}-{short_hash(base, raw)}"]        # same name, new value
        return iri

    # --- supplement ------------------------------------------------------------------------
    def meta(role: str, *subjects: str) -> dict:
        for s in subjects:
            if (s, role) in supplement:
                return supplement[(s, role)]
        return {}

    def add_provenance(decl, info: dict):
        if info.get("year"):
            g.add((decl, DCTERMS.issued, Literal(info["year"], datatype=XSD.gYear)))
        if info.get("doi"):
            g.add((decl, DCTERMS.source, URIRef(f"https://doi.org/{info['doi']}")))

    def add_match(node, key: str):
        if (chebi_id := supplement.get((key, "match"), {}).get("chebi")):
            g.add((node, SKOS.closeMatch, CHEBI[chebi_id]))

    # --- values and footnotes --------------------------------------------------------------
    def add_value(decl, parsed: Parsed):
        q = BNode()
        g.add((decl, CIAAW.value, q))
        g.add((q, RDF.type, QUDT.QuantityValue))
        g.add((q, QUDT.hasUnit, UNIT.UNITLESS))
        if parsed.kind == "interval":
            g.add((q, QUDT.lowerBound, dec(parsed.lower)))
            g.add((q, QUDT.upperBound, dec(parsed.upper)))
            for prop, num in ((CIAAW.significantFiguresLower, parsed.lower),
                              (CIAAW.significantFiguresUpper, parsed.upper)):
                if (n := sig_figs(num)) is not None:
                    g.add((q, prop, Literal(n)))
        elif parsed.kind == "value":
            g.add((q, QUDT.numericValue, dec(parsed.value)))
            g.add((q, QUDT.standardUncertainty, dec(parsed.uncertainty)))
            g.add((q, CIAAW.significantFigures, Literal(sig_figs(parsed.value))))
            g.add((q, CIAAW.significantFiguresUncertainty, Literal(len(parsed.uncertainty_digits))))
        elif parsed.kind == "exact":
            g.add((q, QUDT.numericValue, dec(parsed.value)))

    def add_notes(decl, notes):
        for letter in notes:
            g.add((decl, CIAAW.footnote, CIAAW[f"note-{letter}"]))

    # --- deprecation chain across runs -----------------------------------------------------
    def copy_closure(node):
        for p, o in previous.predicate_objects(node):
            g.add((node, p, o))
            if isinstance(o, BNode):
                copy_closure(o)

    def carry_history(subject, pointer, new_decl, new_sig: frozenset):
        """Keep declarations of the previous run; flag the one that this run supersedes."""
        old = previous.value(subject, pointer)
        skip = {new_decl}
        if old is not None and old != new_decl and graph_signature(previous, old) == new_sig:
            skip.add(old)                 # same value, only the IRI differs (e.g. year added later)
        history = set(previous.objects(subject, CIAAW.hasDeclaration))
        if old is not None:
            history.add(old)
        for d in sorted(history - skip, key=str):
            copy_closure(d)
            g.add((subject, CIAAW.hasDeclaration, d))
            if d == old:
                g.add((d, OWL.deprecated, Literal(True)))
                g.add((d, ADMS.status, ADMS_DEPRECATED))
                g.add((d, DCTERMS.isReplacedBy, new_decl))
                g.add((new_decl, DCTERMS.replaces, d))

    # --- main loop -------------------------------------------------------------------------
    ab_by_symbol = {e.symbol: e for e in abundances}
    for row in weights:
        el = EX[iri_name(row.name)]
        g.add((el, RDF.type, CHEMROF.ChemicalElement))
        g.add((el, RDFS.label, Literal(row.name, lang="en")))
        g.add((el, CHEMROF.symbol, Literal(row.symbol)))
        g.add((el, CHEMROF.atomic_number, Literal(row.z)))
        add_match(el, row.symbol)

        if row.weight.kind in ("interval", "value"):
            info = meta("standard", row.symbol)
            decl = declaration_iri(f"AW-{row.symbol}-standard", row.weight.raw, info.get("year"), row.weight)
            g.add((decl, RDF.type, CIAAW.StandardAtomicWeightDeclaration))
            g.add((decl, CIAAW.ofElement, el))
            add_value(decl, row.weight)
            add_notes(decl, row.notes)
            add_provenance(decl, info)
            g.add((el, CIAAW.hasDeclaration, decl))
            g.add((el, CIAAW.currentStandardAtomicWeight, decl))
            if row.weight.kind == "interval":
                g.add((el, CIAAW.standardAtomicWeightLower, dec(row.weight.lower)))
                g.add((el, CIAAW.standardAtomicWeightUpper, dec(row.weight.upper)))
            else:
                g.add((el, CIAAW.standardAtomicWeightValue, dec(row.weight.value)))
                g.add((el, CIAAW.standardAtomicWeightUncertainty, dec(row.weight.uncertainty)))
            if previous is not None:
                carry_history(el, CIAAW.currentStandardAtomicWeight, decl, signature(row.weight))
        elif row.weight.kind != "none":
            warnings.append(f"{row.symbol}: unexpected standard atomic weight format {row.weight.raw!r}")

        ab = ab_by_symbol.get(row.symbol)
        if ab is None:
            warnings.append(f"{row.symbol}: not found in the abundance table")
            continue
        if (ab.z, ab.name.lower()) != (row.z, row.name.lower()):
            warnings.append(f"{row.symbol}: Z/name differ between tables ({row.z}/{row.name} vs {ab.z}/{ab.name})")
        if ab.longest_lived is not None:
            g.add((el, CIAAW.longestLivedMassNumber, Literal(ab.longest_lived)))
        for mass_number, comp in ab.isotopes:
            key = f"{row.symbol}-{mass_number}"
            iso = EX[key]
            g.add((iso, RDF.type, CHEMROF.Isotope))
            g.add((iso, CIAAW.massNumber, Literal(mass_number)))
            add_match(iso, key)
            g.add((el, CIAAW.hasIsotope, iso))
            info = meta("abundance", key, row.symbol)
            decl = declaration_iri(f"Abundance-{row.symbol}{mass_number}", comp.raw, info.get("year"), comp)
            g.add((decl, RDF.type, CIAAW.IsotopicAbundanceDeclaration))
            g.add((decl, CIAAW.ofIsotope, iso))
            add_value(decl, comp)
            add_notes(decl, sorted(ab.notes))      # footnotes are element-level
            add_provenance(decl, info)
            g.add((iso, CIAAW.hasDeclaration, decl))
            g.add((iso, CIAAW.currentAbundance, decl))
            if previous is not None:
                carry_history(iso, CIAAW.currentAbundance, decl, signature(comp))
        singles = [c for _, c in ab.isotopes]
        if singles and all(c.kind in ("value", "exact") for c in singles):
            total = sum(Decimal(c.value) for c in singles)
            if abs(total - 1) > Decimal("0.0005"):
                warnings.append(f"{row.symbol}: abundances sum to {total}, expected 1")

    if sorted(r.z for r in weights) != list(range(1, 119)):
        warnings.append(f"weights table has {len(weights)} elements; expected Z = 1..118 without gaps")
    letters = {str(o).rsplit("note-", 1)[-1] for o in g.objects(None, CIAAW.footnote)}
    for letter in sorted(letters & set(FOOTNOTES)):
        note = CIAAW[f"note-{letter}"]
        g.add((note, RDF.type, CIAAW.Footnote))
        g.add((note, SKOS.notation, Literal(letter)))
        g.add((note, SKOS.definition, Literal(FOOTNOTES[letter], lang="en")))
    return g, warnings


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
    ap.add_argument("--previous", type=Path, help="Turtle from an earlier run; enables the deprecation chain")
    ap.add_argument("--iri-style", choices=("readable", "hash"), default="readable")
    ap.add_argument("-o", "--output", required=True, type=Path)
    ap.add_argument("--data-ns", default=DEFAULT_DATA_NS)
    ap.add_argument("--vocab-ns", default=DEFAULT_VOCAB_NS)
    ap.add_argument("--no-vocab", action="store_true", help="omit the vocabulary block")
    ap.add_argument("--lenient", action="store_true", help="exit 0 even if cells could not be parsed")
    args = ap.parse_args(argv)

    problems: list[str] = []
    weights = read_weights(args.weights.read_text(encoding="utf-8"), problems)
    abundances = read_abundances(args.abundances.read_text(encoding="utf-8"), problems)
    previous = None
    if args.previous:
        previous = Graph()
        previous.parse(args.previous, format="turtle")
    graph, warnings = build_graph(weights, abundances, load_supplement(args.supplement), args.data_ns,
                                  args.vocab_ns, not args.no_vocab, args.iri_style, previous)

    header = (f"# Generated by ciaaw2ttl.py\n"
              f"# weights:    {args.weights.name} sha256={sha256(args.weights)}\n"
              f"# abundances: {args.abundances.name} sha256={sha256(args.abundances)}\n")
    if args.previous:
        header += f"# previous:   {args.previous.name} sha256={sha256(args.previous)}\n"
    args.output.write_text(header + "\n" + graph.serialize(format="turtle"), encoding="utf-8")

    n_deprecated = len(set(graph.subjects(OWL.deprecated, Literal(True))))
    print(f"{len(weights)} elements, {sum(len(e.isotopes) for e in abundances)} isotopes, "
          f"{len(graph)} triples, {n_deprecated} deprecated declarations -> {args.output}", file=sys.stderr)
    for msg in warnings:
        print(f"WARNING: {msg}", file=sys.stderr)
    for msg in problems:
        print(f"ERROR: {msg}", file=sys.stderr)
    return 0 if (not problems or args.lenient) else 2


if __name__ == "__main__":
    sys.exit(main())
