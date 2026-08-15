"""The Textual app. `towerctl edit x.json` opens the editor directly;
`towerctl edit` / `towerctl new` starts in the browser / a blank program."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from .screens.browser import ProgramBrowser
from .screens.editor import EditorScreen
from .session import EditSession, blank_program
from .theme import TOWERKIT_THEME
from .widgets.sheet import SheetTable


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

    async def action_quit(self) -> None:
        """ctrl+q must not be a shortcut past the unsaved-changes prompt.

        Textual binds it straight to `App.exit`, so a whole session of tower
        edits died on one keypress — and the built-in ctrl+c toast actively
        advertises it ("Press ctrl+q to quit the app"). Routing it into the
        editor's own esc handler makes both keys mean the same thing, and
        makes that toast honest.

        The binding is `priority=True` at App level, so it fires from every
        screen, modals included. Testing `self.screen` therefore missed the
        editor whenever anything sat on top of it — including the
        ExitChoiceModal this very handler raises, so a double-tap on the
        quit key (the single likeliest input at a "quit?" prompt) took the
        session with it. Search the whole stack instead.

        While a question is open over the editor — a modal, or an open
        sheet cell editor — ctrl+q is IGNORED rather than answered for the
        user. Dismissing a StaleFileModal or a half-typed cell to raise a
        different prompt loses the answer in progress and is its own small
        data loss; the user is already looking at a prompt, so the cost of
        ignoring is one keypress. Nothing is destroyed either way.

        Drain BEFORE checking dirty, same reason as `action_back`: text
        sitting in a focused Input has not reached the model yet, so
        `dirty` cannot see it. Checking dirty first would let ctrl+q skip
        the prompt for exactly the typed-but-uncommitted case this task
        exists to close.
        """
        screen = next(
            (s for s in reversed(self.screen_stack) if isinstance(s, EditorScreen)),
            None,
        )
        if screen is not None:
            if screen is not self.screen:
                self.notify(
                    "answer the open dialog first — ctrl+q is ignored while "
                    "it is up; nothing was lost",
                    severity="warning",
                )
                return
            if any(sheet.editing for sheet in screen.query(SheetTable)):
                # The cell editor holds text the model has not seen (its
                # commit is a posted message, so no synchronous drain can
                # collect it). Exiting here dropped it silently; enter
                # commits it, esc abandons it, and ctrl+q now waits.
                self.notify(
                    "finish the cell edit first — enter saves it, esc "
                    "cancels it",
                    severity="warning",
                )
                return
            screen._drain_focused_input()
            if screen.session.dirty:
                # action_back's layers-sheet-open early return (close the
                # sheet, don't prompt/exit) is reachable from here too.
                # Deliberate: it mirrors what esc already does, costs one
                # extra keypress in a rare state, and duplicating the sheet
                # check here just to skip it would fork exit logic across
                # two methods for no real gain.
                await screen.action_back()
                return
        # A clean session reached by browsing sits 3 screens deep, so
        # routing a clean EditorScreen into action_back would pop back to
        # the browser instead of quitting — draining first and falling
        # through here instead keeps ctrl+q always meaning quit when there
        # is nothing to lose, at any stack depth.
        self.exit()
