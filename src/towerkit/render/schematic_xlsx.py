"""The tower as a worksheet: merged ranges over a quantized grid.

Geometry comes from layout.py — THE SAME TowerLayout the graphic renderer
consumes — so stacking, spans and proportions match the chart by
construction. This module only quantizes the layout's normalized
coordinates onto sheet rows/columns and paints merged ranges; no tower math
happens in cell space. Colors/fonts come from the Theme, label text from
render/labels.py — both shared with the graphic.

Quantization is per-BOUNDARY, not per-block: every distinct edge float in
the layout snaps to one integer row/column, and blocks look their edges up.
Neighbours share bit-identical edge floats (layout.py's exactness rule), so
merges tile with no gaps and no overlaps by construction."""

from __future__ import annotations

from collections.abc import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..layout import Rect, TowerLayout, build_layout
from ..model import Program
from ..money import format_money_compact
from ..scale import DEFAULT_GAMMA
from ..theme import Chrome, Theme, contrast_text
from .labels import (
    block_premium_label,
    group_label,
    heading_blocks,
    layer_heading,
    participant_label,
    retention_label,
    unplaced_label,
)
from .table_xlsx import _argb, sanitize_sheet_title

TOTAL_ROWS = 100         # quantization target across the full y-span
GRID_ROW_HEIGHT = 4.0    # uniform thin rows: ~100 × 4pt ≈ one screen of tower
X_CHARS_PER_UNIT = 10.0  # a 1.0-wide line column ≈ 10 Excel character units
AXIS_COL = 1             # column A: attachment boundaries as money labels
AXIS_WIDTH = 10.0
FIRST_GRID_COL = 2
TITLE_ROW, GROUP_ROW, LINE_ROW = 1, 2, 3
FIRST_GRID_ROW = 4


def quantize_boundaries(
    ys: Sequence[float], total_rows: int = TOTAL_ROWS
) -> dict[float, int]:
    """Each distinct boundary → an integer row index, proportional over the
    full span, strictly increasing (the spec's ceil/min-1 floor: gamma
    compression already keeps small layers visible, so bumps are rare)."""
    distinct = sorted(set(ys))
    if len(distinct) < 2:
        return dict.fromkeys(distinct, 0)
    lo, span = distinct[0], distinct[-1] - distinct[0]
    out: dict[float, int] = {}
    prev = -1
    for y in distinct:
        row = max(round((y - lo) / span * total_rows), prev + 1)
        out[y] = row
        prev = row
    return out


def x_boundaries(layout: TowerLayout) -> tuple[float, ...]:
    """Every distinct vertical edge: column drawing extents plus every
    participant/retention rect edge (share boundaries split a line into
    share-proportional sub-columns — the 'column group' per line). Group
    bands use nominal column edges, which coincide with drawing extents at
    band boundaries (gutters only close WITHIN a group, layout.py:231-254),
    so they are covered too."""
    edges: set[float] = set()
    for column in layout.columns:
        edges.add(column.ex0)
        edges.add(column.ex1)
    for block in layout.participants:
        for rect in block.rects:
            edges.add(rect.x0)
            edges.add(rect.x1)
    for retention in layout.retentions:
        for rect in retention.rects:
            edges.add(rect.x0)
            edges.add(rect.x1)
    return tuple(sorted(edges))


def y_boundaries(layout: TowerLayout) -> tuple[float, ...]:
    """Every distinct horizontal edge, including the zero line, every
    ref-line (= every attachment breakpoint) and the retention band."""
    ys: set[float] = {0.0}
    ys.update(y for _, y in layout.ref_lines)
    for layer in layout.layers:
        for rect in layer.outlines:
            ys.add(rect.y0)
            ys.add(rect.y1)
    for block in layout.participants:
        for rect in block.rects:
            ys.add(rect.y0)
            ys.add(rect.y1)
    for retention in layout.retentions:
        for rect in retention.rects:
            ys.add(rect.y0)
            ys.add(rect.y1)
    return tuple(sorted(ys))


def sheet_rows(rows: dict[float, int], y0: float, y1: float) -> tuple[int, int]:
    """Inclusive (top_row, bottom_row) worksheet span for normalized
    [y0, y1]. Rows grow downward; y grows upward."""
    top = max(rows.values())
    return (FIRST_GRID_ROW + top - rows[y1], FIRST_GRID_ROW + top - rows[y0] - 1)


