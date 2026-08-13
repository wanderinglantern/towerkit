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
from towerkit.render.labels import participant_label
from towerkit.render.schematic_xlsx import (
    AXIS_WIDTH,
    CANVAS_WIDTH_UNITS,
    FIRST_GRID_COL,  # noqa: F401 -- import-existence is part of the interface contract
    FIRST_GRID_ROW,
    GROUP_ROW,
    HEADER_TWO_LINE_HEIGHT,
    LINE_ROW,
    _label_spans,
    add_schematic_sheet,
    quantize_boundaries,
    sheet_rows,
    x_boundaries,
    y_boundaries,
)
from towerkit.render.table_xlsx import _argb, finalize_workbook
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
    rows = quantize_boundaries(y_boundaries(layout), label_spans=_label_spans(layout))
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


def _bottom_border_hex(xlsx_path: Path, sheet_xml: str, cell_ref: str) -> str | None:
    """ARGB colour of a cell's bottom border edge, read from the saved XML —
    same styles.xml-reading technique as `_fill_hex`, needed for the same
    reason: openpyxl won't report style on non-anchor merged cells once the
    file is reloaded."""
    with zipfile.ZipFile(xlsx_path) as z:
        styles = ET.fromstring(z.read("xl/styles.xml"))
        sheet = ET.fromstring(z.read(f"xl/worksheets/{sheet_xml}"))
    borders = []
    for border in styles.find(f"{_SML_NS}borders"):
        bottom = border.find(f"{_SML_NS}bottom")
        colour = bottom.find(f"{_SML_NS}color") if bottom is not None else None
        borders.append(colour.get("rgb") if colour is not None else None)
    border_ids = [int(xf.get("borderId", "0")) for xf in styles.find(f"{_SML_NS}cellXfs")]
    for cell in sheet.iter(f"{_SML_NS}c"):
        if cell.get("r") == cell_ref:
            return borders[border_ids[int(cell.get("s", "0"))]]
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

    def test_canvas_fills_the_full_working_width(self, program, marsh, tmp_path):
        """Grant's Excel review (2026-08-13): the tower occupied roughly
        columns A-R and looked cramped. The sheet's total width — the fixed
        axis column plus every proportional tower column — must now fill
        CANVAS_WIDTH_UNITS (~columns A-AM at Excel's default width), not
        whatever a fixed per-unit rate happens to add up to."""
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Casualty Schematic"]
        layout, _, col_of = _grid(program)
        xs = x_boundaries(layout)
        tower_total = sum(
            ws.column_dimensions[get_column_letter(col_of[x_lo])].width
            for x_lo in xs[:-1]
        )
        total = AXIS_WIDTH + tower_total
        assert abs(total - CANVAS_WIDTH_UNITS) < 1.0


# Task 9: visual parity polish (Grant's Excel review) — theme colours, header
# wrap, narrow-merge fitting, the row floor, and the group-band border fix.


def _filler_lines(n: int) -> list[Line]:
    """Undrawn sibling columns (no layer touches them) that only exist to
    inflate a fixture's total tower span. Task 10 widened the canvas to
    fill CANVAS_WIDTH_UNITS regardless of line count (Grant's review
    2026-08-13), so a single-column fixture no longer renders narrow — it
    gets the WHOLE canvas to itself. These fillers put the column under
    test back in the many-lines-sharing-one-canvas position a real cramped
    tower is actually in, without adding anything for the test to read."""
    return [Line(id=f"filler{i}", name=f"Filler {i}") for i in range(n)]


def _long_header_program() -> Program:
    """A narrow column whose line name cannot fit one wrapped line at the
    column's width — line headers must never clip (requirement 2). Ten
    filler siblings (see `_filler_lines`) keep this column's share of the
    canvas narrow post-Task-10; without them the lone column would claim
    the full ~290-unit canvas and never need to wrap."""
    return Program(
        insured="T", program="T", placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="x", name="Excess Casualty Following Form Umbrella"),
               *_filler_lines(10)],
        layers=[
            Layer(id="p", name="Primary", applies_to=["x"], attach=0, limit=1_000_000,
                  participants=[Participant(carrier="A", share_bps=10_000)]),
        ],
    )


