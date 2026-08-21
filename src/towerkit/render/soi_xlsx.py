"""Schedule of Insurance workbook writer.

Takes the pure sections from towerkit.soi plus a theme and writes a styled
.xlsx. Determinism: workbook properties are pinned (no wall clock) and the
finished archive is rewritten with epoch timestamps, so two identical runs
produce byte-identical files (repo rule)."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..model import Program
from ..scale import DEFAULT_GAMMA
from ..soi import SoiSection, build_soi, premium_subtotal, premium_value, sheet_title
from ..theme import Theme
from .schematic_xlsx import add_schematic_sheet
from .table_xlsx import (
    TableColumn,
    TableSection,
    finalize_workbook,
    render_table_sheet,
    sanitize_sheet_title,
)

# STATUS SITS BESIDE THE COVERAGE IT QUALIFIES, ahead of the carrier: a
# schedule of insurance is read as an assertion that the cover exists, and the
# row has to say so before it names anyone (C1, 2026-08-18). Premium stays
# LAST — the subtotal lines are written into the final column.
_HEADERS = (
    "Insured", "Line of Coverage", "Status", "Carrier", "Policy Number",
    "Effective Date", "Expiration Date", "Limits",
    "Deductible / SIR / Retention", "Premium",
)
_WIDTHS = (23.33, 37.83, 15.0, 39.83, 15.0, 11.83, 13.0, 100.0, 34.83, 12.16)
_DATE_COLS = (6, 7)   # effective / expiration (1-based)
_LIMITS_IX, _RETENTION_IX = 7, 8  # 0-based, into a rendered row tuple
_CURRENCY = '"$"#,##0.00'
_DATE_FMT = "mm/dd/yyyy"


def _subtotal(
    section: SoiSection, *, bound: bool, show: bool
) -> tuple[tuple[str, Any], ...]:
    """One block's subtotal line, or nothing when premiums are withheld.

    Computed from the WHOLE section rather than the rows in the block, because
    `premium_subtotal` already selects on `is_bound` and asking it twice with
    two different row sets would be two answers to one question."""
    if not show:
        return ()
    word = "Bound" if bound else "Unbound"
    return ((f"{word} cover — premium subtotal", premium_subtotal(section, bound=bound)),)


def _table_parts(
    sections: list[SoiSection], show_premiums: bool
) -> tuple[list[TableColumn], list[TableSection]]:
    """The SOI sheet as table-writer inputs — one mapping for every caller,
    so every SOI sheet body is IDENTICAL (the golden guard's premise)."""
    ncols = len(_HEADERS) if show_premiums else len(_HEADERS) - 1
    columns: list[TableColumn] = []
    for i, (header, width) in enumerate(
        zip(_HEADERS[:ncols], _WIDTHS[:ncols], strict=True), start=1
    ):
        if i in _DATE_COLS:
            columns.append(TableColumn(header, width, number_format=_DATE_FMT, wrap=False))
        elif header == "Premium":
            columns.append(TableColumn(header, width, number_format=_CURRENCY, align="right"))
        else:
            columns.append(TableColumn(header, width))

    table_sections: list[TableSection] = []
    for section in sections:
        rows = []
        for row in section.rows:
            values: list[object] = [
                row.insured, row.coverage, row.status or "", row.carrier,
                row.policy_number,
                datetime.combine(row.effective, datetime.min.time()),
                datetime.combine(row.expiration, datetime.min.time()),
                row.limits, row.retention,
            ]
            if show_premiums:
                values.append(premium_value(row))
            rows.append(tuple(values))
        # TWO BLOCKS, NOT TWO SUBTOTAL LINES UNDER ONE LIST (Grant,
        # 2026-08-21). Bound and unbound cover were interleaved in one section
        # with a subtotal line each underneath, so the SCHEDULE — the thing a
        # client reads as "what I have" — listed cover nobody has bought yet,
        # and the reader had to check a Status column row by row to tell them
        # apart. The primary block is now bound cover alone, and cover that is
        # not yet bound is lifted out below it under its own heading.
        #
        # This supersedes the 2026-08-18 shape but keeps its rule intact: bound
        # and unbound premium are NEVER added together, and each block prints
        # its own subtotal unconditionally so a reader never has to work out
        # whether a missing line means zero or means nothing was stated. What
        # the cell HOLDS is still premium_subtotal's decision, not a bare sum —
        # a block whose rows state no premium prints an em dash, because
        # "$0.00" under a visible "To be placed" row asserts free cover.
        #
        # The heading carries the section's own label because a book with three
        # programmes would otherwise grow three identical "not yet bound"
        # headings with nothing saying which programme each belonged to.
        bound_rows = [
            row for row, src in zip(rows, section.rows, strict=True) if src.is_bound
        ]
        unbound_rows = [
            row for row, src in zip(rows, section.rows, strict=True)
            if not src.is_bound
        ]

        # The primary block stands even when nothing is bound YET — an empty
        # schedule under the programme's own name is a true and useful thing to
        # print. It is dropped only when every row moved to the block below,
        # which would otherwise leave a heading with nothing under it and a
        # subtotal the reader cannot tie to a single row.
        if bound_rows or not unbound_rows:
            table_sections.append(TableSection(
                section.label, tuple(bound_rows),
                totals=_subtotal(section, bound=True, show=show_premiums),
            ))
        if unbound_rows:
            # "NOT BOUND", NOT "not yet bound". `is_bound` is status ==
            # BOUND, so this block also collects EXPIRED cover — a run-off
            # layer inside a programme that is otherwise live. "Not yet"
            # claims that cover is still coming, which is a false statement
            # about a policy that has already ended, printed on a document the
            # client reads. "Not bound" is true of everything that lands here:
            # quoted, submitted, prospective and expired alike.
            not_yet = (
                f"{section.label} — not bound" if section.label
                else "Not bound"
            )
            table_sections.append(TableSection(
                not_yet, tuple(unbound_rows),
                totals=_subtotal(section, bound=False, show=show_premiums),
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
    return _row_height(str(values[_LIMITS_IX]), str(values[_RETENTION_IX]))


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
