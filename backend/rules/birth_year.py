"""The birth year printed over a redacted birthdate: day and month are the
personal part, the year stays readable for the reimbursement."""

from __future__ import annotations

import re
from datetime import date

from backend.models import Box, Line
from backend.pii import PiiLabel, Span
from backend.rules.patterns import BIRTH_MARK, DATE_RE, NAME_DATE


def birth_year_note(line: Line, spans: list[Span]) -> Box | None:
    """A note with the birth year where the birthdate stood on ``line``, its
    position estimated from the date's share of the text. ``None`` without a
    birthdate, or with several dates and nothing saying which one it is."""
    if not any(s.label is PiiLabel.DATE_OF_BIRTH or s.source == "rule NAME_DATE" for s in spans):
        return None
    found = birth_date_in(line.text)
    if found is None:
        return None
    year = _full_year(re.split(r"\.\s?", found.group())[-1])
    x0 = line.left + line.width * found.start() // len(line.text)
    x1 = line.left + line.width * found.end() // len(line.text)
    return Box(x0, line.top, max(x1, x0 + 1), line.bottom, text=year)


def birth_date_in(text: str) -> re.Match[str] | None:
    """The birthdate on a line known to carry one: the date a mark or a name
    row points at, else the line's only date."""
    marked = BIRTH_MARK.search(text) or NAME_DATE.search(text)
    if marked is not None:
        return DATE_RE.search(text, marked.start())
    dates = list(DATE_RE.finditer(text))
    return dates[0] if len(dates) == 1 else None


def date_key(text: str) -> tuple[int, int, int]:
    """A date as (day, month, full year): "01.02.80" equals "1.2.1980"."""
    day, month, year = re.split(r"\.\s?", text)
    return int(day), int(month), int(_full_year(year))


def _full_year(digits: str) -> str:
    """A two-digit year in this century unless that lies in the future."""
    if len(digits) != 2:
        return digits
    return f"{19 if int(digits) > date.today().year % 100 else 20}{digits}"
