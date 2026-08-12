"""Move or copy a line — with its exclusive dependents — between programs.

Pure core: no rendering imports, no TUI imports (repo rule). The one rule:
exclusive travels, shared stays, move narrows. Shared layers are never
copied narrowed into the target — that would fabricate a placement that
does not exist. Inputs are never mutated; both returned programs are fresh
canonical copies."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Program, dumps_program, loads_program
from .money import format_money

_RETENTION_LABELS = {"deductible": "Deductible", "sir": "SIR", "captive": "Captive"}


@dataclass
class TransferSummary:
    travels: list[str] = field(default_factory=list)
    stays: list[str] = field(default_factory=list)
    renames: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TransferResult:
    src_after: Program
    dst_after: Program
    summary: TransferSummary


def _copy(program: Program) -> Program:
    return loads_program(dumps_program(program))


def _unique_id(wanted: str, taken: set[str]) -> str:
    # same suffix convention as EditSession.unique_id (not imported: pure
    # modules never import from towerkit.tui)
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}" in taken:
        n += 1
    return f"{wanted}-{n}"


def transfer_line(
    src: Program, dst: Program, line_id: str, *, move: bool
) -> TransferResult:
    if line_id not in {ln.id for ln in src.lines}:
        raise KeyError(line_id)
    src_after, dst_after = _copy(src), _copy(dst)
    summary = TransferSummary()
    line_names = {ln.id: ln.name for ln in src.lines}

    def exclusive(applies_to: list[str]) -> bool:
        return set(applies_to) == {line_id}

    def others(applies_to: list[str]) -> str:
        return ", ".join(line_names[i] for i in applies_to if i != line_id)

    # -- what travels (read from the src copy) --------------------------------
    line = next(ln for ln in src_after.lines if ln.id == line_id)
    layers = [ly for ly in src_after.layers if exclusive(ly.applies_to)]
    retentions = [r for r in src_after.retentions if exclusive(r.applies_to)]
    sublimits = [s for s in src_after.sublimits if exclusive(s.applies_to)]

    summary.travels.append(f"Line: {line.name}")
    summary.travels += [f"Layer: {ly.name}" for ly in layers]
    summary.travels += [
        f"Retention: {_RETENTION_LABELS[r.type.value]} {format_money(r.amount)}"
        for r in retentions
    ]
    summary.travels += [
        f"Sublimit: {s.name} {format_money(s.amount)}" for s in sublimits
    ]
    for ly in src.layers:
        if line_id in ly.applies_to and not exclusive(ly.applies_to):
            summary.stays.append(
                f"Layer: {ly.name} — shared with {others(ly.applies_to)}"
            )
    for r in src.retentions:
        if line_id in r.applies_to and not exclusive(r.applies_to):
            summary.stays.append(
                f"Retention: {_RETENTION_LABELS[r.type.value]} "
                f"{format_money(r.amount)} — shared with {others(r.applies_to)}"
            )
    for s in src.sublimits:
        if line_id in s.applies_to and not exclusive(s.applies_to):
            summary.stays.append(
                f"Sublimit: {s.name} — shared with {others(s.applies_to)}"
            )

    # -- graft into the target (collision handling lands in Task 2) ----------
    dst_after.lines.append(line)
    dst_after.layers.extend(layers)
    dst_after.retentions.extend(retentions)
    dst_after.sublimits.extend(sublimits)

    # -- source side ----------------------------------------------------------
    if move:
        src_after.lines = [ln for ln in src_after.lines if ln.id != line_id]
        src_after.layers = [
            ly for ly in src_after.layers if not exclusive(ly.applies_to)
        ]
        for ly in src_after.layers:
            if line_id in ly.applies_to:
                ly.applies_to = [i for i in ly.applies_to if i != line_id]
        src_after.retentions = [
            r
            for r in src_after.retentions
            if not exclusive(r.applies_to)
        ]
        for r in src_after.retentions:
            if line_id in r.applies_to:
                r.applies_to = [i for i in r.applies_to if i != line_id]
        src_after.sublimits = [
            s for s in src_after.sublimits if not exclusive(s.applies_to)
        ]
        for s in src_after.sublimits:
            if line_id in s.applies_to:
                s.applies_to = [i for i in s.applies_to if i != line_id]

    return TransferResult(src_after=src_after, dst_after=dst_after, summary=summary)
