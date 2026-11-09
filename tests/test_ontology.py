"""Tests for the vocabulary (model/ontology.ttl) and its alignment with other ontologies (model/alignment.sssom.tsv).

Run with:  python -m pytest tests/test_ontology.py
"""
import csv
import io
import re

import pytest
from pyshacl import validate as shacl_validate
from rdflib import Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.namespace import DCTERMS, RDFS, SKOS

from ciaaw_draft.namespaces import CIAAW, NS, ONTOLOGY
from ciaaw_draft.paths import DATA_DIR, MODEL_DIR, SPARQL_DIR
from ciaaw_draft.schema import tap2model as t

KINDS = (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty)


@pytest.fixture(scope="module")
def onto():
    return Graph().parse(MODEL_DIR / "ontology.ttl", format="turtle")


def defined(onto) -> set:
    return {s for kind in KINDS for s in onto.subjects(RDF.type, kind) if str(s).startswith(str(CIAAW))}


def deprecated(onto) -> set:
    return set(onto.subjects(OWL.deprecated, Literal(True)))


def used_terms() -> dict[str, set]:
    """The ciaaw: terms that the data, the DCTAP, the rules and the queries use, by where they are used."""
    found = {}
    for path in [DATA_DIR / "ciaaw.ttl", DATA_DIR / "example-output-next.ttl"]:
        g = Graph().parse(path, format="turtle")
        found[path.name] = {term for triple in g for term in (triple[1], triple[2] if triple[1] == RDF.type else None)
                            if term is not None and str(term).startswith(str(CIAAW))}
    for path in [MODEL_DIR / "model_dctap.csv", MODEL_DIR / "rules.ttl", *sorted((SPARQL_DIR / "queries").glob("*.rq"))]:
        terms = {CIAAW[local] for local in re.findall(r"\bciaaw:(\w+)", path.read_text(encoding="utf-8"))}
        if terms or path.suffix != ".rq":          # a query may use no term of the vocabulary, the model always does
            found[path.name] = terms
    return found


def test_the_ontology_has_a_header(onto):
    assert (ONTOLOGY, RDF.type, OWL.Ontology) in onto
    assert onto.value(ONTOLOGY, OWL.versionInfo) is not None and onto.value(ONTOLOGY, OWL.versionIRI) is not None
    assert onto.value(ONTOLOGY, DCTERMS.rights) is not None and onto.value(ONTOLOGY, DCTERMS.license) is None
    assert not list(onto.objects(ONTOLOGY, OWL.imports))           # nothing is imported, see alignment.sssom.tsv


@pytest.mark.parametrize("where", sorted(used_terms()))
def test_every_term_in_use_is_defined_and_current(where, onto):
    terms = used_terms()[where]
    assert terms, where
    assert terms - defined(onto) == set(), where
    assert terms & deprecated(onto) == set(), where


def test_every_current_term_has_a_label_and_a_definition(onto):
    for term in defined(onto) - deprecated(onto):
        assert onto.value(term, RDFS.label) is not None, term
        definition = onto.value(term, SKOS.definition)
        assert definition is not None and definition.language == "en", term


def test_every_earlier_term_says_what_replaces_it(onto):
    old = deprecated(onto)
    assert CIAAW.ofElement in old and CIAAW.value in old
    for term in old:
        replacements = set(onto.objects(term, DCTERMS.isReplacedBy))
        assert replacements or onto.value(term, SKOS.editorialNote) is not None, term
        assert replacements <= defined(onto) - old, term


def test_domains_and_ranges_name_defined_classes(onto):
    classes = set(onto.subjects(RDF.type, OWL.Class))
    for p in (RDFS.domain, RDFS.range):
        for term, target in onto.subject_objects(p):
            members = list(onto.items(onto.value(target, OWL.unionOf))) if onto.value(target, OWL.unionOf) else [target]
            for member in members:
                assert member in classes or str(member).startswith(str(URIRef("http://www.w3.org/2001/XMLSchema#"))), (term, member)


def test_rules_for_every_declaration_need_the_ontology_mixed_in():
    """shape:OneValueForm targets ciaaw:Declaration, which the data never states: the subclasses come from the
    ontology, which validate_graph mixes in. Without it the rule would never fire."""
    data = Graph().parse(DATA_DIR / "example-output.ttl", format="turtle")
    data.add((NS.weights["H-2009"], CIAAW.hasValue, Literal("1.008", datatype=XSD.decimal)))   # an interval, and a value
    conforms, _, text = t.validate_graph(data, t.load_shapes())
    assert not conforms and "OneValueForm" in text
    _, _, without = shacl_validate(data, shacl_graph=t.load_shapes(), advanced=True, allow_warnings=True, inference="none")
    assert "OneValueForm" not in without


# --- the alignment ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def alignment():
    text = (MODEL_DIR / "alignment.sssom.tsv").read_text(encoding="utf-8")
    header = [line[2:] for line in text.splitlines() if line.startswith("# ")]
    curie_map = dict(re.findall(r"^  (\w+): (\S+)$", "\n".join(header), flags=re.M))
    rows = list(csv.DictReader(io.StringIO("\n".join(l for l in text.splitlines() if not l.startswith("#"))), delimiter="\t"))
    return header, curie_map, rows


def test_the_alignment_has_the_required_sssom_metadata(alignment):
    header, curie_map, rows = alignment
    for key in ("mapping_set_id:", "license:", "curie_map:"):
        assert any(line.startswith(key) for line in header), key
    assert rows and set(rows[0]) >= {"subject_id", "predicate_id", "object_id", "mapping_justification"}


def test_every_aligned_term_is_a_current_term_of_the_ontology(alignment, onto):
    _, curie_map, rows = alignment
    for row in rows:
        prefix, local = row["subject_id"].split(":", 1)
        term = URIRef(curie_map[prefix] + local)
        assert term in defined(onto) - deprecated(onto), row["subject_id"]
        assert onto.value(term, RDFS.label) == Literal(row["subject_label"], lang="en"), row["subject_id"]


def test_every_curie_of_the_alignment_has_a_prefix(alignment):
    _, curie_map, rows = alignment
    for row in rows:
        for column in ("subject_id", "predicate_id", "object_id", "mapping_justification"):
            assert row[column].split(":", 1)[0] in curie_map, (column, row[column])
        assert row["predicate_id"] in {"skos:exactMatch", "skos:closeMatch", "skos:broadMatch", "skos:narrowMatch",
                                       "skos:relatedMatch"}, row


def test_the_alignment_covers_the_main_classes(alignment):
    _, _, rows = alignment
    subjects = {row["subject_id"] for row in rows}
    assert {"ciaaw:ChemicalElement", "ciaaw:Isotope", "ciaaw:Declaration", "ciaaw:Report", "ciaaw:Body"} <= subjects
