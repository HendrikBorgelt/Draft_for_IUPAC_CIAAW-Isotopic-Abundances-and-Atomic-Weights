"""Tests for check_wikidata_weights.py: the standard atomic weights against the mass of the elements in Wikidata."""
import json
from decimal import Decimal

from ciaaw_draft.compare import check_wikidata_weights as cw

WD = "http://www.wikidata.org/entity/"


def binding(z="1", item="Q556", amount="1.008", statement="s1", **more):
    b = {"item": WD + item, "label": "hydrogen", "z": z, "statement": WD + "statement/" + statement,
         "amount": amount, "unit": cw.Q_DALTON}
    return {**b, **more}


def local(z=1, **values):
    return {"name": "hydrogen", "z": z, **values}


def kinds(entries, bindings):
    return [(d["kind"], d["name"]) for d in cw.compare(entries, cw.group_wikidata(bindings))]


def test_query_asks_for_the_mass_of_elements_without_deprecated_statements_and_with_the_stated_in_and_doi():
    assert "wdt:P31 wd:Q11344" in cw.QUERY and "p:P2067" in cw.QUERY
    assert "FILTER(?rank != wikibase:DeprecatedRank)" in cw.QUERY
    assert "pr:P248 ?source" in cw.QUERY and "wdt:P356 ?doi" in cw.QUERY


def test_a_value_inside_the_interval_or_the_uncertainty_agrees():
    interval = local(lower=Decimal("1.00784"), upper=Decimal("1.00811"), amount=Decimal("1.009"))
    assert kinds([interval], [binding(amount="1.008")]) == []
    value = local(amount=Decimal("4.002602"), uncertainty=Decimal("0.000002"))
    assert kinds([value], [binding(amount="4.002601")]) == []
    assert kinds([value], [binding(amount="4.0025")]) == [("DIFFERENT", "hydrogen")]


def test_a_rounded_wikidata_value_agrees_if_its_bounds_contain_the_local_weight():
    interval = local(lower=Decimal("15.99903"), upper=Decimal("15.99977"), amount=Decimal("15.9994"))
    assert kinds([interval], [binding(amount="15.999", lower="15.998", upper="16")]) == []
    assert kinds([interval], [binding(amount="15.999")]) == [("DIFFERENT", "hydrogen")]


def test_a_difference_has_the_sources_with_doi_and_the_other_kinds_are_reported():
    entries = [local(amount=Decimal("1.5"), uncertainty=None), local(z=2, amount=Decimal("4"), uncertainty=None),
               local(z=3, amount=Decimal("6.9"), uncertainty=None)]
    bindings = [binding(source=WD + "Q11", sourceLabel="Article", doi="10.1/abc"),
                {k: v for k, v in binding(z="2", item="Q560") .items() if k not in ("amount", "unit", "statement")},
                binding(z="9", item="Q9", statement="s9")]
    differences = list(cw.compare(entries, cw.group_wikidata(bindings)))
    assert [(d["kind"], d["name"]) for d in differences] == [("DIFFERENT", "hydrogen"), ("NO-VALUE", "hydrogen"),
                                                            ("NOT-FOUND", "hydrogen"), ("ONLY-RESOURCE", "hydrogen")]
    assert differences[0]["sources"] == [{"qid": "Q11", "label": "Article", "doi": "10.1/abc"}]
    json.dumps(differences)


def test_a_mass_in_another_unit_is_reported():
    assert ("UNIT", "hydrogen") in kinds([local(amount=Decimal("1.008"), uncertainty=None)],
                                         [binding(unit=WD + "Q11570")])
