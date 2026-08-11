"""Renewal diff: the generated change table and headline metrics. Read-only."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ...compare import compare_programs
from ...model import Program
from ...money import format_money, format_money_compact


class DiffScreen(Screen):
    BINDINGS = [("escape", "back", "Back"), ("q", "back", "Back")]

    CSS = """
    #headline { height: 3; padding: 1 1 0 1; }
    #delta-table { height: 1fr; }
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
        yield Footer()

    def on_mount(self) -> None:
        delta = compare_programs(self.expiring, self.proposed)
        pct = delta.premium_delta_pct
        pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
        self.query_one("#headline", Static).update(
            f"{self.old_name} → {self.new_name}   "
            f"Limit {format_money_compact(delta.limit_old)} → "
            f"{format_money_compact(delta.limit_new)} · "
            f"Premium {format_money(delta.premium_old)} → "
            f"{format_money(delta.premium_new)}{pct_text}"
        )
        table = self.query_one("#delta-table", DataTable)
        table.add_columns("Carrier", "Layer", "Status", "Share", "Line Δ", "Premium Δ")
        for row in delta.rows:
            share = (
                f"{_pct(row.share_old_bps)} → {_pct(row.share_new_bps)}"
            )
            table.add_row(
                row.carrier,
                row.layer_name,
                row.status,
                share,
                _signed(row.line_delta),
                _signed(row.premium_delta),
            )

    def action_back(self) -> None:
        self.app.pop_screen()


def _pct(bps: int | None) -> str:
    return "—" if bps is None else f"{bps / 100:g}%"


def _signed(value: int) -> str:
    if value == 0:
        return "±0"
    sign = "+" if value > 0 else "−"
    return f"{sign}{format_money_compact(abs(value))}"