def _narrow_split_program() -> Program:
    """Grant's Property tower case: a 3-way ~33.33% split on one line column
    among many (below the threshold for a 2- or 3-line label, requirement
    3). Nineteen filler siblings (see `_filler_lines`) keep the Property
    column's share of the canvas narrow post-Task-10 — same technique and
    same reason as `_long_header_program`."""
    return Program(
        insured="T", program="Property", placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="prop", name="Property"), *_filler_lines(19)],
        layers=[
            Layer(id="p", name="Primary", applies_to=["prop"], attach=0,
                  limit=9_000_000, premium=90_000,
                  participants=[
                      Participant(carrier="Alpha", share_bps=3_333),
                      Participant(carrier="Beta", share_bps=3_333),
                      Participant(carrier="Gamma", share_bps=3_334),
                  ]),
        ],
    )


def _three_column_group_program() -> Program:
    """Three lines sharing one group bucket — a GROUP_ROW band spanning 3
    columns, whose interior (non-edge) column is where the pre-fix merge-
    before-style bug loses the bottom accent border (requirement 5)."""
    return Program(
        insured="T", program="T", placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[
            Line(id="gl", name="General Liability", group="Casualty"),
            Line(id="al", name="Auto Liability", group="Casualty"),
            Line(id="el", name="Employers Liability", group="Casualty"),
        ],
        layers=[
            Layer(id="p", name="Primary", applies_to=["gl", "al", "el"], attach=0,
                  limit=3_000_000,
                  participants=[Participant(carrier="A", share_bps=10_000)]),
        ],
    )


