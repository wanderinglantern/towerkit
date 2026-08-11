"""Money and share parsing/formatting.

Money is integer whole dollars everywhere in code. Shares are integer basis
points (0-10000). The on-disk JSON keeps shares as decimal fractions; the
conversion lives here so it is exact in both directions.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from babel.numbers import format_compact_currency, format_currency

BPS_SCALE = 10_000

_LOCALE = "en_US"

# Accepted magnitude suffixes. Deliberately narrow: "mm", "bil", etc. are
# rejected rather than guessed at.
_SUFFIXES = {
    "": 1,
    "k": 1_000,
    "m": 1_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}

_MONEY_RE = re.compile(
    r"""^\s*\$?\s*
        (?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)
        \s*(?P<suffix>[a-zA-Z]*)\s*$""",
    re.VERBOSE,
)


class MoneyParseError(ValueError):
    """Raised when a money string is ambiguous or malformed."""


def parse_money(text: str) -> int:
    """Parse '2m', '250k', '1.5bn', '2,000,000', '$2M' into integer dollars.

    Rejects ambiguity (unknown suffixes, fractional dollars, mixed grouping)
    rather than guessing.
    """
    match = _MONEY_RE.match(text)
    if match is None:
        raise MoneyParseError(f"cannot parse money value: {text!r}")
    num, suffix = match.group("num"), match.group("suffix").lower()
    if suffix not in _SUFFIXES:
        raise MoneyParseError(f"unknown magnitude suffix {suffix!r} in {text!r}")
    if "," in num:
        if suffix:
            raise MoneyParseError(f"mixed digit grouping and suffix in {text!r}")
        num = num.replace(",", "")
    if "." in num and not suffix:
        raise MoneyParseError(f"fractional dollars are not allowed: {text!r}")
    try:
        value = Decimal(num) * _SUFFIXES[suffix]
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise MoneyParseError(f"cannot parse money value: {text!r}") from exc
    if value != value.to_integral_value():
        raise MoneyParseError(f"{text!r} is not a whole number of dollars")
    if value < 0:
        raise MoneyParseError(f"negative amounts are not allowed: {text!r}")
    return int(value)


def format_money(amount: int) -> str:
    """Full form with grouping: 2000000 -> '$2,000,000'."""
    return format_currency(amount, "USD", format="¤#,##0", locale=_LOCALE, currency_digits=False)


def format_money_compact(amount: int) -> str:
    """Compact form for labels: 25000000 -> '$25M', 2340000 -> '$2.34M'."""
    if amount == 0:
        return "$0"
    digits = 0
    for candidate in (0, 1, 2):
        digits = candidate
        text = format_compact_currency(amount, "USD", locale=_LOCALE, fraction_digits=candidate)
        if _compact_round_trips(text, amount):
            return text
    return format_compact_currency(amount, "USD", locale=_LOCALE, fraction_digits=digits)


def _compact_round_trips(text: str, amount: int) -> bool:
    try:
        return parse_money(text) == amount
    except MoneyParseError:
        return False


def share_to_bps(share: Decimal | int | float) -> int:
    """Convert an on-disk decimal fraction (0.35) to basis points (3500).

    Values with sub-basis-point precision are rejected: the schema's unit of
    account is the basis point, and 0.33333 silently rounding to 3333 would be
    a lossy read.
    """
    if isinstance(share, float):
        share = Decimal(str(share))
    scaled = Decimal(share) * BPS_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError(f"share {share} has sub-basis-point precision")
    bps = int(scaled)
    if not 0 <= bps <= BPS_SCALE:
        raise ValueError(f"share {share} is outside [0, 1]")
    return bps


def bps_to_share(bps: int) -> Decimal:
    """Convert basis points back to the on-disk decimal fraction, exactly."""
    return (Decimal(bps) / BPS_SCALE).normalize()


def bps_to_json_number(bps: int) -> int | float:
    """The JSON literal for a share: 10000 -> 1, 3500 -> 0.35, 3333 -> 0.3333.

    Every multiple of 1/10000 has a shortest float repr equal to its 4-decimal
    form, so emitting the float is lossless (tested exhaustively).
    """
    if bps % BPS_SCALE == 0:
        return bps // BPS_SCALE
    return float(bps_to_share(bps))


def format_share(bps: int) -> str:
    """Display form: 3500 -> '35%', 3333 -> '33.33%'."""
    pct = Decimal(bps) / 100
    if pct == pct.to_integral_value():
        return f"{int(pct)}%"
    return f"{pct.normalize()}%"


def parse_share(text: str) -> int:
    """'60%', '60', '12.5' → basis points. Everything is read as a PERCENT
    (the % sign optional) — brokers speak percent, and one consistent rule
    beats guessing whether 0.25 meant a quarter share."""
    cleaned = text.strip().rstrip("%").strip()
    try:
        pct = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MoneyParseError(f"cannot read a share from {text!r}") from exc
    scaled = pct * 100  # percent → bps
    if scaled != scaled.to_integral_value():
        raise MoneyParseError(f"{text!r} has sub-basis-point precision")
    bps = int(scaled)
    if not 0 < bps <= BPS_SCALE:
        raise MoneyParseError(f"{text!r} is not a share between 0% and 100%")
    return bps


def premium_share(premium: int, share_bps: int) -> int:
    """A participant's premium, floor-divided so money stays integer."""
    return premium * share_bps // BPS_SCALE
