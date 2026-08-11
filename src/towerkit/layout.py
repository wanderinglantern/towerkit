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

from .model import Layer, Program
from .money import BPS_SCALE
from .scale import DEFAULT_GAMMA, YMap, build_y_map, retention_depth

COL_WIDTH = 1.0
GUTTER = 0.25
HALF_GUTTER = GUTTER / 2  # 0.125: exact in binary, like every column edge
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
    x0: float  # nominal column edges: labels and headers centre on these
    x1: float
    ex0: float  # drawing extents: every interior gutter is split between its
    ex1: float  # neighbours, so adjacent bands always meet edge-to-edge


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
class GroupBand:
    """A contiguous bucket of columns sharing a `group`, with pro-rata
    roll-ups (a layer spanning n lines contributes covered/n of its numbers)."""

    label: str
    x0: float
    x1: float
    limit: int
    premium: int


@dataclass(frozen=True)
class TowerLayout:
    columns: tuple[Column, ...]
    layers: tuple[LayerBlock, ...]
    participants: tuple[ParticipantBlock, ...]
    retentions: tuple[RetentionBlock, ...]
    ymap: YMap
    ref_lines: tuple[tuple[int, float], ...]  # (dollars, y) at real attachment points
    groups: tuple[GroupBand, ...]
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
    # a follows-underlying layer has a stepped bottom: one y per column,
    # sitting on that column's stack — all of them become breakpoints
    stepped: dict[str, dict[str, int]] = {
        layer.id: program.underlying_tops(layer)
        for layer in drawable
        if layer.follows_underlying
    }
    extra_points = [top for tops in stepped.values() for top in tops.values()]
    ymap = build_y_map(drawable, gamma=gamma, extra_points=extra_points)

    layer_blocks: list[LayerBlock] = []
    participant_blocks: list[ParticipantBlock] = []
    for layer in drawable:
        runs = _runs(columns, sorted({order[lid] for lid in layer.applies_to if lid in order}))
        y0, y1 = ymap.y(layer.attach), ymap.y(layer.top)
        bottoms = None
        if layer.id in stepped:
            bottoms = {
                lid: ymap.y(top) for lid, top in stepped[layer.id].items()
            }
        outlines = tuple(Rect(r.x0, y0, r.x1, y1) for r in runs)
        blocks = _allocate(layer, runs, y0, y1)
        if bottoms is not None:
            outlines = tuple(
                piece
                for outline in outlines
                for piece in _step_bottom(outline, columns, order, bottoms)
            )
            blocks = [
                ParticipantBlock(
                    layer_id=b.layer_id,
                    carrier=b.carrier,
                    share_bps=b.share_bps,
                    rects=tuple(
                        piece
                        for rect in b.rects
                        for piece in _step_bottom(rect, columns, order, bottoms)
                    ),
                )
                for b in blocks
            ]
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
                outlines=outlines,
            )
        )
        participant_blocks.extend(blocks)

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
        groups=_group_bands(program, columns),
        width=width,
        # no retentions drawn → no band, so column labels hug the towers
        retention_band=RETENTION_BAND if retention_blocks else 0.0,
    )


def _columns(program: Program) -> list[Column]:
    """Interior gutters close only between columns of the same bucket
    (both ungrouped counts as one bucket): buckets read as distinct
    towers, columns within one stay edge-to-edge."""
    last = len(program.lines) - 1
    columns = []
    for index, line in enumerate(program.lines):
        x0 = index * (COL_WIDTH + GUTTER)
        x1 = x0 + COL_WIDTH
        join_left = index > 0 and program.lines[index - 1].group == line.group
        join_right = index < last and program.lines[index + 1].group == line.group
        columns.append(
            Column(
                line_id=line.id,
                label=line.label,
                name=line.name,
                index=index,
                x0=x0,
                x1=x1,
                ex0=x0 - HALF_GUTTER if join_left else x0,
                ex1=x1 + HALF_GUTTER if join_right else x1,
            )
        )
    return columns


def _group_bands(program: Program, columns: list[Column]) -> tuple[GroupBand, ...]:
    bands: list[GroupBand] = []
    start = 0
    for index in range(1, len(program.lines) + 1):
        boundary = (
            index == len(program.lines)
            or program.lines[index].group != program.lines[start].group
        )
        if not boundary:
            continue
        group = program.lines[start].group
        if group is not None:
            ids = {ln.id for ln in program.lines[start:index]}
            limit = premium = 0
            for layer in program.layers:
                if layer.limit <= 0:
                    continue
                covered = sum(1 for lid in layer.applies_to if lid in ids)
                if covered:
                    n = len(layer.applies_to)
                    limit += layer.limit * covered // n
                    premium += (layer.premium or 0) * covered // n
            bands.append(
                GroupBand(
                    label=group,
                    x0=columns[start].x0,
                    x1=columns[index - 1].x1,
                    limit=limit,
                    premium=premium,
                )
            )
        start = index
    return tuple(bands)


def _runs(columns: list[Column], indices: list[int]) -> list[Run]:
    """Group consecutive display indices into contiguous runs, tracking each
    run's start in the layer's virtual (gutter-free) coordinate."""
    runs: list[Run] = []
    v = 0.0
    start = prev = indices[0]
    for idx in [*indices[1:], None]:
        if idx is not None and idx == prev + 1:
            prev = idx
            continue
        x0, x1 = columns[start].ex0, columns[prev].ex1
        width = x1 - x0
        runs.append(Run(x0=x0, x1=x1, v0=v, width=width))
        v += width
        if idx is not None:
            start = prev = idx
    return runs


def _allocate(layer: Layer, runs: list[Run], y0: float, y1: float) -> list[ParticipantBlock]:
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


def _step_bottom(
    rect: Rect,
    columns: list[Column],
    order: dict[str, int],
    bottoms: dict[str, float],
) -> list[Rect]:
    """Split a rectangle at column edges and drop each piece's bottom to its
    column's underlying top — the stepped bottom of a follows layer. Splits
    land on the columns' exact drawing-extent floats, so tiling stays exact."""
    pieces: list[Rect] = []
    for column in columns:
        lo = max(rect.x0, column.ex0)
        hi = min(rect.x1, column.ex1)
        if hi <= lo:
            continue
        x_lo = rect.x0 if lo == rect.x0 else column.ex0
        x_hi = rect.x1 if hi == rect.x1 else column.ex1
        y0 = bottoms.get(column.line_id, rect.y0)
        pieces.append(Rect(x_lo, y0, x_hi, rect.y1))
    return pieces or [rect]


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
