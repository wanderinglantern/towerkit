"""The Textual app. `towerctl edit x.json` opens the editor directly;
`towerctl edit` / `towerctl new` starts in the browser / a blank program."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from .screens.browser import ProgramBrowser
from .screens.editor import EditorScreen
from .session import EditSession, blank_program
from .theme import TOWERKIT_THEME


class TowerkitApp(App):
    TITLE = "towerkit"

    def __init__(
        self,
        path: Path | str | None = None,
        new: bool = False,
        theme_path: Path | str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._start_path = Path(path) if path else None
        self._start_new = new
        self.theme_path = Path(theme_path) if theme_path else None
        self.show_totals = True
        self.show_premiums = True
        self.cell_premiums = False
        self.cell_dates = False
        self.soi_schematic = False

    def on_mount(self) -> None:
        # chrome only — rendered charts and the tower preview keep the Marsh
        # render theme from themes/, which this must never touch
        self.register_theme(TOWERKIT_THEME)
        self.theme = "towerkit"
        if self._start_new:
            self.push_screen(
                EditorScreen(EditSession(blank_program(), path=None), theme_path=self.theme_path)
            )
        elif self._start_path is not None:
            self.push_screen(
                EditorScreen(EditSession.open(self._start_path), theme_path=self.theme_path)
            )
        else:
            self.push_screen(ProgramBrowser(theme_path=self.theme_path))
