"""Form inputs: money shorthand, percentage shares, carrier autocomplete.

Money input accepts '2m', '250k', '1.5bn', '2,000,000', '$2M' — rejects
ambiguity rather than guessing. Displays formatted, stores integer dollars.
Share input takes percentages, stores basis points.
"""

from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz, process, utils
from textual.suggester import Suggester
from textual.validation import ValidationResult, Validator
from textual.widgets import Input

from ...money import MoneyParseError, format_money, parse_money


class MoneyValidator(Validator):
    def validate(self, value: str) -> ValidationResult:
        if not value.strip():
            return self.success()
        try:
            parse_money(value)
            return self.success()
        except MoneyParseError as exc:
            return self.failure(str(exc))


class ShareValidator(Validator):
    def validate(self, value: str) -> ValidationResult:
        if not value.strip():
            return self.success()
        if parse_share_pct(value) is None:
            return self.failure("percentage like 35 or 33.33")
        return self.success()


def parse_share_pct(text: str) -> int | None:
    """'35' / '35%' / '33.33' → basis points, or None if not a clean share."""
    cleaned = text.strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        bps = round(float(cleaned) * 100)
    except ValueError:
        return None
    if not 0 <= bps <= 10_000:
        return None
    if abs(float(cleaned) * 100 - bps) > 1e-6:
        return None  # sub-basis-point precision: reject, never round silently
    return bps


class MoneyInput(Input):
    """An Input holding integer dollars, displayed formatted."""

    def __init__(self, amount: int | None = None, **kwargs) -> None:
        value = format_money(amount) if amount is not None else ""
        super().__init__(value=value, validators=[MoneyValidator()], **kwargs)

    @property
    def amount(self) -> int | None:
        text = self.value.strip()
        if not text:
            return None
        try:
            return parse_money(text)
        except MoneyParseError:
            return None

    def set_amount(self, amount: int | None) -> None:
        self.value = format_money(amount) if amount is not None else ""


class CarrierSuggester(Suggester):
    """Completes carrier names from theme pins plus every carrier used across
    programs/*.json — so 'Swiss Re' never fragments into three spellings."""

    def __init__(self, known: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self.known = known

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        prefix = [c for c in self.known if c.lower().startswith(value.lower())]
        if prefix:
            return prefix[0]
        # typo tolerance ("Swis Re") …
        match = process.extractOne(
            value, self.known, scorer=fuzz.WRatio,
            score_cutoff=85, processor=utils.default_process,
        )
        if match:
            return match[0]
        # … and the fragmentation case: a long variant of a known name
        # ("swiss re corporate solutions") should surface the canonical one
        match = process.extractOne(
            value, self.known, scorer=fuzz.partial_ratio,
            score_cutoff=90, processor=utils.default_process,
        )
        return match[0] if match else None


def known_carriers(programs_dir: Path, themes_dir: Path, extra: list[str]) -> list[str]:
    """Theme pins first (they carry the market's canonical spellings), then
    carriers from every program on disk, then the current program's own."""
    seen: dict[str, None] = {}
    for theme_path in sorted(themes_dir.glob("*.json")) if themes_dir.is_dir() else []:
        try:
            data = json.loads(theme_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for carrier in data.get("carriers", {}):
            seen.setdefault(carrier, None)
    for program_path in sorted(programs_dir.glob("*.json")) if programs_dir.is_dir() else []:
        try:
            data = json.loads(program_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for layer in data.get("layers", []) if isinstance(data, dict) else []:
            for participant in layer.get("participants", []):
                if isinstance(participant, dict) and "carrier" in participant:
                    seen.setdefault(str(participant["carrier"]), None)
    for carrier in extra:
        seen.setdefault(carrier, None)
    return list(seen)


def known_insureds(programs_dir: Path, extra: list[str]) -> list[str]:
    """Insured names across every program on disk — one client, one spelling."""
    seen: dict[str, None] = {}
    for program_path in sorted(programs_dir.glob("*.json")) if programs_dir.is_dir() else []:
        try:
            data = json.loads(program_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("insured"):
            seen.setdefault(str(data["insured"]), None)
    for name in extra:
        seen.setdefault(name, None)
    return list(seen)


def known_line_names(programs_dir: Path, extra: list[str]) -> list[str]:
    """Coverage line names across every program on disk plus the current
    program's own — 'General Liability' never fragments into 'Gen Liab'."""
    seen: dict[str, None] = {}
    for program_path in sorted(programs_dir.glob("*.json")) if programs_dir.is_dir() else []:
        try:
            data = json.loads(program_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for line in data.get("lines", []) if isinstance(data, dict) else []:
            if isinstance(line, dict) and line.get("name"):
                seen.setdefault(str(line["name"]), None)
    for name in extra:
        seen.setdefault(name, None)
    return list(seen)
