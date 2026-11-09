#!/usr/bin/env python3
"""Join the data, the shared sparql-examples prefixes and every example into one Turtle file.

  python -m ciaaw_draft.website.combine_sparql_examples [--data data/ciaaw.ttl] [--examples sparql/examples] -o _site/data/combined.ttl

Each part declares its own prefixes, and sparql/examples/prefixes.ttl holds the node that the examples refer to
(sh:prefixes). The parts are therefore joined as text, the way sparql-examples-utils joins its files: a
separate parse of each part would give the shared node a different identity every time.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ciaaw_draft.paths import DATA_DIR, SPARQL_DIR

PREFIXES = "prefixes.ttl"
PREFIXES_TO_ADD = SPARQL_DIR / "prefixes-to-add.ttl"


def combined_turtle(data_path: Path, examples: Path = SPARQL_DIR / "examples") -> str:
    prefixes = examples / PREFIXES
    example_files = sorted(path for path in examples.glob("*.ttl") if path.name != PREFIXES)
    parts = [prefixes, PREFIXES_TO_ADD, data_path, *example_files]
    return "\n".join(path.read_text(encoding="utf-8") for path in parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA_DIR / "ciaaw.ttl", help="Turtle of the current data")
    ap.add_argument("--examples", type=Path, default=SPARQL_DIR / "examples",
                    help="directory with the sparql-examples Turtle and prefixes.ttl")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(combined_turtle(args.data, args.examples), encoding="utf-8", newline="\n")
    print(f"combined Turtle written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
