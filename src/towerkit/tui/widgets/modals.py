"""Small modal dialogs: confirm and text prompt."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


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
