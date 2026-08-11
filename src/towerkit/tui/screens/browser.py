"""Program browser: every programs/*.json with totals and a validation badge."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ...model import dump_program, load_program
from ...money import format_money
from ...validate import validate_file
from ..session import EditSession, blank_program
from ..widgets.modals import (
    ConfirmModal,
    HelpModal,
    PromptModal,
    RenderOptions,
    RenderOptionsModal,
)
from .diff import DiffScreen
from .editor import EditorScreen, _opts


class ProgramBrowser(Screen):
    BINDINGS = [
        ("enter", "open", "Open"),
        ("n", "new", "New"),
        ("c", "clone", "Clone as renewal"),
        ("d", "delete", "Delete"),
        ("r", "render", "Render"),
        ("x", "diff", "Mark/compare"),
        ("t", "render_options", "Options"),
        ("q", "quit", "Quit"),
        ("question_mark", "help", "Help"),
    ]

    CSS = """
    #programs { height: 1fr; }
    #hint { height: 1; color: $text-muted; padding: 0 1; }
    """

    def __init__(
        self, programs_dir: Path | None = None, theme_path: Path | None = None
    ) -> None:
        super().__init__()
        self.programs_dir = programs_dir or Path("programs")
        self.theme_path = theme_path
        self.diff_mark: Path | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield DataTable(id="programs", cursor_type="row")
        yield Static(
            "enter open · n new · c clone as renewal · d delete · r render · "
            "x mark two programs to compare · t render options",
            id="hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#programs", DataTable)
        table.add_columns(
            "", "File", "Insured", "Program", "Placement", "Period",
            "Total limit", "Total premium",
        )
        self.reload()
        table.focus()

    def reload(self) -> None:
        table = self.query_one("#programs", DataTable)
        table.clear()
        self.programs_dir.mkdir(parents=True, exist_ok=True)
        paths = sorted(self.programs_dir.glob("*.json")) + sorted(
            (self.programs_dir / "private").glob("*.json")
        )
        for path in paths:
            program, diags = validate_file(path)
            if not diags.ok:
                badge = f"✗ {len(diags.errors)}"
            elif diags.warnings:
                badge = f"⚠ {len(diags.warnings)}"
            else:
                badge = "✓"
            if program is None:
                table.add_row(badge, path.name, "(unreadable)", "", "", "", "", "", key=str(path))
                continue
            period = f"{program.period.start.year}–{program.period.end.year}"
            table.add_row(
                badge,
                path.name,
                program.insured,
                program.program,
                program.placement.value,
                period,
                format_money(program.total_limit()),
                format_money(program.total_premium()),
                key=str(path),
            )

    def _selected_path(self) -> Path | None:
        table = self.query_one("#programs", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return Path(row_key.value) if row_key and row_key.value else None

    # -- actions ---------------------------------------------------------------

    def action_open(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        try:
            session = EditSession.open(path)
        except Exception as exc:
            self.notify(f"cannot open {path.name}: {exc}", severity="error")
            return
        self.app.push_screen(EditorScreen(session, theme_path=self.theme_path))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

    def action_new(self) -> None:
        session = EditSession(blank_program(), path=None)
        self.app.push_screen(EditorScreen(session, theme_path=self.theme_path))

    def action_clone(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        try:
            program = load_program(path)
        except Exception as exc:
            self.notify(f"cannot read {path.name}: {exc}", severity="error")
            return
        clone = program.clone_as_renewal()
        default = _bump_stem(path.stem)

        def on_name(name: str | None) -> None:
            if not name:
                return
            target = self.programs_dir / (name if name.endswith(".json") else f"{name}.json")
            if target.exists():
                self.notify(f"{target.name} already exists", severity="error")
                return
            dump_program(clone, target)
            self.reload()
            self.notify(f"created {target.name} (proposed, period bumped)")

        self.app.push_screen(
            PromptModal("New file name for the renewal:", default=default), on_name
        )

    def action_delete(self) -> None:
        path = self._selected_path()
        if path is None:
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed and path.exists():
                path.unlink()
                self.reload()
                self.notify(f"deleted {path.name}")

        self.app.push_screen(
            ConfirmModal(f"Delete {path.name}? This cannot be undone.", yes_label="Delete"),
            on_confirm,
        )

    def action_render(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        program, diags = validate_file(path)
        if program is None or not diags.ok:
            self.notify("validation errors — fix before rendering", severity="error")
            return
        from ...render.mpl_program import render_program
        from ...theme import load_theme

        written = render_program(
            program, load_theme(self.theme_path), Path("dist"), path.stem, ["svg", "png"],
            show_totals=_opts(self).show_totals, show_premiums=_opts(self).show_premiums,
            cell_premiums=getattr(_opts(self), "cell_premiums", False),
            cell_dates=getattr(_opts(self), "cell_dates", False),
        )
        self.notify("rendered: " + ", ".join(str(p) for p in written))

    def action_diff(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        if self.diff_mark is None or self.diff_mark == path:
            self.diff_mark = path
            self.notify(f"marked {path.name} — press x on the other program")
            return
        try:
            expiring = load_program(self.diff_mark)
            proposed = load_program(path)
        except Exception as exc:
            self.notify(f"cannot compare: {exc}", severity="error")
            self.diff_mark = None
            return
        self.app.push_screen(
            DiffScreen(expiring, proposed, self.diff_mark.name, path.name)
        )
        self.diff_mark = None

    def action_render_options(self) -> None:
        def on_choice(options: RenderOptions | None) -> None:
            if options is None:
                return
            self.theme_path = Path(options.theme) if options.theme else None
            _opts(self).show_totals = options.show_totals
            _opts(self).show_premiums = options.show_premiums
            _opts(self).cell_premiums = options.cell_premiums
            _opts(self).cell_dates = options.cell_dates
            from ...theme import load_theme

            self.notify(f"theme: {load_theme(self.theme_path).name}")

        self.app.push_screen(
            RenderOptionsModal(
                self.theme_path, _opts(self).show_totals, _opts(self).show_premiums,
                getattr(_opts(self), "cell_premiums", False),
                getattr(_opts(self), "cell_dates", False),
            ),
            on_choice,
        )

    def action_help(self) -> None:
        self.app.push_screen(
            HelpModal(
                """towerkit browser — keys

  enter      open the selected program in the editor
  n          new blank program
  c          clone as next renewal (bumps period, marks proposed)
  d          delete (confirms first)
  r          render the selected program
  x          mark two programs to compare (renewal diff)
  t          render options (theme, totals, premiums, cell extras)
  q          quit        ?  this help
"""
            )
        )

    def action_quit(self) -> None:
        self.app.exit()

    def on_screen_resume(self) -> None:
        self.reload()


def _bump_stem(stem: str) -> str:
    """atomic-2026 → atomic-2027; otherwise append -renewal."""
    for i in range(len(stem) - 4, -1, -1):
        chunk = stem[i : i + 4]
        if chunk.isdigit() and chunk.startswith("2"):
            return stem[:i] + str(int(chunk) + 1) + stem[i + 4 :]
    return f"{stem}-renewal"
