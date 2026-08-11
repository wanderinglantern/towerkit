"""Schedule of Insurance workbook writer.

Takes the pure sections from towerkit.soi plus a theme and writes a styled
.xlsx. Determinism: workbook properties are pinned (no wall clock) and the
finished archive is rewritten with epoch timestamps, so two identical runs
produce byte-identical files (repo rule)."""

from __future__ import annotations

import math
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..soi import SoiSection
from ..theme import Theme
from .common import provenance

_HEADERS = (
    "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
    "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium",
)
_WIDTHS = (23.33, 37.83, 39.83, 15.0, 11.83, 13.0, 100.0, 34.83, 12.16)
_CURRENCY = '"$"#,##0.00'
_DATE_FMT = "mm/dd/yyyy"
_PINNED = datetime(1980, 1, 1)


def _argb(hex_colour: str) -> str:
    return "FF" + hex_colour.lstrip("#").upper()


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
    soi = theme.soi
    ncols = 9 if show_premiums else 8
    thin = Side(style="thin", color=_argb(soi.border))
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(name=soi.font, size=soi.size, bold=True,
                       color=_argb(soi.effective_header_text))
    body_font = Font(name=soi.font, size=soi.size, color=_argb(soi.body_text))
    header_fill = PatternFill("solid", fgColor=_argb(soi.header_fill))
    band_fill = PatternFill("solid", fgColor=_argb(soi.band_fill))

    wb = Workbook()
    ws = wb.active
    ws.title = title
    for i, width in enumerate(_WIDTHS[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    for col, text in enumerate(_HEADERS[:ncols], start=1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.font, cell.fill, cell.border = header_font, header_fill, border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 36.0
    ws.freeze_panes = "A2"

    row_ix = 2
    for section in sections:
        if section.label is not None:
            ws.merge_cells(start_row=row_ix, start_column=1,
                           end_row=row_ix, end_column=8 if show_premiums else ncols)
            label = ws.cell(row=row_ix, column=1, value=section.label)
            label.font, label.fill = header_font, header_fill
            label.alignment = Alignment(vertical="center")
            if show_premiums:
                total = ws.cell(row=row_ix, column=9, value=section.premium_total)
                total.font, total.fill = header_font, header_fill
                total.number_format = _CURRENCY
                total.alignment = Alignment(horizontal="right", vertical="center")
            for col in range(1, ncols + 1):
                ws.cell(row=row_ix, column=col).border = border
            ws.row_dimensions[row_ix].height = 22.0
            row_ix += 1
        for band, row in enumerate(section.rows):
            values: list[object] = [
                row.insured, row.coverage, row.carrier, row.policy_number,
                datetime.combine(row.effective, datetime.min.time()),
                datetime.combine(row.expiration, datetime.min.time()),
                row.limits, row.retention,
            ]
            if show_premiums:
                values.append(row.premium)
            for col, value in enumerate(values, start=1):
                cell = ws.cell(row=row_ix, column=col, value=value)
                cell.font, cell.border = body_font, border
                if band % 2 == 1:
                    cell.fill = band_fill
                if col in (5, 6):
                    cell.number_format = _DATE_FMT
                    cell.alignment = Alignment(horizontal="left", vertical="top")
                elif col == 9:
                    cell.number_format = _CURRENCY
                    cell.alignment = Alignment(horizontal="right", vertical="top",
                                               wrap_text=True)
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="top",
                                               wrap_text=True)
            ws.row_dimensions[row_ix].height = _row_height(row.limits, row.retention)
            row_ix += 1

    props = wb.properties
    props.creator = provenance()
    props.created = _PINNED
    props.modified = _PINNED
    props.lastModifiedBy = None

    buffer = BytesIO()
    wb.save(buffer)
    _normalize_zip(buffer.getvalue(), out_path)
    return out_path


def _row_height(limits: str, retention: str) -> float:
    """Wrapped-line estimate for the two prose columns (widths 100 and 34.83);
    the sample uses fixed 36/54 heights, so the floor is two lines."""
    lines = 1
    for text, width in ((limits, 100), (retention, 34)):
        if text:
            lines = max(lines, math.ceil(len(text) / width))
    return 18.0 * max(lines, 2)


def _normalize_zip(data: bytes, out_path: Path) -> None:
    """Rewrite the archive with epoch timestamps and fixed compression so
    identical content is identical bytes (openpyxl stamps wall-clock zip
    entries)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as src, zipfile.ZipFile(
        out_path, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for name in src.namelist():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, src.read(name))
