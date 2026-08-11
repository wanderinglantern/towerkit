"""Flexible date entry (Dateparser): files stay ISO, humans type anything."""

from datetime import date

from towerkit.dates import parse_flexible_date


class TestFlexibleDates:
    def test_iso_fast_path(self) -> None:
        assert parse_flexible_date("2026-01-15") == date(2026, 1, 15)

    def test_us_slashed(self) -> None:
        assert parse_flexible_date("1/15/2026") == date(2026, 1, 15)

    def test_words(self) -> None:
        assert parse_flexible_date("Jan 15 2026") == date(2026, 1, 15)
        assert parse_flexible_date("15 January 2026") == date(2026, 1, 15)

    def test_month_year_prefers_first(self) -> None:
        assert parse_flexible_date("April 2026") == date(2026, 4, 1)

    def test_garbage_is_none_not_a_guess(self) -> None:
        assert parse_flexible_date("not a date") is None
        assert parse_flexible_date("") is None
