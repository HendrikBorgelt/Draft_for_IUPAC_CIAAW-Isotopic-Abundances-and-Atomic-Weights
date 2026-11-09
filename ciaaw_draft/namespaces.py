"""The IRIs of this project and of the vocabularies it reuses, defined once.

The layout follows the SI Digital Framework of the BIPM (https://si-digital-framework.org/SI): one namespace for the
vocabulary, and one namespace per kind of resource, each of them the address of a folder of the site.

The DCTAP configuration, the SHACL rules, the ontology and the SPARQL queries cannot import this module, so they
repeat these IRIs. tests/test_namespaces.py checks that they agree.
"""
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS, XSD

SITE_URL = "https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/"
REPO_URL = "https://github.com/HendrikBorgelt/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights"
VERSION = "0.1.0"

# prefix -> folder of the site. Every resource has a page, so its IRI is the address of that page: GitHub Pages
# serves isotopes/H-1.html at isotopes/H-1.
FOLDERS = {
    "elements": "elements/",
    "isotopes": "isotopes/",
    "weights": "standard-atomic-weights/",
    "abundances": "isotopic-abundances/",
    "reports": "reports/",
    "footnotes": "footnotes/",
    "bodies": "bodies/",
}
# What each namespace holds, for the header of its part of the data.
TITLES = {
    "elements": "Chemical elements",
    "isotopes": "Isotopes",
    "weights": "Standard atomic weights",
    "abundances": "Isotopic abundances",
    "reports": "Reports",
    "footnotes": "Footnotes of the tables",
    "bodies": "Bodies",
}

PROV = Namespace("http://www.w3.org/ns/prov#")
CHEBI = Namespace("http://purl.obolibrary.org/obo/CHEBI_")
SI_UNITS = Namespace("https://si-digital-framework.org/SI/units/")
UNIT_ONE = SI_UNITS.one              # standard atomic weights and abundances are numbers of unit one
UNIT_DALTON = SI_UNITS.dalton        # atomic masses (planned)

# Where the data comes from. prov:wasDerivedFrom points to these documents.
WEIGHTS_PAGE = URIRef("https://ciaaw.org/atomic-weights.htm")
ABUNDANCES_PAGE = URIRef("https://ciaaw.org/isotopic-abundances.htm")
HISTORY_TABLE = URIRef("https://www.ciaaw.org/data/IUPAC-TSAW.csv")
SUPPLEMENT_FILE = URIRef(f"{REPO_URL}/blob/main/data/supplement.csv")
SOURCE_LABELS = {
    WEIGHTS_PAGE: ("Standard atomic weights, ciaaw.org", "CIAAW"),
    ABUNDANCES_PAGE: ("Isotopic compositions of the elements, ciaaw.org", "CIAAW"),
    HISTORY_TABLE: ("Historical changes of standard atomic weights, ciaaw.org", "CIAAW"),
    SUPPLEMENT_FILE: ("Supplement of this prototype: report years, DOIs and ChEBI matches", ""),
}

STATUS_CURRENT, STATUS_SUPERSEDED = "current", "superseded"

# No licence is declared: the values belong to CIAAW and IUPAC, and the draft vocabulary is meant to be handed over.
# A licence would be stated with dcterms:license next to these statements.
DATA_RIGHTS = ("The values are © CIAAW and IUPAC (ciaaw.org, 2016–2024) and are reproduced with the acknowledgement "
               "that the terms of use of ciaaw.org ask for (https://ciaaw.org/impressum.htm). This prototype declares "
               "no licence of its own. A licence would be stated here with dcterms:license.")
VOCABULARY_RIGHTS = ("No licence has been chosen for this draft vocabulary, which is a proposal to CIAAW. "
                     "A licence would be stated here with dcterms:license.")


class Namespaces:
    """The namespaces of the project under one base address: the site, or another one for tests."""

    def __init__(self, base: str = SITE_URL):
        self.base = base
        self.ontology = URIRef(f"{base}ciaaw-draft")
        self.ciaaw = Namespace(f"{base}ciaaw-draft#")
        for prefix, folder in FOLDERS.items():
            setattr(self, prefix, Namespace(f"{base}{folder}"))

    def prefixes(self) -> dict[str, str]:
        """The project prefixes, as written in Turtle and SPARQL."""
        return {"ciaaw": str(self.ciaaw), **{prefix: str(getattr(self, prefix)) for prefix in FOLDERS}}

    def folder_of(self, iri) -> tuple[str, str] | None:
        """(folder of the site, local name) of a resource IRI, or None for an IRI outside these namespaces."""
        iri = str(iri)
        for prefix, folder in FOLDERS.items():
            namespace = str(getattr(self, prefix))
            if iri.startswith(namespace):
                return folder, iri[len(namespace):]
        return None

    def bind(self, g: Graph) -> None:
        for prefix, namespace in [*self.prefixes().items(), ("units", SI_UNITS), ("prov", PROV), ("chebi", CHEBI),
                                  ("dcterms", DCTERMS), ("skos", SKOS), ("owl", OWL), ("rdfs", RDFS), ("rdf", RDF),
                                  ("xsd", XSD)]:
            g.bind(prefix, namespace, override=True)


NS = Namespaces()
CIAAW = NS.ciaaw
ELEMENTS, ISOTOPES, WEIGHTS, ABUNDANCES = NS.elements, NS.isotopes, NS.weights, NS.abundances
REPORTS, FOOTNOTES, BODIES = NS.reports, NS.footnotes, NS.bodies
ONTOLOGY = NS.ontology
DEFAULT_VOCAB_NS = str(CIAAW)
