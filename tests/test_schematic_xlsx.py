"""Schematic worksheet: quantizer first (pure), then cell content (Task 4)."""

import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from test_soi import make_program

from towerkit.layout import build_layout
from towerkit.model import Layer, Line, Participant, Period, Placement, Program
from towerkit.render.schematic_xlsx import (
    FIRST_GRID_COL,  # noqa: F401 -- import-existence is part of the interface contract
    FIRST_GRID_ROW,
    add_schematic_sheet,
    quantize_boundaries,
    sheet_rows,
    x_boundaries,
    y_boundaries,
)
from towerkit.render.table_xlsx import finalize_workbook
from towerkit.theme import load_theme

REPO = Path(__file__).parent.parent
_SML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


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


@pytest.fixture()
def marsh():
    return load_theme(REPO / "themes" / "marsh.json")


@pytest.fixture()
def program():
    return make_program()


def _write_schematic(program, theme, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "SOI"  # stand-in first sheet; Task 5 provides the real one
    add_schematic_sheet(wb, program, theme)
    return finalize_workbook(wb, path)


def _grid(program):
    layout = build_layout(program)
    rows = quantize_boundaries(y_boundaries(layout))
    xs = x_boundaries(layout)
    col_of = {x: FIRST_GRID_COL + i for i, x in enumerate(xs)}
    return layout, rows, col_of


def _fill_hex(xlsx_path: Path, sheet_xml: str, cell_ref: str) -> str | None:
    """ARGB fill of one cell, read from the saved XML (test_soi_xlsx.py's
    technique: openpyxl won't report styles on non-anchor merged cells)."""
    with zipfile.ZipFile(xlsx_path) as z:
        styles = ET.fromstring(z.read("xl/styles.xml"))
        sheet = ET.fromstring(z.read(f"xl/worksheets/{sheet_xml}"))
    fills = []
    for fill in styles.find(f"{_SML_NS}fills"):
        pattern = fill.find(f"{_SML_NS}patternFill")
        fg = pattern.find(f"{_SML_NS}fgColor") if pattern is not None else None
        fills.append(fg.get("rgb") if fg is not None else None)
    fill_ids = [int(xf.get("fillId", "0")) for xf in styles.find(f"{_SML_NS}cellXfs")]
    for cell in sheet.iter(f"{_SML_NS}c"):
        if cell.get("r") == cell_ref:
            return fills[fill_ids[int(cell.get("s", "0"))]]
    return None


class TestSchematicSheet:
    def test_sheet_appended_and_named(self, program, marsh, tmp_path):
        wb = load_workbook(_write_schematic(program, marsh, tmp_path / "s.xlsx"))
        assert wb.sheetnames == ["SOI", "Casualty Schematic"]

    def test_merged_ranges_never_overlap(self, program, marsh, tmp_path):
        ws = load_workbook(_write_schematic(program, marsh, tmp_path / "s.xlsx"))[
            "Casualty Schematic"
        ]
        bounds = [r.bounds for r in ws.merged_cells.ranges]  # (c0, r0, c1, r1)
        for i, a in enumerate(bounds):
            for b in bounds[i + 1 :]:
                disjoint = a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
                assert disjoint, f"overlapping merges: {a} vs {b}"

    def test_block_merge_lands_exactly_where_the_layout_says(
        self, program, marsh, tmp_path
    ):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Casualty Schematic"]
        layout, rows, col_of = _grid(program)
        zenith = next(
            b for b in layout.participants
            if b.carrier == "Zenith" and b.layer_id == "gl-primary"
        )
        rect = zenith.rects[0]
        r0, r1 = sheet_rows(rows, rect.y0, rect.y1)
        c0, c1 = col_of[rect.x0], col_of[rect.x1] - 1
        ref = f"{get_column_letter(c0)}{r0}:{get_column_letter(c1)}{r1}"
        assert ref in {str(r) for r in ws.merged_cells.ranges}
        anchor = ws[f"{get_column_letter(c0)}{r0}"]
        assert "Zenith 100%" in anchor.value
        assert "Primary — $1M" in anchor.value      # heading rides the widest block
        assert "$50K" in anchor.value               # premium share, like the graphic
        # first-appearance palette assignment, same order as the graphic:
        assert anchor.fill.fgColor.rgb == "FF000F47"  # marsh carrierPalette[0]

    def test_merged_interior_cells_carry_the_fill(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        gamma_blk = next(b for b in layout.participants if b.carrier == "Gamma")
        rect = gamma_blk.rects[0]
        r0, _ = sheet_rows(rows, rect.y0, rect.y1)
        c0 = col_of[rect.x0]
        interior = f"{get_column_letter(c0)}{r0 + 1}"  # prop-primary spans many rows
        assert _fill_hex(path, "sheet2.xml", interior) == "FF82BAFF"  # Gamma = palette[3]

    def test_proportions_boundaries_and_stacking(self, program, marsh, tmp_path):
        layout, rows, _ = _grid(program)

        def span(layer_id: str) -> tuple[int, int]:
            ly = next(lyr for lyr in layout.layers if lyr.layer_id == layer_id)
            return sheet_rows(rows, ly.y0, ly.y1)

        p_top, p_bottom = span("gl-primary")
        x_top, x_bottom = span("gl-x1")
        assert x_bottom + 1 == p_top                       # stacked flush, no gap
        prop_top, prop_bottom = span("prop-primary")
        assert (prop_bottom - prop_top) > (p_bottom - p_top)  # $10M towers over $1M

    def test_pending_retention_axis_and_lines(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Casualty Schematic"]
        values = {
            c.value for row in ws.iter_rows() for c in row if c.value is not None
        }
        assert any("To be placed" in v for v in values)     # al-primary is pending
        assert "SIR $250K" in values                        # retention label
        assert any(v == "$1M" for v in values)              # axis boundary label
        assert "General Liability" in values                # line header
        assert any(v.startswith("Casualty — Limit") for v in values)  # group band

    def test_retention_fill_is_the_typed_theme_fill(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        sir = next(r for r in layout.retentions if r.type == "sir")
        rect = sir.rects[0]
        r0, _ = sheet_rows(rows, rect.y0, rect.y1)
        anchor = f"{get_column_letter(col_of[rect.x0])}{r0}"
        assert _fill_hex(path, "sheet2.xml", anchor) == "FFFFF3DA"  # marsh sir fill

    def test_two_writes_byte_identical(self, program, marsh, tmp_path):
        a = _write_schematic(program, marsh, tmp_path / "a.xlsx")
        b = _write_schematic(program, marsh, tmp_path / "b.xlsx")
        assert a.read_bytes() == b.read_bytes()
