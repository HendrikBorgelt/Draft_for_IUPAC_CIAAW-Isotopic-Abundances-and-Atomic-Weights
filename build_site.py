#!/usr/bin/env python3
"""Build the static prototype site for GitHub Pages.

  python build_site.py [--data example-output.ttl] [--revision example-output-next.ttl] [--out _site]
                       [--sparql-endpoint URL] [--wikidata site/wikidata.csv]

Every page is written as Markdown first. The HTML page is rendered from that same Markdown and wrapped
in site/template.html, so both versions always say the same thing. The Turtle is published unchanged,
and the other RDF formats are translated from it.

The SPARQL page shows the queries in sparql/queries with their results, computed with Oxigraph because
that is the engine behind the endpoint. The queries are published as .rq files, and as sparql-examples Turtle
from sparql/examples. A query editor runs against the endpoint once an endpoint URL is given, through
--sparql-endpoint or the SPARQL_ENDPOINT environment variable.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import string
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from html import escape
from pathlib import Path

import markdown
import pyoxigraph as ox
from rdflib import Graph, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS, OWL, SKOS

import ciaaw2ttl as c
import tap2model as t

HERE = Path(__file__).parent
SITE_SRC = HERE / "site"
REPO_URL = "https://github.com/HendrikBorgelt/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights"
ISSUES_URL = f"{REPO_URL}/issues"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.2.0/dist/mermaid.esm.min.mjs"
ELEMENT_COUNT = 118
WIKIDATA_CSV = SITE_SRC / "wikidata.csv"
REFERENCES_JSON = SITE_SRC / "references.json"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/"
NBSP = " "

CIAAW = c.Namespace(c.DEFAULT_VOCAB_NS)
QUDT, CHEMROF = c.QUDT, c.CHEMROF

# Label, file extension, rdflib format, media type.
FORMATS = [
    ("Turtle", "ttl", "turtle", "text/turtle"),
    ("RDF/XML", "rdf", "xml", "application/rdf+xml"),
    ("JSON-LD", "jsonld", "json-ld", "application/ld+json"),
    ("N-Triples", "nt", "nt", "application/n-triples"),
    ("N3", "n3", "n3", "text/n3"),
]

NAV = [("index", "Home"), ("atomic-weights", "Atomic Weights"), ("isotopic-abundances", "Isotopic Abundances"),
       ("comparison", "Comparison"),
       ("model", "Data Model"), ("downloads", "Downloads"), ("sparql", "SPARQL"), ("about", "About")]
SUBNAV = {"model": [("model", "Data Model"), ("revision-example", "Revision Example")]}

QUERIES = HERE / "sparql" / "queries"
EXAMPLES = HERE / "sparql" / "examples"
WORKER_URL = "https://github.com/JervenBolleman/oxigraph-cloudflare-webworker"
SHORT_NAMES = {c.DEFAULT_DATA_NS: "ex:", str(c.CHEBI): "chebi:"}
EDITOR_URL = "https://cdn.jsdelivr.net/npm/@sib-swiss/sparql-editor@0.2.14/dist/sparql-editor.js"
SPARQL_EDITOR_MARK = "<!-- sparql-editor -->"

MD_NOTICE = ("> **PROTOTYPE:** a suggestion, not an official CIAAW or IUPAC page. "
             "Official values are at [ciaaw.org](https://ciaaw.org).\n\n")

MERMAID_SCRIPT = f"""<script type="module">
import mermaid from "{MERMAID_URL}";
mermaid.initialize({{
  startOnLoad: true,
  securityLevel: "strict",
  theme: "base",
  themeVariables: {{
    primaryColor: "#ebf0f2", primaryBorderColor: "#547995", primaryTextColor: "#222222",
    lineColor: "#547995", secondaryColor: "#f2f6f6", tertiaryColor: "#ffffff",
    fontFamily: "Calibri, Carlito, Segoe UI, Arial, sans-serif"
  }}
}});
</script>"""


# ---------------------------------------------------------------------------
# Numbers in the notation of the CIAAW tables
# ---------------------------------------------------------------------------
def group_digits(number: str) -> str:
    """Group decimals in threes (1.007 84). A lone last digit joins the group before it (9.012 1831)."""
    if "." not in number:
        return number
    whole, decimals = number.split(".")
    groups = [decimals[i:i + 3] for i in range(0, len(decimals), 3)]
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2:] = [groups[-2] + groups[-1]]
    return f"{whole}.{NBSP.join(groups)}"


def format_quantity(g: Graph, quantity) -> str:
    lower, upper = g.value(quantity, QUDT.lowerBound), g.value(quantity, QUDT.upperBound)
    if lower is not None:
        return f"[{group_digits(str(lower))},{NBSP}{group_digits(str(upper))}]"
    value = str(g.value(quantity, QUDT.numericValue))
    uncertainty = g.value(quantity, QUDT.standardUncertainty)
    if uncertainty is None:
        # A turtle round trip writes the decimal 1 as 1.0, but the tables print 1.
        return str(int(Decimal(value))) if Decimal(value) == Decimal(value).to_integral() else group_digits(value)
    places = len(value.split(".")[1]) if "." in value else 0
    return f"{group_digits(value)}({int(Decimal(str(uncertainty)).scaleb(places))})"


# ---------------------------------------------------------------------------
# Reading the RDF
# ---------------------------------------------------------------------------
@dataclass
class Declaration:
    iri: URIRef
    text: str
    issued: str
    sources: list[str]
    notes: list[str]
    deprecated: bool
    replaced_by: URIRef | None


@dataclass
class Isotope:
    iri: URIRef
    mass_number: int
    abundance: Declaration
    history: list[Declaration]
    matches: list[str]


@dataclass
class Element:
    iri: URIRef
    name: str
    symbol: str
    z: int
    weight: Declaration | None
    history: list[Declaration]
    isotopes: list[Isotope]
    longest_lived: int | None
    matches: list[str]

    @property
    def page(self) -> str:
        return local_name(self.iri)

    @property
    def abundance_notes(self) -> list[str]:
        return self.isotopes[0].abundance.notes if self.isotopes else []


def local_name(iri) -> str:
    return re.split(r"[/#]", str(iri))[-1]


def read_declaration(g: Graph, iri: URIRef) -> Declaration:
    return Declaration(
        iri, format_quantity(g, g.value(iri, CIAAW.value)), str(g.value(iri, DCTERMS.issued) or ""),
        sorted(str(s) for s in g.objects(iri, DCTERMS.source)),
        sorted(str(g.value(n, SKOS.notation)) for n in g.objects(iri, CIAAW.footnote)),
        (iri, OWL.deprecated, None) in g and bool(g.value(iri, OWL.deprecated).toPython()),
        g.value(iri, DCTERMS.isReplacedBy))


def read_history(g: Graph, subject) -> list[Declaration]:
    return sorted((read_declaration(g, d) for d in g.objects(subject, CIAAW.hasDeclaration)),
                  key=lambda d: (d.issued, str(d.iri)), reverse=True)


def read_elements(g: Graph) -> list[Element]:
    elements = []
    for iri in g.subjects(RDF.type, CHEMROF.ChemicalElement):
        isotopes = [Isotope(i, int(g.value(i, CIAAW.massNumber)), read_declaration(g, g.value(i, CIAAW.currentAbundance)),
                            read_history(g, i), sorted(str(m) for m in g.objects(i, SKOS.closeMatch)))
                    for i in g.objects(iri, CIAAW.hasIsotope)]
        current = g.value(iri, CIAAW.currentStandardAtomicWeight)
        longest = g.value(iri, CIAAW.longestLivedMassNumber)
        elements.append(Element(
            iri, str(g.value(iri, RDFS.label)), str(g.value(iri, CHEMROF.symbol)),
            int(g.value(iri, CHEMROF.atomic_number)), read_declaration(g, current) if current is not None else None,
            read_history(g, iri), sorted(isotopes, key=lambda i: i.mass_number),
            int(longest) if longest is not None else None, sorted(str(m) for m in g.objects(iri, SKOS.closeMatch))))
    return sorted(elements, key=lambda e: e.z)


def footnotes(g: Graph) -> dict[str, str]:
    return {str(g.value(n, SKOS.notation)): str(g.value(n, SKOS.definition)) for n in g.subjects(RDF.type, CIAAW.Footnote)}


# ---------------------------------------------------------------------------
# Page content (Markdown)
# ---------------------------------------------------------------------------
def coverage_note(elements: list[Element]) -> str:
    if len(elements) >= ELEMENT_COUNT:
        return ""
    return (f"> This prototype currently holds a sample of {len(elements)} of the {ELEMENT_COUNT} elements. "
            "The complete tables are at [ciaaw.org](https://ciaaw.org).\n")


def note_links(letters: list[str], prefix: str = "") -> str:
    return " ".join(f"[{letter}]({prefix}#note-{letter})" for letter in letters)


def notes_section(notes: dict[str, str], letters=None) -> str:
    chosen = sorted(notes if letters is None else set(letters) & set(notes))
    if not chosen:
        return ""
    lines = "\n\n".join(f'<a id="note-{k}"></a>**{k}** {notes[k]}' for k in chosen)
    return f"## Notes\n\n{lines}\n"


def source_links(sources: list[str]) -> str:
    return " ".join(f"[{s.removeprefix('https://doi.org/')}]({s})" for s in sources) or "—"


def read_wikidata(path: Path) -> dict[tuple[int, int | None], str]:
    """(Z, None) -> Q-number of an element, (Z, A) -> Q-number of an isotope; see wikidata_links.py."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(int(r["z"]), int(r["a"]) if r["a"] else None): r["qid"] for r in csv.DictReader(f)}


