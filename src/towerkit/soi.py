"""Schedule of Insurance mapping: program -> ordered sections of rows.

Pure core: all SOI text composition and ordering lives here, fully typed,
with no Excel/openpyxl imports — the workbook writer in render/soi_xlsx.py
consumes what this module produces (working rule: pure modules never import
rendering libraries)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .model import Layer, Line, Program
from .money import BPS_SCALE, format_money, format_share

_RETENTION_LABELS = {"deductible": "Deductible", "sir": "SIR", "captive": "Captive"}


def carrier_text(layer: Layer) -> str:
    if not layer.participants:
        return "To be placed"
    if len(layer.participants) == 1 and layer.participants[0].share_bps == BPS_SCALE:
        return layer.participants[0].carrier
    return ", ".join(
        f"{p.carrier} ({format_share(p.share_bps)})" for p in layer.participants
    )


def _is_primary(layer: Layer) -> bool:
    return layer.attach == 0 and not layer.follows_underlying


def _covered_lines(layer: Layer, program: Program) -> list[Line]:
    return [line for line in program.lines if line.id in layer.applies_to]


def limits_text(layer: Layer, program: Program) -> str:
    if layer.limits_detail:
        return layer.limits_detail
    if layer.statutory:
        base = "Statutory"
    elif layer.follows_underlying:
        base = f"{format_money(layer.limit)} xs underlying"
    elif layer.attach == 0:
        base = format_money(layer.limit)  # primaries by limit alone, never "xs $0"
    else:
        base = f"{format_money(layer.limit)} xs {format_money(layer.attach)}"
    covered = set(layer.applies_to)
    subs = [s for s in program.sublimits if covered & set(s.applies_to)]
    if subs:
        tail = "; ".join(f"Sublimit: {s.name} {format_money(s.amount)}" for s in subs)
        return f"{base}; {tail}"
    return base


def retention_text(layer: Layer, program: Program) -> str:
    if layer.retention_detail:
        return layer.retention_detail
    if not _is_primary(layer):
        return ""
    covered = set(layer.applies_to)
    parts: list[str] = []
    for r in program.retentions:
        if not covered & set(r.applies_to):
            continue
        text = f"{_RETENTION_LABELS[r.type.value]} {format_money(r.amount)}"
        if r.aggregate is not None:
            text += f"; Aggregate {format_money(r.aggregate)}"
        if r.vehicle:
            text += f" (via {r.vehicle})"
        parts.append(text)
    return "; ".join(parts)


def coverage_text(layer: Layer, program: Program) -> str:
    lines = _covered_lines(layer, program)
    if len(lines) == 1:
        line = lines[0]
        if len(program.layers_for_line(line.id)) == 1:
            return line.name
        return f"{line.name} — {layer.name}"
    labels = ", ".join(line.label for line in lines)
    return f"{layer.name} ({labels})"


PROGRAM_WIDE = "Program-wide"

_SHEET_ILLEGAL = re.compile(r"[\\/*?:\[\]]")
_PATH_HOSTILE = re.compile(r"[\\/:]")


@dataclass(frozen=True)
class SoiRow:
    insured: str
    coverage: str
    carrier: str
    policy_number: str
    effective: date
    expiration: date
    limits: str
    retention: str
    premium: int | None


@dataclass(frozen=True)
class SoiSection:
    label: str | None
    rows: tuple[SoiRow, ...]

    @property
    def premium_total(self) -> int:
        return sum(row.premium or 0 for row in self.rows)


def _section_key(layer: Layer, program: Program) -> str | None:
    """The section a layer belongs to: its lines' shared group, None when the
    shared group is absent, PROGRAM_WIDE when its lines span groups."""
    groups = {line.group for line in _covered_lines(layer, program)}
    if len(groups) == 1:
        return groups.pop()
    return PROGRAM_WIDE


def _row(layer: Layer, program: Program) -> SoiRow:
    period = layer.period or program.period
    return SoiRow(
        insured=program.insured,
        coverage=coverage_text(layer, program),
        carrier=carrier_text(layer),
        policy_number=layer.policy_number or "",
        effective=period.start,
        expiration=period.end,
        limits=limits_text(layer, program),
        retention=retention_text(layer, program),
        premium=layer.premium,
    )


def build_soi(program: Program) -> list[SoiSection]:
    line_index = {line.id: i for i, line in enumerate(program.lines)}
    layer_index = {layer.id: i for i, layer in enumerate(program.layers)}

    def sort_key(layer: Layer) -> tuple[int, int, int]:
        anchor = min(line_index[lid] for lid in layer.applies_to if lid in line_index)
        return (anchor, layer.attach, layer_index[layer.id])

    buckets: dict[str | None, list[Layer]] = {}
    for layer in program.layers:
        buckets.setdefault(_section_key(layer, program), []).append(layer)

    order: list[str | None] = []
    for line in program.lines:  # named groups by first appearance
        if line.group is not None and line.group in buckets and line.group not in order:
            order.append(line.group)
    if None in buckets:
        order.append(None)
    if PROGRAM_WIDE in buckets:
        order.append(PROGRAM_WIDE)

    return [
        SoiSection(
            label=key,
            rows=tuple(_row(layer, program) for layer in sorted(buckets[key], key=sort_key)),
        )
        for key in order
    ]


def sheet_title(program: Program) -> str:
    suffix = (
        f" SOI - {program.period.start.year % 100:02d}-{program.period.end.year % 100:02d}"
    )
    name = _SHEET_ILLEGAL.sub("", program.program).strip()
    return name[: 31 - len(suffix)].rstrip() + suffix


def default_filename(program: Program) -> str:
    years = f"{program.period.start.year % 100:02d}-{program.period.end.year % 100:02d}"
    return (
        f"{_PATH_HOSTILE.sub('-', program.insured)}"
        f" - Schedule of Insurance {years}.xlsx"
    )
