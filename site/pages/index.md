# Isotopic abundances and atomic weights as linked data

This site is a working suggestion. It shows how the tables of the Commission on Isotopic Abundances
and Atomic Weights (CIAAW) could also be published as linked data, so that software can cite an
exact value, see which value is current, and follow how a value changed over time.

It is **not** an official CIAAW or IUPAC site. For values you intend to use, go to
[ciaaw.org](https://ciaaw.org).

{{coverage}}

## What is here

- [Atomic weights](atomic-weights.md) and [isotopic abundances](isotopic-abundances.md), generated from the RDF and laid out like the CIAAW tables
- A [data model](model.md) with diagrams for each aspect, written as a DCTAP profile and enforced with SHACL shapes
- A [revision example](revision-example.md) that shows what happens to the data when a value changes
- [Downloads](downloads.md) in Turtle, RDF/XML, JSON-LD, N-Triples and N3
- A live [SPARQL endpoint](sparql.md) with example queries, for asking the data questions directly

## Why publish it this way

- **Every value can be cited.** Each published value is a separate declaration with its own IRI.
- **History is kept.** A changed value creates a new declaration. The old one stays available and links to its replacement.
- **Provenance is recorded.** Every element, isotope and value names the documents it was read from, and every value the report in which CIAAW published it.
- **Precision is preserved.** Numbers are stored as exact decimals, next to the value as CIAAW prints it, which keeps every digit.
- **Data can be checked.** The SHACL shapes test the published data automatically.

## Tell us what you think

We would like to develop this together with CIAAW. If something here is wrong, missing or could be
done better, please [open an issue]({{issues}}). See [About](about.md) for more.
