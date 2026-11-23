"""Diacritic folding after OCR and OCR-variant matching."""

from __future__ import annotations

import pytest

from backend.document import Document
from backend.harvest import Memory
from backend.ocr.paddle import _word_boxes
from backend.ocr_variants import VariantIndex, close
from backend.pii import PiiLabel, Span
from backend.textnorm import fold
from backend.trace import Trace
from backend.wordlists import WordLists
from tests.conftest import cell


@pytest.mark.parametrize(
    "raw,folded",
    [
        ("Símone", "Simone"),
        ("ÉRIKA Amèlie", "ERIKA Amelie"),
        ("René Zoë Øster Łukasz", "Rene Zoe Oster Lukasz"),
        ("Müller Straße ÄÖÜ", "Müller Straße ÄÖÜ"),  # German stays
    ],
)
def test_fold_removes_foreign_diacritics_one_character_for_one(raw, folded):
    assert fold(raw) == folded
    assert len(fold(raw)) == len(raw)


def test_folded_word_pieces_still_line_up_with_the_folded_line():
    pieces = [fold(p) for p in ["Símone", " ", "Muster"]]
    assert _word_boxes(fold("Símone Muster"), pieces, [[0, 0, 60, 10], [60, 0, 70, 10], [70, 0, 130, 10]])


@pytest.mark.parametrize(
    "a,b,want",
    [
        ("Ilja", "llja", True),  # I read as l
        ("Simone", "Símone", True),  # an invented accent
        ("Amelie", "Amèlie", True),
        ("Müller", "Muller", True),  # lost dots
        ("Lüneburg", "Loneburg", True),  # ü read as o, one edit on a long word
        ("Kern", "Kem", True),  # rn read as m
        ("Muster", "Master", False),  # a↔u is no glyph confusion
        ("Meyer", "Mayer", False),
        ("Rose", "Rase", False),  # short words: skeleton only
        ("Andrea", "Andreas", False),
    ],
)
def test_close(a, b, want):
    assert close(a, b) is want


def test_variant_index_skips_exact_matches_and_finds_phrases():
    index = VariantIndex(["Erika Muster"])
    text = "Erika Muster und Erlka Muster"
    assert [(text[s:e], t) for s, e, t in index.find(text)] == [("Erlka Muster", "Erika Muster")]


def _doc(*texts):
    return Document([cell(t, 10, 10 + 50 * i, width=400) for i, t in enumerate(texts)])


def test_a_remembered_name_is_recalled_in_its_ocr_variant_and_traced():
    doc = _doc("Betrifft: llja Probe", "Termin mit Ilja")
    memory = Memory()
    memory.names = {"Ilja"}
    trace = Trace(collect=True)
    spans = doc.per_line(memory.recall(doc, trace))
    assert [s.text for s in spans[doc.lines[0]]] == ["llja"]
    assert "name variant 'llja' ~ 'Ilja'" in trace.collected


def test_a_variant_must_look_like_a_name():
    doc = _doc("die ilja-Methode", "Kollegen")  # small first letter; a stopword
    memory = Memory()
    memory.names = {"Ilja", "Kollegin"}
    assert not any(doc.per_line(memory.recall(doc)).values())


def test_the_positive_list_blackens_an_ocr_variant():
    doc = _doc("Termin mit Amèlie Probe")
    [span] = WordLists(redact=("Amelie",)).redact_spans(doc)
    assert (span.label, span.text) == (PiiLabel.LISTED, "Amèlie")


def test_the_negative_list_carves_out_an_ocr_variant():
    lists = WordLists(keep=("Simone",))
    carved = lists.carve(Span(PiiLabel.PERSON, 0, 12, "Símone Probe", "presidio"))
    assert [s.text for s in carved] == ["Probe"]
    whole = Span(PiiLabel.PERSON, 0, 12, "Sabine Probe", "presidio")
    assert lists.carve(whole) == [whole]


def test_a_list_entry_with_an_accent_meets_the_folded_page():
    doc = _doc("Termin mit Rene Probe")
    assert [s.text for s in WordLists(redact=("René",)).redact_spans(doc)] == ["Rene"]
