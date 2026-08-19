"""Structural edits to a Program — the single definition of what each
mutation means.

Both surfaces call these: the TUI editor wraps them in `EditSession.mutate`
for undo, and the MCP server calls them inside its load → mutate → dump
cycle. Nothing here knows about sessions, screens, or transports.

Money is integer whole dollars, as on disk. Callers parse human strings.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .model import (
    Layer,
    Line,
    NamedLimit,
    Program,
    Retention,
    RetentionType,
    Sublimit,
)
from .money import format_money
from .validate import WARNING, Diagnostic


class Refusal(ValueError):
    """A refusal carrying a STABLE code, in the message and on the object.

    Two callers, two channels, one code. bookkit runs towerkit as a library
    and catches the exception OBJECT, so it branches on `.code`; an MCP
    client sees only text, because the SDK's error path is a flat string —
    `Tool.run` re-raises anything as `ToolError(f"Error executing tool …:
    {e}")` and the server turns that into `CallToolResult(content=[TextContent
    (...)], is_error=True)`. There is no structured channel on an error
    result, no `structuredContent` and no JSON-RPC `data`, so the code has to
    travel inside the message and the `[code] ` prefix is how.

    It lives HERE, not in a surface, for the same reason the guards do: the
    guards attach their own codes at the raise site, and the alternative — a
    surface string-matching this module's prose to decide which code a
    refusal deserves — is exactly the drift that hand-written second table
    became. A code is assigned where the refusal is RAISED, never by reading
    a message back.

    It subclasses `ValueError` so every existing `except (ValidationError,
    ValueError, …)` still catches it and nothing is written.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


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


def remove_layer(program: Program, layer_id: str) -> None:
    _layer(program, layer_id)
    program.layers = [ly for ly in program.layers if ly.id != layer_id]


def set_applies_to(program: Program, layer_id: str, line_ids: list[str]) -> Layer:
    layer = _layer(program, layer_id)
    # `_check_lines`, not a second copy of it. This function carried its own
    # identical unknown-line check, and only one of the two was ever fixed at
    # a time: the message that named nothing the caller could use was this
    # one, four lines from the one that did.
    layer.applies_to = _check_lines(program, line_ids)
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
    """Every line id, checked against the program, duplicates dropped, order kept.

    The refusal NAMES THE IDS THAT EXIST. It used to say only `unknown
    line(s): nope`, which leaves a caller that guessed an id with nothing to
    guess again from — the same standard `_line` and the MCP server's `_by_id`
    already meet, and the reason a refusal is worth sending at all.
    """
    if not line_ids:
        raise ValueError("appliesTo must name at least one line")
    known = program.line_ids()
    unknown = [lid for lid in line_ids if lid not in known]
    if unknown:
        raise KeyError(
            f"unknown line(s): {', '.join(repr(lid) for lid in sorted(unknown))} — "
            f"lines are {', '.join(repr(lid) for lid in known) or '(none)'}"
        )
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


# -- layer detail fields ------------------------------------------------------
#
# `states`, `namedLimits` and `premiumDetail` shipped with no setter: they were
# reachable only by hand-editing JSON. These are the one definition of what
# setting each MEANS, so the editor and any later tool cannot drift apart.
#
# `namedLimits` and `premiumDetail` enforce no validator rule. `validate.py`
# owns those refusals (duplicates, prose-versus-structure, a premium detail on
# a priced layer) and reports each as a diagnostic the surface shows the user.
# Silently repairing the input here would delete the refusal instead of
# delivering it — a draft that breaks a semantic rule must stay editable and
# stay flagged, exactly as a zero limit does.
#
# `states` is the ONE exception, and the reason does not generalise to the
# others: it is not a value the validator repairs, it is a field that MEANS
# NOTHING where it is refused. States say where STATUTORY cover is FILED; a
# dollar-limited layer has no filing to describe, so the field quietly becomes
# a general-purpose note and the one check it exists for — the monopolistic
# funds — is never applied to anything. So `set_states` delegates to
# `set_field` and is REFUSED there (`_guard_states`); the write does not land
# and the draft never carries the meaningless value at all.
#
# validate.py keeps `states-non-statutory` regardless. That is a different
# tier: a file hand-edited on disk, or built by `ingest`, never passes through
# this module, and deleting the validator copy would leave those unnetted.


