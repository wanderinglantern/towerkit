"""Pydantic models — the ONLY definition of a program.

On disk, money is integer whole dollars and shares are decimal fractions
(0.35). In memory, money stays integer dollars and shares become integer basis
points (3500). The conversion happens here, at the model boundary, and is
lossless in both directions.

`load_program` / `dump_program` implement the canonical file format: stable
key order matching the schema, 2-space indent, integers never in float form.
Round-tripping an untouched file must produce a zero diff.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from .money import bps_to_json_number, share_to_bps

SCHEMA_ID = "https://towerkit.dev/schema/program.schema.json"

Money = Annotated[int, Field(ge=0)]


class Placement(StrEnum):
    BOUND = "bound"
    PROPOSED = "proposed"


class RetentionType(StrEnum):
    DEDUCTIBLE = "deductible"
    SIR = "sir"
    CAPTIVE = "captive"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)


class Line(_Model):
    """One coverage line — one column in the diagram. Array order is display order."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    abbr: str | None = None

    @property
    def label(self) -> str:
        return self.abbr or self.id.upper()


class Participant(_Model):
    """In memory the share is always basis points; the disk fraction is
    converted in `program_from_jsonable`, never here — an integer `1` on disk
    means 100%, which no field validator could tell apart from 1 bps."""

    carrier: str = Field(min_length=1)
    share_bps: int = Field(ge=0, le=10_000)


class Period(_Model):
    start: date
    end: date


