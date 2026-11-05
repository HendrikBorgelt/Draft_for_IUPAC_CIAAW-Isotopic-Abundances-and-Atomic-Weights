"""Tests for ttl2quickstatements.py, on the committed example Turtle."""
import csv
import io
import json
from pathlib import Path

import ttl2quickstatements as q

HERE = Path(__file__).parent
TTL = HERE / "example-output.ttl"


def convert(known=frozenset(), no_value=None):
    out = io.StringIO()
    q.convert(TTL, out, known, no_value)
    return list(csv.DictReader(io.StringIO(out.getvalue())))


def test_new_isotopes_get_atomic_number_and_neutrons_instead_of_the_mass_number():
    rows = {r["Len"]: r for r in convert()}
    assert len(rows) == 24 and rows["calcium-40"]["qid"] == ""
    assert (rows["calcium-40"]["P1086"], rows["calcium-40"]["P1148"]) == ("20", "20")
    assert "Paaa" not in q.COLUMNS


def test_abundance_is_a_fraction_like_the_existing_wikidata_statements():
    rows = {r["Len"]: r for r in convert()}
    assert rows["calcium-40"]["P2374"] == "0.96941~0.00156"
    assert rows["hydrogen-1"]["P2374"] == "0.999855[0.99972,0.99999]"


def test_reference_and_year_are_in_the_qualifier_and_source_columns():
    rows = {r["Len"]: r for r in convert()}
    assert rows["hydrogen-2"]["qal585"] == "+2016-00-00T00:00:00Z/9"
    assert rows["hydrogen-2"]["S356"] == '"10.1515/PAC-2015-0503"'


def test_isotopes_that_wikidata_has_are_not_created_again():
    rows = convert(known={(1, 1), (1, 2)})
    assert "hydrogen-1" not in {r["Len"] for r in rows} and len(rows) == 22


def test_a_wikidata_item_without_abundance_gets_only_the_statement():
    rows = convert(known={(2, 3)}, no_value={"helium-3": "Q533498"})
    row = next(r for r in rows if r["qid"] == "Q533498")
    assert row["P2374"] and not row["Len"] and not row["P31"] and not row["P1086"]


def test_read_comparison_takes_the_items_without_a_value(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"differences": [
        {"kind": "NO-VALUE", "name": "helium-3", "qid": "Q1"}, {"kind": "NO-VALUE", "name": "x-1", "qid": "Q2/Q3"},
        {"kind": "DIFFERENT", "name": "argon-36", "qid": "Q4"}]}), encoding="utf-8")
    assert q.read_comparison(path) == {"helium-3": "Q1"}