def add_schematic_sheet(
    wb: Workbook,
    program: Program,
    theme: Theme,
    *,
    gamma: float = DEFAULT_GAMMA,
    show_premiums: bool = True,
) -> None:
    """Append the tower as a worksheet of merged ranges. The caller owns the
    workbook lifecycle (finalize_workbook runs ONCE, after every sheet)."""
    layout = build_layout(program, gamma=gamma)
    chrome = theme.chrome
    colours = theme.carrier_colours(program.carriers())
    ws = wb.create_sheet(sanitize_sheet_title(f"{program.program} Schematic"))
    ws.sheet_view.showGridLines = False

    xs = x_boundaries(layout)
    rows = quantize_boundaries(y_boundaries(layout))
    top = max(rows.values())
    if len(xs) < 2 or top == 0:  # draft with no drawable tower: title only
        _title(ws, program, chrome, last_col=AXIS_COL)
        return
    col_of = {x: FIRST_GRID_COL + i for i, x in enumerate(xs)}
    last_col = FIRST_GRID_COL + len(xs) - 2

    ws.column_dimensions[get_column_letter(AXIS_COL)].width = AXIS_WIDTH
    for x_lo, x_hi in zip(xs, xs[1:], strict=False):
        letter = get_column_letter(col_of[x_lo])
        ws.column_dimensions[letter].width = (x_hi - x_lo) * X_CHARS_PER_UNIT
    for r in range(FIRST_GRID_ROW, FIRST_GRID_ROW + top):
        ws.row_dimensions[r].height = GRID_ROW_HEIGHT

    _title(ws, program, chrome, last_col=last_col)
    _headers(ws, layout, chrome, col_of)
    _axis(ws, layout, rows, chrome)

    pending = {ly.layer_id for ly in layout.layers if ly.signed_bps == 0}
    follows = {ly.id for ly in program.layers if ly.follows_underlying}
    headings = heading_blocks(layout.participants)
    layer_by_id = {ly.layer_id: ly for ly in layout.layers}

    for index, block in enumerate(layout.participants):
        lines: list[str] = []
        if headings.get(block.layer_id) == index:
            owner = layer_by_id[block.layer_id]
            lines.append(layer_heading(owner, follows=block.layer_id in follows))
        if block.carrier is None:
            is_pending = block.layer_id in pending
            lines.append(unplaced_label(block.share_bps, pending=is_pending))
            # graphic: pending = empty dashed outline; open remainder = hatch
            fill = (
                None
                if is_pending
                else PatternFill(
                    "lightUp",
                    fgColor=_argb(chrome.unplaced),
                    bgColor=_argb(chrome.background),
                )
            )
            text_colour = chrome.ink if is_pending else chrome.muted
            edge = Side(
                style="dashed" if is_pending else "thin",
                color=_argb(chrome.ink if is_pending else chrome.unplaced),
            )
        else:
            lines.append(participant_label(block.carrier, block.share_bps))
            if show_premiums:
                premium = block_premium_label(
                    layer_by_id[block.layer_id].premium, block.share_bps
                )
                if premium is not None:
                    lines.append(premium)
            face = colours[block.carrier]
            fill = PatternFill("solid", fgColor=_argb(face))
            text_colour = contrast_text(face, chrome.background, chrome.ink)
            edge = Side(style="thin", color=_argb(chrome.background))
        anchor_rect = max(block.rects, key=lambda r: r.width, default=None)
        for rect in block.rects:
            _block(
                ws, rect, rows, col_of,
                text="\n".join(lines) if rect is anchor_rect else "",
                fill=fill,
                border=Border(left=edge, right=edge, top=edge, bottom=edge),
                font=Font(name=chrome.font, size=8, color=_argb(text_colour)),
            )

    for retention in layout.retentions:
        fill = PatternFill("solid", fgColor=_argb(theme.retention_fill(retention.type)))
        edge = Side(style="thin", color=_argb(chrome.ink))
        for i, rect in enumerate(retention.rects):
            _block(
                ws, rect, rows, col_of,
                text=(
                    retention_label(retention.type, retention.amount, retention.vehicle)
                    if i == 0
                    else ""
                ),
                fill=fill,
                border=Border(left=edge, right=edge, top=edge, bottom=edge),
                font=Font(name=chrome.font, size=7, color=_argb(chrome.ink)),
            )