def parse_states(text: str) -> list[str]:
    """`'NY, NJ'` → `['NY', 'NJ']` — the comma-separated entry syntax, defined
    once so every surface splits it the same way.

    Whitespace is trimmed and empty pieces dropped (a trailing comma is a
    typing artefact, not a state). Nothing else is touched: codes are stored
    VERBATIM, and duplicates are NOT collapsed — the validator refuses them by
    name, and swallowing one here would hide the refusal it exists to make.
    """
    return [part.strip() for part in text.split(",") if part.strip()]


def set_states(program: Program, layer_id: str, states: list[str]) -> Layer:
    """Replace a layer's states wholesale. See `parse_states` for the syntax.

    A thin wrapper over `set_field`, not a second door: the guard that refuses
    states on a dollar-limited layer lives at the choke point, so this setter
    and the generic one cannot come to disagree about what the field means."""
    layer = _layer(program, layer_id)
    set_field(
        program,
        "layer",
        "states",
        [state.strip() for state in states if state.strip()],
        layer_id,
    )
    return layer


def set_premium_detail(program: Program, layer_id: str, detail: str | None) -> Layer:
    """The word a ZERO premium prints. Empty is None, so clearing the field
    drops the key from the file rather than writing `""`."""
    layer = _layer(program, layer_id)
    layer.premium_detail = (detail or "").strip() or None
    return layer


def add_named_limit(
    program: Program, layer_id: str, name: str, amount: int
) -> NamedLimit:
    """Append one coordinate limit. Order is the file's order and is display
    order, so a new one lands at the end and is never sorted into place."""
    layer = _layer(program, layer_id)
    named = NamedLimit(name=name, amount=amount)
    layer.named_limits.append(named)
    return named


def edit_named_limit(
    program: Program,
    layer_id: str,
    index: int,
    name: str | None = None,
    amount: int | None = None,
) -> NamedLimit:
    """`None` means leave alone. Named limits have no ids — like retentions and
    sublimits, the caller addresses them by the index a read reported."""
    layer = _layer(program, layer_id)
    _at(list(layer.named_limits), index, "named limit")
    named = layer.named_limits[index]
    if name is not None:
        named.name = name
    if amount is not None:
        named.amount = amount
    return named


def remove_named_limit(program: Program, layer_id: str, index: int) -> None:
    layer = _layer(program, layer_id)
    _at(list(layer.named_limits), index, "named limit")
    layer.named_limits.pop(index)


# -- guarded field set ---------------------------------------------------------
#
# One choke point and one table. Always-derived fields are denied to the
# generic setter outright; the CONDITIONAL ones cannot be, because `attach` is
# free on an ordinary layer and derived on a follows-underlying one, and
# `limit` is free until the statutory flag is set. A generic setter walks past
# every one of those unless the rule lives here, where the TUI, the MCP server
# and anything later all inherit it.
#
# The table is one findable block rather than four rules scattered through the
# entity sections above, and a key absent from it is set with no guard — which
# is what makes a NEW model field writable with no edit to this file.


class GuardRefused(ValueError):
    """A cross-field invariant refusing a write, in words that name the fix.

    Both halves of the base class are load-bearing. It subclasses `ValueError`
    so the MCP server's existing `except (ValidationError, ValueError,
    KeyError, IndexError, RuntimeError)` still catches it and NOTHING is
    written; it is a DISTINCT type so a surface can attach the `guard_refused`
    error code without matching on message text, and so the TUI can catch a
    guard specifically rather than every ValueError a field commit can raise.
    """

    def __init__(self, message: str, code: str = "guard_refused") -> None:
        super().__init__(message)
        self.code = code


# THE one sentence about how the statutory flag moves. Quoted verbatim by the
# three guards below AND by `mcpsurface.GUARDS`, so the live refusal and
# `describe()` cannot say different things — which is precisely the
# two-descriptions drift `mcpsurface` exists to prevent, and which had already
# happened: these guards named a `layer_statutory` tool while GUARDS, one file
# over, said there was no verb for it.
#
# It names NO tool on purpose. The built server registers 23 and none of them
# writes `layer.statutory`; the verb is Phase 2 (`mcpparity.DEFERRED_MUTATIONS`
# carries `set_statutory` for exactly this reason). A refusal that names a call
# the client cannot make refuses the retry too, which is worse than saying
# plainly that there is no call yet — so this names the editor, which works.
STATUTORY_FLAG_FIX = (
    "The statutory flag moves in the towerkit editor (`towerctl edit`) and nowhere "
    "else yet: no tool writes layer.statutory, and the generic setter denies the "
    "field because a bare set writes a file the validator refuses."
)


