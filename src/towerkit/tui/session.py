"""Edit session: one program, undo/redo, canonical save.

The JSON files are the source of truth; this object is the only mutable state
the TUI holds, and it dies on save/exit. Undo snapshots are canonical JSON
strings, so undo/redo can never drift from what a save would write.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

from .. import edit
from ..edit import ordinal, slugify, suggested_attach  # re-exported: editor.py imports these
from ..model import (
    Layer,
    Line,
    Period,
    Placement,
    Program,
    dumps_program,
    load_program,
    loads_program,
)
from ..validate import Diagnostics, validate_program

__all__ = [
    "EditSession",
    "PLACEHOLDER_ID",
    "StaleFileError",
    "blank_program",
    "ordinal",
    "slugify",
    "suggested_attach",
]


class StaleFileError(RuntimeError):
    """The file changed on disk since this session opened or last saved it.

    `EditSession` holds a whole program in memory and writes it whole, so a
    save over someone else's write is a silent total loss — bookkit's MCP
    writes to these same files, and so does towerkit's own MCP server."""


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def blank_program() -> Program:
    today = date.today()
    start = date(today.year + 1, 1, 1)
    return Program(
        insured="New Insured",
        program="New Program",
        placement=Placement.PROPOSED,
        period=Period(start=start, end=date(start.year + 1, 1, 1)),
        lines=[Line(id="gl", name="General Liability", abbr="GL")],
        layers=[],
        retentions=[],
        sublimits=[],
    )


PLACEHOLDER_ID = re.compile(r"^(layer|line|retention|sublimit)(-\d+)?$")


class EditSession:
    def __init__(self, program: Program, path: Path | None = None) -> None:
        self.program = program
        self.path = path
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._saved_text: str | None = dumps_program(program) if path else None
        self._disk_sha: str | None = _file_sha(path) if path else None

    @classmethod
    def open(cls, path: Path | str) -> EditSession:
        path = Path(path)
        return cls(load_program(path), path=path)

    # -- mutation ------------------------------------------------------------

    def mutate(self, fn: Callable[[Program], object]) -> None:
        """Apply one user-visible edit, snapshotting for undo first.

        Follows-underlying layers auto-heal afterwards: their attachment is
        derived state (the highest underlying top), so editing a lower
        layer's limit can never strand them."""
        before = dumps_program(self.program)
        fn(self.program)
        edit.heal_follows(self.program)
        after = dumps_program(self.program)
        if after != before:
            self._undo.append(before)
            self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(dumps_program(self.program))
        self.program = loads_program(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(dumps_program(self.program))
        self.program = loads_program(self._redo.pop())
        return True

    # -- state ---------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return dumps_program(self.program) != self._saved_text

    def diagnostics(self) -> Diagnostics:
        return validate_program(self.program)

    def save(self, path: Path | None = None, force: bool = False) -> Path:
        """Write canonical JSON. Never silent about errors — the caller must
        have confirmed if diagnostics().ok is False.

        Refuses to overwrite a file that changed since we opened or last
        saved it, unless `force`. Saving AS a different path is not guarded:
        that is a new destination, not a clobber of what we loaded."""
        target = path or self.path
        if target is None:
            raise ValueError("no path for save")
        same_file = self.path is not None and target == self.path
        if same_file and not force and _file_sha(target) != self._disk_sha:
            raise StaleFileError(
                f"{target} changed on disk since it was opened — reload to take "
                f"the file (losing this session's edits), or overwrite it"
            )
        text = dumps_program(self.program)
        target.write_text(text, encoding="utf-8")
        self.path = target
        self._saved_text = text
        self._disk_sha = _file_sha(target)
        return target

    def reload(self) -> None:
        """Take what is on disk, discarding this session's edits and history."""
        if self.path is None:
            raise ValueError("nothing to reload from")
        self.program = load_program(self.path)
        self._undo.clear()
        self._redo.clear()
        self._saved_text = dumps_program(self.program)
        self._disk_sha = _file_sha(self.path)

    # -- structural edits used by the editor ---------------------------------

    def unique_id(self, prefix: str, exclude: str | None = None) -> str:
        return edit.unique_id(self.program, prefix, exclude)

    def add_layer(self, line_ids: list[str] | None = None) -> Layer:
        layer: Layer | None = None

        def do(p: Program) -> None:
            nonlocal layer
            layer = edit.add_layer(p, line_ids)

        self.mutate(do)
        assert layer is not None
        return layer

    def restack(self) -> None:
        """Recalculate every attachment from the stacking order: each layer
        lands on top of what its lines already carry. One keystroke heals a
        tower after limit edits — gaps and overlaps disappear."""
        self.mutate(edit.restack)
