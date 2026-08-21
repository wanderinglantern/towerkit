"""Web panel view of a tower: geometry in, HTML-ready values out.

This is the fourth consumer of `layout.py`'s geometry and the fourth quoter
of `labels.py`'s text, after the vector graphic, the xlsx schematic and
the ASCII preview. It turns a `TowerLayout` into everything a
fixed-pixel-height HTML panel needs to paint **without measuring text** —
because it cannot: there is no font metric server-side, and no
text-measurement pass can run per request.

Two rules follow from that, and they are the module:

1. **It composes no block text of its own.** Every string a block asserts
   comes from `render/labels.py`, the single authority both existing
   renderers already quote. The renderers are required to agree about the
   FACTS a block asserts; they are free to differ about which candidate
   string a fitter chose to assert them with. A hand-rolled f-string here is
   exactly the divergence that rule exists to prevent.
2. **It recomputes no geometry.** The `[0, 1]` rects are passed through from
   the `TowerLayout` verbatim. Turning them into CSS percentages is the
   caller's last mile; this module does not do HTML.

Pure: layout + scale + money + labels. No plotting library, ever — same rule
`scale.py` and `layout.py` carry, and `tests/test_render_web.py` greps for it
(the pre-existing purity test in `test_scale.py` covers only those two files).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..layout import Column, LayerBlock, ParticipantBlock, Rect, TowerLayout, build_layout
from ..model import Program
from ..money import format_money_compact
from ..scale import DEFAULT_GAMMA
from .labels import (
    block_premium_label,
    carrier_only_label,
    group_label,
    heading_blocks,
    is_pending,
    layer_heading,
    layer_terms,
    participant_label,
    retention_label,
    unplaced_label,
)

# --------------------------------------------------------------------------
# The drop thresholds. These come from the DESIGN PROTOTYPE's `buildTower()`,
# not from measurement: they are the numbers a human tuned by eye against the
# mockup, and they are reproduced here so the panel drops the same labels the
# mockup dropped. Do not "improve" them without new eyes-on evidence.
# --------------------------------------------------------------------------

# A layer name is shown only if the block's rendered height clears this many
# CSS pixels. A block spanning more than one column gets the lower bar: it is
# wide enough for the text to sit on one line.
NAME_MIN_PX = 30.0
NAME_MIN_PX_SPANNING = 13.0

# The money figure survives in blocks too short for a name.
MONEY_MIN_PX = 11.0

# Reference-line dollar labels are thinned to this minimum vertical gap.
# Under gamma < 1 the low breakpoints sit within a few pixels of each other
# and one mark per breakpoint crowds the gutter unreadably.
#
# NOTE, and it is a real divergence from the prototype: the prototype hard
# codes this gap as a fraction of y (`minGap = 12 / 170`, "~12px in a chart
# at least 170px tall") while computing block heights against a ~240px
# drawing area (`hPx = heightPct * 2.4`). Those two implied chart heights
# cannot both be right. Here the gap is measured against the caller's real
# `chart_height_px`, which is the number the drop thresholds already use.
# At the prototype's own ~240px area this thins slightly LESS aggressively
# than 12/170 did.
REF_LINE_MIN_GAP_PX = 12.0

# Chrome, not block text: the panel is a working tool, not a client
# deliverable, so it carries the caveat the ASCII preview carries and the
# export chart deliberately does not.
NOT_TO_SCALE_CAVEAT = (
    "not to scale — compressed vertical scale (γ = {gamma}); "
    "reference lines at real attachment points"
)




@dataclass(frozen=True)
class WebBlock:
    """One participant block, ready to paint.

    `rects` is the layout's own geometry, unmodified. The label strings are
    all `labels.py`'s. The flags say which of them clear the drop rule at the
    caller's rendered height.
    """

    layer_id: str
    carrier: str | None  # None = unplaced capacity
    share_bps: int
    rects: tuple[Rect, ...]
    height_px: float
    spans_columns: int
    heading: str | None  # only on the block `heading_blocks` designates
    name_forms: tuple[str, ...]  # richest first; narrowing within is FIT
    terms: str  # the layer's money: how big this block is
    premium: str | None  # this block's own share of the layer premium
    show_name: bool
    show_money: bool

    @property
    def name(self) -> str:
        return self.name_forms[0]

    @property
    def money(self) -> str | None:
        """The block's money line: its own share of the premium when the
        block also names itself, and the layer's terms when it does not — a
        block too short to say WHO it is should still say HOW BIG it is,
        which is what the prototype's money line asserted. A bare premium
        figure with no carrier beside it names nobody's money."""
        return self.premium if self.show_name else self.terms

    @property
    def lines(self) -> tuple[str, ...]:
        """The lines to paint, in order, after the drop rule."""
        out: list[str] = []
        if self.show_name:
            if self.heading is not None:
                out.append(self.heading)
            out.append(self.name)
        if self.show_money and self.money is not None:
            out.append(self.money)
        return tuple(out)


