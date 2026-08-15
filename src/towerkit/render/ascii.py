"""Terminal preview of a tower. Instant structural feedback, not print quality.

All geometry comes from layout.py — this module only rasterises rectangles
onto a character grid. If a drawing decision needs geometry that is not
already in the layout, it belongs in layout.py, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..layout import TowerLayout, build_layout
from ..model import Program
from ..money import format_money_compact
from ..scale import DEFAULT_GAMMA
from ..theme import Theme

FULL = "█"
UNPLACED = "░"
RETENTION = "▒"
NO_COVER = "·"
ZERO = "═"


@dataclass
class _Cell:
    char: str = " "
    colour: int | None = None  # ANSI-256 index
    dim: bool = False


def ansi256(hex_colour: str) -> int:
    """Quantise #RRGGBB to the nearest ANSI-256 index (cube + grey ramp)."""
    hex_colour = hex_colour.lstrip("#")
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (0, 2, 4))

    def cube_idx(v: int) -> int:
        return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40

    cr, cg, cb = cube_idx(r), cube_idx(g), cube_idx(b)
    cube_vals = [0, 95, 135, 175, 215, 255]
    nearest = (cube_vals[cr], cube_vals[cg], cube_vals[cb])
    cube_dist = sum((a - b) ** 2 for a, b in zip((r, g, b), nearest, strict=True))
    grey = max(0, min(23, (((r + g + b) // 3) - 8) // 10))
    grey_val = 8 + grey * 10
    grey_dist = sum((v - grey_val) ** 2 for v in (r, g, b))
    if grey_dist < cube_dist:
        return 232 + grey
    return 16 + 36 * cr + 6 * cg + cb


DANGER = "#C53532"


def render_ascii(
    program: Program,
    theme: Theme,
    width: int = 76,
    height: int = 26,
    colour: bool = True,
    gamma: float = DEFAULT_GAMMA,
    error_layers: frozenset[str] = frozenset(),
    error_lines: frozenset[str] = frozenset(),
) -> str:
    """error_layers / error_lines highlight validation targets in danger red:
    offending layer blocks recolour, and an error line's empty background
    tints red — which is precisely where a gap shows."""
    tower = build_layout(program, gamma=gamma)
    if not tower.columns:
        return "(no coverage lines)"
    return _render_layout(
        tower, program, theme, width, height, colour, error_layers, error_lines
    )


def _render_layout(
    tower: TowerLayout,
    program: Program,
    theme: Theme,
    width: int,
    height: int,
    colour: bool,
    error_layers: frozenset[str] = frozenset(),
    error_lines: frozenset[str] = frozenset(),
) -> str:
    label_w = 0 if width < 46 else (14 if width < 76 else 20)
    chart_w = max(len(tower.columns) * 2, width - label_w - 1)
    ret_rows = 2 if tower.retentions else 0
    # one reserved row above the tower for the statutory chevron band; the
    # band lives at y > 1.0, which to_row would otherwise map to a negative row
    chev_rows = 1 if tower.chevrons else 0
    tower_rows = max(4, height - ret_rows - 2 - chev_rows)
    carrier_colours = theme.carrier_colours(program.carriers())

    def to_col(x: float) -> int:
        return round(x / tower.width * chart_w)

    def to_row(y: float) -> int:
        """Tower y ∈ [0,1] → grid row; y=1 is the first row under the chevron
        band, y=0 is the zero line."""
        return chev_rows + round((1.0 - y) * tower_rows)

    grid = [
        [_Cell() for _ in range(chart_w)]
        for _ in range(chev_rows + tower_rows + 1 + ret_rows + 1)
    ]
    zero_row = chev_rows + tower_rows

    # dim background where a column exists but has no cover at that height;
    # a column with a validation error tints red — a gap lights up exactly
    # where the missing cover is
    for column in tower.columns:
        broken = column.line_id in error_lines
        bg = ansi256(DANGER) if broken else ansi256(theme.chrome.grid)
        for row in range(chev_rows, zero_row):
            for col in range(to_col(column.x0), to_col(column.x1)):
                grid[row][col] = _Cell(NO_COVER, bg, dim=not broken)

    # participant blocks (unplaced capacity hatched grey)
    for block in tower.participants:
        for rect in block.rects:
            r0, r1 = to_row(rect.y1), to_row(rect.y0)
            r1 = max(r1, r0 + 1)
            c0, c1 = to_col(rect.x0), to_col(rect.x1)
            c1 = max(c1, c0 + 1)
            if block.layer_id in error_layers:
                cell = _Cell(FULL, ansi256(DANGER))
            elif block.carrier is None:
                cell = _Cell(UNPLACED, ansi256(theme.chrome.unplaced))
            else:
                cell = _Cell(FULL, ansi256(carrier_colours[block.carrier]))
            for row in range(max(r0, chev_rows), min(r1, zero_row)):
                for col in range(c0, min(c1, chart_w)):
                    grid[row][col] = _Cell(cell.char, cell.colour, cell.dim)
            _stamp_initial(grid, block.carrier, r0, r1, c0, c1, chev_rows, zero_row)

    # statutory chevron band: the bar's top edge, marking cover that continues
    for rect in tower.chevrons:
        for col in range(to_col(rect.x0), min(to_col(rect.x1), chart_w)):
            for row in range(chev_rows):
                grid[row][col] = _Cell("^", ansi256(theme.chrome.ink))

    # heavy zero line
    for col in range(chart_w):
        grid[zero_row][col] = _Cell(ZERO, ansi256(theme.chrome.zero_line))

    # retention blocks below the zero line, on their own compressed scale
    for ret in tower.retentions:
        fill = ansi256(theme.retention_fill(ret.type))
        for rect in ret.rects:
            depth_frac = min(1.0, -rect.y0 / tower.retention_band)
            rows = max(1, round(depth_frac * ret_rows))
            for row in range(zero_row + 1, min(zero_row + 1 + rows, zero_row + 1 + ret_rows)):
                for col in range(to_col(rect.x0), min(to_col(rect.x1), chart_w)):
                    grid[row][col] = _Cell(RETENTION, fill)

    # column labels
    label_row = zero_row + ret_rows + 1
    for column in tower.columns:
        c0, c1 = to_col(column.x0), to_col(column.x1)
        text = column.label[: max(1, c1 - c0)]
        start = c0 + max(0, (c1 - c0 - len(text)) // 2)
        for i, ch in enumerate(text):
            if start + i < chart_w:
                grid[label_row][start + i] = _Cell(ch, ansi256(theme.chrome.muted))

    lines = [_row_to_text(row_cells, colour) for row_cells in grid]
    if label_w:
        _attach_labels(lines, tower, label_w, zero_row, ret_rows, chev_rows)
    return "\n".join(lines)


def _stamp_initial(
    grid: list[list[_Cell]],
    carrier: str | None,
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    top_row: int,
    zero_row: int,
) -> None:
    if carrier is None or c1 - c0 < 3:
        return
    row = (max(r0, top_row) + min(r1, zero_row)) // 2
    col = (c0 + c1) // 2
    if top_row <= row < zero_row and 0 <= col < len(grid[0]):
        base = grid[row][col]
        grid[row][col] = _Cell(carrier[0], base.colour, dim=False)


def _row_to_text(cells: list[_Cell], colour: bool) -> str:
    if not colour:
        return "".join(c.char for c in cells).rstrip()
    out: list[str] = []
    current: tuple[int | None, bool] | None = None
    for cell in cells:
        key = (cell.colour, cell.dim)
        if key != current:
            out.append("\x1b[0m")
            if cell.colour is not None:
                out.append(f"\x1b[38;5;{cell.colour}m")
            if cell.dim:
                out.append("\x1b[2m")
            current = key
        out.append(cell.char)
    out.append("\x1b[0m")
    return "".join(out)


def _attach_labels(
    lines: list[str],
    tower: TowerLayout,
    label_w: int,
    zero_row: int,
    ret_rows: int,
    chev_rows: int = 0,
) -> None:
    """Right-gutter labels: layer names at their mid-height, $0 at the rule,
    'Retention' under it, and a not-to-scale caveat."""
    tower_rows = zero_row - chev_rows
    labels: dict[int, str] = {}
    for block in sorted(tower.layers, key=lambda b: -b.attach):
        mid = chev_rows + round((1.0 - (block.y0 + block.y1) / 2) * tower_rows)
        mid = min(max(mid, chev_rows), zero_row - 1)
        while mid in labels and mid + 1 < zero_row:
            mid += 1
        placed = min(100.0, block.signed_bps / 100)
        suffix = "" if block.signed_bps >= 10_000 else f" ({placed:g}%)"
        labels[mid] = f"{block.name}{suffix}"
    labels[zero_row] = "$0"
    if tower.layers:
        top_label = f"top {format_money_compact(tower.ymap.max_dollars)}"
        labels.setdefault(chev_rows, top_label)
    if ret_rows:
        labels[zero_row + 1] = "Retention"
    labels[zero_row + ret_rows + 1] = "(not to scale)" if tower.ymap.gamma != 1.0 else ""
    for row, text in labels.items():
        if row < len(lines) and text:
            lines[row] = f"{lines[row]}  {text[: label_w - 2]}"