class TestVisualPolish:
    def test_every_participant_fill_matches_the_shared_colour_authority(
        self, program, marsh, tmp_path
    ):
        """Theme parity: every participant block's fill must equal marsh's
        theme.carrier_colours assignment — the SAME authority the graphic
        (mpl_program) draws from — not a hardcoded hex and not a colour
        re-derived independently by the schematic."""
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        expected = marsh.carrier_colours(program.carriers())
        checked = 0
        for block in layout.participants:
            if block.carrier is None:
                continue
            anchor_rect = max(block.rects, key=lambda r: r.width)
            r0, _ = sheet_rows(rows, anchor_rect.y0, anchor_rect.y1)
            ref = f"{get_column_letter(col_of[anchor_rect.x0])}{r0}"
            actual = _fill_hex(path, "sheet2.xml", ref)
            assert actual == _argb(expected[block.carrier])
            checked += 1
        assert checked >= 5  # make_program's fixture covers 5 participant blocks

    def test_line_header_wraps_get_a_two_line_row_height(self, marsh, tmp_path):
        program = _long_header_program()
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["T Schematic"]
        anchor = ws.cell(row=LINE_ROW, column=FIRST_GRID_COL)
        assert anchor.alignment.wrap_text is True
        assert ws.row_dimensions[LINE_ROW].height >= HEADER_TWO_LINE_HEIGHT

    def test_narrow_share_split_merges_fall_back_to_compact_labels(
        self, marsh, tmp_path
    ):
        program = _narrow_split_program()
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Property Schematic"]
        layout, rows, col_of = _grid(program)
        expected = {
            participant_label(carrier, share_bps)
            for carrier, share_bps in (("Alpha", 3_333), ("Beta", 3_333), ("Gamma", 3_334))
        }
        texts = set()
        for block in layout.participants:
            anchor_rect = max(block.rects, key=lambda r: r.width)
            r0, _ = sheet_rows(rows, anchor_rect.y0, anchor_rect.y1)
            c0 = col_of[anchor_rect.x0]
            cell = ws.cell(row=r0, column=c0)
            texts.add(cell.value)
            assert cell.alignment.shrink_to_fit is True
            assert cell.alignment.wrap_text is not True
        # no block below the threshold shows more than the compact
        # "Carrier share%" line — the layer heading never survives it
        assert texts == expected
        assert all("\n" not in (t or "") for t in texts)
        assert not any(" — " in (t or "") for t in texts)

    def test_thin_labeled_layer_gets_the_row_floor(self, tmp_path):
        """Isolates the quantization floor from gamma's own compression
        assist: a $2M primary under a realistic $100M tower is ALREADY kept
        comfortably visible by gamma compression alone (span 20 rows under
        DEFAULT_GAMMA) — this fixture uses a far more extreme ratio ($2K
        under a ~$1B tower) so the pre-fix span genuinely collapses to a
        single row, proving the floor itself, not gamma, does the work."""
        theme = load_theme(None)
        program = Program(
            insured="T", program="T", placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(id="p", name="Primary", applies_to=["gl"], attach=0,
                      limit=2_000, premium=100,
                      participants=[Participant(carrier="A", share_bps=10_000)]),
                Layer(id="x", name="Excess", applies_to=["gl"], attach=2_000,
                      limit=999_998_000, premium=500_000,
                      participants=[Participant(carrier="B", share_bps=10_000)]),
            ],
        )
        path = _write_schematic(program, theme, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        primary = next(ly for ly in layout.layers if ly.layer_id == "p")
        r0, r1 = sheet_rows(rows, primary.y0, primary.y1)
        assert r1 - r0 + 1 >= 2

        # not narrow in x (a full 1.0-wide column): the label stays wrapped,
        # not shrunk — the row floor and the narrow-merge fix are orthogonal
        ws = load_workbook(path)["T Schematic"]
        anchor = ws.cell(row=r0, column=col_of[layout.columns[0].ex0])
        assert anchor.alignment.wrap_text is True
        assert anchor.alignment.shrink_to_fit is not True

    def test_row_floor_reaches_a_stepped_follows_underlying_run(self, tmp_path):
        """A follows-underlying layer's LayerBlock.y0/y1 is only nominally
        the layer's full height — its stepped-bottom runs (what's actually
        drawn, one per underlying column) can each be a thin fraction of
        that. Two GL columns with a near-total underlying give the umbrella
        a wide-but-y-thin stepped run there; a third Property column with a
        small underlying gives it a narrow-but-y-tall run elsewhere — so the
        wide (anchor-winning) run is also the thin one, exactly the case a
        nominal-layer-span floor would miss and a per-rendered-rect floor
        catches."""
        theme = load_theme(None)
        program = Program(
            insured="T", program="T", placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id="gl1", name="GL1"), Line(id="gl2", name="GL2"),
                   Line(id="prop", name="Prop")],
            layers=[
                Layer(id="gl1-p", name="GL1 Primary", applies_to=["gl1"], attach=0,
                      limit=999_998_000,
                      participants=[Participant(carrier="A", share_bps=10_000)]),
                Layer(id="gl2-p", name="GL2 Primary", applies_to=["gl2"], attach=0,
                      limit=999_998_000,
                      participants=[Participant(carrier="A", share_bps=10_000)]),
                Layer(id="prop-p", name="Prop Primary", applies_to=["prop"], attach=0,
                      limit=1_000_000,
                      participants=[Participant(carrier="B", share_bps=10_000)]),
                Layer(id="umb", name="Umbrella", applies_to=["gl1", "gl2", "prop"],
                      attach=0, limit=1_000_000_000, follows_underlying=True,
                      participants=[Participant(carrier="C", share_bps=10_000)]),
            ],
        )
        path = _write_schematic(program, theme, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        block = next(b for b in layout.participants if b.layer_id == "umb")
        anchor_rect = max(block.rects, key=lambda r: r.width)
        assert anchor_rect.x1 - anchor_rect.x0 > 1.125  # the wide GL1+GL2 run won
        r0, r1 = sheet_rows(rows, anchor_rect.y0, anchor_rect.y1)
        assert r1 - r0 + 1 >= 2

        ws = load_workbook(path)["T Schematic"]
        anchor = ws.cell(row=r0, column=col_of[anchor_rect.x0])
        assert anchor.value  # the anchor rect really is the one carrying text

    def test_quantize_boundaries_floors_a_labeled_span_to_two_rows(self):
        """Pure-function proof of the floor mechanism, same style as
        TestQuantizeBoundaries above: without label_spans, y=0.001 gets the
        existing 1-row strict-monotonic floor; with a label_spans entry
        naming (0.0, 0.001), it must additionally clear row_floor rows."""
        bare = quantize_boundaries([0.0, 0.001, 1.0], total_rows=100)
        assert bare[0.001] - bare[0.0] == 1

        floored = quantize_boundaries(
            [0.0, 0.001, 1.0], total_rows=100, label_spans=[(0.0, 0.001)],
        )
        assert floored[0.001] - floored[0.0] >= 2

    def test_group_band_bottom_border_survives_a_three_column_span(self, tmp_path):
        theme = load_theme(None)
        program = _three_column_group_program()
        path = _write_schematic(program, theme, tmp_path / "s.xlsx")
        layout, _, col_of = _grid(program)
        band = next(b for b in layout.groups if b.label == "Casualty")
        c0, c1 = col_of[band.x0], col_of[band.x1] - 1
        assert c1 - c0 == 2  # a genuine 3-column band, not the 2-column case
        interior = c0 + 1
        ref = f"{get_column_letter(interior)}{GROUP_ROW}"
        assert _bottom_border_hex(path, "sheet2.xml", ref) == _argb(theme.chrome.accent)
