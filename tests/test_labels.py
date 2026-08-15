"""Shared block-label authority: both renderers must quote these exactly."""

from towerkit.layout import LayerBlock, ParticipantBlock, Rect
from towerkit.render.labels import (
    block_premium_label,
    heading_blocks,
    layer_heading,
    layer_terms,
    participant_label,
    retention_label,
    unplaced_label,
)


def _layer(name: str, attach: int, limit: int, statutory: bool = False) -> LayerBlock:
    return LayerBlock(
        layer_id="x", name=name, attach=attach, limit=limit, premium=None,
        signed_bps=10_000, y0=0.0, y1=1.0, outlines=(), statutory=statutory,
    )


def test_layer_terms_statutory_has_no_dollar_figure() -> None:
    """Statutory cover has no limit to quote — a dollar figure here would be
    a lie, and '$0' reads as a data error."""
    assert layer_terms(0, 0, statutory=True) == "Statutory"


def test_layer_terms_market_convention() -> None:
    assert layer_terms(0, 5_000_000) == "$5M"          # primary: never "xs $0"
    assert layer_terms(2_000_000, 25_000_000) == "$25M xs $2M"


def test_layer_heading_matches_graphic() -> None:
    excess = _layer("1st Excess", 1_000_000, 4_000_000)
    assert layer_heading(excess, follows=False) == "1st Excess — $4M xs $1M"
    assert layer_heading(excess, follows=True) == "1st Excess — $4M xs underlying"
    assert layer_heading(excess, follows=False, marker="¹") == "1st Excess¹ — $4M xs $1M"
    assert layer_heading(_layer("Primary", 0, 5_000_000), follows=False) == "Primary — $5M"


def test_layer_heading_statutory() -> None:
    block = _layer("Workers Compensation", 0, 0, statutory=True)
    assert layer_heading(block, follows=False) == "Workers Compensation — Statutory"


def test_participant_and_unplaced_labels() -> None:
    assert participant_label("Zenith", 10_000) == "Zenith 100%"
    assert unplaced_label(4_000, pending=False) == "40% open"
    assert unplaced_label(10_000, pending=True) == "To be placed"


def test_retention_label() -> None:
    assert retention_label("sir", 250_000, None) == "SIR $250K"
    assert retention_label("captive", 1_000_000, "Atomic Re") == "CAPTIVE $1M (Atomic Re)"


def test_block_premium_label() -> None:
    assert block_premium_label(None, 5_000) is None
    assert block_premium_label(100_000, 5_000) == "$50K"


def test_heading_rides_the_widest_block() -> None:
    def blk(layer_id: str, width: float) -> ParticipantBlock:
        return ParticipantBlock(
            layer_id=layer_id, carrier="C", share_bps=1,
            rects=(Rect(0.0, 0.0, width, 1.0),),
        )

    blocks = (blk("a", 0.2), blk("a", 0.7), blk("b", 1.0))
    assert heading_blocks(blocks) == {"a": 1, "b": 2}
