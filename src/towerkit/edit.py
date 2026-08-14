"""Structural edits to a Program — the single definition of what each
mutation means.

Both surfaces call these: the TUI editor wraps them in `EditSession.mutate`
for undo, and the MCP server calls them inside its load → mutate → dump
cycle. Nothing here knows about sessions, screens, or transports.

Money is integer whole dollars, as on disk. Callers parse human strings.
"""

from __future__ import annotations

import re

from .model import Line, Program


def slugify(name: str) -> str:
    """'Primary D&O' → 'primary-do': ids nobody has to invent."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "item"


def unique_id(program: Program, prefix: str, exclude: str | None = None) -> str:
    """`exclude` is the id of the thing being renamed. Without it a cosmetic
    edit that re-slugs to the id the entity ALREADY has would collide with
    itself and drift to 'cyber-2', then 'cyber-3', on every later edit."""
    taken = {layer.id for layer in program.layers} | {line.id for line in program.lines}
    taken.discard(exclude)
    if prefix not in taken:
        return prefix
    n = 2
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


def _line(program: Program, line_id: str) -> Line:
    line = next((ln for ln in program.lines if ln.id == line_id), None)
    if line is None:
        raise KeyError(f"no line {line_id!r}")
    return line


def add_line(
    program: Program, name: str, abbr: str | None = None, group: str | None = None
) -> Line:
    line = Line(id=unique_id(program, slugify(name)), name=name, abbr=abbr, group=group)
    program.lines.append(line)
    return line


def rename_line(program: Program, line_id: str, name: str) -> Line:
    """The id follows the name (towerkit 67ac42f), cascading through every
    appliesTo so nothing is stranded on the old slug."""
    line = _line(program, line_id)
    new_id = unique_id(program, slugify(name), exclude=line_id)
    line.name = name
    line.id = new_id
    if new_id != line_id:
        _recast(program, line_id, new_id)
    return line


def _recast(program: Program, old: str, new: str) -> None:
    for layer in program.layers:
        layer.applies_to = [new if x == old else x for x in layer.applies_to]
    for retention in program.retentions:
        retention.applies_to = [new if x == old else x for x in retention.applies_to]
    for sublimit in program.sublimits:
        sublimit.applies_to = [new if x == old else x for x in sublimit.applies_to]


def set_line_group(program: Program, line_id: str, group: str | None) -> Line:
    line = _line(program, line_id)
    line.group = group or None
    return line


def move_line(program: Program, line_id: str, delta: int) -> int:
    """Array order is column order in the diagram. Returns the resulting
    index; a move off either end is a no-op, not an error."""
    index = next((i for i, ln in enumerate(program.lines) if ln.id == line_id), None)
    if index is None:
        raise KeyError(f"no line {line_id!r}")
    target = index + delta
    if not 0 <= target < len(program.lines):
        return index
    lines = program.lines
    lines[index], lines[target] = lines[target], lines[index]
    return target


def remove_line(program: Program, line_id: str) -> None:
    """Cascade: the id leaves every appliesTo, and anything left with an
    EMPTY appliesTo goes with it. appliesTo is min_length=1, so a partial
    cascade would raise on assignment instead of producing a diagnostic —
    and the editor's old drop_line kept the stale id rather than face it."""
    _line(program, line_id)  # raises KeyError when unknown
    program.lines = [ln for ln in program.lines if ln.id != line_id]
    program.layers = [
        layer for layer in program.layers if [x for x in layer.applies_to if x != line_id]
    ]
    for layer in program.layers:
        layer.applies_to = [x for x in layer.applies_to if x != line_id]
    program.retentions = [
        r for r in program.retentions if [x for x in r.applies_to if x != line_id]
    ]
    for retention in program.retentions:
        retention.applies_to = [x for x in retention.applies_to if x != line_id]
    program.sublimits = [
        s for s in program.sublimits if [x for x in s.applies_to if x != line_id]
    ]
    for sublimit in program.sublimits:
        sublimit.applies_to = [x for x in sublimit.applies_to if x != line_id]
