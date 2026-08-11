"""Flexible date entry for the TUI, backed by Dateparser.

Files on disk stay strictly ISO — this is an input convenience only:
"1/15/2026", "Jan 15 2026", "15 Jan 26" and plain ISO all land on the same
date. US date order (MDY) is assumed for slashed forms.
"""

from __future__ import annotations

from datetime import date


def parse_flexible_date(text: str) -> date | None:
    """Parse a human-entered date; None when it cannot be understood."""
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)  # exact fast path
    except ValueError:
        pass
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
