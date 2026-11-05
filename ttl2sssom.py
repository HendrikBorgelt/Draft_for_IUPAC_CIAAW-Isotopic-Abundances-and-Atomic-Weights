#!/usr/bin/env python3
"""Write the mappings of the CIAAW Turtle as an SSSOM/TSV mapping set (https://mapping-commons.github.io/sssom/).

SSSOM (Simple Standard for Sharing Ontological Mappings) describes mappings between entities as a table, with a
YAML header for the mapping set. Two kinds of mappings are written:

  * every element and isotope of the Turtle  ->  its Wikidata item  (skos:exactMatch), from site/wikidata.csv,
    which wikidata_links.py found by atomic number (elements) and atomic number plus number of neutrons
    (isotopes). The justification is semapv:UnspecifiedMatching, and curation_rule_text says what was matched.
  * the skos:closeMatch to ChEBI in the Turtle (from supplement.csv), justification semapv:ManualMappingCuration.

Header: mapping_set_id and license are required by SSSOM. The license is CC0 unless --license says otherwise,
as a choice for the mappings only (not for the CIAAW values). The creators are read from AUTHORS.

Usage:
    ttl2sssom.py build/ciaaw.ttl -o build/ciaaw.sssom.tsv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS
from rdflib.namespace import SKOS

CIAAW = Namespace("https://example.org/ciaaw-draft#")
CHEMROF = Namespace("https://w3id.org/chemrof/")

HERE = Path(__file__).parent
WIKIDATA_CSV = HERE / "site" / "wikidata.csv"
AUTHORS = HERE / "AUTHORS"
SET_ID = "https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/data/ciaaw.sssom.tsv"
REPO = "https://github.com/HendrikBorgelt/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights"
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"

CURIE_MAP = {
    "ciaaw_draft": "https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/id/",
    "element": "https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/elements/",
    "ciaaw": str(CIAAW),
    "chemrof": str(CHEMROF),
    "wd": "http://www.wikidata.org/entity/",
    "CHEBI": "http://purl.obolibrary.org/obo/CHEBI_",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "semapv": "https://w3id.org/semapv/vocab/",
    "orcid": "https://orcid.org/",
}
CHEBI_IRI = CURIE_MAP["CHEBI"]
WIKIDATA_CATEGORY = {"element": "wd:Q11344", "isotope": "wd:Q25276"}   # chemical element, isotope

COLUMNS = ["subject_id", "subject_label", "subject_category", "predicate_id", "object_id", "object_label",
           "object_category", "mapping_justification", "curation_rule_text"]

RULES = {
    "element": "Same atomic number (Wikidata P1086)",
    "isotope": "Same atomic number (Wikidata P1086) and number of neutrons (Wikidata P1148)",
}


def curie(iri) -> str:
    """The CURIE of an IRI, with the longest matching prefix of CURIE_MAP."""
    iri = str(iri)
    prefix = max((p for p, ns in CURIE_MAP.items() if iri.startswith(ns)), key=lambda p: len(CURIE_MAP[p]), default=None)
    if prefix is None:
        raise ValueError(f"no prefix for {iri}")
    return f"{prefix}:{iri[len(CURIE_MAP[prefix]):]}"


def read_wikidata(path: Path) -> dict[tuple[int, int | None], tuple[str, str]]:
    """(Z, None) for an element or (Z, A) for an isotope -> (Q-number, label)."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(int(r["z"]), int(r["a"]) if r["a"] else None): (r["qid"], r["label"]) for r in csv.DictReader(f)}


def read_authors(path: Path) -> list[str]:
    """ORCIDs from the lines "Name <https://orcid.org/0000-...>" of AUTHORS."""
    if not path.exists():
        return []
    return [f"orcid:{m.group(1)}" for m in re.finditer(r"<https://orcid\.org/([0-9X-]+)>", path.read_text(encoding="utf-8"))]


