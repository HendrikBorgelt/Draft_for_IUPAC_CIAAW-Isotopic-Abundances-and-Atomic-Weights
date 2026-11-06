"""Files that cannot import ciaaw_draft.namespaces repeat its IRIs. These tests keep them in step with it."""
import re

import pytest

from ciaaw_draft import namespaces as n
from ciaaw_draft.paths import DATA_DIR, MODEL_DIR, SPARQL_DIR

EXPECTED = {"ciaaw": n.DEFAULT_VOCAB_NS, "ciaaw_draft": n.DEFAULT_DATA_NS, "element": n.DEFAULT_ELEMENT_NS}
DECLARATIONS = [r"(?i:prefix)\s+(\w+):\s*<([^>]+)>",                # SPARQL and Turtle
                r'_:prefix_(\w+) sh:namespace "([^"]+)"',            # sparql-examples
                r'sh:prefix "(\w+)" ; sh:namespace "([^"]+)"',       # the SHACL rules
                r'"(\w+):": "([^"]+)"']                              # the DCTAP configuration
COPIES = [*sorted(DATA_DIR.glob("*.ttl")), *sorted(MODEL_DIR.glob("*.ttl")), MODEL_DIR / "ciaaw.owl",
          MODEL_DIR / "ontology.rq", MODEL_DIR / "dctap.yaml", SPARQL_DIR / "prefixes-to-add.ttl",
          *sorted((SPARQL_DIR / "queries").glob("*.rq")), *sorted((SPARQL_DIR / "examples").glob("*.ttl"))]


def declared(text):
    return [found for pattern in DECLARATIONS for found in re.findall(pattern, text)]


@pytest.mark.parametrize("path", COPIES, ids=lambda p: p.name)
def test_project_prefixes_match_the_namespaces_module(path):
    for prefix, namespace in declared(path.read_text(encoding="utf-8")):
        if prefix in EXPECTED:
            assert namespace == EXPECTED[prefix], f"{path.name}: {prefix}"


@pytest.mark.parametrize("path, form", [(MODEL_DIR / "rules.ttl", "ciaaw"), (MODEL_DIR / "dctap.yaml", "ciaaw"),
                                        (SPARQL_DIR / "prefixes-to-add.ttl", "ciaaw_draft"),
                                        (SPARQL_DIR / "queries" / "02-standard-atomic-weights.rq", "ciaaw")],
                         ids=lambda v: getattr(v, "name", v))
def test_every_form_of_declaration_is_found(path, form):
    """Otherwise the test above could pass by finding nothing."""
    assert form in dict(declared(path.read_text(encoding="utf-8")))


def test_an_old_namespace_in_a_query_is_noticed():
    assert declared("PREFIX ciaaw: <https://example.org/ciaaw-draft#>") == [("ciaaw", "https://example.org/ciaaw-draft#")]
