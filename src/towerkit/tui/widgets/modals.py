"""Small modal dialogs: confirm, text prompt, render options."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, TextArea
from textual.widgets.option_list import Option


class ConfirmModal(ModalScreen[bool]):
    """Yes/no question. Dismisses with True only on explicit confirmation."""

    BINDINGS = [("escape", "dismiss(False)", "Cancel")]

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > VerticalScroll {
        width: 60; height: auto; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    ConfirmModal .modal-hint { color: $text-muted; margin-top: 1; }
    ConfirmModal Horizontal { height: auto; align-horizontal: right; }
    ConfirmModal Button { margin-left: 2; }
    """

    def __init__(self, question: str, yes_label: str = "Yes", no_label: str = "Cancel") -> None:
        super().__init__()
        self.question = question
        self.yes_label = yes_label
        self.no_label = no_label

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.question)
            yield Label(
                "[b]tab[/b] switch · [b]enter[/b] choose · [b]esc[/b] cancel",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button(self.no_label, id="no")
                yield Button(self.yes_label, id="yes", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()  # the safe choice is one enter away

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class PromptModal(ModalScreen[str | None]):
    """One-line text prompt; None on cancel."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PromptModal { align: center middle; }
    PromptModal > VerticalScroll {
        width: 60; height: auto; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    PromptModal .field-label { color: $text-muted; }
    PromptModal .modal-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, label: str, default: str = "") -> None:
        super().__init__()
        self.label = label
        self.default = default

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.label, classes="field-label")
            yield Input(value=self.default, id="prompt")
            yield Label("[b]enter[/b] save · [b]esc[/b] cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # enter is the only save gesture here (no button), so Submitted is
        # the drain point — event.value IS the input's current text
        self.dismiss(event.value.strip() or None)


@dataclass(frozen=True)
class RenderOptions:
    """What the render menu controls; the CLI flags mirror these."""

    theme: str  # "" = built-in default, otherwise a theme file path
    show_totals: bool
    show_premiums: bool
    cell_premiums: bool
    cell_dates: bool
    soi_schematic: bool = False


class RenderOptionsModal(ModalScreen[RenderOptions | None]):
    """Theme selection plus the totals/premiums toggles. None = cancelled."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    RenderOptionsModal { align: center middle; }
    RenderOptionsModal > VerticalScroll {
        width: 56; height: auto; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    RenderOptionsModal OptionList { max-height: 8; }
    RenderOptionsModal .field-label { color: $text-muted; }
    RenderOptionsModal .modal-hint { color: $text-muted; margin-top: 1; }
    RenderOptionsModal Horizontal { height: auto; align-horizontal: right; }
    RenderOptionsModal Button { margin-left: 2; }
    """

    def __init__(
        self,
        current_theme: Path | None,
        show_totals: bool,
        show_premiums: bool,
        cell_premiums: bool = False,
        cell_dates: bool = False,
        soi_schematic: bool = False,
        themes_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.current_theme = current_theme
        self.show_totals = show_totals
        self.show_premiums = show_premiums
        self.cell_premiums = cell_premiums
        self.cell_dates = cell_dates
        self.soi_schematic = soi_schematic
        self.themes_dir = themes_dir

    def compose(self) -> ComposeResult:
        from ...theme import available_themes

        options = [Option(self._label(None, "default (built-in)"), id="")]
        for path in available_themes(self.themes_dir):
            name = path.stem
            try:
                name = json.loads(path.read_text(encoding="utf-8")).get("name", name)
            except (OSError, json.JSONDecodeError):
                pass
            options.append(Option(self._label(path, f"{name} — {path}"), id=str(path)))
        with VerticalScroll():
            yield Label("Theme (preview and renders):", classes="field-label")
            yield OptionList(*options, id="themes")
            yield Checkbox("Show totals in the header", self.show_totals, id="opt-totals")
            yield Checkbox(
                "Show premiums (uncheck for hypotheticals)",
                self.show_premiums,
                id="opt-premiums",
            )
            yield Checkbox(
                "Premium inside each carrier cell",
                self.cell_premiums,
                id="opt-cell-premiums",
            )
            yield Checkbox(
                "Policy term inside each cell",
                self.cell_dates,
                id="opt-cell-dates",
            )
            yield Checkbox(
                "Include schematic worksheet in SOI export",
                self.soi_schematic,
                id="opt-soi-schematic",
            )
            yield Label("[b]enter[/b] apply · [b]esc[/b] cancel", classes="modal-hint")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def _label(self, path: Path | None, text: str) -> str:
        mark = "● " if path == self.current_theme else "  "
        return f"{mark}{text}"

    def on_mount(self) -> None:
        themes = self.query_one("#themes", OptionList)
        current = str(self.current_theme) if self.current_theme else ""
        for index in range(themes.option_count):
            if themes.get_option_at_index(index).id == current:
                themes.highlighted = index
                break
        themes.focus()

    def _result(self) -> RenderOptions:
        themes = self.query_one("#themes", OptionList)
        index = themes.highlighted if themes.highlighted is not None else 0
        theme_id = themes.get_option_at_index(index).id or ""
        return RenderOptions(
            theme=theme_id,
            show_totals=self.query_one("#opt-totals", Checkbox).value,
            show_premiums=self.query_one("#opt-premiums", Checkbox).value,
            cell_premiums=self.query_one("#opt-cell-premiums", Checkbox).value,
            cell_dates=self.query_one("#opt-cell-dates", Checkbox).value,
            soi_schematic=self.query_one("#opt-soi-schematic", Checkbox).value,
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._result())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.dismiss(self._result())
        else:
            self.dismiss(None)


class HelpModal(ModalScreen[None]):
    """Key reference, reachable with '?' — the durable home for shortcuts."""

    BINDINGS = [("escape", "dismiss(None)", "Close"), ("question_mark", "dismiss(None)", "Close")]

    DEFAULT_CSS = """
    HelpModal { align: center middle; }
    HelpModal > VerticalScroll {
        width: 62; height: auto; max-height: 85%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    HelpModal .modal-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.help_text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.help_text)
            yield Label("[b]esc[/b] or [b]?[/b] close", classes="modal-hint")


class ExitChoiceModal(ModalScreen[str]):
    """Dirty-exit choice: save and leave, discard, or keep editing.
    Dismisses with 'save' | 'discard' | 'keep'."""

    BINDINGS = [
        ("s", "dismiss('save')", "Save & exit"),
        ("d", "dismiss('discard')", "Discard"),
        ("escape", "dismiss('keep')", "Keep editing"),
    ]

    DEFAULT_CSS = """
    ExitChoiceModal { align: center middle; }
    ExitChoiceModal > VerticalScroll {
        width: 64; height: auto; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    ExitChoiceModal .modal-hint { color: $text-muted; margin-top: 1; }
    ExitChoiceModal Horizontal { height: auto; align-horizontal: right; }
    ExitChoiceModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Unsaved changes.")
            yield Label(
                "[b]s[/b] save & exit · [b]d[/b] discard · [b]esc[/b] keep editing",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button("Keep editing", id="keep")
                yield Button("Discard", id="discard", variant="warning")
                yield Button("Save & exit", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#save", Button).focus()  # enter takes the primary path

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "keep")


class StaleFileModal(ModalScreen[str]):
    """The file moved under an open editor. Dismisses with
    'reload' | 'overwrite' | 'keep'."""

    BINDINGS = [
        ("r", "dismiss('reload')", "Reload"),
        ("o", "dismiss('overwrite')", "Overwrite"),
        ("escape", "dismiss('keep')", "Keep editing"),
    ]

    DEFAULT_CSS = """
    StaleFileModal { align: center middle; }
    StaleFileModal > VerticalScroll {
        width: 68; height: auto; max-height: 80%; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    StaleFileModal .modal-hint { color: $text-muted; margin-top: 1; }
    StaleFileModal Horizontal { height: auto; align-horizontal: right; }
    StaleFileModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("This file changed on disk since you opened it.")
            yield Label(
                "Reload takes the file and DISCARDS your edits. "
                "Overwrite keeps your edits and discards theirs.",
                classes="modal-hint",
            )
            yield Label(
                "[b]r[/b] reload · [b]o[/b] overwrite · [b]esc[/b] keep editing",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button("Keep editing", id="keep")
                yield Button("Overwrite", id="overwrite", variant="warning")
                yield Button("Reload", id="reload", variant="primary")

    def on_mount(self) -> None:
        # Reload and Overwrite both destroy someone's work (local edits or the
        # other writer's), so an unread Enter must not land on either — only
        # "keep editing" loses nothing.
        self.query_one("#keep", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "keep")


class SendLineModal(ModalScreen[tuple[Path, bool] | None]):
    """Pick a target program file and copy/move mode for a line transfer."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    SendLineModal { align: center middle; }
    SendLineModal > Vertical {
        width: 70; height: auto; max-height: 24; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    SendLineModal OptionList { height: auto; max-height: 12; }
    SendLineModal .field-label { color: $text-muted; }
    SendLineModal .modal-hint { color: $text-muted; margin-top: 1; }
    SendLineModal Horizontal { height: auto; align-horizontal: right; }
    SendLineModal Button { margin-left: 2; }
    """

    def __init__(self, line_name: str, targets: list[Path]) -> None:
        super().__init__()
        self.line_name = line_name
        self.targets = targets

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Send {self.line_name} to…")
            yield OptionList(
                *[str(p) for p in self.targets], id="send-targets"
            )
            yield Checkbox(
                "Move (remove from this program)", value=False, id="send-move"
            )
            yield Label("[b]enter[/b] send · [b]esc[/b] cancel", classes="modal-hint")
            with Horizontal():
                yield Button("Cancel", id="send-cancel")
                yield Button("Send", variant="primary", id="send-confirm")

    def on_mount(self) -> None:
        self.query_one("#send-targets", OptionList).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._confirm()

    def _confirm(self) -> None:
        options = self.query_one("#send-targets", OptionList)
        idx = options.highlighted
        if idx is None:
            return
        move = self.query_one("#send-move", Checkbox).value
        self.dismiss((self.targets[idx], move))


class ImportFileModal(ModalScreen[dict | None]):
    """Import a schedule file plus the meta the source can't carry.

    `PasteImportModal`'s structural twin: a path `Input` instead of a
    `TextArea`, same insured/program/inception/expiry fields. The optional
    dates matter here specifically for text/csv sources — `parse_tower`
    has no date syntax, so without them a text-file schedule can never
    pick up a policy period.
    """

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    ImportFileModal { align: center middle; }
    ImportFileModal > Vertical {
        width: 70; height: auto; max-height: 34; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    ImportFileModal .field-label { color: $text-muted; }
    ImportFileModal .modal-hint { color: $text-muted; margin-top: 1; }
    ImportFileModal Horizontal { height: auto; align-horizontal: right; }
    ImportFileModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Schedule file (xlsx/csv/text):", classes="field-label")
            yield Input(id="import-path", placeholder="schedule.xlsx")
            yield Label("Insured", classes="field-label")
            yield Input(id="import-insured")
            yield Label("Program", classes="field-label")
            yield Input(id="import-program")
            yield Label(
                "Inception / Expiry (any date form; used when the file has none)",
                classes="field-label",
            )
            yield Input(id="import-inception", placeholder="Jan 1 2026")
            yield Input(id="import-expiry", placeholder="Jan 1 2027")
            yield Label(
                "[b]enter[/b] import · [b]tab[/b] next field · [b]esc[/b] cancel",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button("Cancel", id="import-cancel")
                yield Button("Import", variant="primary", id="import-confirm")

    def on_mount(self) -> None:
        self.query_one("#import-path", Input).focus()

    def _confirm(self) -> None:
        self.dismiss({
            "path": self.query_one("#import-path", Input).value.strip(),
            "insured": self.query_one("#import-insured", Input).value.strip(),
            "program": self.query_one("#import-program", Input).value.strip(),
            "inception": self.query_one("#import-inception", Input).value.strip(),
            "expiry": self.query_one("#import-expiry", Input).value.strip(),
        })

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "import-confirm":
            self._confirm()
        else:
            self.dismiss(None)


class PasteImportModal(ModalScreen[dict | None]):
    """Paste a schedule as text plus the meta the text can't carry."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PasteImportModal { align: center middle; }
    #paste-box { width: 90; height: auto; max-height: 38; padding: 1 2;
                 background: $surface; border: thick $primary; }
    #paste-text { height: 10; }
    PasteImportModal .field-label { color: $text-muted; }
    PasteImportModal .modal-hint { color: $text-muted; margin-top: 1; }
    PasteImportModal Horizontal { height: auto; align-horizontal: right; }
    PasteImportModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="paste-box"):
            yield Label("Paste the schedule (one layer per line):", classes="field-label")
            yield TextArea(id="paste-text")
            yield Label("Insured", classes="field-label")
            yield Input(id="paste-insured")
            yield Label("Program", classes="field-label")
            yield Input(id="paste-program")
            yield Label("Inception / Expiry (any date form)", classes="field-label")
            yield Input(id="paste-inception", placeholder="Jan 1 2026")
            yield Input(id="paste-expiry", placeholder="Jan 1 2027")
            yield Label(
                "[b]tab[/b] next field · [b]enter[/b] in a field imports · "
                "[b]esc[/b] cancel",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button("Cancel", id="paste-cancel")
                yield Button("Import", variant="primary", id="paste-confirm")

    def on_mount(self) -> None:
        self.query_one("#paste-text", TextArea).focus()

    def _confirm(self) -> None:
        self.dismiss({
            "text": self.query_one("#paste-text", TextArea).text,
            "insured": self.query_one("#paste-insured", Input).value.strip(),
            "program": self.query_one("#paste-program", Input).value.strip(),
            "inception": self.query_one("#paste-inception", Input).value.strip(),
            "expiry": self.query_one("#paste-expiry", Input).value.strip(),
        })

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "paste-confirm":
            self._confirm()
        else:
            self.dismiss(None)
