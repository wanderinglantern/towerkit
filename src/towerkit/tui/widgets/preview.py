"""Live ASCII tower preview — the proof that the pure-core split is real."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ...model import Program
from ...render.ascii import render_ascii
from ...theme import Theme
from ..theme import DIM, RULE


class TowerPreview(Static):
    # chrome only: a quiet frame and title around the preview — the ANSI
    # content inside keeps the render theme's colors untouched
    DEFAULT_CSS = f"""
    TowerPreview {{
        width: 1fr; height: 1fr; padding: 0 1;
        border: round {RULE};
        border-title-color: {DIM};
        border-title-align: left;
    }}
    """

    def __init__(self, theme: Theme, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.tower_theme = theme
        self.border_title = "tower preview"

    def show_program(self, program: Program, diagnostics=None) -> None:
        error_layers: frozenset[str] = frozenset()
        error_lines: frozenset[str] = frozenset()
        if diagnostics is not None:
            error_layers = frozenset(
                d.ref[1] for d in diagnostics.errors if d.ref[0] == "layer"
            )
            error_lines = frozenset(
                d.ref[1] for d in diagnostics.errors if d.ref[0] == "line"
            )
        width = max(30, self.size.width - 2) if self.size.width else 60
        height = max(12, self.size.height - 1) if self.size.height else 22
        try:
            art = render_ascii(
                program, self.tower_theme, width=width, height=height, colour=True,
                error_layers=error_layers, error_lines=error_lines,
            )
        except Exception as exc:  # a draft must never crash the preview
            self.update(Text(f"preview unavailable: {exc}", style="dim"))
            return
        self.update(Text.from_ansi(art))