class Layer(_Model):
    """A slab of cover: `limit` xs `attach`, spanning the `applies_to` lines.

    `policy_number` and `period` capture the issued policy behind the layer —
    programs can carry several policy effective/expiry dates, so the period
    is per-layer, defaulting to the program period when absent. This data
    also feeds the planned Schedules of Insurance output (v2)."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    policy_number: str | None = Field(alias="policyNumber", default=None)
    period: Period | None = None
    follows_underlying: bool = Field(alias="followsUnderlying", default=False)
    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    attach: Money
    limit: int
    premium: Money | None = None
    participants: list[Participant] = Field(default_factory=list)
    notes: str | None = None

    @property
    def top(self) -> int:
        return self.attach + self.limit

    @property
    def signed_bps(self) -> int:
        return sum(p.share_bps for p in self.participants)


class Retention(_Model):
    """What the insured pays below the tower. Never insurance, never a carrier colour."""

    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    type: RetentionType
    amount: Money
    aggregate: Money | None = None
    vehicle: str | None = None
    notes: str | None = None


class Sublimit(_Model):
    name: str = Field(min_length=1)
    amount: Money
    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    notes: str | None = None


class Program(_Model):
    """A complete placement: one insured, one period, one stack of layers."""

    schema_id: str = Field(alias="$schema", default=SCHEMA_ID)
    insured: str = Field(min_length=1)
    program: str = Field(min_length=1)
    placement: Placement
    period: Period
    currency: str = "USD"
    lines: list[Line] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    retentions: list[Retention] = Field(default_factory=list)
    sublimits: list[Sublimit] = Field(default_factory=list)
    notes: str | None = None

    def line_ids(self) -> list[str]:
        return [line.id for line in self.lines]

    def layers_for_line(self, line_id: str) -> list[Layer]:
        return sorted(
            (layer for layer in self.layers if line_id in layer.applies_to),
            key=lambda layer: layer.attach,
        )

    def total_limit(self) -> int:
        return sum(layer.limit for layer in self.layers)

    def total_premium(self) -> int:
        return sum(layer.premium or 0 for layer in self.layers)

    def clone_as_renewal(self) -> Program:
        """Deep-copy, bump the period by a year, mark proposed — the single
        most common real workflow (browser: 'clone as next renewal')."""
        clone = self.model_copy(deep=True)
        clone.period = Period(
            start=_plus_year(self.period.start), end=_plus_year(self.period.end)
        )
        clone.placement = Placement.PROPOSED
        return clone

    def underlying_tops(self, layer: Layer) -> dict[str, int]:
        """Per-column top of the stack beneath a follows-underlying layer:
        for each line it spans, the highest top among ordinary layers whose
        top does not exceed the follows layer's attachment. This is what the
        stepped bottom edge sits on."""
        tops: dict[str, int] = {}
        for lid in layer.applies_to:
            candidates = [
                other.top
                for other in self.layers
                if other.id != layer.id
                and not other.follows_underlying
                and lid in other.applies_to
                and other.limit > 0
                and other.top <= layer.attach
            ]
            tops[lid] = max(candidates, default=0)
        return tops

    def carriers(self) -> list[str]:
        """Every carrier, in first-appearance order (stable colour assignment)."""
        seen: dict[str, None] = {}
        for layer in self.layers:
            for participant in layer.participants:
                seen.setdefault(participant.carrier, None)
        return list(seen)


def _plus_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28)


# --- canonical serialisation -------------------------------------------------

# Key order is frozen to match the schema. If the TUI reformatted files
# arbitrarily, git diffs between renewal years would be unreadable.
_PROGRAM_KEYS = (
    "$schema", "insured", "program", "placement", "period", "currency",
    "lines", "layers", "retentions", "sublimits", "notes",
)
_LINE_KEYS = ("id", "name", "abbr")
_LAYER_KEYS = (
    "id", "name", "policyNumber", "period", "followsUnderlying", "appliesTo",
    "attach", "limit", "premium", "participants", "notes",
)
_PARTICIPANT_KEYS = ("carrier", "share")
_RETENTION_KEYS = ("appliesTo", "type", "amount", "aggregate", "vehicle", "notes")
_SUBLIMIT_KEYS = ("name", "amount", "appliesTo", "notes")
_PERIOD_KEYS = ("start", "end")


def _ordered(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    missing = set(raw) - set(keys)
    if missing:  # a field exists in the model but not in the canonical order
        raise RuntimeError(f"canonical key order is missing {sorted(missing)}")
    return {k: raw[k] for k in keys if k in raw and raw[k] is not None}


def program_to_jsonable(program: Program) -> dict[str, Any]:
    """The exact object tree the canonical file contains."""
    raw = {
        "$schema": program.schema_id,
        "insured": program.insured,
        "program": program.program,
        "placement": program.placement.value,
        "period": _ordered(
            {"start": program.period.start.isoformat(), "end": program.period.end.isoformat()},
            _PERIOD_KEYS,
        ),
        "currency": program.currency,
        "lines": [
            _ordered({"id": ln.id, "name": ln.name, "abbr": ln.abbr}, _LINE_KEYS)
            for ln in program.lines
        ],
        "layers": [
            _ordered(
                {
                    "id": layer.id,
                    "name": layer.name,
                    "policyNumber": layer.policy_number,
                    "period": (
                        _ordered(
                            {
                                "start": layer.period.start.isoformat(),
                                "end": layer.period.end.isoformat(),
                            },
                            _PERIOD_KEYS,
                        )
                        if layer.period is not None
                        else None
                    ),
                    "followsUnderlying": layer.follows_underlying or None,
                    "appliesTo": list(layer.applies_to),
                    "attach": layer.attach,
                    "limit": layer.limit,
                    "premium": layer.premium,
                    "participants": [
                        _ordered(
                            {"carrier": p.carrier, "share": bps_to_json_number(p.share_bps)},
                            _PARTICIPANT_KEYS,
                        )
                        for p in layer.participants
                    ],
                    "notes": layer.notes,
                },
                _LAYER_KEYS,
            )
            for layer in program.layers
        ],
        "retentions": [
            _ordered(
                {
                    "appliesTo": list(r.applies_to),
                    "type": r.type.value,
                    "amount": r.amount,
                    "aggregate": r.aggregate,
                    "vehicle": r.vehicle,
                    "notes": r.notes,
                },
                _RETENTION_KEYS,
            )
            for r in program.retentions
        ],
        "sublimits": [
            _ordered(
                {
                    "name": s.name,
                    "amount": s.amount,
                    "appliesTo": list(s.applies_to),
                    "notes": s.notes,
                },
                _SUBLIMIT_KEYS,
            )
            for s in program.sublimits
        ],
        "notes": program.notes,
    }
    return _ordered(raw, _PROGRAM_KEYS)


def dumps_program(program: Program) -> str:
    return json.dumps(program_to_jsonable(program), indent=2, ensure_ascii=False) + "\n"


def dump_program(program: Program, path: Path | str) -> None:
    Path(path).write_text(dumps_program(program), encoding="utf-8")


def parse_program_json(text: str) -> Any:
    """Parse JSON with shares kept exact: floats become Decimal, never binary floats."""
    return json.loads(text, parse_float=Decimal)


def program_from_jsonable(data: Any) -> Program:
    """Validate a parsed JSON tree, converting disk shares (fractions) to bps."""
    if isinstance(data, dict) and isinstance(data.get("layers"), list):
        data = {**data, "layers": [_layer_from_disk(layer) for layer in data["layers"]]}
    return Program.model_validate(data)


def _layer_from_disk(layer: Any) -> Any:
    if not isinstance(layer, dict) or not isinstance(layer.get("participants"), list):
        return layer
    participants = [
        {**p, "share_bps": share_to_bps(p["share"])} if isinstance(p, dict) and "share" in p else p
        for p in layer["participants"]
    ]
    for p in participants:
        if isinstance(p, dict):
            p.pop("share", None)
    return {**layer, "participants": participants}


def loads_program(text: str) -> Program:
    return program_from_jsonable(parse_program_json(text))


def load_program(path: Path | str) -> Program:
    return loads_program(Path(path).read_text(encoding="utf-8"))
