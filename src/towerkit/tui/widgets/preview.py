"""Live ASCII tower preview — the proof that the pure-core split is real."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ...model import Program
from ...render.ascii import render_ascii
from ...theme import Theme


class TowerPreview(Static):
    DEFAULT_CSS = """
    TowerPreview { width: 1fr; height: 1fr; padding: 0 1; }
    """

    def __init__(self, theme: Theme, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.tower_theme = theme

    def show_program(self, program: Program) -> None:
        width = max(30, self.size.width - 2) if self.size.width else 60
        height = max(12, self.size.height - 1) if self.size.height else 22
        try:
            art = render_ascii(
                program, self.tower_theme, width=width, height=height, colour=True
            )
        except Exception as exc:  # a draft must never crash the preview
            self.update(Text(f"preview unavailable: {exc}", style="dim"))
            return
        self.update(Text.from_ansi(art))
