"""Renewal comparison: two placements side by side + generated delta table.

Two files and a mode — not a second schema or codebase. Towers are drawn by
the same draw_tower as the single-program chart; the change table comes from
compare.py.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import rc_context

from ..compare import RenewalDelta, compare_programs
from ..model import Program
from ..money import format_money, format_money_compact
from ..scale import DEFAULT_GAMMA
from ..theme import Theme
from .common import rc_params, save_figure
from .mpl_program import draw_tower

MAX_TABLE_ROWS = 18


def render_renewal(
    expiring: Program,
    proposed: Program,
    theme: Theme,
    out_dir: Path | str,
    stem: str,
    formats: list[str] | None = None,
    gamma: float = DEFAULT_GAMMA,
    show_premiums: bool = True,
) -> list[Path]:
    delta = compare_programs(expiring, proposed)
    with rc_context(rc_params(theme)):  # type: ignore[arg-type]
        fig = plt.figure(figsize=(16, 11.5))
        try:
            ax_old = fig.add_axes((0.02, 0.44, 0.47, 0.44))
            ax_new = fig.add_axes((0.51, 0.44, 0.47, 0.44))
            shared: dict[str, None] = {}
            for carrier in [*expiring.carriers(), *proposed.carriers()]:
                shared.setdefault(carrier, None)
            colours = theme.carrier_colours(list(shared))
            draw_tower(ax_old, expiring, theme, gamma=gamma, colours=colours)
            draw_tower(ax_new, proposed, theme, gamma=gamma, colours=colours)
            _tower_caption(fig, 0.255, expiring, theme)
            _tower_caption(fig, 0.745, proposed, theme)
            _headline(fig, expiring, proposed, delta, theme, show_premiums)
            _table(fig, delta, theme, show_premiums)
            return save_figure(fig, Path(out_dir), stem, formats or ["svg"])
        finally:
            plt.close(fig)


def _tower_caption(fig, x: float, program: Program, theme: Theme) -> None:
    fig.text(
        x, 0.895,
        f"{program.placement.value.upper()} · "
        f"{program.period.start.isoformat()} – {program.period.end.isoformat()}",
        fontsize=11, weight="bold", ha="center", color=theme.chrome.ink,
    )


def _headline(
    fig,
    expiring: Program,
    proposed: Program,
    delta: RenewalDelta,
    theme: Theme,
    show_premiums: bool = True,
) -> None:
    chrome = theme.chrome
    fig.text(
        0.02, 0.955, expiring.insured, fontsize=17,
        color=chrome.ink, family=[chrome.title_font or chrome.font, "DejaVu Serif"],
    )
    fig.text(
        0.02, 0.925,
        f"{expiring.program} — renewal comparison",
        fontsize=10.5, color=chrome.muted,
    )
    metrics = (
        f"Limit {format_money_compact(delta.limit_old)} → "
        f"{format_money_compact(delta.limit_new)}"
    )
    if show_premiums:
        pct = delta.premium_delta_pct
        pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
        sign = "+" if delta.premium_delta >= 0 else "−"
        metrics += (
            f" · Premium {format_money(delta.premium_old)} → "
            f"{format_money(delta.premium_new)}   "
            f"Δ {sign}{format_money(abs(delta.premium_delta))}{pct_text}"
        )
    fig.text(0.98, 0.955, metrics, fontsize=11, ha="right", color=chrome.ink)


def _table(fig, delta: RenewalDelta, theme: Theme, show_premiums: bool = True) -> None:
    chrome = theme.chrome
    top = 0.385
    row_h = 0.0185
    if show_premiums:
        columns = [
            (0.02, "Carrier", "left"),
            (0.20, "Layer", "left"),
            (0.44, "Status", "left"),
            (0.60, "Share", "right"),
            (0.76, "Line Δ", "right"),
            (0.97, "Premium Δ", "right"),
        ]
    else:
        columns = [
            (0.02, "Carrier", "left"),
            (0.24, "Layer", "left"),
            (0.52, "Status", "left"),
            (0.72, "Share", "right"),
            (0.97, "Line Δ", "right"),
        ]
    for x, title, ha in columns:
        fig.text(x, top, title, fontsize=9, weight="bold", ha=ha, color=chrome.ink)
    fig.lines.clear()

    # per the brand accessibility matrix: status text on white uses
    # Green 1000 for success; danger crimson is shared
    status_colour = {"NEW": "#2F7500", "RENEWED": chrome.muted, "LAPSED": "#C53532"}
    table_rows = list(delta.rows)
    if not show_premiums:
        # premium impact is hidden, so order by structural impact instead
        table_rows.sort(key=lambda r: -abs(r.line_delta))
    rows = table_rows[:MAX_TABLE_ROWS]
    for i, row in enumerate(rows):
        y = top - (i + 1) * row_h
        share = _share_transition(row)
        line_d = _signed_compact(row.line_delta)
        if show_premiums:
            values = [
                (0.02, row.carrier, "left", chrome.ink),
                (0.20, row.layer_name, "left", chrome.ink),
                (0.44, row.status, "left", status_colour[row.status]),
                (0.60, share, "right", chrome.ink),
                (0.76, line_d, "right", chrome.ink),
                (0.97, _signed_compact(row.premium_delta), "right", chrome.ink),
            ]
        else:
            values = [
                (0.02, row.carrier, "left", chrome.ink),
                (0.24, row.layer_name, "left", chrome.ink),
                (0.52, row.status, "left", status_colour[row.status]),
                (0.72, share, "right", chrome.ink),
                (0.97, line_d, "right", chrome.ink),
            ]
        for x, text, ha, colour in values:
            fig.text(x, y, text, fontsize=8.5, ha=ha, color=colour)
    if len(delta.rows) > MAX_TABLE_ROWS:
        y = top - (len(rows) + 1) * row_h
        order = "|premium Δ|" if show_premiums else "|line Δ|"
        fig.text(
            0.02, y,
            f"… and {len(delta.rows) - MAX_TABLE_ROWS} further rows "
            f"(sorted by {order})",
            fontsize=8, color=chrome.muted,
        )


def _share_transition(row) -> str:
    def pct(bps: int | None) -> str:
        return "—" if bps is None else f"{bps / 100:g}%"

    return f"{pct(row.share_old_bps)} → {pct(row.share_new_bps)}"


def _signed_compact(value: int) -> str:
    if value == 0:
        return "±0"
    sign = "+" if value > 0 else "−"
    return f"{sign}{format_money_compact(abs(value))}"
