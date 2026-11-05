"""Tests for check_bodr.py, with a small isotopes.xml in the format of the BODR package."""
from decimal import Decimal

import check_bodr as cb

ISOTOPES = """<?xml version="1.0" encoding="utf-8"?>
<cml xmlns="http://www.xml-cml.org/schema"><isotopeList id="H">
<isotope id="H1" number="1" elementType="H"><scalar dictRef="bo:relativeAbundance">99.9885</scalar><scalar dictRef="bo:atomicNumber">1</scalar></isotope>
<isotope id="H2" number="2" elementType="H"><scalar dictRef="bo:relativeAbundance">0.0115</scalar><scalar dictRef="bo:atomicNumber">1</scalar></isotope>
<isotope id="H3" number="3" elementType="H"><scalar dictRef="bo:atomicNumber">1</scalar></isotope>
</isotopeList></cml>
"""


def local(name, z, a, **values):
    return {"name": name, "zn": (z, a - z), **values}


def kinds(entries, tmp_path):
    xml = tmp_path / "isotopes.xml"
    xml.write_text(ISOTOPES, encoding="utf-8")
    return [(d["kind"], d["name"]) for d in cb.compare(entries, cb.read_bodr(xml))]


def test_read_bodr_keeps_isotopes_without_abundance(tmp_path):
    xml = tmp_path / "isotopes.xml"
    xml.write_text(ISOTOPES, encoding="utf-8")
    isotopes = cb.read_bodr(xml)
    assert isotopes[(1, 2)] == {"id": "H2", "amount": Decimal("0.0115")}
    assert isotopes[(1, 3)]["amount"] is None


def test_equal_value_and_value_inside_interval_agree(tmp_path):
    entries = [local("hydrogen-2", 1, 2, amount=Decimal("0.0115"), uncertainty=Decimal("0.007")),
               local("hydrogen-1", 1, 1, amount=Decimal("99.9856"), lower=Decimal("99.972"), upper=Decimal("99.999"))]
    assert kinds(entries, tmp_path) == []


def test_differences_are_reported_by_kind(tmp_path):
    entries = [local("hydrogen-1", 1, 1, amount=Decimal("99.0"), uncertainty=None),          # other value
               local("hydrogen-3", 1, 3, amount=Decimal("1"), uncertainty=None),            # BODR has no value
               local("helium-4", 2, 4, amount=Decimal("100"), uncertainty=None)]            # not in BODR
    assert kinds(entries, tmp_path) == [("DIFFERENT", "hydrogen-1"), ("NO-VALUE", "hydrogen-3"),
                                        ("NOT-FOUND", "helium-4"), ("ONLY-RESOURCE", "H2")]
