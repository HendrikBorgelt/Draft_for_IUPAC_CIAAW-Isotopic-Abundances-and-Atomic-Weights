#!/usr/bin/env python3
"""Turn the DCTAP model into SHACL shapes and UML-style diagrams.

  model/model_dctap.csv + model/dctap.yaml + model/rules.ttl
      -> model/model_shacl.ttl   SHACL shapes (rules.ttl is appended unchanged)
      -> model/diagrams.md       one Mermaid class diagram per aspect

Usage:
  python -m ciaaw_draft.schema.tap2model                    regenerate both files
  python -m ciaaw_draft.schema.tap2model --check            exit 1 if they are out of date
  python -m ciaaw_draft.schema.tap2model --validate a.ttl   validate Turtle files against the shapes

How a row becomes SHACL:
  mandatory=true -> sh:minCount 1, repeatable=false -> sh:maxCount 1.
  A valueShape that has a targetClass (or subclasses with one) becomes sh:class. That shape is checked
  through its own target, so references between elements and declarations cannot recurse.
  Any other valueShape becomes sh:node. Several valueShapes become sh:xone.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dctap.config import get_config
from dctap.csvreader import csvreader
from pyshacl import validate as shacl_validate
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection

from ciaaw_draft.paths import MODEL_DIR as MODEL, ROOT

TAP_CSV = MODEL / "model_dctap.csv"
TAP_CONFIG = MODEL / "dctap.yaml"
RULES = MODEL / "rules.ttl"
ONTOLOGY = MODEL / "ontology.ttl"
SHACL_OUT = MODEL / "model_shacl.ttl"
DIAGRAMS_OUT = MODEL / "diagrams.md"

SH = Namespace("http://www.w3.org/ns/shacl#")
NODE_KIND = {"iri": SH.IRI, "literal": SH.Literal, "bnode": SH.BlankNode}
# Back-references and history links double the arrows. The aspect diagrams show them instead.
OVERVIEW_SKIPS = {"ciaaw:hasDeclaration", "ciaaw:isDeclarationOf", "ciaaw:hasPreviousDeclaration",
                  "ciaaw:hasNextDeclaration", "prov:wasDerivedFrom"}


@dataclass
class Statement:
    property: str
    label: str
    mandatory: bool
    repeatable: bool
    node_type: str
    datatype: str
    constraint: object
    constraint_type: str
    value_shapes: list[str]
    aspects: list[str]
    note: str

    @property
    def cardinality(self) -> str:
        return {(True, False): "1", (False, False): "0..1",
                (True, True): "1..*", (False, True): "0..*"}[(self.mandatory, self.repeatable)]


@dataclass
class Shape:
    id: str
    label: str
    target_class: str
    extends: str
    closed: bool
    statements: list[Statement] = field(default_factory=list)

    @property
    def local(self) -> str:
        return self.id.split(":", 1)[1]


@dataclass
class Tap:
    shapes: dict[str, Shape]
    prefixes: dict[str, str]
    aspects: dict[str, dict]
    warnings: list[str]

    def iri(self, curie: str) -> URIRef:
        prefix, _, local = curie.partition(":")
        if local.startswith("//"):
            return URIRef(curie)
        return URIRef(self.prefixes[prefix + ":"] + local)

    def concrete_classes(self, shape_id: str) -> list[str]:
        """Classes that identify a shape: its own target, else the targets of the shapes extending it."""
        shape = self.shapes[shape_id]
        if shape.target_class:
            return [shape.target_class]
        return [s.target_class for s in self.shapes.values() if s.extends == shape_id and s.target_class]


def _flatten_warnings(warnings: dict) -> list[str]:
    found = []
    for scope, elements in warnings.items():
        for element, messages in elements.items():
            found += [f"{scope}/{element}: {m}" for m in (messages if isinstance(messages, list) else [messages])]
    return found


def load_tap(csv_path: Path = TAP_CSV, config_path: Path = TAP_CONFIG) -> Tap:
    config = get_config(nondefault_configfile_name=str(config_path))
    parsed = csvreader(csvfile_str=csv_path.read_text(encoding="utf-8"), config_dict=config)
    shapes = {}
    for raw in parsed["shapes"]:
        shape = Shape(raw["shapeID"], raw.get("shapeLabel", ""), raw.get("targetClass", ""),
                      raw.get("extends", ""), raw.get("closed", "") == "true")
        for row in raw["statement_templates"]:
            value_shapes = row.get("valueShape", [])
            shape.statements.append(Statement(
                row["propertyID"], row.get("propertyLabel", ""), row.get("mandatory") == "true",
                row.get("repeatable") == "true", row.get("valueNodeType", ""), row.get("valueDataType", ""),
                row.get("valueConstraint", ""), row.get("valueConstraintType", ""),
                [value_shapes] if isinstance(value_shapes, str) else value_shapes,
                row.get("aspect", "").split(), row.get("note", "")))
        shapes[shape.id] = shape
    tap = Tap(shapes, config["prefixes"], config.get("aspects", {}), _flatten_warnings(parsed["warnings"]))
    tap.warnings += _consistency_problems(tap)
    return tap


def _consistency_problems(tap: Tap) -> list[str]:
    problems = []
    for shape in tap.shapes.values():
        if shape.extends and shape.extends not in tap.shapes:
            problems.append(f"{shape.id}: extends unknown shape {shape.extends}")
        for st in shape.statements:
            problems += [f"{shape.id} {st.property}: unknown shape {s}" for s in st.value_shapes if s not in tap.shapes]
            problems += [f"{shape.id} {st.property}: unknown aspect {a}" for a in st.aspects if a not in tap.aspects]
            if not st.aspects:
                problems.append(f"{shape.id} {st.property}: no aspect")
    return problems


# ---------------------------------------------------------------------------
# SHACL
# ---------------------------------------------------------------------------
def _rdf_list(g: Graph, items: list) -> BNode:
    head = BNode()
    Collection(g, head, items)
    return head


def _add_constraint(g: Graph, tap: Tap, node, st: Statement) -> None:
    kind = st.constraint_type
    if kind == "picklist":
        if st.node_type == "iri":
            items = [tap.iri(v) for v in st.constraint]
        elif st.datatype == "xsd:string":
            items = [Literal(v) for v in st.constraint]     # identical in RDF 1.1, but not to rdflib
        else:
            items = [Literal(v, datatype=tap.iri(st.datatype)) for v in st.constraint]
        if len(items) == 1 and st.mandatory:
            g.add((node, SH.hasValue, items[0]))
        else:
            g.add((node, SH["in"], _rdf_list(g, items)))
    elif kind == "iristem":
        g.add((node, SH.pattern, Literal("^(" + "|".join(re.escape(v) for v in st.constraint) + ")")))
    elif kind == "pattern":
        g.add((node, SH.pattern, Literal(st.constraint)))
    elif kind == "languagetag":
        g.add((node, SH.languageIn, _rdf_list(g, [Literal(v) for v in st.constraint])))
    elif kind in ("mininclusive", "maxinclusive"):
        prop = SH.minInclusive if kind == "mininclusive" else SH.maxInclusive
        g.add((node, prop, Literal(str(st.constraint), datatype=tap.iri(st.datatype))))
    elif kind in ("minlength", "maxlength"):
        g.add((node, SH.minLength if kind == "minlength" else SH.maxLength, Literal(int(st.constraint))))
    elif kind:
        raise ValueError(f"{st.property}: unsupported valueConstraintType {kind!r}")


def _add_value_shapes(g: Graph, tap: Tap, node, st: Statement) -> None:
    alternatives = []
    for shape_id in st.value_shapes:
        classes = tap.concrete_classes(shape_id)
        if classes:
            alternatives += [("class", tap.iri(c)) for c in classes]
        else:
            alternatives.append(("node", tap.iri(shape_id)))
    if len(alternatives) == 1:
        kind, value = alternatives[0]
        g.add((node, SH[kind], value))
    elif alternatives:
        members = []
        for kind, value in alternatives:
            if kind == "node":
                members.append(value)
            else:
                member = BNode()
                g.add((member, SH["class"], value))
                members.append(member)
        g.add((node, SH.xone, _rdf_list(g, members)))


def to_shacl(tap: Tap) -> Graph:
    g = Graph()
    g.bind("sh", SH)
    for prefix, namespace in tap.prefixes.items():
        g.bind(prefix.rstrip(":"), Namespace(namespace))

    for shape in tap.shapes.values():
        node = tap.iri(shape.id)
        g.add((node, RDF.type, SH.NodeShape))
        if shape.label:
            g.add((node, RDFS.label, Literal(shape.label, lang="en")))
        if shape.target_class:
            g.add((node, SH.targetClass, tap.iri(shape.target_class)))
        if shape.extends:
            g.add((node, SH.node, tap.iri(shape.extends)))
        if shape.closed:
            g.add((node, SH.closed, Literal(True)))
        seen: dict[str, int] = {}
        for st in shape.statements:
            # Named property shapes keep the output stable and make validation reports readable.
            local = f"{shape.local}-{st.property.replace(':', '-')}"
            seen[local] = seen.get(local, 0) + 1
            prop = tap.iri(f"shape:{local}" + (f"-{seen[local]}" if seen[local] > 1 else ""))
            g.add((node, SH.property, prop))
            g.add((prop, SH.path, tap.iri(st.property)))
            if st.label:
                g.add((prop, SH.name, Literal(st.label, lang="en")))
            if st.note:
                g.add((prop, SH.description, Literal(st.note, lang="en")))
            if st.mandatory:
                g.add((prop, SH.minCount, Literal(1)))
            if not st.repeatable:
                g.add((prop, SH.maxCount, Literal(1)))
            if st.node_type:
                g.add((prop, SH.nodeKind, NODE_KIND[st.node_type]))
            if st.datatype:
                g.add((prop, SH.datatype, tap.iri(st.datatype)))
            _add_constraint(g, tap, prop, st)
            _add_value_shapes(g, tap, prop, st)
    return g


def shacl_text(tap: Tap) -> str:
    return ("# GENERATED by tap2model.py. Do not edit: change model/model_dctap.csv or model/rules.ttl.\n\n"
            + to_shacl(tap).serialize(format="turtle")
            + "\n# ---------------------------------------------------------------------------\n"
            + "# Copied unchanged from model/rules.ttl\n"
            + "# ---------------------------------------------------------------------------\n\n"
            + RULES.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# UML-style diagrams
# ---------------------------------------------------------------------------
def _class_title(shape: Shape) -> str:
    return shape.target_class or shape.label or shape.local


def _node_type(st: Statement) -> str:
    return "IRI" if st.node_type == "iri" else st.node_type


def _value_type(st: Statement) -> str:
    return (st.datatype or _node_type(st) or "any").replace("xsd:", "").replace("rdf:", "")


def _class_block(shape: Shape, attributes: list[str], abstract: bool) -> list[str]:
    title = f'    class {shape.local}["{_class_title(shape)}"]'
    if not attributes and not abstract:
        return [title]
    members = (["<<abstract>>"] if abstract else []) + attributes
    return [title + " {"] + [f"        {m}" for m in members] + ["    }"]


def mermaid(tap: Tap, aspect: str | None = None) -> str:
    """Class diagram of one aspect. Without an aspect: overview of the classes without attributes."""
    attributes: dict[str, list[str]] = {}
    associations: list[str] = []
    used: list[str] = []

    def use(shape_id: str) -> None:
        if shape_id not in used:
            used.append(shape_id)
            attributes.setdefault(shape_id, [])

    for shape in tap.shapes.values():
        for st in shape.statements:
            if st.property == "rdf:type" and aspect is None:
                use(shape.id)
            if (aspect is not None and aspect not in st.aspects) or st.property == "rdf:type":
                if aspect in st.aspects:
                    use(shape.id)
                continue
            if aspect is None and (shape.id == "shape:Current" or st.property in OVERVIEW_SKIPS):
                continue
            use(shape.id)
            if st.value_shapes:
                for target in st.value_shapes:
                    if aspect is None and target == "shape:Current":
                        continue
                    use(target)
                    label = f"{st.property} (one of)" if len(st.value_shapes) > 1 else st.property
                    # Mermaid ends a relation label at the next colon, so it has to be escaped.
                    label = label.replace(":", "#58;")
                    line = f'    {shape.local} --> "{st.cardinality}" {tap.shapes[target].local} : {label}'
                    if line not in associations:
                        associations.append(line)
            elif aspect is not None:
                attribute = f"{st.property} : {_value_type(st)} [{st.cardinality}]"
                if attribute not in attributes[shape.id]:
                    attributes[shape.id].append(attribute)

    direction = tap.aspects[aspect].get("direction", "LR") if aspect else "TB"
    lines = ["classDiagram", f"    direction {direction}"]
    parents = {s.extends for s in tap.shapes.values() if s.extends}
    for shape_id in used:
        lines += _class_block(tap.shapes[shape_id], attributes[shape_id], shape_id in parents)
    for shape_id in used:
        parent = tap.shapes[shape_id].extends
        if parent and parent in used:
            lines.append(f"    {tap.shapes[parent].local} <|-- {tap.shapes[shape_id].local}")
    return "\n".join(lines + associations) + "\n"


def statement_table(tap: Tap, aspect: str) -> str:
    """Markdown table of the DCTAP rows that belong to an aspect."""
    lines = ["| Shape | Property | Count | Value | Note |", "| --- | --- | --- | --- | --- |"]
    for shape in tap.shapes.values():
        for st in shape.statements:
            if aspect in st.aspects and st.property != "rdf:type":
                value = " or ".join(f"`{s}`" for s in st.value_shapes) or f"`{st.datatype or _node_type(st)}`"
                lines.append(f"| {shape.label} | `{st.property}` | {st.cardinality} | {value} | {st.note} |")
    return "\n".join(lines) + "\n"


OVERVIEW_NOTE = ("The main classes and how they connect. Attributes, back-references and history links "
                 "are in the diagrams for each aspect.")


def diagrams_text(tap: Tap) -> str:
    parts = ["<!-- GENERATED by tap2model.py. Do not edit: change model/model_dctap.csv. -->\n",
             "# Data model diagrams\n",
             "Prototype, not an official CIAAW model. "
             "Generated from [model_dctap.csv](model_dctap.csv), one diagram per aspect.\n",
             "## Overview\n", f"{OVERVIEW_NOTE}\n", f"```mermaid\n{mermaid(tap)}```\n"]
    for key, aspect in tap.aspects.items():
        parts += [f"## {aspect['title']}\n", f"{aspect['description']}\n", f"```mermaid\n{mermaid(tap, key)}```\n"]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def load_shapes() -> Graph:
    return Graph().parse(SHACL_OUT, format="turtle")


def load_ontology() -> Graph:
    return Graph().parse(ONTOLOGY, format="turtle")


def validate_graph(data: Graph, shapes: Graph | None = None) -> tuple[bool, Graph, str]:
    """Warnings do not make a dataset invalid, but they stay visible in the report. The ontology is mixed into a
    copy of the data, so that a shape for ciaaw:Declaration also applies to the declarations of each subclass."""
    return shacl_validate(data, shacl_graph=shapes if shapes is not None else load_shapes(), ont_graph=load_ontology(),
                          advanced=True, allow_warnings=True, inference="none")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the generated files are out of date")
    ap.add_argument("--validate", nargs="+", type=Path, metavar="TTL", help="validate Turtle files against the shapes")
    args = ap.parse_args(argv)

    if args.validate:
        status = 0
        for path in args.validate:
            conforms, _, text = validate_graph(Graph().parse(path, format="turtle"))
            print(f"{path}: {'conforms' if conforms else 'DOES NOT CONFORM'}")
            if not conforms or "Severity: sh:Warning" in text:
                print(text)
            status = status or (0 if conforms else 1)
        return status

    tap = load_tap()
    for message in tap.warnings:
        print(f"WARNING: {message}", file=sys.stderr)
    outputs = {SHACL_OUT: shacl_text(tap), DIAGRAMS_OUT: diagrams_text(tap)}
    if args.check:
        stale = [p.name for p, text in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print(f"Out of date: {', '.join(stale)}. Run: make model", file=sys.stderr)
        return 1 if stale or tap.warnings else 0
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}", file=sys.stderr)
    return 1 if tap.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
