"""Schedule of Insurance workbook writer.

Takes the pure sections from towerkit.soi plus a theme and writes a styled
.xlsx. Determinism: workbook properties are pinned (no wall clock) and the
finished archive is rewritten with epoch timestamps, so two identical runs
produce byte-identical files (repo rule)."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from ..soi import SoiSection
from ..theme import Theme
from .table_xlsx import TableColumn, TableSection, write_table

_HEADERS = (
    "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
    "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium",
)
_WIDTHS = (23.33, 37.83, 39.83, 15.0, 11.83, 13.0, 100.0, 34.83, 12.16)
_CURRENCY = '"$"#,##0.00'
_DATE_FMT = "mm/dd/yyyy"


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
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

    return write_table(
        columns, table_sections, title=title, theme=theme, out_path=out_path,
        row_height=lambda values: _row_height(str(values[6]), str(values[7])),
    )


def _row_height(limits: str, retention: str) -> float:
    """Wrapped-line estimate for the two prose columns (widths 100 and 34.83);
    the sample uses fixed 36/54 heights, so the floor is two lines."""
    lines = 1
    for text, width in ((limits, 100), (retention, 34)):
        if text:
            lines = max(lines, math.ceil(len(text) / width))
    return 18.0 * max(lines, 2)
