"""Spreadsheet-style inline cell editing for DataTables — data sheets.

`i` opens a slim one-line editor over the cell under the cursor: enter
commits and closes, tab / shift+tab commit and hop across the row's editable
cells, esc cancels, and clicking elsewhere (blur) is esc — never a surprise
write. Values parse per column kind (text | money | share) with the same
parsers the detail forms use; the screen hears one `CellEdited` per accepted
commit and turns it into exactly one session mutation (one undo step).

Ported from bookkit's InlineTable/CellEditor. Hard-won details carried over:
the editor's CSS type selector must not start with an underscore (a TCSS
selector like `_Foo` silently matches nothing), `border: none` must be
repeated in the :focus rule (Input:focus's tall border outranks the bare
type selector and eats both rows of a height-1 widget), and the row identity
is captured when the editor OPENS so a refresh can never retarget the write.

The widget knows nothing about programs or sessions: the screen sets
`fields` (column index → SheetField), `initial_text` (row_key, field_key →
the editor's prefill), and handles CellEdited / InsertRequested /
DeleteRequested.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual_autocomplete import AutoComplete

from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.message import Message
from textual.suggester import Suggester
from textual.widgets import DataTable, Input

from ...money import MoneyParseError, parse_money
from ..theme import GOLD, PANEL, RULE
from .inputs import parse_share_pct

SheetValue = str | int | None

_PLACEHOLDERS = {
    "text": "",
    "money": "2m · 250k · 2,000,000",
    "share": "% · 35 or 33.33",
}


@dataclass
class SheetField:
    """One editable column: how its text parses and completes.

    kind: 'text' (stripped string), 'money' (whole dollars via parse_money,
    empty = None), 'share' (percent → basis points via parse_share_pct).
    `validate` runs after the parse with the ROW's key — the screen hangs
    cross-field guards here (over-signing) so a bad value keeps the editor
    open instead of half-committing. `dropdown` candidates get a
    textual-autocomplete list under the editor; `suggester` is ghost text.
    """

    key: str
    label: str
    kind: str = "text"
    required: bool = False
    suggester: Suggester | None = None
    dropdown: list[str] | None = None
    validate: Callable[[str, SheetValue], str | None] | None = None


class SheetTable(DataTable):
    """DataTable with in-cell editing on the columns the screen marks editable."""

    BINDINGS = [
        Binding("i", "sheet_edit", "Edit cell", show=False),
        Binding("a", "sheet_insert", "Add row", show=False),
        Binding("delete", "sheet_delete", "Remove row", show=False),
        Binding("backspace", "sheet_delete", "Remove row", show=False),
    ]

    class CellEdited(Message):
        """A cell's value parsed (and validated) cleanly — write it."""

        def __init__(
            self,
            table: SheetTable,
            row_key: str,
            field: SheetField,
            value: SheetValue,
            coordinate: Coordinate,
        ) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key
            self.field = field
            self.value = value
            self.coordinate = coordinate

        @property
        def control(self) -> SheetTable:
            return self.table

    class InsertRequested(Message):
        """`a` on a sheet that allows inserts — the screen adds the row."""

        def __init__(self, table: SheetTable) -> None:
            super().__init__()
            self.table = table

        @property
        def control(self) -> SheetTable:
            return self.table

    class DeleteRequested(Message):
        """del/backspace on a row of a sheet that allows deletes."""

        def __init__(self, table: SheetTable, row_key: str) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key

        @property
        def control(self) -> SheetTable:
            return self.table

    class EditorClosed(Message):
        """The cell editor went away — the screen may flush a deferred rebuild."""

        def __init__(self, table: SheetTable) -> None:
            super().__init__()
            self.table = table

        @property
        def control(self) -> SheetTable:
            return self.table

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # column index → SheetField; empty dict means nothing is editable
        self.fields: dict[int, SheetField] = {}
        # (row_key, field_key) → raw text to prefill the editor with
        self.initial_text: Callable[[str, str], str] = lambda _rk, _fk: ""
        self.can_insert = False
        self.can_delete = False
        self._editor: SheetCellEditor | None = None
        # the autocomplete overlay mounted beside the editor, when a field
        # carries dropdown candidates
        self._dropdown: AutoComplete | None = None

    @property
    def editing(self) -> bool:
        """True while a cell editor is open — the screen defers rebuilds
        (a rebuild would pull the cell out from under the editor)."""
        return self._editor is not None

    # -- key actions -----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # disabled sheet actions release their key back to the screen
        # (e.g. `a` still adds a layer when the layers sheet has focus)
        if action == "sheet_edit":
            return bool(self.fields) and self.row_count > 0
        if action == "sheet_insert":
            return self.can_insert
        if action == "sheet_delete":
            return self.can_delete and self.row_count > 0
        return True

    def action_sheet_edit(self) -> None:
        if not self.fields or self.cursor_row is None or not self.row_count:
            return
        # the cursor's own column when editable, else the row's first
        column = (
            self.cursor_column
            if self.cursor_column in self.fields
            else min(self.fields)
        )
        self._open_editor(Coordinate(self.cursor_row, column))

    def action_sheet_insert(self) -> None:
        self.post_message(self.InsertRequested(self))

    def action_sheet_delete(self) -> None:
        row_key = self._row_key_at(self.cursor_row)
        if row_key is not None:
            self.post_message(self.DeleteRequested(self, row_key))

    def action_select_cursor(self) -> None:
        # enter on an editable cell edits it (spreadsheet-style); everything
        # else keeps DataTable's select semantics (RowSelected etc.)
        if self.cursor_type == "cell" and self.cursor_column in self.fields:
            self._open_editor(self.cursor_coordinate)
            return
        super().action_select_cursor()

    # -- opening ---------------------------------------------------------------

    def _row_key_at(self, row: int | None) -> str | None:
        if row is None:
            return None
        try:
            value = self.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        except Exception:
            return None
        return str(value) if value is not None else None

    def _open_editor(self, coordinate: Coordinate) -> None:
        # capture the row identity NOW — a refresh while the editor is open
        # must never retarget the write onto whatever row moved under it
        row_key = self._row_key_at(coordinate.row)
        field = self.fields.get(coordinate.column)
        if row_key is None or field is None:
            return
        self._close_editor(notify_screen=False)
        try:
            region = self._get_cell_region(coordinate)
        except Exception:
            return  # cell scrolled out of a rebuilt table — nothing to anchor to
        # the cell region is in the table's virtual space; place the editor
        # on the screen at the cell's on-screen position
        origin = self.content_region.offset - self.scroll_offset + region.offset
        width = max(region.width, 24)
        max_x = self.content_region.right
        x = min(origin.x, max_x - width)
        editor = SheetCellEditor(
            self,
            row_key,
            coordinate,
            field,
            initial=self.initial_text(row_key, field.key),
            placeholder=_PLACEHOLDERS.get(field.kind, ""),
            suggester=field.suggester,
        )
        self._editor = editor
        self.screen.mount(editor)
        editor.styles.offset = (max(x, 0), origin.y)
        editor.styles.width = width
        if field.dropdown:
            from textual_autocomplete import AutoComplete

            dropdown = AutoComplete(editor, candidates=list(field.dropdown))
            self._dropdown = dropdown
            self.screen.mount(dropdown)
        editor.focus()

    # -- editor callbacks ------------------------------------------------------

    def _parse(self, field: SheetField, raw: str) -> SheetValue:
        """Raw editor text → typed value, or ValueError with the user message."""
        text = raw.strip()
        if field.kind == "money":
            if not text:
                value: SheetValue = None
            else:
                try:
                    value = parse_money(text)
                except MoneyParseError as exc:
                    raise ValueError(str(exc)) from exc
        elif field.kind == "share":
            if not text:
                value = None
            else:
                value = parse_share_pct(text)
                if value is None:
                    raise ValueError("share is a percentage: 35 or 33.33")
        else:
            value = text or None
        if field.required and value is None:
            raise ValueError(f"{field.label} can't be empty")
        return value

    def _commit(
        self, row_key: str, coordinate: Coordinate, field: SheetField, raw: str
    ) -> bool:
        """Parse, validate, post. False keeps the editor open (bad input is
        rejected in place, never half-committed)."""
        try:
            value = self._parse(field, raw)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return False
        if field.validate is not None:
            error = field.validate(row_key, value)
            if error:
                self.app.notify(error, severity="error")
                return False
        self.post_message(self.CellEdited(self, row_key, field, value, coordinate))
        return True

    def _hop(self, coordinate: Coordinate, direction: int) -> None:
        """Move the editor to the next/previous editable column in the row."""
        columns = sorted(self.fields)
        pos = columns.index(coordinate.column) + direction
        if 0 <= pos < len(columns):
            self._open_editor(Coordinate(coordinate.row, columns[pos]))
        else:
            self._close_editor()

    def _close_editor(self, notify_screen: bool = True) -> None:
        if self._dropdown is not None:
            dropdown, self._dropdown = self._dropdown, None
            dropdown.remove()
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.remove()
            self.focus()
            if notify_screen:
                self.post_message(self.EditorClosed(self))


