"""The detection-commentary collector (:mod:`backend.trace`)."""

from __future__ import annotations

import logging

import pytest

from backend.trace import Trace


class Boom:
    """Formatting this raises — the probe for "did we interpolate?"."""

    def __str__(self) -> str:
        raise AssertionError("formatted a line nobody was going to read")


def test_collects_in_order_with_arguments_applied():
    trace = Trace(collect=True)
    trace.add("line @(%d,%d): %r", 10, 20, "Muster")
    trace.add("    -> REDACT (%s)", "static-rule")
    assert trace.collected == "line @(10,20): 'Muster'\n    -> REDACT (static-rule)"


def test_not_collecting_reports_none():
    # None, not "": the caller hands `collected` straight to the report, and the
    # key must be absent rather than present-and-empty.
    assert Trace().collected is None
    assert Trace(collect=True).collected == ""


def test_wanted_follows_the_log_level_when_not_collecting(caplog):
    trace = Trace()
    with caplog.at_level(logging.INFO, logger="backend.trace"):
        assert not trace.wanted
    with caplog.at_level(logging.DEBUG, logger="backend.trace"):
        assert trace.wanted


def test_wanted_is_true_while_collecting_whatever_the_log_level(caplog):
    # The gate presidio's `return_decision_process` hangs on: without this a
    # ?debug=true report would carry lines with no `match ...` under them.
    with caplog.at_level(logging.WARNING, logger="backend.trace"):
        assert Trace(collect=True).wanted


def test_nothing_is_formatted_when_nobody_is_reading(caplog):
    # add() runs several times per OCR line; the interpolation must be as lazy as
    # logging's own.
    with caplog.at_level(logging.INFO, logger="backend.trace"):
        Trace().add("expensive %s", Boom())


def test_formatting_still_happens_for_a_collector():
    trace = Trace(collect=True)
    with pytest.raises(AssertionError):
        trace.add("expensive %s", Boom())


def test_a_message_with_no_arguments_is_taken_literally():
    # No args means no `%` pass, so a literal percent survives — which "100%
    # covered"-shaped lines depend on.
    trace = Trace(collect=True)
    trace.add("100% covered")
    assert trace.collected == "100% covered"


def test_collecting_also_logs(caplog):
    # The trace joins the log, it does not replace it: PII_LOG_LEVEL=DEBUG must
    # behave exactly as before whether or not someone asked for a copy.
    with caplog.at_level(logging.DEBUG, logger="backend.trace"):
        Trace(collect=True).add("line @(%d,%d)", 1, 2)
    assert "line @(1,2)" in caplog.text
