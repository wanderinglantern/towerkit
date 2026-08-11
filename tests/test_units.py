"""Money and share units: integer everywhere, lossless at the JSON boundary."""

import json
from decimal import Decimal

import pytest

from towerkit.money import (
    MoneyParseError,
    bps_to_json_number,
    bps_to_share,
    format_money,
    format_money_compact,
    format_share,
    parse_money,
    parse_share,
    premium_share,
    share_to_bps,
)


class TestParseMoney:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2m", 2_000_000),
            ("250k", 250_000),
            ("1.5bn", 1_500_000_000),
            ("2,000,000", 2_000_000),
            ("$2M", 2_000_000),
            ("$ 2M", 2_000_000),
            ("0", 0),
            ("102m", 102_000_000),
            ("2.5m", 2_500_000),
            ("0.5k", 500),
            ("1b", 1_000_000_000),
            ("750000", 750_000),
        ],
    )
    def test_accepts(self, text: str, expected: int) -> None:
        assert parse_money(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",           # empty
            "2mm",        # unknown suffix — never guess
            "2 million",  # words are ambiguous shorthand
            "1.5",        # fractional dollars
            "2,000k",     # mixed grouping and suffix
            "1,00,000",   # broken grouping
            "-5m",        # negative
            "1.0005k",    # fractional dollars after scaling
            "abc",
        ],
    )
    def test_rejects(self, text: str) -> None:
        with pytest.raises(MoneyParseError):
            parse_money(text)


class TestShares:
    def test_disk_fraction_to_bps(self) -> None:
        assert share_to_bps(Decimal("0.35")) == 3500
        assert share_to_bps(Decimal("0.3333")) == 3333
        assert share_to_bps(1) == 10_000
        assert share_to_bps(0) == 0

    def test_sub_bps_precision_rejected(self) -> None:
        with pytest.raises(ValueError, match="sub-basis-point"):
            share_to_bps(Decimal("0.33333"))

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            share_to_bps(Decimal("1.2"))

    def test_every_bps_value_round_trips_through_json(self) -> None:
        # The whole point of bps: shares survive serialise → parse exactly.
        for bps in range(10_001):
            literal = json.dumps(bps_to_json_number(bps))
            parsed = json.loads(literal, parse_float=Decimal)
            assert share_to_bps(parsed) == bps, f"bps {bps} corrupted via {literal}"

    def test_third_split_round_trips(self) -> None:
        thirds = [3334, 3333, 3333]
        assert sum(thirds) == 10_000
        for bps in thirds:
            assert share_to_bps(bps_to_share(bps)) == bps

    def test_share_sum_is_exact(self) -> None:
        # The reason floats were banned: this comparison needs no tolerance.
        assert sum([3334, 3333, 3333]) == 10_000
        assert sum([3333, 3333, 3333]) < 10_000


class TestPremiumShare:
    def test_stays_integer(self) -> None:
        assert premium_share(3_900_000, 6_000) == 2_340_000
        assert isinstance(premium_share(1_000_001, 3_333), int)

    def test_floor_division(self) -> None:
        assert premium_share(100, 3_333) == 33


class TestFormatting:
    def test_full(self) -> None:
        assert format_money(2_000_000) == "$2,000,000"
        assert format_money(0) == "$0"

    def test_compact(self) -> None:
        assert format_money_compact(25_000_000) == "$25M"
        assert format_money_compact(2_500_000) == "$2.5M"
        assert format_money_compact(250_000) == "$250K"
        assert format_money_compact(1_500_000_000) == "$1.5B"
        assert format_money_compact(0) == "$0"

    def test_share_display(self) -> None:
        assert format_share(3500) == "35%"
        assert format_share(3333) == "33.33%"
        assert format_share(10_000) == "100%"


class TestParseShare:
    @pytest.mark.parametrize(
        ("text", "bps"),
        [("60%", 6000), ("60", 6000), ("12.5%", 1250), ("100", 10_000), ("0.5", 50)],
    )
    def test_accepts(self, text: str, bps: int) -> None:
        assert parse_share(text) == bps

    @pytest.mark.parametrize("text", ["", "sixty", "0", "101", "12.345", "-5"])
    def test_rejects(self, text: str) -> None:
        with pytest.raises(MoneyParseError):
            parse_share(text)
