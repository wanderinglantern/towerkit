"""Schematic worksheet: quantizer first (pure), then cell content (Task 4)."""

from datetime import date

from towerkit.layout import build_layout
from towerkit.model import Layer, Line, Participant, Period, Placement, Program
from towerkit.render.schematic_xlsx import (
    FIRST_GRID_COL,  # noqa: F401 -- import-existence is part of the interface contract
    FIRST_GRID_ROW,
    quantize_boundaries,
    sheet_rows,
    x_boundaries,
    y_boundaries,
)


def _mini_program() -> Program:
    """One line, a $1M primary under a $25M excess split 60/40."""
    return Program(
        insured="T", program="T", placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        layers=[
            Layer(id="p", name="Primary", applies_to=["gl"], attach=0,
                  limit=1_000_000,
                  participants=[Participant(carrier="A", share_bps=10_000)]),
            Layer(id="x", name="Excess", applies_to=["gl"], attach=1_000_000,
                  limit=25_000_000,
                  participants=[Participant(carrier="B", share_bps=6_000),
                                Participant(carrier="C", share_bps=4_000)]),
        ],
    )


class TestQuantizeBoundaries:
    def test_full_coverage_and_proportionality(self) -> None:
        rows = quantize_boundaries([0.0, 0.1, 1.0], total_rows=100)
        assert rows[0.0] == 0 and rows[0.1] == 10 and rows[1.0] == 100

    def test_min_one_row_floor_is_strict_monotonicity(self) -> None:
        rows = quantize_boundaries([0.0, 0.001, 0.002, 1.0], total_rows=100)
        assert rows[0.001] == 1 and rows[0.002] == 2

    def test_negative_retention_band_shares_the_grid(self) -> None:
        rows = quantize_boundaries([-0.18, 0.0, 1.0], total_rows=100)
        assert rows[-0.18] == 0 and rows[1.0] == 100

    def test_adjacent_spans_tile_exactly(self) -> None:
        rows = quantize_boundaries([0.0, 0.3, 1.0])
        below_top, below_bottom = sheet_rows(rows, 0.0, 0.3)
        above_top, above_bottom = sheet_rows(rows, 0.3, 1.0)
        assert above_top == FIRST_GRID_ROW            # y=1.0 is the first grid row
        assert above_bottom + 1 == below_top          # shared boundary, no gap
        assert below_bottom == FIRST_GRID_ROW + rows[1.0] - 1


class TestLayoutBoundaries:
    def test_x_boundaries_include_share_splits(self) -> None:
        xs = x_boundaries(build_layout(_mini_program()))
        assert xs == (0.0, 0.6, 1.0)                  # 60/40 split at exactly 0.6

    def test_y_boundaries_include_every_attachment_and_zero(self) -> None:
        layout = build_layout(_mini_program())
        ys = y_boundaries(layout)
        assert ys[0] == 0.0 and ys[-1] == 1.0
        assert layout.ymap.y(1_000_000) in ys         # the primary/excess boundary
