from backend.replay import (
    Outcome,
    Verdict,
    _summary,
    compare,
    main,
    parse_expected,
    render,
    safe_update,
)

NAME = "line @(10,10 200x40 conf=99.0): 'Andrea Muster'"
DATE = "line @(10,60 200x40 conf=99.0): '01.01.2000'"


def _outcome(*verdicts: Verdict) -> list[Outcome]:
    return [Outcome(index=0, verdicts=verdicts)]


def test_safe_update_rewrites_reasons_and_holds_moved_verdicts():
    expected = _outcome(Verdict(NAME, "REDACT", "presidio", 1.0), Verdict(DATE, "keep", "DATE", 0))
    replayed = _outcome(
        Verdict(NAME, "REDACT", "name-memory", 1.0), Verdict(DATE, "REDACT", "overlap", 1.0)
    )

    result = safe_update(replayed, expected)

    assert result is not None
    assert result.reasons == 1
    assert len(result.held) == 1 and "expected keep (DATE), got REDACT (overlap)" in result.held[0]
    assert [(v.kind, v.reason) for v in result.outcomes[0].verdicts] == [
        ("REDACT", "name-memory"),
        ("keep", "DATE"),
    ]
    # The held line still fails against the rewritten snapshot; the reason no longer does.
    snapshot = parse_expected(render(result.outcomes, "presidio"))
    assert len(compare(replayed, snapshot)) == 1


def test_safe_update_keeps_a_held_partial_percentage():
    snapshot = parse_expected(render(_outcome(Verdict(NAME, "PARTIAL", "overlap", 0.37)), "x"))
    result = safe_update(_outcome(Verdict(NAME, "keep", None, 0.0)), snapshot)

    assert result is not None
    assert "PARTIAL 37% (overlap)" in render(result.outcomes, "x")


def test_safe_update_refuses_a_stale_snapshot():
    expected = _outcome(Verdict(NAME, "REDACT", "presidio", 1.0))
    replayed = _outcome(Verdict(DATE, "REDACT", "presidio", 1.0))

    assert safe_update(replayed, expected) is None
    assert safe_update(replayed, expected + expected) is None


def test_summary_reports_ok():
    assert _summary(3, {}) == "OK: 3 document(s)"


def test_summary_names_each_failed_document():
    summary = _summary(3, {"a_ocr.txt": 2, "b_ocr.txt": 1})

    assert summary.splitlines() == [
        "FAILED: 2 of 3 document(s), 3 difference(s)",
        "  a_ocr.txt: 2",
        "  b_ocr.txt: 1",
    ]


def test_check_fails_when_there_is_nothing_to_replay(tmp_path, capsys):
    assert main(["check", str(tmp_path)]) == 1
    assert capsys.readouterr().out.startswith("FAILED: no _ocr.txt dumps found")
