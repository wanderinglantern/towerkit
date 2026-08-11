"""Layout: columns, runs, share allocation. Plain numbers and rectangles.

Lines are columns; a layer's horizontal extent is the union of its appliesTo
columns. Where those columns are non-contiguous in display order the layer
becomes several rectangles ("runs"), and participant shares are allocated
continuously across the total span, so a participant can straddle a run
boundary.

Coordinates: columns are 1.0 wide with a 0.25 gutter, so every column edge is
an exact binary float (a multiple of 0.25). Participant boundaries are each
computed once and shared between neighbours — adjacent rectangles get
bit-identical edges, which is what makes "no gaps, no overlaps" an exact test
rather than a tolerance.

This module imports nothing but the stdlib, model.py and scale.py — no
plotting library, ever. Both the vector renderer and the ASCII preview
consume this geometry; if either needs more, it belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Program
from .money import BPS_SCALE
from .scale import DEFAULT_GAMMA, YMap, build_y_map, retention_depth

COL_WIDTH = 1.0
GUTTER = 0.25
RETENTION_BAND = 0.18  # height of the retention band, in tower-height units


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Column:
    line_id: str
    label: str
    name: str
    index: int
    x0: float
    x1: float


@dataclass(frozen=True)
class Run:
    """A horizontally contiguous stretch of columns, in real x and in the
    layer's virtual (gutter-free) coordinate."""

    x0: float
    x1: float
    v0: float  # start in virtual coordinate
    width: float


@dataclass(frozen=True)
class ParticipantBlock:
    layer_id: str
    carrier: str | None  # None = unplaced capacity (hatched grey, warning)
    share_bps: int
    rects: tuple[Rect, ...]


@dataclass(frozen=True)
class LayerBlock:
    layer_id: str
    name: str
    attach: int
    limit: int
    premium: int | None
    signed_bps: int
    y0: float
    y1: float
    outlines: tuple[Rect, ...]  # one per run


@dataclass(frozen=True)
class RetentionBlock:
    index: int
    type: str
    amount: int
    vehicle: str | None
    rects: tuple[Rect, ...]  # y0 negative: below the zero line


@dataclass(frozen=True)
class TowerLayout:
    columns: tuple[Column, ...]
    layers: tuple[LayerBlock, ...]
    participants: tuple[ParticipantBlock, ...]
    retentions: tuple[RetentionBlock, ...]
    ymap: YMap
    ref_lines: tuple[tuple[int, float], ...]  # (dollars, y) at real attachment points
    width: float
    retention_band: float


def build_layout(program: Program, gamma: float = DEFAULT_GAMMA) -> TowerLayout:
    """Pure geometry for one program. Tolerates draft data (skips layers with
    non-positive limits or no known lines) so the live preview never crashes;
    correctness complaints are the validator's job."""
    columns = _columns(program)
    order = {col.line_id: col.index for col in columns}
    drawable = [
        layer
        for layer in program.layers
        if layer.limit > 0 and any(lid in order for lid in layer.applies_to)
    ]
    ymap = build_y_map(drawable, gamma=gamma)

    layer_blocks: list[LayerBlock] = []
    participant_blocks: list[ParticipantBlock] = []
    for layer in drawable:
        runs = _runs(columns, sorted({order[lid] for lid in layer.applies_to if lid in order}))
        y0, y1 = ymap.y(layer.attach), ymap.y(layer.top)
        layer_blocks.append(
            LayerBlock(
                layer_id=layer.id,
                name=layer.name,
                attach=layer.attach,
                limit=layer.limit,
                premium=layer.premium,
                signed_bps=layer.signed_bps,
                y0=y0,
                y1=y1,
                outlines=tuple(Rect(r.x0, y0, r.x1, y1) for r in runs),
            )
        )
        participant_blocks.extend(_allocate(layer, runs, y0, y1))

    max_retention = max((r.amount for r in program.retentions), default=0)
    retention_blocks: list[RetentionBlock] = []
    for index, retention in enumerate(program.retentions):
        indices = sorted({order[lid] for lid in retention.applies_to if lid in order})
        if not indices or retention.amount <= 0:
            continue
        depth = RETENTION_BAND * retention_depth(retention.amount, max_retention)
        rects = tuple(
            Rect(r.x0, -depth, r.x1, 0.0) for r in _runs(columns, indices)
        )
        retention_blocks.append(
            RetentionBlock(
                index=index,
                type=retention.type.value,
                amount=retention.amount,
                vehicle=retention.vehicle,
                rects=rects,
            )
        )

    width = columns[-1].x1 if columns else 0.0
    ref_lines = tuple((d, ymap.y(d)) for d in ymap.breakpoints)
    return TowerLayout(
        columns=tuple(columns),
        layers=tuple(layer_blocks),
        participants=tuple(participant_blocks),
        retentions=tuple(retention_blocks),
        ymap=ymap,
        ref_lines=ref_lines,
        width=width,
        retention_band=RETENTION_BAND,
    )


