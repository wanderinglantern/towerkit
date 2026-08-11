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
) -> list[Path]:
    delta = compare_programs(expiring, proposed)
    with rc_context(rc_params(theme)):  # type: ignore[arg-type]
        fig = plt.figure(figsize=(16, 11.5))
        try:
            ax_old = fig.add_axes((0.02, 0.44, 0.47, 0.44))
            ax_new = fig.add_axes((0.51, 0.44, 0.47, 0.44))
            draw_tower(ax_old, expiring, theme, gamma=gamma)
            draw_tower(ax_new, proposed, theme, gamma=gamma)
            _tower_caption(fig, 0.255, expiring, theme)
            _tower_caption(fig, 0.745, proposed, theme)
            _headline(fig, expiring, proposed, delta, theme)
            _table(fig, delta, theme)
            chrome = theme.chrome
            caveat = (
                f"Vertical scale compressed (γ = {gamma:g}) — NOT TO SCALE. "
                "Each tower is scaled to its own breakpoints."
                if gamma != 1.0
                else "Vertical scale is linear. Each tower is scaled to its own breakpoints."
            )
            fig.text(0.02, 0.012, caveat, fontsize=8.5, color=chrome.muted)
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
    fig, expiring: Program, proposed: Program, delta: RenewalDelta, theme: Theme
) -> None:
    chrome = theme.chrome
    fig.text(0.02, 0.955, expiring.insured, fontsize=16, weight="bold", color=chrome.ink)
    fig.text(
        0.02, 0.925,
        f"{expiring.program} — renewal comparison",
        fontsize=10.5, color=chrome.muted,
    )
    pct = delta.premium_delta_pct
    pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
    sign = "+" if delta.premium_delta >= 0 else "−"
    metrics = (
        f"Limit {format_money_compact(delta.limit_old)} → "
        f"{format_money_compact(delta.limit_new)} · "
        f"Premium {format_money(delta.premium_old)} → {format_money(delta.premium_new)}   "
        f"Δ {sign}{format_money(abs(delta.premium_delta))}{pct_text}"
    )
    fig.text(0.98, 0.955, metrics, fontsize=11, ha="right", color=chrome.ink)


def _table(fig, delta: RenewalDelta, theme: Theme) -> None:
    chrome = theme.chrome
    top = 0.385
    row_h = 0.0185
    columns = [
        (0.02, "Carrier", "left"),
        (0.20, "Layer", "left"),
        (0.44, "Status", "left"),
        (0.60, "Share", "right"),
        (0.76, "Line Δ", "right"),
        (0.97, "Premium Δ", "right"),
    ]
    for x, title, ha in columns:
        fig.text(x, top, title, fontsize=9, weight="bold", ha=ha, color=chrome.ink)
    fig.lines.clear()

    status_colour = {"NEW": "#2E8540", "RENEWED": chrome.muted, "LAPSED": "#C6373C"}
    rows = delta.rows[:MAX_TABLE_ROWS]
    for i, row in enumerate(rows):
        y = top - (i + 1) * row_h
        share = _share_transition(row)
        line_d = _signed_compact(row.line_delta)
        prem_d = _signed_compact(row.premium_delta)
        values = [
            (0.02, row.carrier, "left", chrome.ink),
            (0.20, row.layer_name, "left", chrome.ink),
            (0.44, row.status, "left", status_colour[row.status]),
            (0.60, share, "right", chrome.ink),
            (0.76, line_d, "right", chrome.ink),
            (0.97, prem_d, "right", chrome.ink),
        ]
        for x, text, ha, colour in values:
            fig.text(x, y, text, fontsize=8.5, ha=ha, color=colour)
    if len(delta.rows) > MAX_TABLE_ROWS:
        y = top - (len(rows) + 1) * row_h
        fig.text(
            0.02, y,
            f"… and {len(delta.rows) - MAX_TABLE_ROWS} further rows "
            "(sorted by |premium Δ|; see towerctl compare output)",
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
