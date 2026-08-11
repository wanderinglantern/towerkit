"""Single placement → SVG/PDF/PNG.

All geometry comes from layout.py; this module draws rectangles and text.
Label placement measures actual text extents against the target rectangle —
no hardcoded size thresholds.

No y-axis is drawn when gamma != 1: the scale is deliberately non-linear, so
we draw reference lines at real attachment points, dollar labels in the
gutter, and a visible not-to-scale caveat instead of a lying axis.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import rc_context
from matplotlib.axes import Axes
from matplotlib.patches import Rectangle
from matplotlib.patheffects import withStroke

from ..layout import Rect, TowerLayout, build_layout
from ..model import Program
from ..money import format_money, format_money_compact, format_share
from ..scale import DEFAULT_GAMMA
from ..theme import Theme, contrast_text
from .common import rc_params, save_figure

GUTTER_LEFT = 1.35  # data units reserved left of the tower for dollar labels
SUPERSCRIPTS = "¹²³⁴⁵⁶⁷⁸⁹"


def render_program(
    program: Program,
    theme: Theme,
    out_dir: Path | str,
    stem: str,
    formats: list[str] | None = None,
    gamma: float = DEFAULT_GAMMA,
    show_totals: bool = True,
    show_premiums: bool = True,
) -> list[Path]:
    noted = [layer for layer in sorted(program.layers, key=lambda ly: ly.attach) if layer.notes]
    markers = {
        layer.id: SUPERSCRIPTS[i] for i, layer in enumerate(noted[: len(SUPERSCRIPTS)])
    }
    with rc_context(rc_params(theme)):  # type: ignore[arg-type]
        fig = plt.figure(figsize=(13.5, 9.5))
        try:
            # footnote lines need room under the chart
            extra = 0.021 * len(markers)
            ax = fig.add_axes((0.02, 0.06 + extra, 0.96, 0.82 - extra))
            tower = draw_tower(ax, program, theme, gamma=gamma, note_markers=markers)
            _titles(fig, program, theme, show_totals=show_totals, show_premiums=show_premiums)
            _footer(fig, program, theme, tower, markers)
            return save_figure(fig, Path(out_dir), stem, formats or ["svg"])
        finally:
            plt.close(fig)


def draw_tower(
    ax: Axes,
    program: Program,
    theme: Theme,
    gamma: float = DEFAULT_GAMMA,
    note_markers: dict[str, str] | None = None,
) -> TowerLayout:
    """Draw one tower onto an axes. Shared verbatim with the renewal renderer."""
    tower = build_layout(program, gamma=gamma)
    chrome = theme.chrome
    colours = theme.carrier_colours(program.carriers())

    ax.set_axis_off()
    bottom = -tower.retention_band - 0.10
    ax.set_xlim(-GUTTER_LEFT, max(tower.width, 1.0) + 0.1)
    ax.set_ylim(bottom, 1.06)

    # reference lines at real attachment points, dollar labels in the gutter
    for dollars, y in tower.ref_lines:
        ax.axhline(y=y, color=chrome.grid, linewidth=0.7, zorder=0)
        if dollars > 0:
            ax.text(
                -0.12, y + 0.004, format_money_compact(dollars),
                ha="right", va="bottom", fontsize=8.5, color=chrome.muted,
            )

    # participant blocks
    for block in tower.participants:
        if block.carrier is None:
            face, hatch, edge = chrome.background, "////", chrome.unplaced
        else:
            face, hatch, edge = colours[block.carrier], None, chrome.background
        for rect in block.rects:
            ax.add_patch(
                Rectangle(
                    (rect.x0, rect.y0), rect.width, rect.height,
                    facecolor=face, hatch=hatch, edgecolor=edge,
                    linewidth=0.6, zorder=2,
                )
            )
        _participant_label(ax, block, theme, colours)

    # layer outlines and layer labels
    for layer in tower.layers:
        for outline in layer.outlines:
            ax.add_patch(
                Rectangle(
                    (outline.x0, outline.y0), outline.width, outline.height,
                    facecolor="none", edgecolor=chrome.ink, linewidth=1.1, zorder=3,
                )
            )
        terms = f"{format_money_compact(layer.limit)} xs {format_money_compact(layer.attach)}"
        mark = (note_markers or {}).get(layer.layer_id, "")
        _fit_text(
            ax,
            layer.outlines[0],
            [f"{layer.name}{mark} — {terms}", f"{layer.name}{mark}", ""],
            fontsize=9,
            color=chrome.ink,
            weight="bold",
            va_top=True,
            zorder=5,
            halo=chrome.background,
            # a title needs headroom above the centred participant label;
            # in shallow layers the participant text carries the information
            min_height_factor=3.0,
        )

    # heavy zero line
    ax.plot(
        [0, tower.width], [0, 0],
        color=chrome.zero_line, linewidth=2.6, zorder=4, solid_capstyle="butt",
    )

    # retention blocks, typed fills, never a carrier colour
    for ret in tower.retentions:
        fill = theme.retention_fill(ret.type)
        label = f"{ret.type.upper()} {format_money_compact(ret.amount)}"
        if ret.vehicle:
            label = f"{label} ({ret.vehicle})"
        for rect in ret.rects:
            ax.add_patch(
                Rectangle(
                    (rect.x0, rect.y0), rect.width, rect.height,
                    facecolor=fill, edgecolor=chrome.ink, linewidth=0.7, zorder=2,
                )
            )
        _fit_text(
            ax, ret.rects[0],
            [label, f"{ret.type.upper()}", ""],
            fontsize=8, color=chrome.ink, zorder=5,
        )

    # full line names under the retention band (or right under the zero
    # line when the program has no retentions — no dead white band)
    label_y = -tower.retention_band - 0.05
    for column in tower.columns:
        ax.text(
            (column.x0 + column.x1) / 2, label_y,
            "\n".join(textwrap.wrap(column.name, 14)),
            ha="center", va="top", fontsize=9, color=chrome.ink, weight="bold",
        )
    return tower


def _participant_label(ax: Axes, block, theme: Theme, colours: dict[str, str]) -> None:
    rect = max(block.rects, key=lambda r: r.width, default=None)
    if rect is None:
        return
    if block.carrier is None:
        text_colour = theme.chrome.muted
        candidates = [f"{format_share(block.share_bps)} open", ""]
    else:
        face = colours[block.carrier]
        text_colour = contrast_text(face, theme.chrome.background, theme.chrome.ink)
        share = format_share(block.share_bps)
        full = f"{block.carrier} {share}"
        # long carrier names wrap onto several lines before degrading to an
        # initial — "Indian Harbor Insurance Company" must not render as "I"
        candidates = [full]
        for width in (16, 11):
            for text in (full, block.carrier):
                wrapped = "\n".join(textwrap.wrap(text, width))
                if "\n" in wrapped and wrapped not in candidates:
                    candidates.append(wrapped)
        candidates += [block.carrier, block.carrier[:1], ""]
    _fit_text(ax, rect, candidates, fontsize=8.5, color=text_colour, zorder=5)


def _fit_text(
    ax: Axes,
    rect: Rect,
    candidates: list[str],
    fontsize: float,
    color: str,
    weight: str = "normal",
    va_top: bool = False,
    zorder: float = 5,
    halo: str | None = None,
    min_height_factor: float = 1.0,
) -> None:
    """Place the first candidate string that actually fits the rectangle,
    measured with real text extents — no hardcoded size thresholds.
    An empty candidate means give up silently."""
    if va_top:
        x, y, ha, va = rect.x0 + 0.03, rect.y1 - 0.008, "left", "top"
    else:
        x, y = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        ha, va = "center", "center"
    renderer = ax.figure.canvas.get_renderer()  # type: ignore[attr-defined]
    for candidate in candidates:
        if not candidate:
            return
        text = ax.text(
            x, y, candidate, ha=ha, va=va, fontsize=fontsize,
            color=color, weight=weight, zorder=zorder,
        )
        if halo is not None:
            text.set_path_effects([withStroke(linewidth=2.4, foreground=halo)])
        bbox = text.get_window_extent(renderer=renderer)
        target = ax.transData.transform([(rect.x0, rect.y0), (rect.x1, rect.y1)])
        pad = 2.0
        if (
            bbox.width <= (target[1][0] - target[0][0]) - pad
            and bbox.height * min_height_factor <= (target[1][1] - target[0][1]) - pad
        ):
            return
        text.remove()


def _titles(
    fig, program: Program, theme: Theme, show_totals: bool = True, show_premiums: bool = True
) -> None:
    chrome = theme.chrome
    period = f"{program.period.start.isoformat()} – {program.period.end.isoformat()}"
    # brand type rules: serif headings are Regular only, never bold
    fig.text(
        0.04, 0.965, program.insured, fontsize=18,
        color=chrome.ink, family=[chrome.title_font or chrome.font, "DejaVu Serif"],
    )
    fig.text(
        0.04, 0.925,
        f"{program.program} · {period} · {program.placement.value.upper()}",
        fontsize=10.5, color=chrome.muted,
    )
    if show_totals:
        totals = f"Total limit {format_money(program.total_limit())}"
        if show_premiums:
            totals += f" · Total premium {format_money(program.total_premium())}"
        fig.text(0.96, 0.965, totals, fontsize=10.5, ha="right", color=chrome.ink)


def _footer(
    fig,
    program: Program,
    theme: Theme,
    tower: TowerLayout,
    note_markers: dict[str, str] | None = None,
) -> None:
    chrome = theme.chrome
    lines: list[str] = []
    for layer in program.layers:
        mark = (note_markers or {}).get(layer.id)
        if mark:
            lines.append(f"{mark} {layer.name}: {layer.notes}")
    if program.sublimits:
        subs = " · ".join(
            f"{s.name} {format_money_compact(s.amount)} ({'/'.join(s.applies_to).upper()})"
            for s in program.sublimits
        )
        lines.append(f"Sublimits: {subs}")
    for i, line in enumerate(reversed(lines)):
        fig.text(0.04, 0.022 + i * 0.021, line, fontsize=8.5, color=chrome.muted)