def _block(
    ws: Worksheet,
    rect: Rect,
    rows: dict[float, int],
    col_of: dict[float, int],
    *,
    text: str,
    fill: PatternFill | None,
    border: Border,
    font: Font,
) -> None:
    """One rect → one merged range. MUST merge BEFORE styling non-anchor
    cells: openpyxl's merge_cells() replaces every non-anchor cell with a
    fresh MergedCell (worksheet.py's _clean_merge_range), so any fill/value
    set on those coordinates first is discarded — merge first, style after,
    the same order render_table_sheet already uses in table_xlsx.py."""
    r0, r1 = sheet_rows(rows, rect.y0, rect.y1)
    c0, c1 = col_of[rect.x0], col_of[rect.x1] - 1
    if (r0, c0) != (r1, c1):  # a 1×1 block is already one cell; no merge
        ws.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)
    anchor = ws.cell(row=r0, column=c0, value=text or None)
    anchor.font = font
    anchor.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            cell = ws.cell(row=r, column=c)
            if fill is not None:
                cell.fill = fill
            cell.border = border


def _title(ws: Worksheet, program: Program, chrome: Chrome, *, last_col: int) -> None:
    period = f"{program.period.start.isoformat()} – {program.period.end.isoformat()}"
    cell = ws.cell(
        row=TITLE_ROW, column=AXIS_COL,
        value=f"{program.insured} — {program.program} · {period}",
    )
    cell.font = Font(
        name=chrome.title_font or chrome.font, size=12, color=_argb(chrome.ink)
    )
    cell.alignment = Alignment(horizontal="left", vertical="center")
    if last_col > AXIS_COL:
        ws.merge_cells(
            start_row=TITLE_ROW, start_column=AXIS_COL,
            end_row=TITLE_ROW, end_column=last_col,
        )
    ws.row_dimensions[TITLE_ROW].height = 22.0
    ws.row_dimensions[GROUP_ROW].height = 14.0
    ws.row_dimensions[LINE_ROW].height = 16.0


def _headers(
    ws: Worksheet, layout: TowerLayout, chrome: Chrome, col_of: dict[float, int]
) -> None:
    """Group bands above, line names under them (the spec's header order —
    the graphic puts both below the tower, a chart-only convention)."""
    accent = Side(style="medium", color=_argb(chrome.accent))
    for band in layout.groups:
        c0, c1 = col_of[band.x0], col_of[band.x1] - 1
        cell = ws.cell(row=GROUP_ROW, column=c0, value=group_label(band))
        cell.font = Font(name=chrome.font, size=8, color=_argb(chrome.accent))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        for c in range(c0, c1 + 1):
            ws.cell(row=GROUP_ROW, column=c).border = Border(bottom=accent)
        if c1 > c0:
            ws.merge_cells(
                start_row=GROUP_ROW, start_column=c0, end_row=GROUP_ROW, end_column=c1
            )
    for column in layout.columns:
        c0, c1 = col_of[column.ex0], col_of[column.ex1] - 1
        cell = ws.cell(row=LINE_ROW, column=c0, value=column.name)
        cell.font = Font(name=chrome.font, size=9, bold=True, color=_argb(chrome.ink))
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if c1 > c0:
            ws.merge_cells(
                start_row=LINE_ROW, start_column=c0, end_row=LINE_ROW, end_column=c1
            )


def _axis(
    ws: Worksheet, layout: TowerLayout, rows: dict[float, int], chrome: Chrome
) -> None:
    """Column A: one merged cell per y-interval, labeled with its floor
    attachment, bottom-aligned — the label sits just above its boundary,
    like the chart's gutter labels. $0 stays silent."""
    ref = list(layout.ref_lines)  # (dollars, y), ascending by construction
    for (d_lo, y_lo), (_d_hi, y_hi) in zip(ref, ref[1:], strict=False):
        if d_lo <= 0:
            continue
        r0, r1 = sheet_rows(rows, y_lo, y_hi)
        cell = ws.cell(row=r0, column=AXIS_COL, value=format_money_compact(d_lo))
        cell.font = Font(name=chrome.font, size=7, color=_argb(chrome.muted))
        cell.alignment = Alignment(horizontal="right", vertical="bottom")
        if r1 > r0:
            ws.merge_cells(
                start_row=r0, start_column=AXIS_COL, end_row=r1, end_column=AXIS_COL
            )
