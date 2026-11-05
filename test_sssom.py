"""Tests for ttl2sssom.py: the output follows SSSOM/TSV (YAML header in comments, then a table)."""
import csv
import io
import re
from pathlib import Path

import pytest

import ttl2sssom as s

HERE = Path(__file__).parent
TTL = HERE / "example-output.ttl"
REQUIRED_COLUMNS = {"subject_id", "predicate_id", "object_id", "mapping_justification"}


@pytest.fixture(scope="module")
def sssom():
    out = io.StringIO()
    s.write(TTL, out)
    header = [line[2:] for line in out.getvalue().splitlines() if line.startswith("# ")]
    table = [line for line in out.getvalue().splitlines() if not line.startswith("#")]
    rows = list(csv.DictReader(table, delimiter="\t"))
    return header, rows


def header_values(header):
    return dict(re.findall(r"^(\w+): ?(\S.*)$", "\n".join(header), re.M))


def test_header_has_the_required_set_metadata(sssom):
    values = header_values(sssom[0])
    assert values["mapping_set_id"].startswith("https://") and values["license"].startswith("https://")


def test_sssom_version_is_a_string_so_yaml_does_not_read_it_as_a_number(sssom):
    """Unquoted, 1.0 is a float and the SSSOM validator rejects the file."""
    from ruamel.yaml import YAML
    parsed = YAML(typ="safe").load("\n".join(sssom[0]))
    assert parsed["sssom_version"] == "1.0" and isinstance(parsed["sssom_version"], str)


def test_curie_map_in_the_header_is_the_one_in_the_code(sssom):
    lines = sssom[0][sssom[0].index("curie_map:") + 1:]
    declared = dict(re.findall(r"^  (\w+): (\S+)$", "\n".join(lines), re.M))
    assert declared == s.CURIE_MAP


def test_the_required_columns_are_filled_in_every_row(sssom):
    rows = sssom[1]
    assert rows and REQUIRED_COLUMNS <= set(rows[0])
    assert all(r[c] for r in rows for c in REQUIRED_COLUMNS)


def test_every_curie_uses_a_prefix_of_the_curie_map(sssom):
    for r in sssom[1]:
        for column in ("subject_id", "predicate_id", "object_id", "mapping_justification", "subject_category", "object_category"):
            if r[column]:
                assert r[column].split(":")[0] in s.CURIE_MAP, (column, r[column])


def test_elements_and_isotopes_map_to_wikidata_and_chebi(sssom):
    mappings = {(r["subject_id"], r["predicate_id"]): r["object_id"] for r in sssom[1]}
    assert mappings[("element:Hydrogen", "skos:exactMatch")] == "wd:Q556"
    assert mappings[("ex:H-2", "skos:exactMatch")] == "wd:Q102296"
    assert mappings[("ex:H-2", "skos:closeMatch")] == "CHEBI:29237"


def test_the_creators_come_from_the_authors_file(sssom):
    assert "orcid:0009-0009-9304-6641" in "\n".join(sssom[0])


def test_curie_uses_the_longest_prefix():
    assert s.curie("https://example.org/ciaaw-draft#value") == "ciaaw:value"
    assert s.curie("http://purl.obolibrary.org/obo/CHEBI_49637") == "CHEBI:49637"
