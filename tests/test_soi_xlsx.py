"""SOI workbook writer: styling mirrors the sample; output is byte-identical."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook
from test_soi import make_program

from towerkit.render.soi_xlsx import write_soi
from towerkit.soi import build_soi, sheet_title
from towerkit.theme import load_theme

_SML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@pytest.fixture()
def program():
    return make_program()


@pytest.fixture()
def theme():
    return load_theme(None)


def _write(program, theme, path: Path, **kw) -> Path:
    return write_soi(
        build_soi(program), title=sheet_title(program), theme=theme, out_path=path, **kw
    )


def _core_xml(xlsx_path: Path) -> str:
    with zipfile.ZipFile(xlsx_path) as z:
        return z.read("docProps/core.xml").decode()


def _row2_fill_hexes(xlsx_path: Path) -> list[str | None]:
    """Per-column fill (as ARGB hex, or None if unfilled) for sheet row 2,
    read straight from the saved XML.

    openpyxl's reader doesn't reliably expose style on the non-anchor cells
    of a merged range: reloading the same file and inspecting e.g. ws["D2"]
    (a MergedCell) always reports patternType=None regardless of what's on
    disk, even though the underlying <c> element carries a real fillId. So
    this reads xl/worksheets/sheet1.xml + xl/styles.xml directly rather than
    going through load_workbook — see the Task 5 fix report.
    """
    with zipfile.ZipFile(xlsx_path) as z:
        styles = ET.fromstring(z.read("xl/styles.xml"))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    fills = []
    for fill in styles.find(f"{_SML_NS}fills"):
        pattern = fill.find(f"{_SML_NS}patternFill")
        fg = pattern.find(f"{_SML_NS}fgColor") if pattern is not None else None
        fills.append(fg.get("rgb") if fg is not None else None)
    fill_ids = [int(xf.get("fillId", "0")) for xf in styles.find(f"{_SML_NS}cellXfs")]

    row2 = next(r for r in sheet.iter(f"{_SML_NS}row") if r.get("r") == "2")
    hexes = []
    for cell in row2.findall(f"{_SML_NS}c"):
        s = int(cell.get("s", "0"))
        hexes.append(fills[fill_ids[s]])
    return hexes


class TestDeterminism:
    def test_two_writes_are_byte_identical(self, program, theme, tmp_path) -> None:
        a = _write(program, theme, tmp_path / "a.xlsx")
        b = _write(program, theme, tmp_path / "b.xlsx")
        assert a.read_bytes() == b.read_bytes()

    def test_modified_timestamp_is_pinned_not_wall_clock(self, program, theme, tmp_path) -> None:
        # A same-second write can pass the byte-identity test by coincidence
        # even if <dcterms:modified> isn't actually neutralized. Assert the
        # pinned value directly so the test can't pass by timing luck.
        path = _write(program, theme, tmp_path / "s.xlsx")
        assert "<dcterms:modified" in _core_xml(path)
        assert "1980-01-01T00:00:00Z</dcterms:modified>" in _core_xml(path)


class TestContent:
    def test_headers_and_title(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        assert ws.title == "Casualty SOI - 26-27"
        assert [c.value for c in ws[1]] == [
            "Insured", "Line of Coverage", "Carrier", "Policy Number",
            "Effective Date", "Expiration Date", "Limits",
            "Deductible / SIR / Retention", "Premium",
        ]

    def test_header_style_mirrors_sample(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        cell = ws["A1"]
        assert cell.fill.fgColor.rgb == "FF003865"
        assert cell.font.bold and cell.font.name == "Noto Sans"
        assert cell.alignment.horizontal == "center" and cell.alignment.wrap_text
        assert ws.freeze_panes == "A2"

    def test_section_band_rows_merged_with_rollup(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        # row 2 = "Casualty" band: merged A:H, roll-up premium in I
        assert ws["A2"].value == "Casualty"
        assert "A2:H2" in {str(r) for r in ws.merged_cells.ranges}
        assert ws["I2"].value == 100_000
        assert ws["I2"].number_format == '"$"#,##0.00'

    def test_section_band_fill_covers_every_column(self, program, theme, tmp_path) -> None:
        # Merged A2:H2 plus the I2 roll-up should all carry the accent fill —
        # one solid band, not a navy patch at each end with white in between.
        path = _write(program, theme, tmp_path / "s.xlsx")
        hexes = _row2_fill_hexes(path)
        assert len(hexes) == 9  # A..I
        assert hexes == ["FF003865"] * 9

    def test_zebra_restarts_per_section(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        # Casualty: band r2, body r3-r6; unlabeled section body starts r7.
        assert ws["A3"].fill.patternType is None          # first body row: white
        assert ws["A4"].fill.fgColor.rgb == "FFF7F3EE"    # second: banded
        assert ws["A7"].fill.patternType is None          # new section restarts white

    def test_unlabeled_section_has_no_band_row(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        assert ws["B7"].value == "Property — Primary"  # body row, not a section label

    def test_dates_are_real_dates(self, program, theme, tmp_path) -> None:
        import datetime

        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        cell = ws["E3"]
        assert cell.value == datetime.datetime(2026, 2, 1)
        assert cell.number_format == "mm/dd/yyyy"

    def test_no_premiums_drops_the_column(self, program, theme, tmp_path) -> None:
        ws = load_workbook(
            _write(program, theme, tmp_path / "s.xlsx", show_premiums=False)
        ).active
        assert [c.value for c in ws[1]][-1] == "Deductible / SIR / Retention"
        assert ws.max_column == 8
        assert "A2:H2" in {str(r) for r in ws.merged_cells.ranges}  # full-width band
