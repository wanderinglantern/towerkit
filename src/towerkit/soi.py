"""Schedule of Insurance mapping: program -> ordered sections of rows.

Pure core: all SOI text composition and ordering lives here, fully typed,
with no Excel/openpyxl imports — the workbook writer in render/soi_xlsx.py
consumes what this module produces (working rule: pure modules never import
rendering libraries)."""

from __future__ import annotations

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
    if layer.follows_underlying:
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
