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
        width: 60; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
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
            with Horizontal():
                yield Button(self.no_label, id="no")
                yield Button(self.yes_label, id="yes", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class PromptModal(ModalScreen[str | None]):
    """One-line text prompt; None on cancel."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PromptModal { align: center middle; }
    PromptModal > VerticalScroll {
        width: 60; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    """

    def __init__(self, label: str, default: str = "") -> None:
        super().__init__()
        self.label = label
        self.default = default

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.label)
            yield Input(value=self.default, id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


@dataclass(frozen=True)
class RenderOptions:
    """What the render menu controls; the CLI flags mirror these."""

    theme: str  # "" = built-in default, otherwise a theme file path
    show_totals: bool
    show_premiums: bool
    cell_premiums: bool
    cell_dates: bool


class RenderOptionsModal(ModalScreen[RenderOptions | None]):
    """Theme selection plus the totals/premiums toggles. None = cancelled."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    RenderOptionsModal { align: center middle; }
    RenderOptionsModal > VerticalScroll {
        width: 56; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    RenderOptionsModal OptionList { max-height: 8; }
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
        themes_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.current_theme = current_theme
        self.show_totals = show_totals
        self.show_premiums = show_premiums
        self.cell_premiums = cell_premiums
        self.cell_dates = cell_dates
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
            yield Label("Theme (preview and renders):")
            yield OptionList(*options, id="themes")
            yield Checkbox("Show totals in the header", self.show_totals, id="opt-totals")
            yield Checkbox(
                "Show premiums (uncheck for hypothetical designs)",
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
        width: 62; max-height: 85%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.help_text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.help_text)


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
        width: 64; max-height: 80%; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    ExitChoiceModal Horizontal { height: auto; align-horizontal: right; }
    ExitChoiceModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Unsaved changes.")
            yield Label("[dim]s save & exit · d discard · esc keep editing[/dim]")
            with Horizontal():
                yield Button("Keep editing", id="keep")
                yield Button("Discard", id="discard", variant="warning")
                yield Button("Save & exit", id="save", variant="primary")

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
            with Horizontal():
                yield Button("Cancel", id="send-cancel")
                yield Button("Send", variant="primary", id="send-confirm")

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


class PasteImportModal(ModalScreen[dict | None]):
    """Paste a schedule as text plus the meta the text can't carry."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PasteImportModal { align: center middle; }
    #paste-box { width: 90; height: auto; max-height: 32; padding: 1 2;
                 background: $surface; border: thick $primary; }
    #paste-text { height: 10; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="paste-box"):
            yield Label("Paste the schedule (one layer per line):")
            yield TextArea(id="paste-text")
            yield Label("Insured")
            yield Input(id="paste-insured")
            yield Label("Program")
            yield Input(id="paste-program")
            yield Label("Inception / Expiry (any date form)")
            yield Input(id="paste-inception", placeholder="Jan 1 2026")
            yield Input(id="paste-expiry", placeholder="Jan 1 2027")
            with Horizontal():
                yield Button("Import", variant="primary", id="paste-confirm")
                yield Button("Cancel", id="paste-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "paste-confirm":
            self.dismiss({
                "text": self.query_one("#paste-text", TextArea).text,
                "insured": self.query_one("#paste-insured", Input).value.strip(),
                "program": self.query_one("#paste-program", Input).value.strip(),
                "inception": self.query_one("#paste-inception", Input).value.strip(),
                "expiry": self.query_one("#paste-expiry", Input).value.strip(),
            })
        else:
            self.dismiss(None)