def _guard_attach(program: Program, layer: Layer, value: object) -> None:
    """`attach` is derived on a follows-underlying layer and zero on a
    statutory one.

    EXEMPT, by name: `heal_follows`, `restack` and `transfer.py` assign
    `layer.attach` directly. They are the DERIVERS of the value this guard
    protects — routed through the choke point they would refuse themselves,
    and every follows-underlying layer would stop healing.
    """
    if layer.follows_underlying:
        raise GuardRefused(
            f"{layer.name}: attach is DERIVED on a follows-underlying layer — it "
            f"reseats itself on the highest underlying top "
            f"({format_money(max(program.underlying_tops(layer).values(), default=0))}) "
            f"on every write. Change the underlying layer's limit, or clear the flag "
            f"first with layer_follows(layer_id={layer.id!r}, follows=false)."
        )
    if layer.statutory:
        # Not in the spec's table, deliberately added: the TUI already blocked
        # attach on a statutory layer, so a guard covering only the follows
        # case would open a hole the editor had closed.
        raise GuardRefused(
            f"{layer.name}: statutory cover owns its column from $0 — the invariant "
            f"is statutory implies attach == 0. Clear the flag first, then set the "
            f"attachment. {STATUTORY_FLAG_FIX}"
        )


def _guard_limit(program: Program, layer: Layer, value: object) -> None:
    """`statutory ⇒ limit == 0` is what keeps the bar out of every dollar
    total by construction, and out of the global vertical scale."""
    if layer.statutory:
        raise GuardRefused(
            f"{layer.name}: statutory cover has no dollar limit — the invariant is "
            f"statutory implies limit == 0. Clear the flag first, then set the "
            f"limit. {STATUTORY_FLAG_FIX}"
        )


def _guard_states(program: Program, layer: Layer, value: object) -> None:
    """States are a coverage FACT about a statutory layer, not a note.

    Clearing to `[]` is always allowed: a layer that has just lost its
    statutory flag must stay tidyable, or the guard strands exactly the data
    it exists to keep off dollar-limited layers.

    The four states that cannot be privately covered at all — ND, OH, WA, WY —
    are `validate.MONOPOLISTIC_STATES` and STAY there. This guard decides only
    whether the field may be written; whether the codes name a monopolistic
    fund, or a US jurisdiction at all, is validate's question. A second copy of
    that list here is the exact bug this pass exists to kill.
    """
    if layer.statutory or not value:
        return
    raise GuardRefused(
        f"{layer.name}: states say where STATUTORY cover is FILED; a dollar-"
        f"limited layer cannot carry them, or the field becomes a general-purpose "
        f"note. Put the text in notes, which takes it today — or make the layer "
        f"statutory first. {STATUTORY_FLAG_FIX}"
    )


def _advise_currency(program: Program, value: object) -> list[Diagnostic]:
    """NOT a refusal — an advisory, because the write does far less than it
    looks like it does and silence about that was the failure mode.

    The second sentence is not decoration. `currency` is read in exactly three
    places (the canonical dump, the MCP read, and construction in `ingest`),
    and `money.py` hard-codes "USD" into `format_currency` and
    `format_compact_currency` — so setting it changes the file and the read,
    and NOTHING a reader of the chart, the SOI or the CLI ever sees.
    """
    return [
        Diagnostic(
            WARNING,
            "currency-not-converted",
            f"currency is now {value}: NO figure was converted — every amount is the "
            f"same integer it was, and only the label moved. towerkit formats money as "
            f"US dollars wherever it renders, so this changes the file and program_read "
            f"and nothing a reader of the chart or SOI sees. Restate the amounts "
            f"yourself if the program was re-quoted.",
            ("program", None),
        )
    ]


