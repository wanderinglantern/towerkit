"""The Textual app. `towerctl edit x.json` opens the editor directly;
`towerctl edit` / `towerctl new` starts in the browser / a blank program."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from .screens.browser import ProgramBrowser
from .screens.editor import EditorScreen
from .session import EditSession, blank_program


class TowerkitApp(App):
    TITLE = "towerkit"

    def __init__(
        self, path: Path | str | None = None, new: bool = False, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._start_path = Path(path) if path else None
        self._start_new = new

    def on_mount(self) -> None:
        if self._start_new:
            self.push_screen(EditorScreen(EditSession(blank_program(), path=None)))
        elif self._start_path is not None:
            self.push_screen(EditorScreen(EditSession.open(self._start_path)))
        else:
            self.push_screen(ProgramBrowser())