def _columns(program: Program) -> list[Column]:
    columns = []
    for index, line in enumerate(program.lines):
        x0 = index * (COL_WIDTH + GUTTER)
        columns.append(
            Column(
                line_id=line.id,
                label=line.label,
                name=line.name,
                index=index,
                x0=x0,
                x1=x0 + COL_WIDTH,
            )
        )
    return columns


def _runs(columns: list[Column], indices: list[int]) -> list[Run]:
    """Group consecutive display indices into contiguous runs, tracking each
    run's start in the layer's virtual (gutter-free) coordinate."""
    runs: list[Run] = []
    v = 0.0
    start = prev = indices[0]
    for idx in indices[1:] + [None]:  # type: ignore[list-item]
        if idx is not None and idx == prev + 1:
            prev = idx
            continue
        x0, x1 = columns[start].x0, columns[prev].x1
        width = x1 - x0
        runs.append(Run(x0=x0, x1=x1, v0=v, width=width))
        v += width
        if idx is not None:
            start = prev = idx
    return runs


def _allocate(layer, runs: list[Run], y0: float, y1: float) -> list[ParticipantBlock]:
    """Allocate shares left→right across the concatenated runs.

    Every boundary is computed exactly once, so neighbouring blocks share
    bit-identical edges; run interiors use the run's own x0/x1 literals, so
    splits are exact too.
    """
    total = runs[-1].v0 + runs[-1].width
    entries: list[tuple[str | None, int]] = [
        (p.carrier, p.share_bps) for p in layer.participants if p.share_bps > 0
    ]
    signed = sum(bps for _, bps in entries)
    if signed < BPS_SCALE:
        entries.append((None, BPS_SCALE - signed))  # unplaced capacity, rendered hatched

    cumulative = [0]
    for _, bps in entries:
        cumulative.append(cumulative[-1] + bps)
    # virtual boundaries; the last is exactly `total` because c == BPS_SCALE
    boundaries = [total * (c / BPS_SCALE) for c in cumulative]

    blocks: list[ParticipantBlock] = []
    real = [_to_real(t, runs) for t in boundaries]
    for k, (carrier, bps) in enumerate(entries):
        v_lo, v_hi = boundaries[k], boundaries[k + 1]
        rects = []
        for run in runs:
            lo = max(v_lo, run.v0)
            hi = min(v_hi, run.v0 + run.width)
            if hi <= lo:
                continue
            # A boundary exactly on a run edge belongs to the run it opens/closes.
            x_lo = run.x0 if lo == run.v0 else real[k]
            x_hi = run.x1 if hi == run.v0 + run.width else real[k + 1]
            rects.append(Rect(x_lo, y0, x_hi, y1))
        blocks.append(
            ParticipantBlock(
                layer_id=layer.id, carrier=carrier, share_bps=bps, rects=tuple(rects)
            )
        )
    return blocks


def _to_real(t: float, runs: list[Run]) -> float:
    """Map a virtual coordinate to real x. Run edges map to the run's own
    x0/x1 floats, never to arithmetic that merely approximates them."""
    for run in runs:
        if t <= run.v0 + run.width:
            if t <= run.v0:
                return run.x0
            if t == run.v0 + run.width:
                return run.x1
            return run.x0 + (t - run.v0)
    return runs[-1].x1