class SheetCellEditor(Input):
    """The one-line input that floats over the cell being edited."""

    # NOTE: the type selector must not start with an underscore — an invalid
    # TCSS type selector silently matches nothing and the styles no-op
    DEFAULT_CSS = f"""
    SheetCellEditor {{
        position: absolute;
        height: 1;
        padding: 0 1;
        border: none;
        background: {PANEL};
        color: {GOLD};
        text-style: bold;
    }}
    SheetCellEditor:focus {{
        background: {RULE};
        /* Input:focus's tall border outranks the bare type selector and
           would eat both rows of a height-1 widget — kill it here too */
        border: none;
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "commit_close", "Save", priority=True),
        Binding("tab", "commit_next", "Save + next", priority=True),
        Binding("shift+tab", "commit_prev", "Save + previous", priority=True),
    ]

    def __init__(
        self,
        table: SheetTable,
        row_key: str,
        coordinate: Coordinate,
        field: SheetField,
        initial: str,
        placeholder: str = "",
        suggester: Suggester | None = None,
    ) -> None:
        super().__init__(value=initial, placeholder=placeholder, suggester=suggester)
        self._table = table
        self._row_key = row_key
        self._coordinate = coordinate
        self._field = field

    def action_cancel(self) -> None:
        self._table._close_editor()

    def action_commit_close(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._close_editor()

    def action_commit_next(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._hop(self._coordinate, +1)

    def action_commit_prev(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._hop(self._coordinate, -1)

    def on_blur(self) -> None:
        # clicking elsewhere abandons the edit — esc semantics, never a
        # surprise write
        if self._table._editor is self:
            self._table._close_editor()
