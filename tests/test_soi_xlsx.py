"""SOI workbook writer: styling mirrors the sample; output is byte-identical."""

from pathlib import Path

import pytest
from openpyxl import load_workbook
from test_soi import make_program

from towerkit.render.soi_xlsx import write_soi
from towerkit.soi import build_soi, sheet_title
from towerkit.theme import load_theme


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


class TestDeterminism:
    def test_two_writes_are_byte_identical(self, program, theme, tmp_path) -> None:
        a = _write(program, theme, tmp_path / "a.xlsx")
        b = _write(program, theme, tmp_path / "b.xlsx")
        assert a.read_bytes() == b.read_bytes()


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
