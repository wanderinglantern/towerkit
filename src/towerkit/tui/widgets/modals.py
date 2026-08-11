"""Small modal dialogs: confirm, text prompt, render options."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList
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
        self.themes_dir = themes_dir or Path("themes")

    def compose(self) -> ComposeResult:
        options = [Option(self._label(None, "default (built-in)"), id="")]
        if self.themes_dir.is_dir():
            for path in sorted(self.themes_dir.glob("*.json")):
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
