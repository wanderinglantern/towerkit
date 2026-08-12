"""Flexible date entry for the TUI, backed by Dateparser.

Files on disk stay strictly ISO — this is an input convenience only:
"1/15/2026", "Jan 15 2026", "15 Jan 26" and plain ISO all land on the same
date. US date order (MDY) is assumed for slashed forms.
"""

from __future__ import annotations

import re
from datetime import date

_NUMERIC_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})$")


def _parse_numeric(text: str) -> date | None:
    """M/D/YY(YY) fast path: MDY by convention, 2-digit years are 20xx.
    Without this, dateparser's future-preference resolves a past 2-digit
    year by adding a CENTURY (6/24/26 → 2126-06-24)."""
    match = _NUMERIC_RE.match(text)
    if match is None:
        return None
    first, second, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    month, day = first, second
    if month > 12 and day <= 12:  # 24/6 can only be day-first
        month, day = day, month
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_flexible_date(text: str) -> date | None:
    """Parse a human-entered date; None when it cannot be understood."""
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)  # exact fast path
    except ValueError:
        pass
    numeric = _parse_numeric(text)
    if numeric is not None:
        return numeric
    import dateparser  # deferred: importing language data is slow

    parsed = dateparser.parse(
        text,
        languages=["en"],
        settings={
            "DATE_ORDER": "MDY",
            "PREFER_DAY_OF_MONTH": "first",
            "PREFER_DATES_FROM": "future",
        },
    )
    return parsed.date() if parsed else None