def rows(g: Graph, wikidata: dict):
    for element in sorted(g.subjects(RDF.type, CHEMROF.ChemicalElement), key=lambda e: int(g.value(e, CHEMROF.atomic_number))):
        z = int(g.value(element, CHEMROF.atomic_number))
        label = str(g.value(element, RDFS.label))
        entities = [("element", element, label, (z, None), "chemrof:ChemicalElement")]
        entities += [("isotope", i, f"{label}-{g.value(i, CIAAW.massNumber)}", (z, int(g.value(i, CIAAW.massNumber))),
                      "chemrof:Isotope") for i in sorted(g.objects(element, CIAAW.hasIsotope), key=lambda i: int(g.value(i, CIAAW.massNumber)))]
        for kind, node, name, key, category in entities:
            if key in wikidata:
                qid, wd_label = wikidata[key]
                yield {"subject_id": curie(node), "subject_label": name, "subject_category": category,
                       "predicate_id": "skos:exactMatch", "object_id": f"wd:{qid}", "object_label": wd_label,
                       "object_category": WIKIDATA_CATEGORY[kind], "mapping_justification": "semapv:UnspecifiedMatching",
                       "curation_rule_text": RULES[kind]}
            for match in sorted(g.objects(node, SKOS.closeMatch), key=str):
                if str(match).startswith(CHEBI_IRI):
                    yield {"subject_id": curie(node), "subject_label": name, "subject_category": category,
                           "predicate_id": "skos:closeMatch", "object_id": curie(match), "object_label": "",
                           "object_category": "", "mapping_justification": "semapv:ManualMappingCuration",
                           "curation_rule_text": "Given in supplement.csv"}


def header(set_id: str, license_: str, authors: list[str], version: str) -> list[str]:
    lines = ['sssom_version: "1.0"', "curie_map:", *(f"  {p}: {ns}" for p, ns in CURIE_MAP.items()),
             f"mapping_set_id: {set_id}", "mapping_set_title: CIAAW draft - elements and isotopes",
             "mapping_set_description: >",
             "  Mappings of the elements and isotopes in the unofficial CIAAW linked-data prototype to Wikidata",
             "  and ChEBI. The prototype is a suggestion, not an official CIAAW or IUPAC product.",
             f"license: {license_}", f"mapping_provider: {REPO}", "mapping_tool: ttl2sssom.py",
             f"mapping_date: {date.today().isoformat()}"]
    if version:
        lines.append(f"mapping_set_version: {version}")
    if authors:
        lines += ["creator_id:", *(f"  - {a}" for a in authors)]
    return ["# " + line for line in lines]


def write(ttl: Path, out, wikidata: Path = WIKIDATA_CSV, authors: Path = AUTHORS, set_id: str = SET_ID,
          license_: str = CC0, version: str = "") -> int:
    g = Graph()
    g.parse(ttl, format="turtle")
    out.write("\n".join(header(set_id, license_, read_authors(authors), version)) + "\n")
    writer = csv.DictWriter(out, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    n = 0
    for row in rows(g, read_wikidata(wikidata)):
        writer.writerow(row)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ttl", type=Path, help="Turtle file written by ciaaw2ttl.py")
    ap.add_argument("-o", "--output", type=Path, help="SSSOM/TSV output (default: stdout)")
    ap.add_argument("--wikidata", type=Path, default=WIKIDATA_CSV, help="CSV from wikidata_links.py")
    ap.add_argument("--authors", type=Path, default=AUTHORS, help="AUTHORS file with ORCIDs for creator_id")
    ap.add_argument("--mapping-set-id", default=SET_ID)
    ap.add_argument("--mapping-set-version", default="")
    ap.add_argument("--license", default=CC0, help="URL of the license of the mappings (default CC0 1.0)")
    args = ap.parse_args(argv)
    options = dict(wikidata=args.wikidata, authors=args.authors, set_id=args.mapping_set_id, license_=args.license,
                   version=args.mapping_set_version)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as f:
            n = write(args.ttl, f, **options)
    else:
        n = write(args.ttl, sys.stdout, **options)
    print(f"{n} mappings written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
