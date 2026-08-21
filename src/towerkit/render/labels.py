"""Shared label text for tower blocks — the single authority both renderers
(the matplotlib graphic and the xlsx schematic worksheet) quote, so a block
reads identically on the chart and in the cells. Pure: layout + money only."""

from __future__ import annotations

from collections.abc import Sequence

from ..layout import GroupBand, LayerBlock, ParticipantBlock
from ..money import format_money_compact, format_share, premium_share


def layer_terms(
    attach: int, limit: int, statutory: bool = False, buffer: bool = False
) -> str:
    """Market convention: a primary is quoted by its limit alone — 'xs $0'
    is meaningless and reads as an error on a chart. Statutory cover has no
    limit to quote at all.

    A buffer is a deliberate uninsured band, not an error and not missing
    cover (model.py's `Layer.buffer`, validate.py's `_check_buffer`) — the
    band still has real money to quote, so the word is APPENDED rather than
    substituted the way `statutory` replaces the whole string. The word is
    "buffer", the same one `_stack_editor.html` prints beside a slab, so a
    reader never sees the drawing and the editor describe the same fact in
    different vocabulary — a hatch pattern alone is a convention the reader
    has to already know; the word is what makes an uninsured band read as a
    DECISION rather than a rendering artefact."""
    if statutory:
        return "Statutory"
    terms = (
        f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
        if attach > 0
        else format_money_compact(limit)
    )
    return f"{terms} — buffer" if buffer else terms


def layer_heading(layer: LayerBlock, follows: bool, marker: str = "") -> str:
    # Statutory is checked first because the two are mutually exclusive —
    # nothing underlies a statutory bar.
    if layer.statutory:
        terms = layer_terms(layer.attach, layer.limit, statutory=True)
    elif follows:
        terms = f"{format_money_compact(layer.limit)} xs underlying"
    else:
        terms = layer_terms(layer.attach, layer.limit)
    return f"{layer.name}{marker} — {terms}"


def participant_label(carrier: str, share_bps: int) -> str:
    return f"{carrier} {format_share(share_bps)}"


def carrier_only_label(carrier: str) -> str:
    """Narrowest non-empty fallback for a share-split cell too tight even for
    'Carrier share%' (schematic_xlsx's narrow-merge ladder): the fill colour
    still carries which share this is, the label just names who holds it."""
    return carrier


def is_pending(layer: LayerBlock) -> bool:
    """A layer nobody has signed at all: "To be placed", distinct from a
    partially-open remainder. This is a claim about the world, and it must be
    decided ONCE — `unplaced_label` below renders the two cases and this
    predicate chooses between them, so a renderer that re-derives it can
    disagree with one that does not.

    It lives here because this module is the authority every renderer quotes.
    `render/web.py` and `render/mpl_program.py` both call it."""
    return layer.signed_bps == 0


def unplaced_label(share_bps: int, pending: bool) -> str:
    """Pending layer (nothing signed): 'To be placed'. Partially-open
    remainder: the open share."""
    return "To be placed" if pending else f"{format_share(share_bps)} open"


def retention_label(type_: str, amount: int, vehicle: str | None) -> str:
    label = f"{type_.upper()} {format_money_compact(amount)}"
    return f"{label} ({vehicle})" if vehicle else label


def group_label(band: GroupBand) -> str:
    rollup = f"{band.label} — Limit {format_money_compact(band.limit)}"
    if band.premium:
        rollup += f" · Premium {format_money_compact(band.premium)}"
    return rollup


def block_premium_label(layer_premium: int | None, share_bps: int) -> str | None:
    if layer_premium is None:
        return None
    return format_money_compact(premium_share(layer_premium, share_bps))


def heading_blocks(participants: Sequence[ParticipantBlock]) -> dict[str, int]:
    """Which block of each layer carries the layer heading: the WIDEST — a
    narrow lead share must not doom the name (graphic rule, kept verbatim)."""

    def width(block: ParticipantBlock) -> float:
        return max((r.width for r in block.rects), default=0.0)

    best: dict[str, int] = {}
    for index, block in enumerate(participants):
        current = best.get(block.layer_id)
        if current is None or width(block) > width(participants[current]):
            best[block.layer_id] = index
    return best
