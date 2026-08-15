"""Program browser: every programs/*.json with totals and a validation badge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ...atomicio import backup_path
from ...model import dump_program, load_program
from ...validate import ProgramInvalidError, validate_file
from ..session import EditSession, blank_program, slugify
from ..theme import AMBER, DIM, GREEN, RED, dash, money_text, right, status_text
from ..widgets.modals import (
    ConfirmModal,
    HelpModal,
    ImportFileModal,
    PasteImportModal,
    PromptModal,
    RenderOptions,
    RenderOptionsModal,
)
from .diff import DiffScreen
from .editor import EditorScreen, _opts

if TYPE_CHECKING:
    from ...ingest import DraftProgram


class ProgramBrowser(Screen):
    BINDINGS = [
        ("enter", "open", "Open"),
        ("n", "new", "New"),
        ("c", "clone", "Clone as renewal"),
        ("d", "delete", "Delete"),
        ("r", "render", "Render"),
        ("x", "diff", "Mark/compare"),
        ("t", "render_options", "Options"),
        ("i", "import_file", "Import"),
        ("p", "paste_import", "Paste"),
        ("w", "template", "Template"),
        ("q", "quit", "Quit"),
        ("question_mark", "help", "Help"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    CSS = """
    #programs { height: 1fr; }
    #empty {
        display: none; height: 1fr; padding: 2 4;
        content-align: center middle; text-align: center;
    }
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
        yield Static(id="empty")
        yield Static(
            f"[{DIM}][b]j/k[/b] move · [b]enter[/b] open · [b]n[/b] new · "
            "[b]c[/b] clone · [b]d[/b] delete · [b]r[/b] render · "
            "[b]x[/b] mark/compare · [b]t[/b] options · [b]i[/b] import · "
            "[b]p[/b] paste · [b]w[/b] template · [b]?[/b] help[/]",
            id="hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#programs", DataTable)
        table.add_columns(
            "", "File", "Insured", "Program", "Placement", "Period",
            right("Total limit"), right("Total premium"),
        )
        self.reload()
        table.focus()  # j/k works immediately

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
                badge = Text(f"✗ {len(diags.errors)}", style=f"bold {RED}")
            elif diags.warnings:
                badge = Text(f"⚠ {len(diags.warnings)}", style=AMBER)
            else:
                badge = Text("✓", style=GREEN)
            if program is None:
                table.add_row(
                    badge, path.name, Text("(unreadable)", style=RED),
                    dash(), dash(), dash(), dash(), dash(), key=str(path),
                )
                continue
            period = f"{program.period.start.year}–{program.period.end.year}"
            table.add_row(
                badge,
                path.name,
                program.insured,
                program.program,
                status_text(program.placement.value),
                Text(period, style=DIM),
                money_text(program.total_limit()),
                money_text(program.total_premium()),
                key=str(path),
            )
        empty = self.query_one("#empty", Static)
        empty.display = table.row_count == 0
        table.display = not empty.display
        if empty.display:
            empty.update(
                f"No programs in {self.programs_dir}/ yet.\n\n"
                f"[{DIM}]Press [b]n[/b] to create your first program, or drop "
                "program *.json files into that folder and they appear here.[/]"
            )
        elif not table.has_focus:
            table.focus()

    def _selected_path(self) -> Path | None:
        table = self.query_one("#programs", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return Path(row_key.value) if row_key and row_key.value else None

    # -- actions ---------------------------------------------------------------

    def action_cursor_down(self) -> None:
        self.query_one("#programs", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#programs", DataTable).action_cursor_up()

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

    def _notify_write_failure(self, target: Path, exc: OSError) -> None:
        """Same position the editor takes in `_notify_save_failure`: a write
        that cannot reach disk is a message, not a crash. Nothing here is
        irreplaceable — the browser holds no session — but killing the TUI
        on a full disk is exactly the failure this codebase refuses."""
        reason = exc.strerror or str(exc)
        self.notify(
            f"could not write {target}: {reason} — nothing was written",
            severity="error",
            timeout=10,
        )

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
            try:
                dump_program(clone, target)
            except OSError as exc:
                self._notify_write_failure(target, exc)
                return
            self.reload()
            self.notify(f"created {target.name} (proposed, period bumped)")

        self.app.push_screen(
            PromptModal("New file name for the renewal:", default=default), on_name
        )

    def action_template(self) -> None:
        def on_name(name: str | None) -> None:
            if not name:
                return
            target = Path(name if name.lower().endswith(".xlsx") else f"{name}.xlsx")

            def write_it() -> None:
                from ...ingest_template import write_template

                self.notify(f"template written: {write_template(target)}")

            if target.exists():  # a filled template is keyed-in user data — confirm first

                def on_confirm(confirmed: bool | None) -> None:
                    if confirmed:
                        write_it()

                self.app.push_screen(
                    ConfirmModal(f"{target.name} exists — overwrite?", yes_label="Overwrite"),
                    on_confirm,
                )
                return
            write_it()

        self.app.push_screen(
            PromptModal("Template file name:", default="template.xlsx"), on_name
        )

    def action_import_file(self) -> None:
        def on_fields(fields: dict | None) -> None:
            if not fields or not fields["path"].strip():
                return
            from ...ingest import import_schedule

            try:
                draft = import_schedule(
                    Path(fields["path"]).expanduser(),
                    insured=fields["insured"],
                    program=fields["program"],
                    inception=fields["inception"],
                    expiry=fields["expiry"],
                )
            except Exception as exc:
                self.notify(f"import failed: {exc}", severity="error")
                return
            self._finish_import(draft)

        self.app.push_screen(ImportFileModal(), on_fields)

    def action_paste_import(self) -> None:
        def on_fields(fields: dict | None) -> None:
            if not fields or not fields["text"].strip():
                return
            from ...ingest import import_schedule

            try:
                draft = import_schedule(
                    None,
                    text=fields["text"],
                    insured=fields["insured"],
                    program=fields["program"],
                    inception=fields["inception"],
                    expiry=fields["expiry"],
                )
            except Exception as exc:
                self.notify(f"import failed: {exc}", severity="error")
                return
            self._finish_import(draft)

        self.app.push_screen(PasteImportModal(), on_fields)

    def _finish_import(self, draft: DraftProgram) -> None:
        # diagnostics are surfaced after the build, not before: to_program()
        # is where validation runs, and its warnings used to be discarded
        program = None
        try:
            program = draft.to_program()
        except ProgramInvalidError:
            pass
        for diag in draft.diagnostics.items:
            self.notify(
                str(diag), severity="error" if diag.severity == "error" else "warning"
            )
        if program is None or draft.diagnostics.errors:
            n = len(draft.diagnostics.errors)
            self.notify(
                f"import failed: {n} error{'s' if n != 1 else ''} in the "
                f"schedule — nothing written",
                severity="error",
                timeout=10,
            )
            return
        out = self.programs_dir / (
            f"{slugify(program.insured)}-{slugify(program.program)}.json"
        )
        if out.exists():  # program files are the source of truth — never clobber
            self.notify(f"{out.name} exists — not overwriting", severity="error")
            return
        try:
            self.programs_dir.mkdir(parents=True, exist_ok=True)
            dump_program(program, out)
        except OSError as exc:
            self._notify_write_failure(out, exc)
            return
        self.reload()
        self.notify(f"imported {out.name}")
        self.app.push_screen(
            EditorScreen(EditSession.open(out), theme_path=self.theme_path)
        )

    def action_delete(self) -> None:
        path = self._selected_path()
        if path is None:
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed and path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    reason = exc.strerror or str(exc)
                    self.notify(
                        f"could not delete {path.name}: {reason} — the file "
                        f"is still there",
                        severity="error",
                        timeout=10,
                    )
                    return
                try:
                    backup_path(path).unlink(missing_ok=True)
                except OSError:
                    pass  # hidden housekeeping; the program file is gone
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

  j/k        move up and down the list
  enter      open the selected program in the editor
  n          new blank program
  c          clone as next renewal (period bumped,
             marked proposed)
  d          delete (confirms first)
  r          render the selected program
  x          mark two programs to compare (diff)
  t          render options (theme, totals,
             premiums, cell extras)
  i          import a schedule file (xlsx/csv/text)
  p          paste a schedule as text
  w          write a blank import template (.xlsx)
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
