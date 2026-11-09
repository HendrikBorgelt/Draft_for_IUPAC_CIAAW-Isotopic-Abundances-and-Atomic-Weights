#!/usr/bin/env python3
"""Build the static prototype site for GitHub Pages.

  python -m ciaaw_draft.website.build_site [--data data/ciaaw.ttl] [--revision data/example-output-next.ttl] [--out _site]
                       [--sparql-endpoint URL] [--wikidata site/wikidata.csv]

Every page is written as Markdown first. The HTML page is rendered from that same Markdown and wrapped
in site/template.html, so both versions always say the same thing. The Turtle is published unchanged,
and the other RDF formats are translated from it.

The SPARQL page shows the queries in sparql/queries with their results, computed with Oxigraph because
that is the engine behind the endpoint. The queries are published as .rq files, and as sparql-examples Turtle
from sparql/examples. A query editor runs against SPARQL_ENDPOINT below. Use --sparql-endpoint or the
SPARQL_ENDPOINT environment variable for another endpoint, and --sparql-endpoint "" to leave the editor out.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import string
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import markdown
import pyoxigraph as ox
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, SKOS

from ciaaw_draft import namespaces as n
from ciaaw_draft.convert import ciaaw2ttl as c
from ciaaw_draft.convert import ttl2sssom
from ciaaw_draft.schema import tap2model as t

from ciaaw_draft.paths import DATA_DIR, MODEL_DIR, ROOT, SITE_SRC, SPARQL_DIR

REPO_URL = n.REPO_URL
ISSUES_URL = f"{REPO_URL}/issues"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.2.0/dist/mermaid.esm.min.mjs"
ELEMENT_COUNT = 118
WIKIDATA_CSV = SITE_SRC / "wikidata.csv"
REFERENCES_JSON = SITE_SRC / "references.json"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/"
NBSP = " "

CIAAW, PROV = n.CIAAW, n.PROV

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
SUBNAV = {"atomic-weights": [("atomic-weights", "Values"), ("visualization", "Visualization")],
          "model": [("model", "Data Model"), ("ciaaw-draft", "Vocabulary"), ("revision-example", "Revision Example")]}
ONTOLOGY = MODEL_DIR / "ontology.ttl"
ALIGNMENT = MODEL_DIR / "alignment.sssom.tsv"

QUERIES = SPARQL_DIR / "queries"
EXAMPLES = SPARQL_DIR / "examples"
WORKER_URL = "https://github.com/JervenBolleman/oxigraph-cloudflare-webworker"
SPARQL_ENDPOINT = "https://ciaaw-draft.jerven-bolleman.workers.dev/sparql"
SHORT_NAMES = {**{namespace: f"{prefix}:" for prefix, namespace in n.NS.prefixes().items()},
               str(n.CHEBI): "chebi:", str(n.SI_UNITS): "units:", "http://www.w3.org/2001/XMLSchema#": "xsd:"}
TURTLE_NOTICE = "# Prototype: a suggestion, not an official CIAAW or IUPAC record. Official values are at https://ciaaw.org\n\n"
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
    """Group decimals in threes (1.007 84) with no-break spaces, as in the CIAAW tables."""
    return c.group_digits(number, NBSP)


def printed(g: Graph, declaration) -> str:
    """The value of a declaration as CIAAW prints it (ciaaw:hasValueAsString), with no-break spaces."""
    return str(g.value(declaration, CIAAW.hasValueAsString)).replace(" ", NBSP)


def page_path(iri) -> str | None:
    """The page of a resource of this site, from the root: elements/Hydrogen, standard-atomic-weights/H-2009, the
    index of a folder for a namespace itself, and ciaaw-draft for the vocabulary. None for anything else."""
    found = n.NS.folder_of(iri)
    if found is None:
        return "ciaaw-draft" if str(iri).split("#")[0] == str(n.ONTOLOGY) else None
    folder, local = found
    return f"{folder}{local or 'index'}"


# ---------------------------------------------------------------------------
# Reading the RDF
# ---------------------------------------------------------------------------
@dataclass
class Declaration:
    iri: URIRef
    text: str
    issued: str                 # year of the recommendation
    sources: list[str]          # DOI links of the reports
    notes: list[str]
    superseded: bool
    next: URIRef | None
    previous: URIRef | None = None
    ended: str = ""             # year in which the next declaration replaced it
    reports: list[URIRef] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)


@dataclass
class Isotope:
    iri: URIRef
    mass_number: int
    abundance: Declaration
    history: list[Declaration]
    matches: list[str]
    derived_from: list[str] = field(default_factory=list)

    @property
    def page(self) -> str:
        return local_name(self.iri)


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
    derived_from: list[str] = field(default_factory=list)

    @property
    def page(self) -> str:
        return local_name(self.iri)

    @property
    def abundance_notes(self) -> list[str]:
        return self.isotopes[0].abundance.notes if self.isotopes else []


def local_name(iri) -> str:
    return re.split(r"[/#]", str(iri))[-1]


def read_declaration(g: Graph, iri: URIRef) -> Declaration:
    reports = sorted(g.objects(iri, CIAAW.hasDefiningReport), key=str)
    return Declaration(
        iri, printed(g, iri), str(g.value(iri, CIAAW.hasStartValidity) or ""),
        sorted(str(g.value(r, RDFS.seeAlso)) for r in reports if g.value(r, RDFS.seeAlso) is not None),
        sorted(str(g.value(note, SKOS.notation)) for note in g.objects(iri, CIAAW.hasFootnote)),
        str(g.value(iri, CIAAW.hasStatus)) == n.STATUS_SUPERSEDED, g.value(iri, CIAAW.hasNextDeclaration),
        g.value(iri, CIAAW.hasPreviousDeclaration), str(g.value(iri, CIAAW.hasEndValidity) or ""), reports,
        sorted(str(s) for s in g.objects(iri, PROV.wasDerivedFrom)))


def read_history(g: Graph, subject) -> list[Declaration]:
    return sorted((read_declaration(g, d) for d in g.objects(subject, CIAAW.hasDeclaration)),
                  key=lambda d: (d.issued, str(d.iri)), reverse=True)


def read_elements(g: Graph) -> list[Element]:
    elements = []
    for iri in g.subjects(RDF.type, CIAAW.ChemicalElement):
        isotopes = [Isotope(i, int(g.value(i, CIAAW.hasMassNumber)), read_declaration(g, g.value(i, CIAAW.hasCurrentAbundance)),
                            read_history(g, i), sorted(str(m) for m in g.objects(i, SKOS.closeMatch)),
                            sorted(str(s) for s in g.objects(i, PROV.wasDerivedFrom)))
                    for i in g.objects(iri, CIAAW.hasIsotope)]
        current = g.value(iri, CIAAW.hasCurrentStandardAtomicWeight)
        longest = g.value(iri, CIAAW.hasLongestLivedMassNumber)
        elements.append(Element(
            iri, str(g.value(iri, RDFS.label)), str(g.value(iri, CIAAW.hasSymbol)),
            int(g.value(iri, CIAAW.hasAtomicNumber)), read_declaration(g, current) if current is not None else None,
            read_history(g, iri), sorted(isotopes, key=lambda i: i.mass_number),
            int(longest) if longest is not None else None, sorted(str(m) for m in g.objects(iri, SKOS.closeMatch)),
            sorted(str(s) for s in g.objects(iri, PROV.wasDerivedFrom))))
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


def derived_from_links(documents: list[str]) -> str:
    """The documents a resource was read from (prov:wasDerivedFrom), by name."""
    return ", ".join(f"[{n.SOURCE_LABELS.get(URIRef(d), (d,))[0]}]({d})" for d in documents) or "—"


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
    return declarations_bibliography([*e.history, *(d for i in e.isotopes for d in i.history)], references)


def declarations_bibliography(declarations: list[Declaration], references: dict[str, str]) -> str:
    """The Bibliography of some declarations: their DOIs, formatted by citation.js."""
    return bibliography_section({s.removeprefix("https://doi.org/") for d in declarations for s in d.sources
                                 if s.startswith("https://doi.org/10.")}, references)


def bibliography_section(dois: set[str], references: dict[str, str]) -> str:
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
    doi: str = ""       # article to cite for the resource, formatted from references.json
    label: str = ""                     # name in the navigation and the overview, if not the name
    subject: str = "isotope"            # what is compared: one row per subject
    value: str = "abundance"            # the compared value, as in "the resource has no natural abundance"
    texts: dict | None = None            # explanation of a kind in COMPARISON_KINDS that differs from the default


RESOURCES = [
    Resource("wikidata-comparison", "Wikidata", "https://www.wikidata.org", SITE_SRC / "wikidata-comparison.json",
             "wikidata-comparison",
             "Isotopic abundances in this data compared with the natural abundance (P2374) of the isotopes in "
             "[Wikidata](https://www.wikidata.org), matched on atomic number and number of neutrons. Values are in "
             "percent. The comparison is made by `check_wikidata.py`.", WIKIDATA_ENTITY, label="Wikidata abundances"),
    Resource("wikidata-weights-comparison", "Wikidata", "https://www.wikidata.org",
             SITE_SRC / "wikidata-weights-comparison.json", "wikidata-weights-comparison",
             "Standard atomic weights in this data compared with the mass (P2067, in dalton) of the chemical elements "
             "in [Wikidata](https://www.wikidata.org), matched on the atomic number. A weight agrees if the Wikidata "
             "value is inside the local interval or within the local uncertainty, or if the bounds of a rounded "
             "Wikidata value contain the local weight. The comparison is made by `check_wikidata_weights.py`.",
             WIKIDATA_ENTITY, label="Wikidata atomic weights", subject="element", value="mass",
             texts={"ONLY-RESOURCE": "{name} has a mass for the element, the local data has no standard atomic weight "
                                     "for it. CIAAW gives none for these elements (no stable isotopes, or not "
                                     "assessed)."}),
    Resource("bodr-comparison", "BODR", "https://github.com/egonw/bodr", SITE_SRC / "bodr-comparison.json",
             "bodr-comparison",
             "Isotopic abundances in this data compared with the relative abundances in the isotopes file of the "
             "[Blue Obelisk Data Repository](https://github.com/egonw/bodr) (BODR), matched on atomic number and mass "
             "number. Values are in percent. BODR gives no uncertainty: a local value must equal the BODR value, and "
             "for a local interval the BODR value must fall within it. The comparison is made by `check_bodr.py`.",
             doi="10.1186/1758-2946-3-37"),
]

SUBNAV["comparison"] = [("comparison", "Overview"), *((r.key, r.label or r.name) for r in RESOURCES)]

# kind in the JSON, title, explanation ({name} is the resource)
COMPARISON_KINDS = [
    ("DIFFERENT", "Different values", "{name} has {value} for the {subject}, and it differs from the local one."),
    ("NO-VALUE", "No value", "The {subject} is in {name}, but without a {value}."),
    ("NOT-FOUND", "Not found", "{name} has no item for this {subject}."),
    ("UNIT", "Unexpected unit", "The {value} in {name} has an unexpected unit."),
    ("ONLY-RESOURCE", "Only in the resource", "{name} has a {value}, the local data has no such {subject}."),
]


def item_links(resource: Resource, ids: str) -> str:
    """Links to the items of the resource; several items are joined with a slash."""
    return " ".join(f"[{i}]({resource.item_url}{i})" if resource.item_url else i for i in ids.split("/") if i)


def reference_links(resource: Resource, sources: list[dict]) -> str:
    """The references of a value in the resource: the DOI if the source has one, else the source item."""
    return " ".join(f"[{s['doi']}](https://doi.org/{s['doi']})" if s.get("doi")
                    else f"[{s.get('label') or s['qid']}]({resource.item_url}{s['qid']})" if resource.item_url
                    else s.get("label") or s["qid"] for s in sources)


def read_comparison(resource: Resource) -> dict | None:
    return json.loads(resource.json.read_text(encoding="utf-8")) if resource.json.exists() else None


def page_comparison(resources: list[Resource], sample: bool = False) -> str:
    """The overview: one row per resource, with the counts of what differs."""
    rows = []
    for r in resources:
        result = read_comparison(r)
        counts = result["counts"] if result else {}
        cells = " | ".join(str(counts.get(kind, 0)) if result else "—" for kind, _, _ in COMPARISON_KINDS[:3])
        rows.append(f"| [{r.label or r.name}]({r.key}.md) | {result['generated'] if result else 'not generated'} | {cells} |")
    return "\n".join([
        "# Comparison\n",
        "The isotopic abundances and the standard atomic weights in this data are compared with other resources, to "
        "find values that differ and isotopes or elements that are missing on one side. A difference is a reason to look at both sides, not a verdict on "
        "which one is right.\n",
        "| Resource | Compared on | Different values | No value | Not found |", "| --- | --- | --- | --- | --- |",
        *rows, "",
        ("Only a sample of the elements is on this site, so the results are only a demonstration. " if sample else "")
        + "Each comparison is a script that writes JSON, which the pages are built from, see the "
        "[README]({{repo}}#readme).\n"]).replace("{{repo}}", REPO_URL)


def resource_bibliography(result: dict) -> str:
    """The references of the values in the resource that have a DOI, formatted by citation.js; "" if none."""
    dois = {s["doi"] for d in result["differences"] for s in d.get("sources", []) if s.get("doi")}
    return "\n" + bibliography_section(dois, read_references(REFERENCES_JSON)) if dois else ""


def page_resource_comparison(resource: Resource, sample: bool = False) -> str:
    """The differences between the local abundances and one resource, from the JSON of its script."""
    intro = f"# Comparison with {resource.name}\n\n{resource.description}\n" if not resource.label else \
        f"# Comparison: {resource.label}\n\n{resource.description}\n"
    if resource.doi:
        cited = read_references(REFERENCES_JSON).get(resource.doi.upper(), resource.doi)
        intro += (f'\n<div class="csl-bib-body">\n<div class="csl-entry">{cited} '
                  f'<a href="https://doi.org/{resource.doi}">CrossRef</a></div>\n</div>\n')
    result = read_comparison(resource)
    if result is None:
        return intro + f"\n> The comparison has not been generated yet. Run `make {resource.command}`.\n"
    counts = result["counts"]
    parts = [intro,
             f"Compared on {result['generated']}: {result['local_isotopes']} {resource.subject}s in `{result['source']}`, "
             f"{result.get('resource_items', result.get('wikidata_items'))} items in {resource.name}"
             f"{' (' + result['resource_version'] + ')' if result.get('resource_version') else ''}."
             f"{' Only a sample of the elements is on this site, so many {resource.subject}s are only in the resource.' if sample else ''}\n",
             "| Result | Count |", "| --- | --- |",
             *(f"| {title} | {counts.get(kind, 0)} |" for kind, title, _ in COMPARISON_KINDS)]
    cited = any(d.get("sources") for d in result["differences"])
    for kind, title, text in COMPARISON_KINDS:
        rows = [d for d in result["differences"] if d["kind"] == kind]
        if not rows:
            continue
        parts += ["", f"## {title}\n", f"{(resource.texts or {}).get(kind, text).format(name=resource.name, value=resource.value, subject=resource.subject)}\n",
                  f"| {resource.subject.capitalize()} | Local | Value in resource | Item |" + (" Source |" if cited else ""),
                  "| --- | --- | --- | --- |" + (" --- |" if cited else ""),
                  *(f"| {d['name']} | {d['local'] or '—'} | {d['wikidata'] or '—'} | {item_links(resource, d['qid'])} |"
                    + (f" {reference_links(resource, d.get('sources', [])) or '—'} |" if cited else "")
                    for d in rows)]
    return "\n".join(parts) + resource_bibliography(result) + "\n"


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
            rows.append(f"{lead} [{isotope.mass_number}]({page_path(isotope.iri)}.md) | {isotope.abundance.text} | {letters} |")
    table = "\n".join(["| *Z* | E | Element | A | Representative isotopic composition | Notes |",
                       "| --- | --- | --- | --- | --- | --- |", *rows])
    return (f"# Isotopic Abundances\n\n{coverage_note(elements)}\n"
            "Each row is a separate declaration in the RDF. A mass number in square brackets is the "
            f"longest-lived isotope of an element without stable isotopes.\n\n{table}\n\n{notes_section(notes)}")


def declaration_link(iri, root: str | None) -> str:
    """The name of a declaration. root leads from this page to the root of the site; None means no link."""
    name = curie(iri)
    return f"`{name}`" if root is None else f"[`{name}`]({root}{page_path(iri)}.md)"


def declaration_status(d: Declaration, root: str | None) -> str:
    if not d.superseded:
        return "Current"
    return f"Superseded{f' in {d.ended}' if d.ended else ''} by {declaration_link(d.next, root)}"


def declaration_table(history: list[Declaration], root: str | None = None) -> str:
    lines = ["| Value | Year | Report | Status | Declaration |", "| --- | --- | --- | --- | --- |"]
    for d in history:
        lines.append(f"| {d.text} | {d.issued or '—'} | {source_links(d.sources)} | {declaration_status(d, root)} "
                     f"| {declaration_link(d.iri, root)} |")
    return "\n".join(lines)


def turtle_section(name: str, turtle: str) -> str:
    return f"## Turtle\n\nThis resource as a file: [{name}.ttl]({name}.ttl)\n\n```turtle\n{turtle}```\n"


def page_isotope(e: Element, i: Isotope, notes: dict[str, str], links: dict, turtle: str,
                 references: dict[str, str] | None = None) -> str:
    facts = ["| Property | Value |", "| --- | --- |", f"| Element | [{e.name}](../elements/{e.page}.md) |",
             f"| Mass number | {i.mass_number} |", f"| Derived from | {derived_from_links(i.derived_from)} |",
             f"| IRI | `{i.iri}` |"]
    if i.matches:
        facts.append(f"| ChEBI | {chebi_links(i.matches)} |")
    if wikidata_link(links, e.z, i.mass_number):
        facts.append(f"| Wikidata | {wikidata_link(links, e.z, i.mass_number)} |")
    parts = [f"# {e.name}-{i.mass_number}\n", "\n".join(facts) + "\n",
             "## Isotopic abundance\n", declaration_table(i.history, "../") + "\n"]
    if i.abundance.notes:
        parts += [f"Notes: {note_links(i.abundance.notes)}\n", notes_section(notes, i.abundance.notes)]
    if bib := declarations_bibliography(i.history, references or {}):
        parts.append(bib)
    parts.append(turtle_section(i.page, turtle))
    return "\n".join(parts)


def page_declaration(d: Declaration, title: str, about: str, notes: dict[str, str], turtle: str,
                     references: dict[str, str] | None = None) -> str:
    reports = ", ".join(f"[`{curie(r)}`](../{page_path(r)}.md)" for r in d.reports) or "—"
    facts = ["| Property | Value |", "| --- | --- |", f"| About | {about} |", f"| Value | {d.text} |",
             f"| Year | {d.issued or '—'} |", f"| Report | {reports} {source_links(d.sources) if d.sources else ''} |",
             f"| Status | {declaration_status(d, '../')} |"]
    if d.previous is not None:
        facts.append(f"| Previous | {declaration_link(d.previous, '../')} |")
    facts += [f"| Derived from | {derived_from_links(d.derived_from)} |", f"| IRI | `{d.iri}` |"]
    if d.notes:
        facts.append(f"| Notes | {note_links(d.notes)} |")
    parts = [f"# {title}\n", "\n".join(facts) + "\n"]
    if d.notes:
        parts.append(notes_section(notes, d.notes))
    if references is not None and (bib := declarations_bibliography([d], references)):
        parts.append(bib)
    parts.append(turtle_section(local_name(d.iri), turtle))
    return "\n".join(parts)


def page_element(e: Element, notes: dict[str, str], links: dict | None = None,
                 references: dict | None = None, turtle: str = "") -> str:
    links = links or {}
    facts = ["| Property | Value |", "| --- | --- |", f"| Symbol | {e.symbol} |", f"| Atomic number | {e.z} |",
             f"| Derived from | {derived_from_links(e.derived_from)} |", f"| IRI | `{e.iri}` |"]
    if e.matches:
        facts.append(f"| ChEBI | {chebi_links(e.matches)} |")
    if wikidata_link(links, e.z):
        facts.append(f"| Wikidata | {wikidata_link(links, e.z)} |")
    if e.longest_lived:
        facts.append(f"| Longest-lived isotope | {e.longest_lived} |")
    parts = [f"# {e.name}\n", "\n".join(facts) + "\n", "## Standard atomic weight\n"]
    if e.weight:
        letters = f"Notes: {note_links(e.weight.notes)}\n" if e.weight.notes else ""
        parts += [declaration_table(e.history, "../") + "\n", letters]
    else:
        parts.append("The table gives no standard atomic weight for this element.\n")
    parts.append("## Isotopes\n")
    if e.isotopes:
        rows = ["| A | Representative isotopic composition | Year | Report | Declaration | ChEBI | Wikidata |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for i in e.isotopes:
            d = i.abundance
            rows.append(f"| [{i.mass_number}](../{page_path(i.iri)}.md) | {d.text} | {d.issued or '—'} | {source_links(d.sources)} "
                        f"| {declaration_link(d.iri, '../')} | {chebi_links(i.matches) or '—'} "
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
    if turtle:
        parts.append(turtle_section(e.page, turtle))
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
             "| [model_shacl.ttl](model/model_shacl.ttl) | The generated SHACL shapes, including those rules |",
             "| [ontology.ttl](model/ontology.ttl) | The vocabulary, written by hand: every term with a definition. "
             "See the [Vocabulary](ciaaw-draft.md) page |",
             "| [alignment.sssom.tsv](model/alignment.sssom.tsv) | How the terms relate to BFO, IAO, ChEBI, PROV, Wikidata "
             "and the SI Digital Framework, as SSSOM mappings, without importing them |\n",
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
    lines = ["flowchart LR", f'    {name}["{curie(element)}"]']
    for d in history:
        state = "superseded" if d.superseded else "current"
        lines.append(f'    {node(d)}["{curie(d.iri)}<br/>{d.text}<br/>{state}"]')
        lines.append(f'    {name} -- "ciaaw:hasDeclaration" --> {node(d)}')
        if not d.superseded:
            lines.append(f'    {name} == "ciaaw:hasCurrentStandardAtomicWeight" ==> {node(d)}')
    by_iri = {d.iri: d for d in history}
    for d in history:
        if d.next in by_iri:
            lines.append(f'    {node(d)} -- "ciaaw:hasNextDeclaration" --> {node(by_iri[d.next])}')
    return "\n".join(lines) + "\n"


def page_revision(g: Graph) -> str:
    changed = [e for e in read_elements(g) if len(e.history) > 1]
    parts = ["# Revision Example\n",
             "> **The new value on this page is invented.** It only demonstrates what the data looks like after a "
             "revision. It is not a CIAAW value, not even a proposed one.\n",
             "When CIAAW revises a value, nothing is edited or deleted. The converter compares the new table with the "
             "previous output and then:\n",
             "1. creates a new declaration with the new value and the status current,\n"
             "2. moves the element's current pointer to it,\n"
             "3. gives the old declaration the status superseded and the year it ended, and links the two with "
             "`ciaaw:hasNextDeclaration` and `ciaaw:hasPreviousDeclaration`, and\n"
             "4. keeps every declaration in the element's history.\n",
             "This is how the SI Reference Point of the BIPM chains the definitions of a unit.\n"]
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


def page_downloads(out: Path, elements: list[Element], parts: list[tuple[str, Path]] | None = None) -> str:
    rows = ["| Part | File | Size | SHA-256 |", "| --- | --- | --- | --- |"]
    for title, path in parts or []:
        rel = path.relative_to(out).as_posix()
        rows.append(f"| {title} | [{path.name}]({rel}) | {size_text(path)} | `{sha256_of(path)}` |")
    return "\n".join([
        "# Downloads\n", coverage_note(elements),
        "Every format holds the same triples. The Turtle is the converter's output, and the others are translated "
        "from it. The IRIs are placeholders until a persistent home is agreed.\n",
        "## Current data\n", format_table(out, "data/ciaaw") + "\n",
        f"SHA-256 of [ciaaw.ttl](data/ciaaw.ttl): `{sha256_of(out / 'data' / 'ciaaw.ttl')}`\n",
        "## Parts of the data\n",
        "The same data split by kind of resource, one file per namespace, as the "
        "[SI Reference Point](https://si-digital-framework.org/SI) of the BIPM publishes its knowledge graphs. "
        "Each file starts with a header (`owl:Ontology`) that gives its version, the documents its resources were "
        "read from and the rights statement. The checksums let you check a download.\n",
        "\n".join(rows) + "\n",
        "## Single resources\n",
        "Every element, isotope, declaration, report and footnote has its own page and Turtle file, and its IRI "
        "opens that page. For example, the page of [hydrogen-1](isotopes/H-1.md) links to [H-1.ttl](isotopes/H-1.ttl). "
        "GitHub Pages cannot choose a format per request, so software should add `.ttl` to an IRI, or read the "
        "`<link rel=\"alternate\">` of the page. The terms of the vocabulary are described on the "
        "[Vocabulary](ciaaw-draft.md) page.\n",
        "## Rights\n", f"{n.DATA_RIGHTS}\n",
        "## Mappings\n",
        "[ciaaw.sssom.tsv](data/ciaaw.sssom.tsv) maps the elements and isotopes of the current data to their "
        "[Wikidata](https://www.wikidata.org) items (`skos:exactMatch`, matched on atomic number and number of "
        "neutrons) and to ChEBI (`skos:closeMatch`). It is an [SSSOM](https://mapping-commons.github.io/sssom/) "
        "mapping set in the TSV serialisation: a YAML header in comment lines, then one mapping per row. The "
        "license (CC0) covers the mappings only, not the CIAAW values.\n",
        "## Revision example\n",
        "A small sample after an invented revision of hydrogen. See the [revision example](revision-example.md).\n",
        format_table(out, "data/ciaaw-revision-example") + "\n",
        "## Model\n",
        "The vocabulary, written by hand, with a definition for every term:\n", format_table(out, "model/ontology") + "\n",
        "[alignment.sssom.tsv](model/alignment.sssom.tsv) relates its terms to BFO, IAO, ChEBI, PROV, Wikidata and the "
        "SI Digital Framework, without importing them. The license (CC0) covers the mappings only.\n",
        "The [DCTAP profile](model/model_dctap.csv) with its [configuration](model/dctap.yaml), "
        "and the generated SHACL shapes:\n", format_table(out, "model/model_shacl") + "\n",
        "The diagrams are also available as [Markdown](model/diagrams.md).\n",
        "## Querying the data\n",
        "See the [SPARQL page](sparql.md) for example queries.\n"])


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Example:
    stem: str
    title: str
    description: str
    query: str
    columns: list[str]
    rows: list[list[str]]


def curie(iri) -> str:
    for namespace, prefix in SHORT_NAMES.items():
        if str(iri).startswith(namespace):
            return prefix + str(iri)[len(namespace):]
    return str(iri)


def short_term(term) -> str:
    return "" if term is None else curie(term.value)


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


def result_cell(value: str) -> str:
    """A result with a page on this site links to it, like its IRI does in the query editor."""
    for namespace, prefix in SHORT_NAMES.items():
        if value.startswith(prefix) and (path := page_path(namespace + value.removeprefix(prefix))):
            return f"[{value}]({path}.md)"
    return value


def result_table(example: Example) -> str:
    lines = ["| " + " | ".join(example.columns) + " |", "| " + " | ".join("---" for _ in example.columns) + " |"]
    lines += ["| " + " | ".join(result_cell(value) for value in row) + " |" for row in example.rows]
    return "\n".join(lines)


def page_sparql(examples: list[Example], endpoint: str, coverage: str = "") -> str:
    if endpoint:
        construct = "query=CONSTRUCT { ?s a ?o } WHERE { ?s a ?o }"
        status = [f"A prototype endpoint is running at `{endpoint}`. It may change or disappear without notice. "
                  "It only answers SPARQL requests, so opening the address in a browser shows an error.\n",
                  "## Try it\n", f"{SPARQL_EDITOR_MARK}\n",
                  "From the command line:\n",
                  f'```bash\ncurl --data-urlencode "query=ASK {{ ?s ?p ?o }}" {endpoint}\n```\n',
                  f'```bash\ncurl -H "Accept: text/turtle" --data-urlencode "{construct}" {endpoint}\n```\n',
                  "`CONSTRUCT` and `DESCRIBE` need an RDF format in the `Accept` header, as in the second example. "
                  "Without one, the endpoint answers with error 400.\n"]
    else:
        status = ["> This build of the site has no live endpoint, so it has no query editor. "
                  "The queries below can be run on the [downloaded data](downloads.md).\n"]
    parts = ["# SPARQL\n", coverage, *status,
             "## What the endpoint holds\n",
             f"It serves the [current data](data/ciaaw.ttl) and the example queries read-only, using [Oxigraph](https://github.com/oxigraph/oxigraph) "
             f"inside a Cloudflare Worker ([oxigraph-cloudflare-webworker]({WORKER_URL})). The "
             "[revision example](revision-example.md) is not loaded, so its invented value cannot appear in results.\n",
             "Every element, isotope, declaration and report has its own page, and the IRIs in the results lead there. "
             "Each page links to the Turtle file of that resource.\n",
             "## Decimals and trailing zeros\n",
             "Oxigraph stores decimals as numbers, so `162.500` comes back as `162.5` and `0.0000` as `0`. The value is "
             "the same, but the printed digits are not. `ciaaw:hasValueAsString` holds the value as CIAAW prints "
             "it, with every digit, as `si:hasValueAsString` does in the SI Reference Point.\n",
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

    def add(self, path: str, title: str, text: str, section: str, editor: str = "", turtle: bool = False,
            html: str = "") -> None:
        """Write path.md and path.html from the same Markdown. turtle: the page has a Turtle file next to it.
        html: the HTML body to publish instead of rendering the Markdown, which is then only the Markdown twin."""
        depth = path.count("/")
        root = "../" * depth
        page = path.rsplit("/", 1)[-1]
        target = self.out / f"{path}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(MD_NOTICE + text.replace(SPARQL_EDITOR_MARK + "\n", ""), encoding="utf-8", newline="\n")
        body = html or render_body(text)
        scripts = MERMAID_SCRIPT if "```mermaid" in text else ""
        if editor:
            scripts += (f'<script type="module" src="{EDITOR_URL}"></script>\n'
                        f'<script type="module" src="{root}static/sparql-editor.js"></script>')
        body = body.replace(SPARQL_EDITOR_MARK, editor)
        head = f'<link rel="alternate" type="text/turtle" href="{page}.ttl">' if turtle else ""
        html = self.template.substitute(
            title=title, root=root, page=page, nav=self.nav(section, root), subnav=self.subnav(page, section, root),
            body=body, issues=ISSUES_URL, repo=REPO_URL, markdown=f"{page}.md", scripts=scripts, head=head)
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


def resource_graph(g: Graph, iri: URIRef) -> Graph:
    """What the Turtle file of a resource holds: its triples, its declarations with their footnotes and reports,
    and what points to it."""
    out = Graph(namespace_manager=g.namespace_manager)
    todo = [iri, *g.objects(iri, CIAAW.hasDeclaration)]
    todo += [linked for node in list(todo) for p in (CIAAW.hasFootnote, CIAAW.hasDefiningReport)
             for linked in g.objects(node, p)]
    seen = set()
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node)
            for predicate, value in g.predicate_objects(node):
                out.add((node, predicate, value))
                if isinstance(value, BNode):
                    todo.append(value)
    for subject, predicate in g.subject_predicates(iri):
        out.add((subject, predicate, iri))
    return out


def write_turtle(out: Path, g: Graph, iri: URIRef, path: str) -> str:
    """Write the Turtle file of a resource next to its page, and return the text to show on that page."""
    text = TURTLE_NOTICE + resource_graph(g, iri).serialize(format="turtle").strip() + "\n"
    target = out / f"{path}.ttl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return text


def add_subpages(site: Site, g: Graph, e: Element, notes: dict[str, str], links: dict,
                 references: dict[str, str] | None = None) -> None:
    """The pages of the isotopes and declarations of an element. Their IRIs lead here."""
    def add_declaration(d: Declaration, title: str, about: str, section: str, cite: bool = False) -> None:
        path = page_path(d.iri)
        turtle = write_turtle(site.out, g, d.iri, path)
        site.add(path, title, page_declaration(d, title, about, notes, turtle, references if cite else None),
                 section, turtle=True)

    for d in e.history:
        title = f"Standard atomic weight of {e.name}" + (f", {d.issued}" if d.issued else "")
        add_declaration(d, title, f"[{e.name}](../{page_path(e.iri)}.md)", "atomic-weights")
    for i in e.isotopes:
        name, path = f"{e.name}-{i.mass_number}", page_path(i.iri)
        turtle = write_turtle(site.out, g, i.iri, path)
        site.add(path, name.capitalize(), page_isotope(e, i, notes, links, turtle, references), "isotopic-abundances", turtle=True)
        for d in i.history:
            add_declaration(d, f"Isotopic abundance of {name}", f"[{name}](../{path}.md)", "isotopic-abundances", cite=True)


def resource_facts(g: Graph, iri: URIRef, skip=()) -> list[str]:
    """A table of the literal properties of a resource, and of the links to other pages of the site."""
    rows = ["| Property | Value |", "| --- | --- |"]
    for p, o in sorted(g.predicate_objects(iri), key=lambda po: (curie(po[0]), str(po[1]))):
        if p in skip or p == RDF.type:
            continue
        if isinstance(o, URIRef):
            path = page_path(o)
            value = f"[`{curie(o)}`](../{path}.md)" if path else f"<{o}>"
        else:
            value = str(o)
        rows.append(f"| `{curie(p)}` | {value} |")
    rows.append(f"| IRI | `{iri}` |")
    return rows


def add_shared_pages(site: Site, g: Graph, references: dict[str, str]) -> None:
    """The pages of the reports, footnotes and bodies, which declarations share."""
    for report in sorted(g.subjects(RDF.type, CIAAW.Report), key=str):
        path = page_path(report)
        turtle = write_turtle(site.out, g, report, path)
        declarations = sorted(g.subjects(CIAAW.hasDefiningReport, report), key=str)
        uses = "\n".join(f"- [`{curie(d)}`](../{page_path(d)}.md) {g.value(d, RDFS.label)}" for d in declarations)
        doi = str(g.value(report, DCTERMS.identifier))
        parts = [f"# Report {doi}\n", "\n".join(resource_facts(g, report)) + "\n",
                 bibliography_section({doi}, references), f"## Declarations from this report\n\n{uses}\n",
                 turtle_section(local_name(report), turtle)]
        site.add(path, f"Report {doi}", "\n".join(parts), "atomic-weights", turtle=True)
    for kind, section in ((CIAAW.Footnote, "atomic-weights"), (CIAAW.Body, "about")):
        for node in sorted(g.subjects(RDF.type, kind), key=str):
            path = page_path(node)
            turtle = write_turtle(site.out, g, node, path)
            title = str(g.value(node, RDFS.label))
            site.add(path, title[0].upper() + title[1:],
                     "\n".join([f"# {title[0].upper() + title[1:]}\n", "\n".join(resource_facts(g, node)) + "\n",
                                turtle_section(local_name(node), turtle)]), section, turtle=True)


def write_parts(out: Path, g: Graph) -> list[tuple[str, Path]]:
    """The data split by namespace, one Turtle file per kind of resource with its header, as the SI Reference Point
    publishes its knowledge graphs. Each part is also the Turtle file of the index page of its folder."""
    parts = []
    for prefix, folder in n.FOLDERS.items():
        namespace = str(getattr(n.NS, prefix))
        part = Graph(namespace_manager=g.namespace_manager)
        for triple in g:
            if str(triple[0]).startswith(namespace):
                part.add(triple)
        if not len(part):
            continue
        for source in set(part.objects(None, PROV.wasDerivedFrom)):
            for triple in g.triples((source, None, None)):
                part.add(triple)
        text = TURTLE_NOTICE + part.serialize(format="turtle")
        for target in (out / "data" / f"{folder.rstrip('/')}.ttl", out / folder / "index.ttl"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        parts.append((n.TITLES[prefix], out / "data" / f"{folder.rstrip('/')}.ttl"))
    return parts


def add_indexes(site: Site, g: Graph) -> None:
    """One page per namespace, the address of the namespace itself: its header and the list of its resources."""
    for prefix, folder in n.FOLDERS.items():
        head = URIRef(str(getattr(n.NS, prefix)))
        if (head, RDF.type, OWL.Ontology) not in g:
            continue
        members = sorted((s for s in set(g.subjects()) if str(s).startswith(str(head)) and s != head),
                         key=lambda s: (str(g.value(s, RDFS.label) or ""), str(s)))
        listing = "\n".join(f"- [`{curie(s)}`]({local_name(s)}.md) {g.value(s, RDFS.label) or ''}" for s in members)
        facts = resource_facts(g, head)
        title = n.TITLES[prefix]
        site.add(f"{folder}index", title, "\n".join([
            f"# {title}\n", "\n".join(facts) + "\n", f"## {len(members)} resources\n\n{listing}\n",
            f"## Turtle\n\nThis part of the data as a file: [index.ttl](index.ttl)\n"]), "downloads", turtle=True)


def page_vocabulary(onto: Graph) -> str:
    """The terms of the ontology, so that their IRIs (ciaaw-draft#hasSymbol) lead to a description."""
    def term_rows(kinds) -> str:
        rows = ["| Term | Definition | Domain → range |", "| --- | --- | --- |"]
        terms = sorted({s for kind in kinds for s in onto.subjects(RDF.type, kind) if isinstance(s, URIRef)
                        and str(s).startswith(str(CIAAW)) and (s, OWL.deprecated, Literal(True)) not in onto}, key=str)
        for term in terms:
            local = local_name(term)
            ends = [", ".join(f"`{curie(c)}`" for c in classes(onto.value(term, p))) for p in (RDFS.domain, RDFS.range)]
            rows.append(f'| <a id="{local}"></a>`ciaaw:{local}` | {onto.value(term, SKOS.definition)} | '
                        f"{' → '.join(e or '—' for e in ends) if any(ends) else ''} |")
        return "\n".join(rows)

    def classes(node) -> list:
        if node is None:
            return []
        union = onto.value(node, OWL.unionOf)
        return list(Collection(onto, union)) if union is not None else [node]

    head = onto.value(predicate=RDF.type, object=OWL.Ontology)
    deprecated = sorted(onto.subjects(OWL.deprecated, Literal(True)), key=str)
    old = "\n".join(f'| <a id="{local_name(t)}"></a>`ciaaw:{local_name(t)}` | '
                     f"{', '.join(f'`{curie(r)}`' for r in sorted(onto.objects(t, DCTERMS.isReplacedBy), key=str)) or onto.value(t, SKOS.editorialNote)} |"
                     for t in deprecated)
    return "\n".join([
        "# Vocabulary\n",
        f"{onto.value(head, DCTERMS.description)}\n",
        f"Version {onto.value(head, OWL.versionInfo)}. The file: [ciaaw-draft.ttl](ciaaw-draft.ttl), also in "
        "[other formats](downloads.md#model). The terms follow the "
        "[SI Reference Point](https://si-digital-framework.org/SI) of the BIPM, and "
        "[alignment.sssom.tsv](model/alignment.sssom.tsv) relates them to BFO, IAO, ChEBI, PROV and Wikidata.\n",
        f"{onto.value(head, DCTERMS.rights)}\n",
        "## Classes\n", term_rows([OWL.Class]) + "\n",
        "## Properties\n", term_rows([OWL.ObjectProperty, OWL.DatatypeProperty]) + "\n",
        "## Earlier terms\n",
        "Kept so that their IRIs keep working, as in OBO practice: deprecated, with what replaces them.\n",
        "| Term | Replaced by |", "| --- | --- |", old + "\n"])


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
    from ciaaw_draft.website import visualization
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
    with (out / "data" / "ciaaw.sssom.tsv").open("w", newline="", encoding="utf-8") as f:
        ttl2sssom.write(data_path, f)
    translate(revision, revision_path, out / "data" / "ciaaw-revision-example")
    translate(t.load_shapes(), t.SHACL_OUT, out / "model" / "model_shacl")
    translate(t.load_ontology(), ONTOLOGY, out / "model" / "ontology")
    shutil.copyfile(ONTOLOGY, out / "ciaaw-draft.ttl")

    # pyshacl adds triples to the shapes graph it is given, so it gets its own copy.
    shapes = t.load_shapes()
    reports = {p.name: t.validate_graph(g, shapes)[0] for p, g in ((data_path, data), (revision_path, revision))}
    for source in (t.TAP_CSV, t.TAP_CONFIG, t.RULES, t.DIAGRAMS_OUT, ALIGNMENT):
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
    body, twin = visualization.page(elements, data, notes, coverage_note(elements))
    site.add("visualization", "Visualization", twin, "atomic-weights", html=body)
    for element in elements:
        path = f"elements/{element.page}"
        turtle = write_turtle(out, data, element.iri, path)
        site.add(path, element.name.capitalize(), page_element(element, notes, links, references, turtle),
                 "atomic-weights", turtle=True)
        add_subpages(site, data, element, notes, links, references)
    add_shared_pages(site, data, references)
    parts = write_parts(out, data)
    add_indexes(site, data)
    site.add("model", "Data Model", page_model(tap, reports), "model")
    site.add("ciaaw-draft", "Vocabulary", page_vocabulary(t.load_ontology()), "model", turtle=True)
    site.add("revision-example", "Revision Example", page_revision(revision), "model")
    site.add("downloads", "Downloads", page_downloads(out, elements, parts), "downloads")
    resources = RESOURCES if resources is None else resources
    sample = len(elements) < ELEMENT_COUNT
    site.add("comparison", "Comparison", page_comparison(resources, sample), "comparison")
    for resource in resources:
        site.add(resource.key, f"Comparison with {resource.label or resource.name}", page_resource_comparison(resource, sample), "comparison")
    examples = read_examples(data_path)
    site.add("sparql", "SPARQL", page_sparql(examples, sparql_endpoint, coverage_note(elements)), "sparql",
             editor=sparql_editor(sparql_endpoint, examples))
    return reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA_DIR / "ciaaw.ttl", help="Turtle shown as the current data")
    ap.add_argument("--revision", type=Path, default=DATA_DIR / "example-output-next.ttl",
                    help="Turtle for the revision example")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--sparql-endpoint", default=os.environ.get("SPARQL_ENDPOINT") or SPARQL_ENDPOINT,
                    help='URL of the live SPARQL endpoint; "" leaves the query editor out')
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
