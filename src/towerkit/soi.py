"""Schedule of Insurance mapping: program -> ordered sections of rows.

Pure core: all SOI text composition and ordering lives here, fully typed,
with no Excel/openpyxl imports — the workbook writer in render/soi_xlsx.py
consumes what this module produces (working rule: pure modules never import
rendering libraries)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .model import Layer, Line, Placement, Program
from .money import BPS_SCALE, format_money, format_share


class SoiStatus(StrEnum):
    """Whether the cover on a row EXISTS — the one thing a Schedule of
    Insurance asserts by printing a policy at all. Values are the client-facing
    cell text; this is a display vocabulary, so it is not slug-cased.

    towerkit's model reaches four of these on its own (see `row_status`); the
    other two exist because bookkit's `PlacementStatus` reaches them and the
    column has to be able to say so. Only BOUND is cover in force."""

    BOUND = "Bound"
    PARTIALLY_BOUND = "Partially bound"
    QUOTED = "Quoted"            # bookkit only: no quote state in the model
    SUBMITTED = "Submitted"      # bookkit only: no submission state in the model
    PROPOSED = "Proposed"
    EXPIRED = "Expired"          # bookkit only: soi.py has no notion of "today"
    TO_BE_PLACED = "To be placed"


def row_status(layer: Layer, program: Program) -> SoiStatus:
    """The status of one layer, from the only facts the model carries: whether
    anyone is on the risk, whether their shares close, and whether the program
    as a whole is bound. Nothing here is inferred from a line of business.

    Order matters. A layer with no participants is TO_BE_PLACED whatever the
    program says; a proposed program is never described as bound even when its
    shares close, because a design with carriers pencilled against it is still
    a design. Only then does an unclosed share stack read PARTIALLY_BOUND —
    which the validator already reports as `layer-unplaced` (a warning, so such
    a program renders), and which is NOT cover in force."""
    if not layer.participants:
        return SoiStatus.TO_BE_PLACED
    if program.placement is not Placement.BOUND:
        return SoiStatus.PROPOSED
    if layer.signed_bps < BPS_SCALE:
        return SoiStatus.PARTIALLY_BOUND
    return SoiStatus.BOUND

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


# Grant's phrase, verbatim (2026-08-18, answering C10). "Statutory" alone
# invites the reader to ask "statutory where?"; naming state limits says the
# cover follows the states' own schedules without towerkit inventing a
# sentence about any particular one. `limits_detail` still wins over it —
# a broker who wants the states listed types them, and the escape hatch is
# ahead of this line on purpose.
_STATUTORY_TEXT = "Statutory - State Limits"


def limits_text(layer: Layer, program: Program) -> str:
    if layer.limits_detail:
        return layer.limits_detail
    if layer.statutory:
        base = _STATUTORY_TEXT
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


def retention_text(
    layer: Layer, program: Program, claimed: set[int] | None = None
) -> str:
    """The retention text for one row.

    ONE RETENTION IS STATED ONCE. A captive or SIR that applies to several
    lines used to print on every primary it touched — two WC rows sharing one
    captive read as two retentions, which is a number the client's CFO would
    add up (C10, 2026-08-18). `claimed` carries the indices already stated by
    an earlier row, so the retention lands on the FIRST row that carries it
    and is silent afterwards; `build_soi` threads one set through the sheet in
    display order. Passing None (the standalone call) keeps the old
    single-row semantics.

    A primary's `retention_detail` prose claims what it covers too — the
    broker has stated it in their own words, and a later row repeating it in
    ours is the same double-count. Prose on an EXCESS layer claims nothing:
    excess rows never state a retention, so letting one claim would silence
    the primary that actually carries it."""
    covered = set(layer.applies_to)
    mine = [
        i for i, r in enumerate(program.retentions) if covered & set(r.applies_to)
    ]
    if layer.retention_detail:
        if claimed is not None and _is_primary(layer):
            claimed.update(mine)
        return layer.retention_detail
    if not _is_primary(layer):
        return ""
    parts: list[str] = []
    for i in mine:
        if claimed is not None:
            if i in claimed:
                continue
            claimed.add(i)
        r = program.retentions[i]
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
    # Optional and last so callers that compose rows from their own data
    # (bookkit's book-data section) keep working unchanged. An unstated status
    # renders blank and is NOT counted as bound: a schedule may decline to say
    # whether cover exists, but it must never assert cover it cannot vouch for.
    status: SoiStatus | None = None

    @property
    def is_bound(self) -> bool:
        return self.status is SoiStatus.BOUND


@dataclass(frozen=True)
class SoiSection:
    label: str | None
    rows: tuple[SoiRow, ...]

    @property
    def premium_total(self) -> int:
        """Every row, bound or not. Kept for callers that want the all-in
        figure; the SHEET never prints it, because bound and unbound premium
        added together is the number that misled the reader (C1)."""
        return sum(row.premium or 0 for row in self.rows)

    @property
    def bound_premium_total(self) -> int:
        """Premium for cover that is actually in force — the only subtotal a
        reader may treat as the cost of their programme."""
        return sum(row.premium or 0 for row in self.rows if row.is_bound)

    @property
    def unbound_premium_total(self) -> int:
        return sum(row.premium or 0 for row in self.rows if not row.is_bound)


def _section_key(layer: Layer, program: Program) -> str | None:
    """The section a layer belongs to: its lines' shared group, None when the
    shared group is absent, PROGRAM_WIDE when its lines span groups."""
    groups = {line.group for line in _covered_lines(layer, program)}
    if len(groups) == 1:
        return groups.pop()
    return PROGRAM_WIDE


def premium_value(row: SoiRow) -> int | str | None:
    """What the premium cell holds: the number, blank when there is no premium
    to state, and `Included` for a zero.

    $0.00 reads as free cover (C10). A zero premium never means free — it
    means the layer is priced with another one, the way Employers Liability is
    priced with the Workers Compensation policy it sits beside. Which layer is
    a fact the model cannot express, so towerkit states what it knows and
    stops there; a broker who wants "Included with Part A" needs a premium
    detail field, which is a canonical-format change and is queued, not
    invented here. Totalling is unaffected — a zero adds zero either way."""
    if row.premium is None:
        return None
    if row.premium == 0:
        return "Included"
    return row.premium


def _row(layer: Layer, program: Program, claimed: set[int] | None = None) -> SoiRow:
    period = layer.period or program.period
    return SoiRow(
        insured=program.insured,
        coverage=coverage_text(layer, program),
        carrier=carrier_text(layer),
        policy_number=layer.policy_number or "",
        effective=period.start,
        expiration=period.end,
        limits=limits_text(layer, program),
        retention=retention_text(layer, program, claimed),
        premium=layer.premium,
        status=row_status(layer, program),
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

    # One pass in DISPLAY order, threading the claimed-retentions set through
    # it, so "stated once" means "stated on the first row the reader meets".
    claimed: set[int] = set()
    sections: list[SoiSection] = []
    for key in order:
        rows = tuple(
            _row(layer, program, claimed)
            for layer in sorted(buckets[key], key=sort_key)
        )
        sections.append(SoiSection(label=key, rows=rows))
    return sections


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