def read_references(path: Path) -> dict[str, str]:
    """DOI (upper case) -> APA reference as HTML, see update_references.js."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def bibliography(e: Element, references: dict[str, str]) -> str:
    """The sources of all declarations on an element page, formatted by citation.js, or "" without sources."""
    dois = {s.removeprefix("https://doi.org/") for d in [*e.history, *(d for i in e.isotopes for d in i.history)]
            for s in d.sources if s.startswith("https://doi.org/10.")}
    if not dois:
        return ""
    entries = sorted(((references.get(doi.upper(), doi), doi) for doi in dois), key=lambda r: r[0].lower())
    items = "\n".join(f'<div class="csl-entry">{text} <a href="https://doi.org/{doi}">CrossRef</a> '
                      f'<a href="https://qlever.scholia.wiki/doi/{doi}">Scholia</a></div>' for text, doi in entries)
    return f'## Bibliography\n\n<div class="csl-bib-body">\n{items}\n</div>\n'


@dataclass
class Resource:
    """An external resource the local data is compared with; the JSON comes from a script, see README.md."""
    key: str            # page name
    name: str
    url: str
    json: Path
    command: str        # make target that writes the JSON
    description: str
    item_url: str = ""  # prefix of the identifiers in the "Item" column; without it they are not linked


RESOURCES = [
    Resource("wikidata-comparison", "Wikidata", "https://www.wikidata.org", SITE_SRC / "wikidata-comparison.json",
             "wikidata-comparison",
             "Isotopic abundances in this data compared with the natural abundance (P2374) of the isotopes in "
             "[Wikidata](https://www.wikidata.org), matched on atomic number and number of neutrons. Values are in "
             "percent. The comparison is made by `check_wikidata.py`.", WIKIDATA_ENTITY),
]

SUBNAV["comparison"] = [("comparison", "Overview"), *((r.key, r.name) for r in RESOURCES)]

# kind in the JSON, title, explanation ({name} is the resource)
COMPARISON_KINDS = [
    ("DIFFERENT", "Different values", "{name} has an abundance for the isotope, and it differs from the local one."),
    ("NO-VALUE", "No value", "The isotope is in {name}, but without a natural abundance."),
    ("NOT-FOUND", "Not found", "{name} has no item for this isotope."),
    ("UNIT", "Unexpected unit", "The abundance in {name} is not a percentage or a plain number."),
    ("ONLY-WIKIDATA", "Only in the resource", "{name} has an abundance, the local data has no such isotope."),
]


def item_links(resource: Resource, ids: str) -> str:
    """Links to the items of the resource; several items are joined with a slash."""
    return " ".join(f"[{i}]({resource.item_url}{i})" if resource.item_url else i for i in ids.split("/") if i)


def read_comparison(resource: Resource) -> dict | None:
    return json.loads(resource.json.read_text(encoding="utf-8")) if resource.json.exists() else None


def page_comparison(resources: list[Resource]) -> str:
    """The overview: one row per resource, with the counts of what differs."""
    rows = []
    for r in resources:
        result = read_comparison(r)
        counts = result["counts"] if result else {}
        cells = " | ".join(str(counts.get(kind, 0)) if result else "—" for kind, _, _ in COMPARISON_KINDS[:3])
        rows.append(f"| [{r.name}]({r.key}.md) | {result['generated'] if result else 'not generated'} | {cells} |")
    return "\n".join([
        "# Comparison\n",
        "The isotopic abundances in this data are compared with other resources, to find values that differ and "
        "isotopes that are missing on one side. A difference is a reason to look at both sides, not a verdict on "
        "which one is right.\n",
        "| Resource | Compared on | Different values | No value | Not found |", "| --- | --- | --- | --- | --- |",
        *rows, "",
        "Only a sample of the elements is on this site, so the results are only a demonstration. Each comparison "
        "is a script that writes JSON, which the pages are built from, see the [README]({{repo}}#readme).\n"]).replace(
        "{{repo}}", REPO_URL)


def page_resource_comparison(resource: Resource) -> str:
    """The differences between the local abundances and one resource, from the JSON of its script."""
    intro = f"# Comparison with {resource.name}\n\n{resource.description}\n"
    result = read_comparison(resource)
    if result is None:
        return intro + f"\n> The comparison has not been generated yet. Run `make {resource.command}`.\n"
    counts = result["counts"]
    parts = [intro,
             f"Compared on {result['generated']}: {result['local_isotopes']} isotopes in `{result['source']}`, "
             f"{result.get('resource_items', result.get('wikidata_items'))} items in {resource.name}. Only a sample of the elements is on this site, "
             "so many isotopes are only in the resource.\n",
             "| Result | Count |", "| --- | --- |",
             *(f"| {title} | {counts.get(kind, 0)} |" for kind, title, _ in COMPARISON_KINDS)]
    for kind, title, text in COMPARISON_KINDS:
        rows = [d for d in result["differences"] if d["kind"] == kind]
        if not rows:
            continue
        parts += ["", f"## {title}\n", f"{text.format(name=resource.name)}\n",
                  "| Isotope | Local | Value in resource | Item |", "| --- | --- | --- | --- |",
                  *(f"| {d['name']} | {d['local'] or '—'} | {d['wikidata'] or '—'} | {item_links(resource, d['qid'])} |"
                    for d in rows)]
    return "\n".join(parts) + "\n"


def wikidata_link(links: dict, z: int, a: int | None = None) -> str:
    qid = links.get((z, a))
    return f"[{qid}]({WIKIDATA_ENTITY}{qid})" if qid else ""


def chebi_links(matches: list[str]) -> str:
    return " ".join(f"[CHEBI:{m.rsplit('_', 1)[-1]}]({m})" for m in matches)


def page_atomic_weights(elements: list[Element], notes: dict[str, str]) -> str:
    rows = [f"| {e.z} | {e.symbol} | [{e.name}](elements/{e.page}.md) | {e.weight.text if e.weight else '—'} "
            f"| {note_links(e.weight.notes) if e.weight else ''} |" for e in elements]
    table = "\n".join(["| *Z* | Symbol | Element | Standard Atomic Weight | Notes |",
                       "| --- | --- | --- | --- | --- |", *rows])
    return (f"# Standard Atomic Weights\n\n{coverage_note(elements)}\n"
            "Values are generated from the [published RDF](downloads.md). An interval means that the atomic "
            "weight of normal materials falls within those bounds. A number in brackets is the uncertainty "
            f"in the last digits.\n\n{table}\n\n{notes_section(notes)}")


def page_isotopic_abundances(elements: list[Element], notes: dict[str, str]) -> str:
    rows = []
    for e in elements:
        first = f"| {e.z} | {e.symbol} | [{e.name}](elements/{e.page}.md) |"
        if not e.isotopes and e.longest_lived:
            rows.append(f"{first} [{e.longest_lived}] | — | |")
        for index, isotope in enumerate(e.isotopes):
            lead = first if index == 0 else "| | | |"
            letters = note_links(e.abundance_notes) if index == 0 else ""
            rows.append(f"{lead} {isotope.mass_number} | {isotope.abundance.text} | {letters} |")
    table = "\n".join(["| *Z* | E | Element | A | Representative isotopic composition | Notes |",
                       "| --- | --- | --- | --- | --- | --- |", *rows])
    return (f"# Isotopic Abundances\n\n{coverage_note(elements)}\n"
            "Each row is a separate declaration in the RDF. A mass number in square brackets is the "
            f"longest-lived isotope of an element without stable isotopes.\n\n{table}\n\n{notes_section(notes)}")


def declaration_table(history: list[Declaration]) -> str:
    lines = ["| Value | Year | Source | Status | Declaration |", "| --- | --- | --- | --- | --- |"]
    for d in history:
        status = f"Deprecated, see `{local_name(d.replaced_by)}`" if d.deprecated else "Current"
        lines.append(f"| {d.text} | {d.issued or '—'} | {source_links(d.sources)} | {status} | `{local_name(d.iri)}` |")
    return "\n".join(lines)


def page_element(e: Element, notes: dict[str, str], links: dict | None = None,
                 references: dict | None = None) -> str:
    links = links or {}
    facts = ["| Property | Value |", "| --- | --- |", f"| Symbol | {e.symbol} |", f"| Atomic number | {e.z} |",
             f"| IRI | `{e.iri}` |"]
    if e.matches:
        facts.append(f"| ChEBI | {chebi_links(e.matches)} |")
    if wikidata_link(links, e.z):
        facts.append(f"| Wikidata | {wikidata_link(links, e.z)} |")
    if e.longest_lived:
        facts.append(f"| Longest-lived isotope | {e.longest_lived} |")
    parts = [f"# {e.name}\n", "\n".join(facts) + "\n", "## Standard atomic weight\n"]
    if e.weight:
        letters = f"Notes: {note_links(e.weight.notes)}\n" if e.weight.notes else ""
        parts += [declaration_table(e.history) + "\n", letters]
    else:
        parts.append("The table gives no standard atomic weight for this element.\n")
    parts.append("## Isotopes\n")
    if e.isotopes:
        rows = ["| A | Representative isotopic composition | Year | Source | Declaration | ChEBI | Wikidata |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for i in e.isotopes:
            d = i.abundance
            rows.append(f"| {i.mass_number} | {d.text} | {d.issued or '—'} | {source_links(d.sources)} "
                        f"| `{local_name(d.iri)}` | {chebi_links(i.matches) or '—'} "
                        f"| {wikidata_link(links, e.z, i.mass_number) or '—'} |")
        parts.append("\n".join(rows) + "\n")
        if e.abundance_notes:
            parts.append(f"Notes: {note_links(e.abundance_notes)}\n")
    else:
        parts.append("The table lists no isotopic composition for this element.\n")
    used = set(e.abundance_notes) | set(e.weight.notes if e.weight else [])
    if used:
        parts.append(notes_section(notes, used))
    if bib := bibliography(e, references or {}):
        parts.append(bib)
    parts.append("[All atomic weights](../atomic-weights.md) · [All isotopic abundances](../isotopic-abundances.md)\n")
    return "\n".join(parts)


def rule_labels() -> list[str]:
    rules = Graph().parse(t.RULES, format="turtle")
    return sorted(str(rules.value(s, RDFS.label)) for s in rules.subjects(RDF.type, t.SH.NodeShape))


def page_model(tap: t.Tap, reports: dict[str, bool]) -> str:
    status = "\n".join(f"- `{name}`: {'conforms' if ok else 'does not conform'}" for name, ok in reports.items())
    rules = "\n".join(f"- {label}" for label in rule_labels())
    parts = ["# Data Model\n",
             "The model is written once, as a [DCTAP](https://www.dublincore.org/specifications/dctap/) table. "
             "The SHACL shapes and all diagrams below are generated from it, so they cannot drift apart. "
             "The same approach is used for the [PubChemRDF schema](https://pubchem.ncbi.nlm.nih.gov/docs/rdf-schema), "
             "and other formats such as ShEx or LinkML can be derived from the same table.\n",
             "| File | Purpose |", "| --- | --- |",
             "| [model_dctap.csv](model/model_dctap.csv) | The DCTAP profile, the source of the model |",
             "| [dctap.yaml](model/dctap.yaml) | Prefixes, extra columns and aspects |",
             "| [rules.ttl](model/rules.ttl) | Rules that compare several nodes, which a DCTAP row cannot express |",
             "| [model_shacl.ttl](model/model_shacl.ttl) | The generated SHACL shapes, including those rules |\n",
             "## Validation\n", f"Checked against the shapes when this site was built:\n\n{status}\n",
             "## Overview\n", f"{t.OVERVIEW_NOTE}\n", f"```mermaid\n{t.mermaid(tap)}```\n"]
    for key, aspect in tap.aspects.items():
        parts += [f"## {aspect['title']}\n", f"{aspect['description']}\n", f"```mermaid\n{t.mermaid(tap, key)}```\n",
                  t.statement_table(tap, key)]
    parts += ["## Rules across nodes\n", f"These checks are defined in [rules.ttl](model/rules.ttl):\n\n{rules}\n"]
    return "\n".join(parts)


def revision_diagram(g: Graph, element: URIRef, history: list[Declaration]) -> str:
    def node(d: Declaration) -> str:
        return re.sub(r"\W", "_", local_name(d.iri))

    name = local_name(element)
    lines = ["flowchart LR", f'    {name}["ex:{name}"]']
    for d in history:
        state = "deprecated" if d.deprecated else "current"
        lines.append(f'    {node(d)}["ex:{local_name(d.iri)}<br/>{d.text}<br/>{state}"]')
        lines.append(f'    {name} -- "ciaaw:hasDeclaration" --> {node(d)}')
        if not d.deprecated:
            lines.append(f'    {name} == "ciaaw:currentStandardAtomicWeight" ==> {node(d)}')
    by_iri = {d.iri: d for d in history}
    for d in history:
        if d.replaced_by in by_iri:
            lines.append(f'    {node(d)} -- "dcterms:isReplacedBy" --> {node(by_iri[d.replaced_by])}')
    return "\n".join(lines) + "\n"


def page_revision(g: Graph) -> str:
    changed = [e for e in read_elements(g) if len(e.history) > 1]
    parts = ["# Revision Example\n",
             "> **The new value on this page is invented.** It only demonstrates what the data looks like after a "
             "revision. It is not a CIAAW value, not even a proposed one.\n",
             "When CIAAW revises a value, nothing is edited or deleted. The converter compares the new table with the "
             "previous output and then:\n",
             "1. creates a new declaration with the new value,\n"
             "2. moves the element's current pointer to it,\n"
             "3. flags the old declaration as deprecated and links the two, and\n"
             "4. keeps every declaration in the element's history.\n"]
    for e in changed:
        parts += [f"## {e.name}\n", f"```mermaid\n{revision_diagram(g, e.iri, e.history)}```\n",
                  declaration_table(e.history) + "\n"]
    parts.append("The complete file is under [Downloads](downloads.md#revision-example).\n")
    return "\n".join(parts)


def size_text(path: Path) -> str:
    return f"{path.stat().st_size / 1024:.0f} KB"


def format_table(out: Path, stem: str) -> str:
    lines = ["| Format | File | Size |", "| --- | --- | --- |"]
    for label, extension, _, _ in FORMATS:
        rel = f"{stem}.{extension}"
        lines.append(f"| {label} | [{Path(rel).name}]({rel}) | {size_text(out / rel)} |")
    return "\n".join(lines)


def page_downloads(out: Path, elements: list[Element]) -> str:
    return "\n".join([
        "# Downloads\n", coverage_note(elements),
        "Every format holds the same triples. The Turtle is the converter's output, and the others are translated "
        "from it. All IRIs under `example.org` are placeholders.\n",
        "## Current data\n", format_table(out, "data/ciaaw") + "\n",
        "## Revision example\n",
        "A small sample after an invented revision of hydrogen. See the [revision example](revision-example.md).\n",
        format_table(out, "data/ciaaw-revision-example") + "\n",
        "## Model\n",
        "The [DCTAP profile](model/model_dctap.csv) with its [configuration](model/dctap.yaml), "
        "and the generated SHACL shapes:\n", format_table(out, "model/model_shacl") + "\n",
        "The diagrams are also available as [Markdown](model/diagrams.md).\n",
        "## Querying the data\n",
        "See the [SPARQL page](sparql.md) for example queries.\n"])


@dataclass
class Example:
    stem: str
    title: str
    description: str
    query: str
    columns: list[str]
    rows: list[list[str]]


def short_term(term) -> str:
    if term is None:
        return ""
    for namespace, prefix in SHORT_NAMES.items():
        if term.value.startswith(namespace):
            return prefix + term.value[len(namespace):]
    return term.value


def read_examples(turtle: Path) -> list[Example]:
    """Run the example queries on Oxigraph, so the page shows what the endpoint would return."""
    store = ox.Store()
    store.load(turtle.read_bytes(), format=ox.RdfFormat.TURTLE)
    examples = []
    for path in sorted(QUERIES.glob("*.rq")):
        query = path.read_text(encoding="utf-8")
        title, description = (line.removeprefix("# ") for line in query.splitlines()[:2])
        solutions = store.query(query)
        columns = [variable.value for variable in solutions.variables]
        rows = [[short_term(row[name]) for name in columns] for row in solutions]
        examples.append(Example(path.stem, title, description, query, columns, rows))
    return examples


def result_table(example: Example) -> str:
    lines = ["| " + " | ".join(example.columns) + " |", "| " + " | ".join("---" for _ in example.columns) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in example.rows]
    return "\n".join(lines)


def page_sparql(examples: list[Example], endpoint: str) -> str:
    if endpoint:
        status = [f"The prototype endpoint is at `{endpoint}`. It may change or disappear without notice.\n"]
        command_line = ["From the command line:\n",
                        f'```bash\ncurl --data-urlencode "query=ASK {{ ?s ?p ?o }}" {endpoint}\n```\n',
                        "For `CONSTRUCT` and `DESCRIBE`, ask for an RDF format, for example with "
                        "`-H \"Accept: text/turtle\"`.\n"]
    else:
        status = [f"> **The live endpoint is not running yet.** See [issue 1]({ISSUES_URL}/1). "
                  "The query editor appears here once it is, with each example in its own tab. Until then, the queries "
                  "below can already be run on the [downloaded data](downloads.md).\n"]
        command_line = []
    parts = ["# SPARQL\n", *status,
             "## Try it\n", f"{SPARQL_EDITOR_MARK}\n", *command_line,
             "## What the endpoint holds\n",
             f"It serves the [current data](data/ciaaw.ttl) read-only, using [Oxigraph](https://github.com/oxigraph/oxigraph) "
             f"inside a Cloudflare Worker ([oxigraph-cloudflare-webworker]({WORKER_URL})). The "
             "[revision example](revision-example.md) is not loaded, so its invented value cannot appear in results.\n",
             "## Decimals and trailing zeros\n",
             "Oxigraph stores decimals as numbers, so `162.500` comes back as `162.5` and `0.0000` as `0`. The value is "
             "the same, but the printed digits are not. Use `ciaaw:significantFigures` to restore them, or the "
             "[Turtle file](data/ciaaw.ttl), which keeps the original digits. Zero bounds have no significant "
             "figures recorded, so their printed form is only available in the Turtle file.\n",
             "## Example queries\n",
             "The results below were computed with Oxigraph when this site was built. Each query is also published as "
             "a plain `.rq` file, which you can paste into the editor, and as sparql-examples Turtle. The shared "
             "[prefixes](sparql/examples/prefixes.ttl) are in their own file.\n"]
    for example in examples:
        parts += [f"### {example.title}\n", f"{example.description}\n", f"```sparql\n{example.query}```\n",
                  f"Files: [`{example.stem}.rq`](sparql/queries/{example.stem}.rq), "
                  f"[Turtle](sparql/examples/{example.stem}.ttl)\n",
                  result_table(example) + "\n"]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Writing the site
# ---------------------------------------------------------------------------
def render_body(text: str) -> str:
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    body = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>', r'<pre class="mermaid">\1</pre>', body, flags=re.S)
    # Links to pages point at the Markdown version; in HTML they should stay in HTML.
    # model/diagrams.md is a download in its own right, so it keeps its extension.
    return re.sub(r'href="((?!https?:|#|model/)[^"#]+)\.md(#[^"]*)?"', r'href="\1.html\2"', body)


@dataclass
class Site:
    out: Path
    sparql_endpoint: str = ""
    template: string.Template = field(default_factory=lambda: string.Template(
        (SITE_SRC / "template.html").read_text(encoding="utf-8")))

    def nav(self, section: str, root: str) -> str:
        return "".join(f'<li{" class=selected" if key == section else ""}><a href="{root}{key}.html">{label}</a></li>'
                       for key, label in NAV)

    def subnav(self, page: str, section: str, root: str) -> str:
        items = SUBNAV.get(section)
        if not items:
            return ""
        links = "".join(f'<li{" class=selected" if key == page else ""}><a href="{root}{key}.html">{label}</a></li>'
                        for key, label in items)
        return f'<nav id="breadcrumb" aria-label="Section"><ul>{links}</ul></nav>'

    def add(self, path: str, title: str, text: str, section: str, editor: str = "") -> None:
        """Write path.md and path.html from the same Markdown."""
        depth = path.count("/")
        root = "../" * depth
        page = path.rsplit("/", 1)[-1]
        target = self.out / f"{path}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(MD_NOTICE + text.replace(SPARQL_EDITOR_MARK + "\n", ""), encoding="utf-8", newline="\n")
        body = render_body(text)
        scripts = MERMAID_SCRIPT if "```mermaid" in text else ""
        if editor:
            scripts += (f'<script type="module" src="{EDITOR_URL}"></script>\n'
                        f'<script type="module" src="{root}static/sparql-editor.js"></script>')
        body = body.replace(SPARQL_EDITOR_MARK, editor)
        html = self.template.substitute(
            title=title, root=root, page=page, nav=self.nav(section, root), subnav=self.subnav(page, section, root),
            body=body, issues=ISSUES_URL, repo=REPO_URL, markdown=f"{page}.md", scripts=scripts)
        (self.out / f"{path}.html").write_text(html, encoding="utf-8", newline="\n")


def translate(graph: Graph, source: Path, stem: Path) -> None:
    """Publish the Turtle byte for byte and translate it into the other formats."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    context = {prefix: str(ns) for prefix, ns in graph.namespaces() if prefix}
    for _, extension, rdf_format, _ in FORMATS:
        target = stem.with_name(f"{stem.name}.{extension}")
        if rdf_format == "turtle":
            shutil.copyfile(source, target)
        elif rdf_format == "json-ld":
            target.write_text(graph.serialize(format=rdf_format, context=context, indent=2), encoding="utf-8", newline="\n")
        else:
            target.write_text(graph.serialize(format=rdf_format), encoding="utf-8", newline="\n")


