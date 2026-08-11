"""Edit session: one program, undo/redo, canonical save.

The JSON files are the source of truth; this object is the only mutable state
the TUI holds, and it dies on save/exit. Undo snapshots are canonical JSON
strings, so undo/redo can never drift from what a save would write.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

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


def suggested_attach(program: Program, line_ids: list[str]) -> int:
    """Default attachment for a new layer: the top of the existing stack for
    those lines. Contiguity by construction beats contiguity by validation."""
    tops = [
        layer.top
        for layer in program.layers
        if layer.limit > 0 and any(lid in layer.applies_to for lid in line_ids)
    ]
    return max(tops, default=0)


class EditSession:
    def __init__(self, program: Program, path: Path | None = None) -> None:
        self.program = program
        self.path = path
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._saved_text: str | None = dumps_program(program) if path else None

    @classmethod
    def open(cls, path: Path | str) -> EditSession:
        path = Path(path)
        return cls(load_program(path), path=path)

    # -- mutation ------------------------------------------------------------

    def mutate(self, fn: Callable[[Program], None]) -> None:
        """Apply one user-visible edit, snapshotting for undo first."""
        before = dumps_program(self.program)
        fn(self.program)
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

    def save(self, path: Path | None = None) -> Path:
        """Write canonical JSON. Never silent about errors — the caller must
        have confirmed if diagnostics().ok is False."""
        target = path or self.path
        if target is None:
            raise ValueError("no path for save")
        text = dumps_program(self.program)
        target.write_text(text, encoding="utf-8")
        self.path = target
        self._saved_text = text
        return target

    # -- structural edits used by the editor ---------------------------------

    def unique_id(self, prefix: str) -> str:
        taken = {layer.id for layer in self.program.layers} | {
            line.id for line in self.program.lines
        }
        if prefix not in taken:
            return prefix
        n = 2
        while f"{prefix}-{n}" in taken:
            n += 1
        return f"{prefix}-{n}"

    def add_layer(self, line_ids: list[str] | None = None) -> Layer:
        lines = line_ids or ([self.program.lines[0].id] if self.program.lines else [])
        attach = suggested_attach(self.program, lines)
        layer = Layer(
            id=self.unique_id("layer"),
            name="New Layer",
            applies_to=lines or ["gl"],
            attach=attach,
            limit=5_000_000,
            participants=[],
        )
        self.mutate(lambda p: p.layers.append(layer))
        return layer
