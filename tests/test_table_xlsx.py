"""Generic styled-table writer: SOI styling for arbitrary sectioned tables."""

from pathlib import Path

import pytest
from openpyxl import load_workbook

from towerkit.render.table_xlsx import TableColumn, TableSection, write_table
from towerkit.theme import load_theme

COLS = (
    TableColumn("Item", 30.0),
    TableColumn("Amount", 12.0, number_format='"$"#,##0.00', align="right"),
)
SECTIONS = (
    TableSection("Section A", (("first", 100), ("second", 200)), total=300),
    TableSection(None, (("loose row", 5),)),
)


@pytest.fixture()
def theme():
    return load_theme(None)


def test_headers_sections_and_total(theme, tmp_path: Path):
    path = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "t.xlsx")
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == ["Item", "Amount"]
    assert ws["A2"].value == "Section A"      # label row
    assert ws["B2"].value == 300              # total in last column
    assert ws["A3"].value == "first"
    assert ws["A5"].value == "loose row"      # unlabeled section has no label row
    assert ws.freeze_panes == "A2"


def test_two_runs_byte_identical(theme, tmp_path: Path):
    a = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "a.xlsx")
    b = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "b.xlsx")
    assert a.read_bytes() == b.read_bytes()


def test_row_height_hook(theme, tmp_path: Path):
    path = write_table(
        COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "h.xlsx",
        row_height=lambda values: 44.0,
    )
    ws = load_workbook(path).active
    assert ws.row_dimensions[3].height == 44.0


def test_title_sanitized_for_illegal_sheet_chars(theme, tmp_path: Path):
    # openpyxl raises ValueError for / \ ? * [ ] : in a sheet title —
    # write_table is the authority, so no caller can crash on a name like
    # "A/B Partners". This must not raise, and the surviving title must
    # still be recognizably the client name.
    path = write_table(
        COLS, SECTIONS, title="A/B [test]: weird?", theme=theme,
        out_path=tmp_path / "sanitized.xlsx",
    )
    wb = load_workbook(path)
    title = wb.active.title
    assert title == "A B test weird"
    for char in "/\\?*[]:":
        assert char not in title
    assert len(title) <= 31
