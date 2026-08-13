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

from ..layout import TowerLayout

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