@dataclass(frozen=True)
class WebLayer:
    """A layer outline and the facts about it that are not per-block."""

    layer_id: str
    name: str
    heading: str
    terms: str
    pending: bool
    statutory: bool
    buffer: bool
    y0: float
    y1: float
    outlines: tuple[Rect, ...]


@dataclass(frozen=True)
class WebRefLine:
    dollars: int
    y: float
    label: str


@dataclass(frozen=True)
class WebRetention:
    index: int
    label: str
    rects: tuple[Rect, ...]


@dataclass(frozen=True)
class WebGroup:
    label: str
    x0: float
    x1: float


@dataclass(frozen=True)
class WebTower:
    """Everything the panel paints. `layout` is the geometry it was built
    from, kept whole so a caller never has to rebuild it (and so a test can
    assert the rects came through untouched)."""

    layout: TowerLayout
    chart_height_px: float
    layers: tuple[WebLayer, ...]
    blocks: tuple[WebBlock, ...]
    ref_lines: tuple[WebRefLine, ...]
    retentions: tuple[WebRetention, ...]
    groups: tuple[WebGroup, ...]
    caveat: str | None
    top_dollars: int | None  # None when nothing on the dollar scale exists

    @property
    def columns(self) -> tuple[Column, ...]:
        return self.layout.columns

    @property
    def chevrons(self) -> tuple[Rect, ...]:
        return self.layout.chevrons

    @property
    def width(self) -> float:
        return self.layout.width

    @property
    def retention_band(self) -> float:
        return self.layout.retention_band


def build_web_tower(
    program: Program,
    chart_height_px: float,
    gamma: float = DEFAULT_GAMMA,
) -> WebTower:
    """Build the panel view of `program` at a rendered height.

    `chart_height_px` is the height, in CSS pixels, of the **chart drawing
    area** — the element the tower's `[0, 1]` y-coordinates are drawn into —
    and NOT the panel's outer box. This is a distinct number and passing the
    wrong one fails silently: the outer box also contains the header row, the
    retention band below the zero line and the caveat line, none of which are
    part of the 100%-tall region a block's height is a fraction of. In the
    design prototype the outer panel is 340px while the drawing area is
    ~240px (its own block heights are `heightPct * 2.4`); passing 340 makes
    every block look ~42% taller than it renders, which UNDER-fires every
    threshold and drops too few labels. No test that merely checks the
    thresholds are applied consistently can catch that — only the caller
    passing the right box can. Derive it from the actual CSS height of the
    chart element, measured or hardcoded; do not re-derive it from the outer
    panel later by guessing.
    """
    if chart_height_px <= 0:
        raise ValueError("chart_height_px must be positive")

    tower = build_layout(program, gamma=gamma)
    columns = tower.columns

    follows = {layer.id for layer in program.layers if layer.follows_underlying}
    # A buffer is a `Layer` fact, not a layout fact — `LayerBlock` (layout.py)
    # carries no `buffer` field, the same way it carries no `follows`. Read it
    # off `program.layers` by id, the pattern `follows` already uses, rather
    # than growing layout.py's geometry type for a fact that is never drawn
    # differently, only styled differently by the caller.
    buffers = {layer.id for layer in program.layers if layer.buffer}
    layer_by_id = {layer.layer_id: layer for layer in tower.layers}
    headings = {
        block.layer_id: layer_heading(
            block, follows=block.layer_id in follows, buffer=block.layer_id in buffers,
        )
        for block in tower.layers
    }
    heading_owner = heading_blocks(tower.participants)

    web_layers = tuple(
        WebLayer(
            layer_id=block.layer_id,
            name=block.name,
            heading=headings[block.layer_id],
            terms=layer_terms(
                block.attach, block.limit,
                statutory=block.statutory, buffer=block.layer_id in buffers,
            ),
            pending=is_pending(block),
            statutory=block.statutory,
            buffer=block.layer_id in buffers,
            y0=block.y0,
            y1=block.y1,
            outlines=block.outlines,
        )
        for block in tower.layers
    )

    blocks = tuple(
        _web_block(
            block,
            layer_by_id[block.layer_id],
            columns,
            chart_height_px,
            heading=headings[block.layer_id] if heading_owner.get(block.layer_id) == i else None,
            buffer=block.layer_id in buffers,
        )
        for i, block in enumerate(tower.participants)
    )

    top_dollars = tower.ymap.max_dollars if tower.ymap.max_dollars > 0 else None
    return WebTower(
        layout=tower,
        chart_height_px=chart_height_px,
        layers=web_layers,
        blocks=blocks,
        ref_lines=_thin_ref_lines(tower, columns, chart_height_px),
        retentions=tuple(
            WebRetention(
                index=ret.index,
                label=retention_label(ret.type, ret.amount, ret.vehicle),
                rects=ret.rects,
            )
            for ret in tower.retentions
        ),
        groups=tuple(
            WebGroup(label=group_label(band), x0=band.x0, x1=band.x1)
            for band in tower.groups
        ),
        caveat=(
            NOT_TO_SCALE_CAVEAT.format(gamma=format(tower.ymap.gamma, "g"))
            if tower.ymap.gamma != 1.0
            else None
        ),
        top_dollars=top_dollars,
    )