# Keyed by (mcpsurface kind, field). Absent from both tables means "set it".
# `field` is the CANONICAL name — the alias where there is one — and a nested
# scalar is keyed by its dotted path exactly as the surface advertises it
# (`("program", "period.start")`), so a cross-field rule can be added there
# without touching `set_field`.
_GUARDS: dict[tuple[str, str], Callable[[Program, Any, object], None]] = {
    ("layer", "attach"): _guard_attach,
    ("layer", "limit"): _guard_limit,
    ("layer", "states"): _guard_states,
}

_ADVISORIES: dict[tuple[str, str], Callable[[Program, object], list[Diagnostic]]] = {
    ("program", "currency"): _advise_currency,
}

# Kinds addressed by a list index into a PROGRAM-level collection.
_INDEXED: dict[str, str] = {"retention": "retentions", "sublimit": "sublimits"}

# Kinds whose rows hang off a LAYER: the address is the layer id AND the index
# of the row within that layer, because neither half identifies a row on its
# own. Two layers each have a participant 0, and a carrier name is not unique.
_LAYER_ROWS: dict[str, str] = {
    "participant": "participants",
    "named_limit": "named_limits",
}

# Named here rather than imported from `mcpsurface`: that module imports THIS
# one, and a kind roster is not worth a cycle.
_KINDS: tuple[str, ...] = ("program", "line", "layer", *_LAYER_ROWS, *_INDEXED)


def _entity(
    program: Program, kind: str, target: str | int | None, index: int | None = None
) -> Any:
    """The object `field` is set on.

    `target` is the id for a line or layer, the list index for a retention or
    sublimit, the LAYER id for a participant or named limit, and None for the
    program. `index` addresses the row within a layer and is meaningless for
    every other kind.
    """
    if kind == "program":
        return program
    if target is None:
        raise ValueError(f"{kind} needs a target")
    if kind == "line":
        return _line(program, str(target))
    if kind == "layer":
        return _layer(program, str(target))
    attr = _LAYER_ROWS.get(kind)
    if attr is not None:
        # These were refused outright until 2026-08-19, on the grounds that one
        # `target` cannot carry both halves of the address — but the surface
        # already advertised `participant.carrier` and `.share_bps` as writable
        # with no denial reason, so every such write reached that refusal and
        # died on an internal function name. The address the surface publishes
        # (`mcpsurface.TARGET`: target AND index) is the address taken here.
        layer = _layer(program, str(target))
        rows = getattr(layer, attr)
        what = kind.replace("_", " ")
        if index is None:
            raise ValueError(
                f"a {what} is addressed by layer id and index; layer {layer.id!r} has "
                f"{len(rows)} — pass the index program_read reported for the row"
            )
        _at(list(rows), int(index), what)
        return rows[int(index)]
    attr = _INDEXED.get(kind)
    if attr is None:
        raise ValueError(f"no such kind {kind!r}; kinds are {', '.join(_KINDS)}")
    items = getattr(program, attr)
    position = int(target)
    _at(list(items), position, kind)
    return items[position]


def _resolve(entity: Any, kind: str, field: str) -> tuple[str, str]:
    """(attribute, canonical) for a field addressed by EITHER name.

    Callers name fields as the FILE does (`policyNumber`), the model names
    them as Python does (`policy_number`), and both have to reach the same
    guard key — otherwise a caller spelling it the other way slips past."""
    for name, info in type(entity).model_fields.items():
        alias = info.alias or name
        if field in (name, alias):
            return name, alias
    raise KeyError(f"no {kind} field {field!r}")


