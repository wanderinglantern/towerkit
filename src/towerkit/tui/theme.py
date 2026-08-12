"""The towerkit TUI look — the same warm dark palette as bookkit, so the
pair reads as one product. This themes the CHROME only: the tower preview
and every rendered chart keep the Marsh render theme (themes/marsh.json),
which is authoritative and untouched here.

Color is signal: gold means "you are here", red means invalid/over-signed,
amber means warning/unplaced, green means bound/placed, blue means proposed
or in flight, dim means secondary. Every colored state also carries a glyph
or word so it survives monochrome terminals."""

from __future__ import annotations

from rich.text import Text
from textual.theme import Theme

from ..money import format_money_compact

BG = "#15171c"  # screen
SURFACE = "#1a1d23"  # panes
PANEL = "#232733"  # bars, cards, modals
RULE = "#3a4150"  # borders, separators
FG = "#d5d2c9"  # primary text
DIM = "#8a8577"  # secondary text
GOLD = "#d6b35a"  # focus, selection, accent
RED = "#d57367"  # invalid, over-signed, error
AMBER = "#d9a441"  # warning, partially placed
GREEN = "#84a98c"  # bound, fully placed
BLUE = "#7f9cc4"  # proposed, in flight

TOWERKIT_THEME = Theme(
    name="towerkit",
    primary=GOLD,
    secondary=BLUE,
    accent=GOLD,
    foreground=FG,
    background=BG,
    surface=SURFACE,
    panel=PANEL,
    success=GREEN,
    warning=AMBER,
    error=RED,
    dark=True,
    variables={
        "block-cursor-background": GOLD,
        "block-cursor-foreground": BG,
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": RULE,
        "block-cursor-blurred-foreground": FG,
        "block-cursor-blurred-text-style": "none",
        "footer-background": PANEL,
        "footer-key-foreground": GOLD,
        "footer-description-foreground": DIM,
        "input-selection-background": f"{GOLD} 35%",
    },
)

STATUS_STYLES: dict[str, str] = {
    "bound": GREEN,
    "proposed": BLUE,
}


def status_text(status: str) -> Text:
    return Text(status, style=STATUS_STYLES.get(status, FG))


def money_text(dollars: int | None) -> Text:
    """Right-aligned compact dollars — towerkit money is WHOLE DOLLARS."""
    if not dollars:
        return Text("—", style=DIM, justify="right")
    return Text(format_money_compact(dollars), justify="right")


def dash() -> Text:
    return Text("—", style=DIM)


def right(label: str) -> Text:
    """A right-aligned column header, to sit over numeric columns."""
    return Text(label, justify="right")


def severity_text(message: str, severity: str) -> Text:
    """Validation strip lines: '◆' red for errors, '△' amber for warnings."""
    if severity == "error":
        return Text(f"◆ {message}", style=f"bold {RED}")
    if severity == "warning":
        return Text(f"△ {message}", style=AMBER)
    return Text(message, style=DIM)