def _web_block(
    block: ParticipantBlock,
    layer: LayerBlock,
    columns: tuple[Column, ...],
    chart_height_px: float,
    heading: str | None,
    buffer: bool,
) -> WebBlock:
    # The label rides the widest rect, the same choice the graphic makes
    # (`mpl_program._participant_label`), so both renderers are talking about
    # the same piece of a run-split or stepped-bottom block.
    label_rect = max(block.rects, key=lambda r: r.width, default=None)
    height_px = (label_rect.height if label_rect else 0.0) * chart_height_px
    spans = _spanned_columns(block.rects, columns)

    if block.carrier is None:
        name_forms: tuple[str, ...] = (
            unplaced_label(block.share_bps, is_pending(layer), buffer=buffer),
        )
        premium = None  # unplaced capacity is nobody's premium
    else:
        full = participant_label(block.carrier, block.share_bps)
        narrow = carrier_only_label(block.carrier)
        name_forms = (full,) if narrow == full else (full, narrow)
        premium = block_premium_label(layer.premium, block.share_bps)

    threshold = NAME_MIN_PX_SPANNING if spans > 1 else NAME_MIN_PX
    return WebBlock(
        layer_id=block.layer_id,
        carrier=block.carrier,
        share_bps=block.share_bps,
        rects=block.rects,
        height_px=height_px,
        spans_columns=spans,
        heading=heading,
        name_forms=name_forms,
        terms=layer_terms(
            layer.attach, layer.limit, statutory=layer.statutory, buffer=buffer,
        ),
        premium=premium,
        show_name=height_px >= threshold,
        show_money=height_px >= MONEY_MIN_PX,
    )


def _spanned_columns(rects: tuple[Rect, ...], columns: tuple[Column, ...]) -> int:
    """How many columns this block covers, read off the geometry it was
    given. The prototype asked this of a whole LAYER because it drew one
    label per layer; a participant block is the thing that carries text here,
    so it is its own width that decides whether the text gets a line to
    itself."""
    return sum(
        1
        for column in columns
        if any(rect.x0 < column.ex1 and rect.x1 > column.ex0 for rect in rects)
    )


def _thin_ref_lines(
    tower: TowerLayout,
    columns: tuple[Column, ...],
    chart_height_px: float,
) -> tuple[WebRefLine, ...]:
    """Keep the top of the tower, then walk down dropping any mark closer
    than REF_LINE_MIN_GAP_PX to the last one kept — except that among two
    marks that close, the attachment spanning more lines wins. The top always
    wins.

    Statutory cover contributes no breakpoints (`scale.py`), so a
    statutory-only program has an empty dollar scale and produces no marks
    and no top of tower at all, rather than a nonsense "$0".
    """
    candidates = sorted(
        ((dollars, y) for dollars, y in tower.ref_lines if dollars > 0),
        key=lambda pair: -pair[0],
    )
    if not candidates:
        return ()

    spans: dict[int, int] = {}
    for layer in tower.layers:
        if layer.statutory or layer.attach <= 0:
            continue
        width = _spanned_columns(layer.outlines, columns)
        spans[layer.attach] = max(spans.get(layer.attach, 0), width)

    top = tower.ymap.max_dollars
    min_gap = REF_LINE_MIN_GAP_PX / chart_height_px
    marks: list[tuple[int, float]] = []
    for dollars, y in candidates:
        if not marks or abs(marks[-1][1] - y) >= min_gap:
            marks.append((dollars, y))
            continue
        if marks[-1][0] != top and spans.get(dollars, 0) > spans.get(marks[-1][0], 0):
            marks[-1] = (dollars, y)
    return tuple(
        WebRefLine(dollars=dollars, y=y, label=format_money_compact(dollars))
        for dollars, y in marks
    )
