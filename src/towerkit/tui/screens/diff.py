"""Renewal diff: the generated change table and headline metrics. Read-only."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ...compare import LAPSED, NEW, compare_programs
from ...model import Program
from ...money import format_money, format_money_compact
from ..theme import AMBER, DIM, GREEN, RED, right

# diff convention: green + added, red − removed, amber ~ changed — each state
# carries its glyph and status word so it survives monochrome terminals
_STATUS_STYLES = {NEW: (f"+ {NEW}", GREEN), LAPSED: (f"− {LAPSED}", RED)}


class DiffScreen(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("q", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    CSS = """
    #headline { height: 3; padding: 1 1 0 1; }
    #delta-table { height: 1fr; }
    #diff-hint { height: 1; color: $text-muted; padding: 0 1; }
    """

    def __init__(
        self, expiring: Program, proposed: Program, old_name: str, new_name: str
    ) -> None:
        super().__init__()
        self.expiring = expiring
        self.proposed = proposed
        self.old_name = old_name
        self.new_name = new_name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="headline")
        yield DataTable(id="delta-table", cursor_type="row")
        yield Static(
            f"[{DIM}][b]+[/b] new · [b]−[/b] lapsed · [b]~[/b] changed · "
            "[b]j/k[/b] move · [b]esc[/b]/[b]q[/b] back[/]",
            id="diff-hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        delta = compare_programs(self.expiring, self.proposed)
        pct = delta.premium_delta_pct
        pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
        self.query_one("#headline", Static).update(
            f"[b]{self.old_name}[/b] [{DIM}]→[/] [b]{self.new_name}[/b]   "
            f"[{DIM}]Limit[/] {format_money_compact(delta.limit_old)} "
            f"[{DIM}]→[/] {format_money_compact(delta.limit_new)} [{DIM}]·[/] "
            f"[{DIM}]Premium[/] {format_money(delta.premium_old)} "
            f"[{DIM}]→[/] {format_money(delta.premium_new)}"
            f"{_signed_style(pct_text, delta.premium_delta)}"
        )
        table = self.query_one("#delta-table", DataTable)
        table.add_columns(
            "Carrier", "Layer", "Status", "Share", right("Line Δ"), right("Premium Δ")
        )
        for row in delta.rows:
            share = f"{_pct(row.share_old_bps)} → {_pct(row.share_new_bps)}"
            changed = row.line_delta != 0 or row.premium_delta != 0
            label, style = _STATUS_STYLES.get(
                row.status,
                (f"~ {row.status}", AMBER) if changed else (f"= {row.status}", DIM),
            )
            table.add_row(
                row.carrier,
                row.layer_name,
                Text(label, style=style),
                share,
                _signed(row.line_delta),
                _signed(row.premium_delta),
            )

    def action_cursor_down(self) -> None:
        self.query_one("#delta-table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#delta-table", DataTable).action_cursor_up()

    def action_back(self) -> None:
        self.app.pop_screen()


def _pct(bps: int | None) -> str:
    return "—" if bps is None else f"{bps / 100:g}%"


def _signed(value: int) -> Text:
    """Right-aligned signed delta: green +, red −, dim ±0."""
    if value == 0:
        return Text("±0", style=DIM, justify="right")
    sign, style = ("+", GREEN) if value > 0 else ("−", RED)
    return Text(f"{sign}{format_money_compact(abs(value))}", style=style, justify="right")


def _signed_style(text: str, value: int) -> str:
    """The headline's premium-change percentage, colored by direction."""
    if not text:
        return ""
    color = GREEN if value > 0 else RED if value < 0 else DIM
    return f"[{color}]{text}[/]"
