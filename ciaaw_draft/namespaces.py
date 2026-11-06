"""The IRIs of this project and of the vocabularies it reuses, defined once.

The DCTAP configuration, the SHACL rules and the SPARQL queries cannot import this module, so they repeat these
IRIs. tests/test_namespaces.py checks that they agree.
"""
from rdflib import Namespace, URIRef

SITE_URL = "https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/"
# Every resource has a page on the website, so the IRIs are the addresses of those pages. GitHub Pages serves
# id/H-1.html at id/H-1. Isotopes and declarations share one namespace, elements have their own.
DEFAULT_DATA_NS = f"{SITE_URL}id/"
DEFAULT_ELEMENT_NS = f"{SITE_URL}elements/"
DEFAULT_VOCAB_NS = f"{SITE_URL}ciaaw-draft#"
CIAAW = Namespace(DEFAULT_VOCAB_NS)
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")
CHEMROF = Namespace("https://w3id.org/chemrof/")
CHEBI = Namespace("http://purl.obolibrary.org/obo/CHEBI_")
ADMS = Namespace("http://www.w3.org/ns/adms#")
ADMS_DEPRECATED = URIRef("http://purl.org/adms/status/Deprecated")   # TODO verify this IRI
