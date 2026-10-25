"""A street without suffix above its ZIP+city line (``backend.rules.postal``)."""

from __future__ import annotations

import pytest

from backend.document import Document
from backend.pii import PiiLabel
from backend.rules import rule_spans
from backend.rules.postal import streets_above_city
from tests.conftest import cell


def _block(street, city="12345 Musterstadt", left=10, gap=4):
    return [
        cell("Frau", 10, 100),
        cell("Andrea Muster", 10, 124),
        cell(street, left, 148),
        cell(city, 10, 168 + gap),
    ]


@pytest.mark.parametrize(
    "street", ["Musterau 12", "Am Musteranger 3a", "Unter den Mustern 4", "Musterhof 8 - 10"]
)
def test_a_street_without_suffix_above_its_city_is_an_address(street):
    lines = _block(street)
    assert streets_above_city(lines) == {lines[2]}

def test_a_street_without_suffix_below_its_city_is_an_address():
    assert streets_above_city(_block("12345 Musterstadt", "Am Musteranger 3a")) == set()


def test_the_street_above_its_city_is_a_named_rule_span():
    lines = _block("Musterau 12")
    spans = [s for s in rule_spans(Document(lines)) if s.source == "rule STREET_ABOVE_CITY"]
    assert [(s.label, s.text) for s in spans] == [(PiiLabel.ADDRESS, "Musterau 12")]


def test_rule_spans_take_the_item_table_from_the_document():
    lines = _block("Musterau 12")
    doc = Document(lines, item_table={lines[2]})
    assert not [s for s in rule_spans(doc) if s.source == "rule STREET_ABOVE_CITY"]


@pytest.mark.parametrize(
    "lines",
    [
        # in another column
        _block("Musterau 12", left=300),
        # too far above
        _block("Musterau 12", gap=40),
        # no house number
        _block("Musterau"),
        # the city is only part of its line
        _block("Musterau 12", city="Praxis Muster, 12345 Musterstadt"),
        # headings and running text above a city line
        _block("Rechnung"),
        _block("Liquidation vom 12.03.2026 für"),
        _block("Gesamtbetrag 23,40"),
    ],
)
def test_no_street_above_a_city_without_the_shape(lines):
    assert streets_above_city(lines) == set()


def test_no_street_above_a_city_inside_the_item_table():
    lines = _block("Musterau 12")
    assert streets_above_city(lines, table={lines[2]}) == set()
    assert streets_above_city(lines, table={lines[3]}) == set()
