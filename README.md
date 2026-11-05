# CIAAW isotopic abundances and atomic weights as RDF (draft)

> **Prototype.** This is a suggestion for discussion, not an official CIAAW/IUPAC product.
> The model and IRIs (`example.org`) are placeholders. We would like to work with the CIAAW
> on this; please open an issue with comments or questions.

The scripts convert the tables on [ciaaw.org](https://ciaaw.org) into Turtle, following the
draft declaration model from issue 20: every published value is its own declaration with a
`qudt:QuantityValue`, and elements and isotopes keep stable IRIs that point to the current
declaration and its history.

## Files

| File | Purpose |
|---|---|
| `ciaaw2ttl.py` | CIAAW HTML tables → Turtle |
| `supplement.csv` | What the tables lack: report year, DOI, ChEBI matches |
| `ttl2quickstatements.py` | Turtle → QuickStatements CSV for Wikidata (isotope abundances only) |
| `test_ciaaw2ttl.py` | Unit tests |
| `example-output*.ttl` | Example results |
| `Makefile` | Runs the steps below |

## Creating the Turtle

1. **Install the dependencies** (Python 3.9+): `make install`
2. **Download the two CIAAW pages** with `make download`, which saves them into `data/` as they
   are (or save them yourself from the browser):
   - <https://ciaaw.org/atomic-weights.htm> → `data/atomic-weights.htm`
   - <https://ciaaw.org/isotopic-abundances.htm> → `data/isotopic-abundances.htm`
3. **Check `supplement.csv`** (`subject,role,year,doi,chebi`). Roles are `standard`,
   `abundance` and `match` (a `skos:closeMatch` to ChEBI). An element symbol with role
   `abundance` applies to all its isotopes.
4. **Run `make ttl`**. The result is `build/ciaaw.ttl`. The script reports unparsable cells as
   `ERROR` lines and exits non-zero; use `--lenient` when calling it directly to ignore that.

### A new release

To keep the history of changed values, pass the Turtle of the earlier release:

```
make ttl PREVIOUS=ciaaw-2022.ttl
```

Declarations whose value changed are kept, flagged `owl:deprecated`, and linked with
`dcterms:isReplacedBy` / `dcterms:replaces`.

### Options

Other options of `ciaaw2ttl.py` (`--iri-style hash`, `--data-ns`, `--vocab-ns`, `--no-vocab`)
are described by `python3 ciaaw2ttl.py --help`. Call the script directly to use them.

## Wikidata (optional)

`make quickstatements` writes `build/quickstatements.csv`, one new item per isotope, for
import with [QuickStatements](https://quickstatements.toolforge.org/). Atomic weights are not
exported yet.

## Tests

```
make test
```

Note: the tests read HTML fixtures from `fixtures/`, which is not in this repository yet, so
tests that need them will fail until the directory is added.

## Contributing

Please open an issue for questions, corrections or ideas. See `TODO.md` for planned work
(DCTAP model, SHACL shapes, published pages).
