"""Schedule of Insurance workbook writer.

Takes the pure sections from towerkit.soi plus a theme and writes a styled
.xlsx. Determinism: workbook properties are pinned (no wall clock) and the
finished archive is rewritten with epoch timestamps, so two identical runs
produce byte-identical files (repo rule)."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..model import Program
from ..scale import DEFAULT_GAMMA
from ..soi import SoiSection, build_soi, sheet_title
from ..theme import Theme
from .schematic_xlsx import add_schematic_sheet
from .table_xlsx import (
    TableColumn,
    TableSection,
    finalize_workbook,
    render_table_sheet,
    sanitize_sheet_title,
)

_HEADERS = (
    "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
    "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium",
)
_WIDTHS = (23.33, 37.83, 39.83, 15.0, 11.83, 13.0, 100.0, 34.83, 12.16)
_CURRENCY = '"$"#,##0.00'
_DATE_FMT = "mm/dd/yyyy"


def _table_parts(
    sections: list[SoiSection], show_premiums: bool
) -> tuple[list[TableColumn], list[TableSection]]:
    """The SOI sheet as table-writer inputs — one mapping for every caller,
    so every SOI sheet body is IDENTICAL (the golden guard's premise)."""
    ncols = 9 if show_premiums else 8
    columns: list[TableColumn] = []
    for i, (header, width) in enumerate(
        zip(_HEADERS[:ncols], _WIDTHS[:ncols], strict=True), start=1
    ):
        if i in (5, 6):  # effective / expiration
            columns.append(TableColumn(header, width, number_format=_DATE_FMT, wrap=False))
        elif i == 9:     # premium
            columns.append(TableColumn(header, width, number_format=_CURRENCY, align="right"))
        else:
            columns.append(TableColumn(header, width))

    table_sections: list[TableSection] = []
    for section in sections:
        rows = []
        for row in section.rows:
            values: list[object] = [
                row.insured, row.coverage, row.carrier, row.policy_number,
                datetime.combine(row.effective, datetime.min.time()),
                datetime.combine(row.expiration, datetime.min.time()),
                row.limits, row.retention,
            ]
            if show_premiums:
                values.append(row.premium)
            rows.append(tuple(values))
        table_sections.append(TableSection(
            section.label, tuple(rows),
            total=section.premium_total
            if (section.label is not None and show_premiums) else None,
        ))

    return columns, table_sections


def _row_height(limits: str, retention: str) -> float:
    """Wrapped-line estimate for the two prose columns (widths 100 and 34.83);
    the sample uses fixed 36/54 heights, so the floor is two lines."""
    lines = 1
    for text, width in ((limits, 100), (retention, 34)):
        if text:
            lines = max(lines, math.ceil(len(text) / width))
    return 18.0 * max(lines, 2)


def _soi_row_height(values: tuple[object, ...]) -> float:
    return _row_height(str(values[6]), str(values[7]))


def render_soi_sheet(
    ws: Worksheet,
    sections: list[SoiSection],
    *,
    theme: Theme,
    show_premiums: bool = True,
) -> None:
    """PUBLIC per-sheet API: the SOI body onto one worksheet of an open
    workbook (bookkit composes it beside its own table sheets and finalizes
    once). The prose-column row-height heuristic rides along; titling the
    sheet is the caller's job, like render_table_sheet."""
    columns, table_sections = _table_parts(sections, show_premiums)
    render_table_sheet(
        ws, columns, table_sections, theme=theme, row_height=_soi_row_height
    )


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(title)
    render_soi_sheet(ws, sections, theme=theme, show_premiums=show_premiums)
    return finalize_workbook(wb, out_path)


def write_soi_workbook(
    program: Program,
    *,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
    include_schematic: bool = False,
    gamma: float = DEFAULT_GAMMA,
) -> Path:
    """One workbook, normalized ONCE: the SOI sheet plus the schematic sheet
    when asked. include_schematic=False is byte-identical to write_soi by
    construction — same sheet body, same finalize."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(sheet_title(program))
    render_soi_sheet(ws, build_soi(program), theme=theme, show_premiums=show_premiums)
    if include_schematic:
        add_schematic_sheet(wb, program, theme, gamma=gamma, show_premiums=show_premiums)
    return finalize_workbook(wb, out_path)