def sparql_editor(endpoint: str, examples: list[Example]) -> str:
    """The query editor, with one tab per example. The editor needs an endpoint, so without one there is none."""
    if not endpoint:
        return ""
    tabs = [{"id": f"example-{e.stem}", "name": e.title, "query": e.query} for e in examples]
    return (f'<sparql-editor endpoint="{escape(endpoint, quote=True)}" default-method="POST" '
            f'data-examples="{escape(json.dumps(tabs), quote=True)}"></sparql-editor>')


def hand_written(name: str, elements: list[Element]) -> str:
    text = (SITE_SRC / "pages" / f"{name}.md").read_text(encoding="utf-8")
    return (text.replace("{{coverage}}", coverage_note(elements)).replace("{{issues}}", ISSUES_URL)
            .replace("{{repo}}", REPO_URL))


def build(data_path: Path, revision_path: Path, out: Path, sparql_endpoint: str = "",
          wikidata_path: Path = WIKIDATA_CSV, references_path: Path = REFERENCES_JSON,
          resources: list[Resource] | None = None) -> dict[str, bool]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(SITE_SRC / "static", out / "static")
    shutil.copytree(QUERIES, out / "sparql" / "queries")
    shutil.copytree(EXAMPLES, out / "sparql" / "examples")

    data = Graph().parse(data_path, format="turtle")
    revision = Graph().parse(revision_path, format="turtle")
    tap = t.load_tap()

    translate(data, data_path, out / "data" / "ciaaw")
    translate(revision, revision_path, out / "data" / "ciaaw-revision-example")
    translate(t.load_shapes(), t.SHACL_OUT, out / "model" / "model_shacl")

    # pyshacl adds triples to the shapes graph it is given, so it gets its own copy.
    shapes = t.load_shapes()
    reports = {p.name: t.validate_graph(g, shapes)[0] for p, g in ((data_path, data), (revision_path, revision))}
    for source in (t.TAP_CSV, t.TAP_CONFIG, t.RULES, t.DIAGRAMS_OUT):
        shutil.copyfile(source, out / "model" / source.name)

    elements = read_elements(data)
    notes = footnotes(data)
    links = read_wikidata(wikidata_path)
    references = read_references(references_path)
    site = Site(out, sparql_endpoint)
    site.add("index", "Home", hand_written("index", elements), "index")
    site.add("about", "About", hand_written("about", elements), "about")
    site.add("atomic-weights", "Standard Atomic Weights", page_atomic_weights(elements, notes), "atomic-weights")
    site.add("isotopic-abundances", "Isotopic Abundances", page_isotopic_abundances(elements, notes), "isotopic-abundances")
    for element in elements:
        site.add(f"elements/{element.page}", element.name.capitalize(), page_element(element, notes, links, references), "atomic-weights")
    site.add("model", "Data Model", page_model(tap, reports), "model")
    site.add("revision-example", "Revision Example", page_revision(revision), "model")
    site.add("downloads", "Downloads", page_downloads(out, elements), "downloads")
    resources = RESOURCES if resources is None else resources
    site.add("comparison", "Comparison", page_comparison(resources), "comparison")
    for resource in resources:
        site.add(resource.key, f"Comparison with {resource.name}", page_resource_comparison(resource), "comparison")
    examples = read_examples(data_path)
    site.add("sparql", "SPARQL", page_sparql(examples, sparql_endpoint), "sparql",
             editor=sparql_editor(sparql_endpoint, examples))
    return reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=HERE / "example-output.ttl", help="Turtle shown as the current data")
    ap.add_argument("--revision", type=Path, default=HERE / "example-output-next.ttl",
                    help="Turtle for the revision example")
    ap.add_argument("--out", type=Path, default=HERE / "_site")
    ap.add_argument("--sparql-endpoint", default=os.environ.get("SPARQL_ENDPOINT", ""),
                    help="URL of the live SPARQL endpoint; without it the query form is left out")
    ap.add_argument("--wikidata", type=Path, default=WIKIDATA_CSV,
                    help="CSV with the Wikidata items, see wikidata_links.py (missing file: no Wikidata links)")
    args = ap.parse_args(argv)
    reports = build(args.data, args.revision, args.out, args.sparql_endpoint, args.wikidata)
    for name, ok in reports.items():
        print(f"{name}: {'conforms' if ok else 'DOES NOT CONFORM'}", file=sys.stderr)
    print(f"site written to {args.out}", file=sys.stderr)
    return 0 if all(reports.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
