#!/usr/bin/env python3
"""Run a SPARQL CONSTRUCT query over Turtle files with Oxigraph and write the result as Turtle.

    construct.py QUERY.rq DATA.ttl [DATA2.ttl ...] -o OUT.ttl

Each file is loaded into a named graph (its file: IRI) and the query sees their union as the default graph.
The prefixes declared in the query are used in the output. Example:

    construct.py model/ontology.rq ciaaw.ttl -o model/ciaaw.owl
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pyoxigraph as ox
from rdflib import BNode, Graph, Literal, OWL, RDF, URIRef


def term(node):
    if isinstance(node, ox.NamedNode):
        return URIRef(node.value)
    if isinstance(node, ox.BlankNode):
        return BNode(node.value)
    if node.language:
        return Literal(node.value, lang=node.language)
    if node.datatype.value == "http://www.w3.org/2001/XMLSchema#string":
        return Literal(node.value)
    return Literal(node.value, datatype=URIRef(node.datatype.value))


def construct(query: str, data: list[Path]) -> Graph:
    store = ox.Store()
    for path in data:
        store.bulk_load(path=str(path), to_graph=ox.NamedNode(path.resolve().as_uri()))
    graph = Graph()
    for prefix, iri in re.findall(r"(?im)^\s*PREFIX\s+([\w-]*):\s*<([^>]*)>", query):
        graph.bind(prefix, iri, replace=True)
    for triple in store.query(query, use_default_graph_as_union=True):
        graph.add((term(triple.subject), term(triple.predicate), term(triple.object)))
    return graph


def turtle(graph: Graph) -> str:
    """Turtle with the owl:Ontology header, if there is one, before the other subjects."""
    ontologies = set(graph.subjects(RDF.type, OWL.Ontology))
    if not ontologies:
        return graph.serialize(format="turtle")
    head, rest = Graph(), Graph()
    for prefix, iri in graph.namespaces():
        head.bind(prefix, iri)
        rest.bind(prefix, iri)
    for s, p, o in graph:
        (head if s in ontologies else rest).add((s, p, o))
    prefixes = [line for line in graph.serialize(format="turtle").splitlines() if line.startswith("@prefix")]
    blocks = ["\n".join(line for line in part.serialize(format="turtle").splitlines() if not line.startswith("@prefix")).strip("\n")
              for part in (head, rest)]
    return "\n".join(prefixes) + "\n\n" + "\n\n".join(blocks) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("query", type=Path, help="SPARQL CONSTRUCT query")
    ap.add_argument("data", type=Path, nargs="+", help="Turtle files to query")
    ap.add_argument("-o", "--output", type=Path, help="Turtle output (default: stdout)")
    args = ap.parse_args(argv)
    graph = construct(args.query.read_text(encoding="utf-8"), args.data)
    text = turtle(graph)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(f"{len(graph)} triple{'' if len(graph) == 1 else 's'}" + (f" -> {args.output}" if args.output else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