def set_field(
    program: Program,
    kind: str,
    field: str,
    value: object,
    target: str | int | None = None,
    index: int | None = None,
) -> list[Diagnostic]:
    """Set one field, through the guards. THE choke point.

    Every generic write takes this path and the named setters delegate to it,
    never the reverse: a generic setter that fell back to a bare `setattr` for
    fields with no named setter would mean a newly guarded field needs editing
    in two files again, which is the rot this whole pass exists to stop.

    Returns write-time advisories — statements about what the write did NOT
    do. They are not validator diagnostics: re-reading the file will not
    reproduce them, which is why they must not be merged into `warnings`.

    `target` and `index` together are the address; which halves a kind needs
    is `mcpsurface.TARGET`, and a participant or a named limit needs both
    because its row hangs off a layer.

    Raises `GuardRefused` for a guard, `KeyError` for an unknown target or
    field, `IndexError` for an out-of-range index, and `ValueError` /
    pydantic's `ValidationError` for a value the model refuses.
    """
    entity = _entity(program, kind, target, index)
    head, _, rest = field.partition(".")
    attr, canonical = _resolve(entity, kind, head)
    owner = entity
    if rest:
        # Scalars nested one level are addressed by dotted path; the
        # CONTAINING object is denied, so setting one member can never blank
        # its siblings the way a wholesale set would.
        container = getattr(entity, attr)
        if container is None:
            raise ValueError(
                f"{kind}.{canonical} is not set, so there is nothing for {field!r} "
                f"to write into"
            )
        sub_attr, sub_canonical = _resolve(container, canonical, rest)
        # The nested write REJOINS the guard path rather than returning here.
        # The contract of `_GUARDS` is that a key absent from it is set with no
        # guard — which is what makes a new model field writable with no edit
        # to this module. A dotted write that returned early made that contract
        # silently false for every nested scalar: `period.start`,
        # `period.end` and the render flags are exactly where the next
        # cross-field rule (start before end) belongs, and it would have been
        # dead on arrival with nothing failing.
        owner, attr, canonical = container, sub_attr, f"{canonical}.{sub_canonical}"
    # `entity` is what the guard sees, never `owner`: a rule about
    # `program.period.start` reads the period's other half, and a rule about a
    # layer's period reads the layer.
    guard = _GUARDS.get((kind, canonical))
    if guard is not None:
        guard(program, entity, value)
    setattr(owner, attr, value)
    advise = _ADVISORIES.get((kind, canonical))
    return advise(program, value) if advise is not None else []


def set_container(
    program: Program,
    kind: str,
    field: str,
    container: Any,
    target: str | int | None = None,
    index: int | None = None,
) -> Any:
    """Materialise the MISSING parent of a dotted field — `program.render`
    before `render.showTotals` can be written.

    The write lives here rather than in the surface that noticed the gap.
    `program.render` and `layer.period` are on the denylist: they are exactly
    the objects no caller may set wholesale, because setting one blanks every
    sibling. A surface that constructs one with a bare `setattr` is a second
    definition of when that is allowed, in the one module the denylist is
    supposed to keep out of the business — and the TUI, which creates the same
    two containers, would not inherit it.

    So the rule is stated once, here:

    - the field must be dotted. A container is only ever materialised on the
      way to a scalar inside it; there is no "create an empty render".
    - the container must be ABSENT. Auto-creation replaces nothing — an
      existing object is the caller's data and overwriting it is the
      sibling-blanking write the denylist exists to prevent.

    Deciding WHICH containers can be defaulted is the surface's question, not
    this one's: it reads the model's required fields (`Period` needs both
    dates and so is never defaulted) and hands the built object here.
    """
    entity = _entity(program, kind, target, index)
    head, _, rest = field.partition(".")
    if not rest:
        raise ValueError(
            f"{kind}.{field} is not a dotted path; a container is materialised only "
            f"on the way to a scalar inside it"
        )
    attr, canonical = _resolve(entity, kind, head)
    if getattr(entity, attr) is not None:
        raise ValueError(
            f"{kind}.{canonical} is already set; it is denied to a wholesale write "
            f"because replacing it blanks every field of it the caller did not send"
        )
    setattr(entity, attr, container)
    return container


def set_statutory(program: Program, layer_id: str, statutory: bool) -> Layer:
    """Set the flag that OWNS `statutory ⇒ limit == 0`, forcing the invariant
    rather than recording an intent that the validator then refuses.

    This is the last copy of that invariant, which lived in a closure inside
    the TUI's statutory checkbox. It does NOT go through `set_field`: the flag
    is denied to the generic setter precisely because it carries these three
    consequences, and routing the zeros through the choke point would have
    `_guard_limit` refuse the write it is performing.

    Clearing the flag does not invent a limit, and deliberately does not drop
    any `states` already there: guard C blocks WRITING them onto a
    dollar-limited layer, and deleting typed data to satisfy a rule the
    validator already reports by name (`states-non-statutory`) would be the
    silent repair this module refuses everywhere else.
    """
    layer = _layer(program, layer_id)
    layer.statutory = statutory
    if statutory:
        # A statutory layer owns its column from $0 with no dollar limit, and
        # has nothing beneath it to follow.
        layer.limit = 0
        layer.attach = 0
        layer.follows_underlying = False
    return layer
