"""Structural edits to a Program — the single definition of what each
mutation means.

Both surfaces call these: the TUI editor wraps them in `EditSession.mutate`
for undo, and the MCP server calls them inside its load → mutate → dump
cycle. Nothing here knows about sessions, screens, or transports.

Money is integer whole dollars, as on disk. Callers parse human strings.
"""

from __future__ import annotations

import re

from .model import Layer, Line, Program, Retention, RetentionType, Sublimit


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


def adopt(program: Program, source: Program) -> None:
    """Replace the four structural collections wholesale from another program.

    The line-transfer flow computes a whole new source program and applies it
    in one step; without this, that call site would reach past this module and
    assign the collections directly."""
    program.lines = list(source.lines)
    program.layers = list(source.layers)
    program.retentions = list(source.retentions)
    program.sublimits = list(source.sublimits)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def suggested_attach(program: Program, line_ids: list[str]) -> int:
    """Default attachment for a new layer: the top of the existing stack for
    those lines. Contiguity by construction beats contiguity by validation."""
    tops = [
        layer.top
        for layer in program.layers
        if layer.limit > 0 and any(lid in layer.applies_to for lid in line_ids)
    ]
    return max(tops, default=0)


def heal_follows(program: Program) -> None:
    """Follows-underlying attachment is derived state: recompute it so that
    editing a lower layer's limit can never strand the layer above."""
    for layer in program.layers:
        if layer.follows_underlying:
            tops = program.underlying_tops(layer)
            layer.attach = max(tops.values(), default=0)


def _layer(program: Program, layer_id: str) -> Layer:
    layer = next((ly for ly in program.layers if ly.id == layer_id), None)
    if layer is None:
        raise KeyError(f"no layer {layer_id!r}")
    return layer


def add_layer(program: Program, line_ids: list[str] | None = None) -> Layer:
    lines = line_ids or ([program.lines[0].id] if program.lines else [])
    attach = suggested_attach(program, lines)
    if attach == 0:
        name = "New Layer"
    else:
        n = sum(
            1
            for ly in program.layers
            if ly.attach > 0 and ly.limit > 0 and any(lid in ly.applies_to for lid in lines)
        )
        name = f"{ordinal(n + 1)} Excess"
    layer = Layer(
        id=unique_id(program, "layer"),
        name=name,
        applies_to=lines or ["gl"],
        attach=attach,
        limit=5_000_000,
        participants=[],
    )
    program.layers.append(layer)
    return layer


def rename_layer(program: Program, layer_id: str, name: str) -> Layer:
    layer = _layer(program, layer_id)
    layer.name = name
    layer.id = unique_id(program, slugify(name), exclude=layer_id)
    return layer


def remove_layer(program: Program, layer_id: str) -> None:
    _layer(program, layer_id)
    program.layers = [ly for ly in program.layers if ly.id != layer_id]


def set_applies_to(program: Program, layer_id: str, line_ids: list[str]) -> Layer:
    layer = _layer(program, layer_id)
    if not line_ids:
        raise ValueError("a layer must apply to at least one line")
    known = set(program.line_ids())
    unknown = [lid for lid in line_ids if lid not in known]
    if unknown:
        raise KeyError(f"unknown line(s): {', '.join(sorted(unknown))}")
    layer.applies_to = list(dict.fromkeys(line_ids))
    return layer


def set_follows_underlying(program: Program, layer_id: str, follows: bool) -> Layer:
    layer = _layer(program, layer_id)
    layer.follows_underlying = follows
    return layer


def restack(program: Program) -> None:
    """Recalculate every attachment from the stacking order: each layer lands
    on top of what its lines already carry. One call heals a tower after limit
    edits — gaps and overlaps disappear."""
    tops: dict[str, int] = {line.id: 0 for line in program.lines}
    ordered = sorted((ly for ly in program.layers if ly.limit > 0), key=lambda ly: ly.attach)
    for layer in ordered:
        base = max((tops.get(lid, 0) for lid in layer.applies_to), default=0)
        layer.attach = base
        for lid in layer.applies_to:
            tops[lid] = base + layer.limit


def _check_lines(program: Program, line_ids: list[str]) -> list[str]:
    if not line_ids:
        raise ValueError("appliesTo must name at least one line")
    known = set(program.line_ids())
    unknown = [lid for lid in line_ids if lid not in known]
    if unknown:
        raise KeyError(f"unknown line(s): {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(line_ids))


def add_retention(
    program: Program,
    applies_to: list[str],
    type: RetentionType | str,
    amount: int,
    aggregate: int | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> Retention:
    retention = Retention(
        applies_to=_check_lines(program, applies_to),
        type=RetentionType(type),
        amount=amount,
        aggregate=aggregate,
        vehicle=vehicle,
        notes=notes,
    )
    program.retentions.append(retention)
    return retention


def _at(items: list[object], index: int, what: str) -> int:
    if not 0 <= index < len(items):
        raise IndexError(f"no {what} at index {index} (there are {len(items)})")
    return index


def edit_retention(
    program: Program,
    index: int,
    applies_to: list[str] | None = None,
    type: RetentionType | str | None = None,
    amount: int | None = None,
    aggregate: int | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> Retention:
    """`None` means leave alone. Retentions have no ids — the caller addresses
    them by the index a read reported, guarded by an expecting_* field at the
    tool layer."""
    _at(list(program.retentions), index, "retention")
    retention = program.retentions[index]
    if applies_to is not None:
        retention.applies_to = _check_lines(program, applies_to)
    if type is not None:
        retention.type = RetentionType(type)
    if amount is not None:
        retention.amount = amount
    if aggregate is not None:
        retention.aggregate = aggregate
    if vehicle is not None:
        retention.vehicle = vehicle
    if notes is not None:
        retention.notes = notes
    return retention


def remove_retention(program: Program, index: int) -> None:
    _at(list(program.retentions), index, "retention")
    program.retentions.pop(index)


def add_sublimit(
    program: Program,
    name: str,
    amount: int,
    applies_to: list[str],
    notes: str | None = None,
) -> Sublimit:
    sublimit = Sublimit(
        name=name,
        amount=amount,
        applies_to=_check_lines(program, applies_to),
        notes=notes,
    )
    program.sublimits.append(sublimit)
    return sublimit


def edit_sublimit(
    program: Program,
    index: int,
    name: str | None = None,
    amount: int | None = None,
    applies_to: list[str] | None = None,
    notes: str | None = None,
) -> Sublimit:
    _at(list(program.sublimits), index, "sublimit")
    sublimit = program.sublimits[index]
    if name is not None:
        sublimit.name = name
    if amount is not None:
        sublimit.amount = amount
    if applies_to is not None:
        sublimit.applies_to = _check_lines(program, applies_to)
    if notes is not None:
        sublimit.notes = notes
    return sublimit


def remove_sublimit(program: Program, index: int) -> None:
    _at(list(program.sublimits), index, "sublimit")
    program.sublimits.pop(index)
