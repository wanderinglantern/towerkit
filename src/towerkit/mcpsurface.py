"""The write surface, DERIVED from `model.py` — kind → field → entry.

The MCP server shipped with a hand-written table of what a program contains.
Three layer detail fields, `namedLimits`, `states`, the per-layer period and
the whole of `RenderSettings` were added to the model afterwards and none of
them ever reached the connector, because reaching it meant remembering a
second file. Nobody remembered. This module is that table computed instead of
typed: add a field to `model.py` and it is writable, with its type, its rules
and its refusal text, and nothing here changes.

Three things are declared rather than derived, and each is declared because a
rule cannot know it:

- `DENIED` — the reviewed denylist. Fifteen rows, each with the reason
  `describe` prints back to the caller. Grant reviewed it on 2026-08-19 and
  struck `program.currency` out, so currency is writable.
- `GUARDS` — the *documentation* of the conditional rules. The rules
  themselves live in `edit.py`, where the TUI inherits them too; a guard in a
  surface is a guard the other two surfaces do not have.
- `VALUE_RULES` — the money/date/enum prose, in ONE place. bookkit shipped two
  tool descriptions that said money was cents and dollars respectively; a
  description that reprints a rule is the second table again, in prose.

Everything else follows from the model:

- The advertised name is the JSON **alias** where a field has one (`policyNumber`,
  `appliesTo`, `$schema`), the python name where it does not. That is what the
  broker sees in the file and in `schema/program.schema.json`, and what
  `program_read` already returns. The cost is paid here: pydantic refuses
  `setattr` by alias, so every entry also carries the python path the setter
  uses.
- A field whose annotation is a model is ALWAYS denied as a container and
  ALWAYS expands to `<container>.<child>` for every child — which is exactly
  the three container rows on the denylist, and auto-denies any nested model
  added later. Depth is one; deeper raises at build time rather than leaving a
  field silently unreachable.
- Money is knowable only from `model.MONEY`. `attach` is `int` with `ge=0` and
  so is `Participant.share_bps`; `limit` carries no constraint at all. A
  MONEY_FIELDS set here would be the second table this module exists to kill.

Imports stay narrow on purpose: `model`, `money`, `dates` and `edit`. Never
`mcpserver` — it imports this, and the reverse is a cycle — and never a
plotting library, the `scale.py` / `layout.py` rule extended to the spine.

CHECK ORDER for a compare-and-set write, which decides which error code comes
back and is part of the contract: resolve program → session/sha gate → resolve
entity and index → row guard → `denied_reason` → `resolve` → parent exists or
is defaultable → `parse_expecting` → `parse_value` → `compare_values` →
`apply` (edit.py's guards) → hard validate.
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from . import edit
from .dates import parse_flexible_date
from .model import (
    MONEY,
    Layer,
    Line,
    NamedLimit,
    Participant,
    Program,
    Retention,
    Sublimit,
)
from .money import BPS_SCALE, MoneyParseError, format_money, format_share, parse_money

if TYPE_CHECKING:  # runtime import would only re-enter through `edit`
    from .validate import Diagnostic

# `Period` and `RenderSettings` are NOT kinds. They are reached as dotted
# children only — a kind for them would be a second way to set the same
# scalar, and two ways to set one thing is how two definitions drift.
KIND_MODELS: dict[str, type[BaseModel]] = {
    "program": Program,
    "line": Line,
    "layer": Layer,
    "participant": Participant,
    "retention": Retention,
    "sublimit": Sublimit,
    "named_limit": NamedLimit,
}

KINDS: tuple[str, ...] = tuple(KIND_MODELS)

# Which arguments address a row of this kind. `target` is the line id or the
# layer id (and the LAYER id for a participant or a named limit, which hang off
# one); `index` addresses the four collections that have no ids.
TARGET: dict[str, list[str]] = {
    "program": [],
    "line": ["target"],
    "layer": ["target"],
    "participant": ["target", "index"],
    "retention": ["index"],
    "sublimit": ["index"],
    "named_limit": ["target", "index"],
}

# The existing `expecting_lines` / `expecting_name` idea, unified under one
# argument name. An index is a position in a list a caller read a moment ago;
# the row guard is what proves the list has not moved underneath them.
ROW_GUARD: dict[str, str | None] = {
    "program": None,
    "line": None,
    "layer": None,
    "participant": "carrier",
    "retention": "appliesTo",
    "sublimit": "name",
    "named_limit": "name",
}


# --- the denylist ------------------------------------------------------------
#
# Reviewed by Grant, 2026-08-19. One row per line so a line can be struck out;
# `program.currency` was, and is writable. The reasons are printed verbatim by
# `describe`, so they are addressed to the caller, not to us.
#
# The three container rows are ALSO produced by a rule (a field whose type is a
# model is denied and expanded), and `build_surface` refuses to build if a
# container is missing from here — the row survives so the reason can be shown.

DENIED: dict[str, str] = {
    "program.$schema": (
        "Owned by the writer. Setting it makes the file claim conformance to a "
        "schema it was not validated against."
    ),
    "program.period": (
        "The object. Its scalars stay writable by dotted path — period.start and "
        "period.end — so setting one can never blank the other."
    ),
    "program.render": (
        "The object. Its scalars stay writable by dotted path — render.showTotals "
        "and the rest — so setting one can never blank its siblings."
    ),
    "program.lines": (
        "Verb-owned: line_add, line_edit, line_move, line_remove. Removing a line "
        "cascades to every layer, retention and sublimit left with an empty "
        "appliesTo."
    ),
    "program.layers": (
        "Verb-owned: layer_remove, layer_lines, layer_follows, program_restack, and "
        "program_edit_field for one layer field at a time. A wholesale set would "
        "rewrite every attachment, participant and named limit in the tower in one "
        "unguarded step. There is no tool that ADDS a layer yet — that is Phase 2, "
        "and until it ships a new layer comes from program_clone_renewal."
    ),
    "program.retentions": (
        "Verb-owned: retention_add, retention_edit, retention_remove — so every "
        "appliesTo is checked against the line ids before the write."
    ),
    "program.sublimits": (
        "Verb-owned: sublimit_add, sublimit_edit, sublimit_remove — so every "
        "appliesTo is checked against the line ids before the write."
    ),
    "line.id": (
        "Ids slug from the name on first naming and are stable afterwards. Rename "
        "through line_edit; the id cascade is part of that verb."
    ),
    "layer.id": (
        "Ids are assigned on add and are stable afterwards. Nothing a caller "
        "reads depends on their text."
    ),
    "layer.period": (
        "The object. Its scalars stay writable by dotted path — period.start and "
        "period.end — so setting one can never blank the other."
    ),
    "layer.followsUnderlying": (
        "Already a verb — layer_follows — which heals the attachment as it sets the "
        "flag. Two ways to set one thing is how two definitions drift."
    ),
    "layer.appliesTo": (
        "Verb-owned: layer_lines. It validates every line id before the write, and "
        "an unknown id would strand the layer in no column at all."
    ),
    "layer.namedLimits": (
        "Order is the file's order and is display order, never sorted. A wholesale "
        "set silently reorders what a broker arranged. Named limits are edited row "
        "by row with program_edit_field (kind named_limit); no tool adds or removes "
        "a row yet — that is Phase 2."
    ),
    "layer.statutory": (
        "Carries the invariant statutory implies limit == 0. A bare set writes a "
        "file the validator refuses."
    ),
    "layer.participants": (
        "Verb-owned, plus the over-signing veto: the shares on a layer have to add "
        "up before the write, not after it. One existing row is edited with "
        "program_edit_field (kind participant); no tool adds or removes a "
        "participant yet — that is Phase 2, so a layer with no participants stays "
        "pending until it ships."
    ),
}


# --- the conditional rules, documented -----------------------------------------
#
# The rules themselves are `edit.py`'s `_GUARDS` / `_ADVISORIES`, so the TUI and
# anything later inherit them. These strings are what `describe` shows, and they
# name a fix the caller can carry out TODAY: a refusal that points at a verb
# Phase 1 has not shipped refuses the retry too.
#
# Anything either side could word differently is QUOTED from `edit.py`, never
# restated. `edit.STATUTORY_FLAG_FIX` is the whole of what a caller can do about
# the statutory flag, and it is interpolated below rather than paraphrased —
# because these strings and the live refusals had already drifted into saying
# opposite things (the guards named a `layer_statutory` tool; this table said
# there was no verb for it), which is the two-descriptions bug in miniature.

GUARDS: dict[str, str] = {
    "layer.attach": (
        "Refused on a follows-underlying layer, where the attachment is derived "
        "from the layers beneath it and reseats itself on every write — change the "
        "underlying layer's limit, or clear followsUnderlying with layer_follows "
        "first. Refused on a statutory layer, which owns its column from $0. "
        f"{edit.STATUTORY_FLAG_FIX}"
    ),
    "layer.limit": (
        "Refused on a statutory layer: statutory cover has no dollar limit, and "
        f"the invariant is statutory implies limit == 0. {edit.STATUTORY_FLAG_FIX}"
    ),
    "layer.states": (
        "Refused on a dollar-limited layer. States say where STATUTORY cover is "
        "filed; on a priced layer the field would become a general-purpose note. "
        f"Clearing to [] is always allowed. {edit.STATUTORY_FLAG_FIX}"
    ),
    "layer.limitsDetail": (
        "Written, and nothing blocks. The layer's own detail diagnostics come back "
        "on the response."
    ),
    "layer.retentionDetail": (
        "Written, and nothing blocks. The layer's own detail diagnostics come back "
        "on the response."
    ),
    "layer.premiumDetail": (
        "Written, and nothing blocks. The layer's own detail diagnostics come back "
        "on the response."
    ),
    "layer.period.start": (
        "A layer period needs both dates, so this refuses on a layer that has no "
        "period yet, and Phase 1 has no call that creates one. Setting it from the "
        "program period is deliberately NOT done: it would write an expiry the "
        "caller never supplied into the field the Schedule of Insurance reads."
    ),
    "layer.period.end": (
        "A layer period needs both dates, so this refuses on a layer that has no "
        "period yet, and Phase 1 has no call that creates one. Setting it from the "
        "program period is deliberately NOT done: it would write an expiry the "
        "caller never supplied into the field the Schedule of Insurance reads."
    ),
    "program.currency": (
        # QUOTED from `edit.py`, never restated — the rule this table's own
        # header sets, broken here until 2026-08-19. The restatement had
        # dropped the load-bearing half: money.py hard-codes USD, so the label
        # moves nowhere a reader can see.
        f"Written, and the response warns: {edit.CURRENCY_NOT_CONVERTED}"
    ),
    "participant.share_bps": (
        "Written, and nothing blocks. One row's share is set in place; the shares "
        "on the layer are NOT re-proportioned and the sum is not vetoed here, so "
        "an edit that takes the layer past 100% comes back as the layer-oversigned "
        "diagnostic in `errors` and the write still lands — a tower under "
        "construction is invalid by construction. The veto belongs to the add and "
        "remove verbs, which write a share the caller never read; those are Phase 2."
    ),
}


# --- the value rules, in one place --------------------------------------------
#
# THE only home for these sentences. A tool description cites the rule by NAME
# ("money, see describe"); it never reprints one, because prose is where the
# first field table rotted. `test_value_rules_have_one_owner` greps for it.

VALUE_RULES: dict[str, str] = {
    "money": (
        "Whole dollars. Send '5m', '250k', '$5,000,000', '5000000', or a JSON "
        "integer of dollars. Never cents, never a decimal. Values come back as "
        "'$5,000,000'. A leading minus is accepted in the same forms ('-$5'), "
        "and whether the field will take one is its own bound, published as "
        "`bounds` beside its type."
    ),
    "date": (
        "Send a string. ISO ('2026-06-01') is exact; '6/1/2026' is month/day/year. "
        "Stored and returned as ISO."
    ),
    "enum": "Exactly one of the listed lowercase strings.",
    "bool": "JSON true or false. 'true', 'yes', 1 and 0 are refused.",
    "list_of_strings": (
        "A JSON array of strings. `states` also accepts one comma-separated string "
        "('NY, NJ')."
    ),
    "share_bps": "Basis points, an integer 0-10000. 3500 is 35%.",
    "text": "Free text, sent as a string.",
    "clearing": (
        "Send null to clear an optional field and drop the key from the file; an "
        "empty string does the same for free text. A required field refuses both. "
        "In `expecting` the two part company: null means UNSET and an empty string "
        "means the empty string itself, which is a value a text field can hold."
    ),
}

# `stale_value` is not in the design doc's list, which has no code for a
# compare-and-set mismatch. `stale_sha` would be a lie — nothing about the
# file hash is wrong — so it is added here and covers both the row guard and
# the field-level `expecting` mismatch.
#
# `internal_error` is likewise absent from the design doc, and is the code for
# a failure that is nobody's contract: an exception the write path did not
# anticipate. It exists because one escaped. A `TypeError` raised inside
# `mcpsurface.apply` fell through `mcpserver._write`'s except clause and
# reached the client as a bare `Error executing tool program_edit_field: ...`
# with no `[code] ` prefix at all — outside the stable-code guarantee this
# branch is built on, and invisible to a caller catching `edit.Refusal`. A
# code that says "this is a towerkit bug, not your value" is worth having;
# silence is not.
ERROR_CODES: tuple[str, ...] = (
    "stale_sha",
    "stale_value",
    "not_read",
    "outside_roots",
    "no_such_program",
    "no_such_target",
    "denied_field",
    "guard_refused",
    "bad_value",
    "exists",
    "no_snapshot",
    "internal_error",
)

# Fields whose named setter in `edit.py` does something a plain assignment
# cannot. Everything absent routes through `edit.set_field`, THE choke point,
# which is what makes a newly guarded field need no edit here.
#
# There is exactly ONE row, and the wildcards that used to sit beside it are
# the reason to keep it that way. `retention.*`, `sublimit.*` and
# `named_limit.*` routed every field of three kinds into `edit.edit_retention`,
# `edit.edit_sublimit` and `edit.edit_named_limit` as `**{python_name: value}`
# — and those three functions' explicit keyword parameters ARE a hand-written
# enumeration of the three models. Nothing cross-checked them, so a field added
# to `Retention` was ADVERTISED writable by `describe()` and came back
# `edit_retention() got an unexpected keyword argument 'broker_ref'` over the
# wire: the sixth field table, inside the module whose whole purpose is to be
# the single published truth. `_check_lines` — the one thing those functions
# added — now runs at the choke point (`edit._NORMALISERS`), so the guard
# survives and the enumeration does not.
_SETTERS: dict[str, str] = {
    # The id follows the name and cascades through every appliesTo. This is the
    # direct consequence of denying `line.id`: a plain write strands every
    # reference on the old slug. (`layer.name` is the opposite — layer ids come
    # from `unique_id(program, "layer")`, never from the name.)
    "line.name": "rename_line",
}

# The one list[str] that also accepts the comma-separated form, because
# `edit.parse_states` is already the single definition of that syntax and the
# TUI enters states that way. The flag is on the ENTRY, never on the type: a
# second writable list[str] must not inherit the ambiguity by accident.
_COMMA_STRING: frozenset[str] = frozenset({"layer.states"})


class BadValue(ValueError):
    """A wire value the field cannot take, named in the field's own lexicon."""

    code = "bad_value"


@dataclass(frozen=True)
class Entry:
    """One writable field. `field` is what the caller says; `path` is what
    pydantic accepts — `setattr(layer, 'policyNumber', x)` raises."""

    kind: str
    field: str
    path: tuple[str, ...]
    type: str
    required: bool
    clearable: bool
    values: tuple[str, ...] | None = None
    guard: str | None = None
    setter: str | None = None
    container: str | None = None
    # `builtins.type` spelled out: the `type` FIELD above shadows the builtin
    # inside this class body, and `type[BaseModel]` would name a `str`.
    container_model: builtins.type[BaseModel] | None = None
    accepts_comma_string: bool = False
    # A `text` field's own length bounds, read off the model's constraints so
    # the surface refuses what the model would refuse — with a message the
    # caller can act on. See `_length`.
    min_length: int | None = None
    max_length: int | None = None
    # The same thing for the numeric types, read off `ge` / `le`. See `_bounds`.
    minimum: int | None = None
    maximum: int | None = None


# --- derivation ----------------------------------------------------------------


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _flatten(info: FieldInfo) -> tuple[Any, list[Any], bool]:
    """(base type, all metadata, is-optional).

    Two shapes have to be handled, because pydantic flattens them differently:
    `Money` lands its members in `FieldInfo.metadata`, while `Money | None`
    keeps them on the inner `Annotated`'s own `__metadata__` and leaves
    `FieldInfo.metadata` empty. Both have to find the MONEY tag or `premium`
    and `Retention.aggregate` stop being money.
    """
    annotation: Any = info.annotation
    metadata: list[Any] = list(info.metadata)
    optional = False
    if get_origin(annotation) in (Union, UnionType):
        args = get_args(annotation)
        inner = [arg for arg in args if arg is not type(None)]
        optional = len(inner) < len(args)
        if len(inner) != 1:
            raise RuntimeError(f"the surface cannot read the union {annotation!r}")
        annotation = inner[0]
    nested = getattr(annotation, "__metadata__", None)
    if nested is not None:
        metadata = [*metadata, *nested]
        annotation = annotation.__origin__
    # One more level, and it is the difference between `Money` and
    # `Money | None` carrying the same rules. `Money` is
    # `Annotated[int, Field(ge=0), MONEY]`: pydantic flattens the `Field` into
    # `FieldInfo.metadata` as `Ge(0)` when the field is required, and leaves
    # the raw `FieldInfo` in the annotation's own `__metadata__` when it is
    # optional. Unexpanded, `layer.premium` — `Money | None` — advertised no
    # minimum while `layer.attach` advertised `ge=0`, from the identical alias.
    # The MONEY tag survived either way, which is why the difference went
    # unnoticed: the type was right and the bound was gone.
    return annotation, [*_expanded(metadata)], optional


def _expanded(metadata: list[Any]) -> Iterator[Any]:
    for item in metadata:
        yield item
        if isinstance(item, FieldInfo):
            yield from item.metadata


def _length(metadata: list[Any]) -> tuple[int | None, int | None]:
    """A string field's `min_length`/`max_length`, as pydantic records them.

    Read from the MODEL rather than declared here, for the reason the whole
    module exists. `Program.currency` carries `min_length=3, max_length=3`;
    without this the only thing enforcing it is pydantic's assignment
    validator, whose refusal reaches the client as a four-line
    `1 validation error for Program\ncurrency\n  String should have at least
    3 characters [type=string_too_short, ...]`. That breaks the standard the
    design doc sets for every refusal — name a value that WOULD be accepted —
    and a model handed it has to guess what the rule is.
    """
    low: int | None = None
    high: int | None = None
    for item in metadata:
        bound = getattr(item, "min_length", None)
        if isinstance(bound, int):
            low = bound
        bound = getattr(item, "max_length", None)
        if isinstance(bound, int):
            high = bound
    return low, high


def _bounds(metadata: list[Any]) -> tuple[int | None, int | None]:
    """A numeric field's `ge` / `le`, as pydantic records them.

    The counterpart to `_length`, and it was missing. `layer.attach` is
    `Money`, which is `Annotated[int, Field(ge=0), MONEY]`, and
    `program_edit_field(kind="layer", field="attach", value=-1)` came back as a
    raw four-line pydantic repr with a documentation URL — the same defect the
    `Program.currency` `min_length` fix closed for strings, left open on money.
    `model.py:246` states the rule: a bound on the MODEL is the difference
    between a write being REFUSED and a bad file being written and reported.

    Read here so the refusal can name a value that would be accepted. Not a
    second rule: pydantic refuses the same value a moment later on assignment.

    `layer.limit` deliberately carries no `ge`, so it has no bound here either
    and a negative limit stays writable — a draft state `validate.py` reports
    by name. That is exactly why this is read off the model and not declared.
    """
    low: int | None = None
    high: int | None = None
    for item in metadata:
        bound = getattr(item, "ge", None)
        if isinstance(bound, int) and not isinstance(bound, bool):
            low = bound
        bound = getattr(item, "le", None)
        if isinstance(bound, int) and not isinstance(bound, bool):
            high = bound
    return low, high


def _classify(where: str, base: Any, metadata: list[Any]) -> tuple[str, tuple[str, ...] | None]:
    """The advertised type, and the enum's values when it has any.

    An annotation this cannot name raises rather than defaulting to text. A
    field whose type is misclassified is exactly the mistake the module exists
    to prevent, and a quiet default is how one ships.
    """
    if any(item is MONEY for item in metadata):
        return "money", None
    if base is date:
        return "date", None
    if isinstance(base, type) and issubclass(base, Enum):
        return "enum", tuple(str(member.value) for member in base)
    if base is bool:  # BEFORE int: bool is a subclass of it
        return "bool", None
    if get_origin(base) is list and get_args(base) == (str,):
        return "list_of_strings", None
    if base is int:
        if _bounds(metadata)[1] == BPS_SCALE:
            return "share_bps", None
        raise RuntimeError(
            f"{where} is an int the surface cannot name: tag it model.MONEY if it is "
            f"dollars, bound it to 10000 if it is basis points"
        )
    if base is str:
        return "text", None
    raise RuntimeError(f"{where} has a type the surface cannot advertise: {base!r}")


def _setter_for(kind: str, field: str) -> str | None:
    """One field, one lookup. The `{kind}.*` wildcard this used to fall back
    on is what let three kinds route past the choke point into functions whose
    parameter lists were a second copy of the models."""
    return _SETTERS.get(f"{kind}.{field}")


def _entry(
    kind: str,
    field: str,
    path: tuple[str, ...],
    info: FieldInfo,
    base: Any,
    metadata: list[Any],
    optional: bool,
    container: str | None = None,
    container_model: type[BaseModel] | None = None,
) -> Entry:
    key = f"{kind}.{field}"
    kind_of, values = _classify(key, base, metadata)
    low, high = _length(metadata) if kind_of == "text" else (None, None)
    floor, ceiling = _bounds(metadata) if kind_of in ("money", "share_bps") else (None, None)
    return Entry(
        kind=kind,
        field=field,
        path=path,
        type=kind_of,
        required=info.is_required(),
        clearable=optional,
        values=values,
        guard=GUARDS.get(key),
        setter=_setter_for(kind, field),
        container=container,
        container_model=container_model,
        accepts_comma_string=key in _COMMA_STRING,
        min_length=low,
        max_length=high,
        minimum=floor,
        maximum=ceiling,
    )


def build_surface(
    models: Mapping[str, type[BaseModel]] | None = None,
    denied: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Entry]]:
    """kind → advertised field → entry, in MODEL declaration order.

    Declaration order IS canonical file order — `model.program_to_jsonable`
    derives the file's key order from the same `model_fields` this walks — so
    `describe` reads like the file a broker opens. Never alphabetical.
    """
    models = KIND_MODELS if models is None else models
    denied = DENIED if denied is None else denied

    for key in denied:
        kind, _, field = key.partition(".")
        owner = models.get(kind)
        if owner is None:
            raise RuntimeError(f"the denylist names an unknown kind: {key}")
        if field not in {info.alias or name for name, info in owner.model_fields.items()}:
            raise RuntimeError(f"the denylist names a field {kind} does not have: {key}")

    surface: dict[str, dict[str, Entry]] = {}
    for kind, cls in models.items():
        fields: dict[str, Entry] = {}
        for name, info in cls.model_fields.items():
            alias = info.alias or name
            key = f"{kind}.{alias}"
            base, metadata, optional = _flatten(info)
            element = get_args(base)[0] if get_origin(base) is list and get_args(base) else None
            if _is_model(base) or _is_model(element):
                # A field whose type is a model is denied as a container BY
                # RULE, so a nested model added later cannot arrive writable.
                # The denylist row survives only to carry the reason.
                if key not in denied:
                    raise RuntimeError(
                        f"{key} is a model and is denied by rule; add it to DENIED with "
                        f"the reason describe() should print"
                    )
                if _is_model(base):
                    fields.update(_expand(kind, alias, name, base))
                continue
            if key in denied:
                continue
            fields[alias] = _entry(kind, alias, (name,), info, base, metadata, optional)
        surface[kind] = fields
    return surface


def _expand(
    kind: str, alias: str, name: str, container: type[BaseModel]
) -> dict[str, Entry]:
    """`period` → `period.start`, `period.end`. Depth is exactly one."""
    out: dict[str, Entry] = {}
    for child_name, child_info in container.model_fields.items():
        child_alias = child_info.alias or child_name
        base, metadata, optional = _flatten(child_info)
        if _is_model(base):
            # Loud, never a silent skip that leaves a field unreachable with
            # nothing saying so — the same call the canonical serialiser's
            # `_check_nothing_was_dropped` makes.
            raise RuntimeError(
                f"{kind}.{alias}.{child_alias} nests a model inside a nested model; "
                f"the surface addresses scalars one level deep only"
            )
        field = f"{alias}.{child_alias}"
        out[field] = _entry(
            kind,
            field,
            (name, child_name),
            child_info,
            base,
            metadata,
            optional,
            container=alias,
            container_model=container,
        )
    return out


SURFACE: dict[str, dict[str, Entry]] = build_surface()


# --- addressing ----------------------------------------------------------------


def denied_reason(kind: str, field: str) -> str | None:
    """Consulted BEFORE `resolve`: 'that field does not exist' and 'you may
    not write that field' are different problems and a caller retries them
    differently."""
    return DENIED.get(f"{kind}.{field}")


def resolve(kind: str, field: str) -> Entry:
    fields = SURFACE.get(kind)
    if fields is None:
        raise KeyError(f"no kind {kind!r}; kinds are {', '.join(KINDS)}")
    entry = fields.get(field)
    if entry is None:
        raise KeyError(f"no {kind} field {field!r}")
    return entry


def subject(kind: str, target: str | None, index: int | None) -> str:
    """How a refusal names the row, in the words of the thing the broker is
    looking at."""
    what = kind.replace("_", " ")
    if kind == "program":
        return "program"
    if index is None:
        return f"{what} {target!r}"
    if target is None:
        return f"{what} {index}"
    return f"{what} {index} on layer {target!r}"


# --- containers that are not there yet -----------------------------------------


def container_defaultable(entry: Entry) -> bool:
    """Can the missing parent be materialised without inventing data?

    `RenderSettings()` is valid with no arguments, so `render.showTotals`
    auto-creates it. `Period` requires both dates, so it cannot — and seeding a
    layer period from the program period is deliberately not done: it would
    write an expiry the caller never supplied into the field the Schedule of
    Insurance reads.
    """
    model = entry.container_model
    if model is None:
        return False
    return not any(info.is_required() for info in model.model_fields.values())


def create_container(entry: Entry) -> tuple[BaseModel, str]:
    """The new parent and a note saying exactly what was written into it —
    every default, not just the one the caller asked for, because the caller
    is now responsible for values they never sent."""
    model = entry.container_model
    if model is None or not container_defaultable(entry):
        raise ValueError(f"{entry.field} has no container that can be defaulted")
    created = model()
    written = []
    for name, info in model.model_fields.items():
        value = getattr(created, name)
        if value is None:  # dropped from the file, so not "written"
            continue
        shown = "true" if value is True else "false" if value is False else str(value)
        written.append(f"{info.alias or name}={shown}")
    child = entry.field.split(".", 1)[1]
    return created, (
        f"created {entry.container} with defaults ({', '.join(written)}); "
        f"you changed {child}"
    )


def missing_container_message(entry: Entry, who: str) -> str:
    model = entry.container_model
    required: list[str] = (
        [info.alias or name for name, info in model.model_fields.items() if info.is_required()]
        if model is not None
        else []
    )
    return f"{who} has no {entry.container}; {' and '.join(required)} must be set together"


# --- the wire lexicon -----------------------------------------------------------


def parse_value(entry: Entry, value: object) -> Any:
    """One wire value into the model's own type, or `BadValue` naming a form
    that WOULD be accepted."""
    if value is None or (value == "" and entry.type == "text"):
        if not entry.clearable:
            raise BadValue(
                f"{entry.field} is not optional, so it cannot be cleared — "
                f"{VALUE_RULES['clearing']}"
            )
        return None
    if entry.type == "money":
        return _parse_money(entry, value)
    if entry.type == "date":
        if not isinstance(value, str):
            raise BadValue(f"{entry.field} takes a date string — {VALUE_RULES['date']}")
        parsed = parse_flexible_date(value)
        if parsed is None:
            raise BadValue(f"{value!r} is not a date — {VALUE_RULES['date']}")
        return parsed
    if entry.type == "enum":
        values = entry.values or ()
        if value not in values:
            raise BadValue(
                f"{entry.field} takes one of {', '.join(repr(v) for v in values)}, "
                f"not {value!r}"
            )
        return value
    if entry.type == "bool":
        # NOT coerced. pydantic would turn "true" and 1 into True on
        # assignment, and these five decide what a saved chart prints.
        if not isinstance(value, bool):
            raise BadValue(f"{entry.field} takes {VALUE_RULES['bool']}")
        return value
    if entry.type == "list_of_strings":
        return _parse_list(entry, value)
    if entry.type == "share_bps":
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadValue(f"{entry.field} takes {VALUE_RULES['share_bps']}")
        # The 0-10000 range is `Participant.share_bps`'s own `ge`/`le`, read
        # off the model by `_bounds`. Spelling it out here was a copy of the
        # model that nothing compared against it.
        _check_bounds(entry, value)
        return value
    if not isinstance(value, str):
        raise BadValue(f"{entry.field} takes {VALUE_RULES['text']}")
    _check_length(entry, value)
    return value


def _check_length(entry: Entry, value: str) -> None:
    """The model's own length bounds, refused here so the message is usable.

    Not a second rule — `_length` read it off the model, and pydantic would
    refuse the same value on assignment a moment later. What this adds is the
    sentence: the pydantic refusal reaches the client as its own multi-line
    repr and never names an acceptable length.
    """
    low, high = entry.min_length, entry.max_length
    if low is not None and len(value) < low:
        raise BadValue(f"{value!r} is too short for {entry.field} — {_length_rule(low, high)}")
    if high is not None and len(value) > high:
        raise BadValue(f"{value!r} is too long for {entry.field} — {_length_rule(low, high)}")


def _length_rule(low: int | None, high: int | None) -> str:
    if low == high:
        return f"it takes exactly {_chars(low)}"
    if low is None:
        return f"it takes at most {_chars(high)}"
    if high is None:
        return f"it takes at least {_chars(low)}"
    return f"it takes {low}-{high} characters"


def _chars(count: int | None) -> str:
    return f"{count} character" + ("" if count == 1 else "s")


def _check_bounds(entry: Entry, value: int) -> None:
    """The model's own `ge`/`le`, refused here so the message is usable.

    The numeric half of `_check_length`, and it was missing: `attach` is
    `Money` (`ge=0`), and `value=-1` reached the client as pydantic's own
    four-line repr with a URL in it — a refusal naming no value that would be
    accepted, which is the standard the design doc sets for every one of them.
    A field with no bound on the model has none here; that is the whole
    contract of reading it off the model rather than declaring it.
    """
    if entry.minimum is not None and value < entry.minimum:
        raise BadValue(
            f"{_amount(entry, value)} is outside what {entry.field} takes — "
            f"{_bound_rule(entry)}"
        )
    if entry.maximum is not None and value > entry.maximum:
        raise BadValue(
            f"{_amount(entry, value)} is outside what {entry.field} takes — "
            f"{_bound_rule(entry)}"
        )


def _bound_rule(entry: Entry) -> str:
    """Called only from `_check_bounds`, so at least one bound exists."""
    low, high = entry.minimum, entry.maximum
    if low is not None and high is not None:
        return f"it takes {_amount(entry, low)} to {_amount(entry, high)}"
    if low is not None:
        return f"it takes {_amount(entry, low)} or more"
    return f"it takes {_amount(entry, high)} or less"


def _amount(entry: Entry, value: int | None) -> str:
    """A number in the lexicon its own field speaks, so the bound a refusal
    names can be pasted back."""
    if value is None:
        return "null"
    return format_money(value) if entry.type == "money" else str(value)


def _parse_money(entry: Entry, value: object) -> int:
    if isinstance(value, bool):  # bool is an int; True is not one dollar
        raise BadValue(f"{entry.field} takes {VALUE_RULES['money']}")
    if isinstance(value, int):
        _check_bounds(entry, value)
        return value
    if isinstance(value, float):
        raise BadValue(
            f"{entry.field} is whole dollars and {value!r} is not — {VALUE_RULES['money']}"
        )
    if not isinstance(value, str):
        raise BadValue(f"{entry.field} takes {VALUE_RULES['money']}")
    # The MINUS SIGN is read here and not in `money.parse_money`, which refuses
    # a negative outright and is the TUI's and `ingest`'s parser too. It has to
    # be read SOMEWHERE: `layer.limit` carries no `ge` on purpose, so -5 is a
    # state the field can hold, `render_value` and `expecting_literal` both
    # print it as '-$5', and a refusal that offers a literal the parser then
    # rejects is a loop the caller cannot leave. Whether a negative is ALLOWED
    # is the model's question, asked by `_check_bounds` below, not the
    # spelling's.
    text = value.strip()
    negative = text.startswith("-")
    try:
        amount = parse_money(text[1:].lstrip() if negative else text)
    except MoneyParseError as exc:
        raise BadValue(f"{exc} — {VALUE_RULES['money']}") from exc
    amount = -amount if negative else amount
    _check_bounds(entry, amount)
    return amount


def _parse_list(entry: Entry, value: object) -> list[str]:
    if isinstance(value, str):
        if not entry.accepts_comma_string:
            raise BadValue(
                f"{entry.field} takes a JSON array of strings, not one string — "
                f"{VALUE_RULES['list_of_strings']}"
            )
        return edit.parse_states(value)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BadValue(
            f"{entry.field} takes a JSON array of strings — {VALUE_RULES['list_of_strings']}"
        )
    # Blank pieces dropped, order and case left VERBATIM — `edit.parse_states`
    # states the same rule for the comma form, and `validate` owns duplicates.
    return [item.strip() for item in value if item.strip()]


def parse_expecting(entry: Entry, value: object) -> Any:
    """`expecting` through the field's OWN parser, so '$5,000,000', '5m' and
    5000000 all match a stored 5000000.

    `null` is accepted for any field, clearable or not: a field can be unset on
    a draft and the guard has to be expressible either way, or it becomes
    opt-out — and an optional compare-and-set is not a compare-and-set.

    `""` means THE EMPTY STRING, not "unset", and it is not put through
    `parse_value` — which reads `""` as a CLEAR and answers `None`. The two
    readings had made a text field holding `""` permanently unwritable over
    the protocol, on a file `towerctl validate` exits 0 on: `expecting=null`
    refused with "notes is '', not the null you expected — pass expecting=\"\"",
    and `expecting=""` refused with "send null, not an empty string". The two
    refusals named each other and no value ever reached the write. `""` is
    reachable — `edit.set_field(p, "layer", "notes", "")` writes it, and older
    files and hand edits carry it — so it has to be expressible, and the value
    the mismatch offers is the value that works.

    A length bound is NOT applied to it: `expecting` is a comparison operand,
    not a write. A field that cannot hold `""` simply never matches one.
    """
    if value is None:
        return None
    if value == "" and entry.type == "text":
        return ""
    return parse_value(entry, value)


def compare_values(entry: Entry, expected: object, current: object) -> bool:
    """Parsed against parsed. Lists compare element-wise and ORDER-SENSITIVELY
    — a program's states order is verbatim and load-bearing."""
    if entry.type == "enum":
        return _enum_value(expected) == _enum_value(current)
    return bool(expected == current)


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def render_value(entry: Entry, value: object) -> str:
    """The current value in the SAME lexicon the field accepts, so a refusal
    can be pasted straight back into `expecting` and the retry succeeds."""
    if value is None:
        return "null"
    if entry.type == "money":
        return format_money(int(value)) if isinstance(value, int) else str(value)
    if entry.type == "date":
        return value.isoformat() if isinstance(value, date) else str(value)
    if entry.type == "enum":
        # `.value` explicitly, never `!r` and never `str()`: f"{Placement.BOUND!r}"
        # is "<Placement.BOUND: 'bound'>", which no client can pass back, and
        # StrEnum's friendly __str__ is a property of THIS enum base, not a
        # promise the next one keeps.
        return f"'{_enum_value(value)}'"
    if entry.type == "bool":
        return "true" if value else "false"
    if entry.type == "list_of_strings":
        items = ", ".join(f'"{item}"' for item in value) if isinstance(value, list) else ""
        return f"[{items}]"
    if entry.type == "share_bps":
        return f"{format_share(int(value))} ({value} bps)" if isinstance(value, int) else str(value)
    return f"'{value}'"


def expecting_literal(entry: Entry, value: object) -> str:
    """What to paste into `expecting` — a JSON literal, always.

    Not the same thing as what a refusal PRINTS. `render_value` writes prose
    for a human reading the sentence ($5,000,000, 35% (3500 bps), 'bound');
    this writes the value the way the caller has to SEND it, because the
    message says "pass expecting=<this>" and the client copies it.

    JSON is not decoration. A refusal that offered `expecting='$2,000,000'`
    came back as `cannot parse money value: "'$2,000,000'"` when the caller
    did as it was told, and for text it looped forever — `pass expecting='USD'`
    refused `'USD'` with the identical message. Lists and booleans were already
    emitting JSON; every type does now, and `json.dumps` does the escaping so a
    carrier called O'Neill or a name with a quote in it round-trips too.
    """
    if value is None:
        return "null"
    if entry.type == "share_bps":
        # An integer of basis points, which is what the field takes. The prose
        # form ("35% (3500 bps)") is `render_value`'s and is not sendable.
        return str(value)
    if entry.type == "bool":
        return "true" if value else "false"
    if entry.type == "list_of_strings":
        return json.dumps(list(value) if isinstance(value, list) else [])
    if entry.type == "money":
        # The money STRING, never the bare integer: the spec's standard, and a
        # model handed a raw number has no way to tell dollars from cents.
        return json.dumps(format_money(int(value)) if isinstance(value, int) else str(value))
    if entry.type == "date":
        return json.dumps(value.isoformat() if isinstance(value, date) else str(value))
    if entry.type == "enum":
        return json.dumps(_enum_value(value))
    return json.dumps(str(value))


def mismatch_message(entry: Entry, who: str, current: object, expected: object) -> str:
    """`stale_value`. It names the value that would be accepted AND the call
    that produces a fresh read, because a refusal with neither is a dead end."""
    return (
        f"{who} {entry.field} is {render_value(entry, current)}, not the "
        f"{render_value(entry, expected)} you expected — pass "
        f"expecting={expecting_literal(entry, current)} to overwrite it, or "
        f"call program_read."
    )


# --- the write -----------------------------------------------------------------


def edit_address(entry: Entry, target: str | None, index: int | None) -> str | int | None:
    """What `edit._entity` calls `target` for this kind.

    `target` and `index` both travel from the wire, because one argument
    cannot say both: a retention is a position in a PROGRAM collection while a
    participant is a position within a LAYER, so the retention's position is
    its target and the participant's target is its layer. Stated once here
    because two callers need it — the write and the container that has to be
    materialised before the write.
    """
    return index if entry.kind in ("retention", "sublimit") else target


def apply(
    program: Program,
    entry: Entry,
    target: str | None,
    index: int | None,
    value: object,
) -> list[Diagnostic]:
    """Perform the write, through `edit.py` and nothing else.

    Every entry but one goes to `edit.set_field`, THE choke point, which is
    where the conditional guards and normalisers live and is why a newly
    guarded field needs no edit here. `line.name` goes to `rename_line`
    because the id cascade is part of what renaming a line MEANS.

    It used to split three whole KINDS off to `edit.edit_retention`,
    `edit.edit_sublimit` and `edit.edit_named_limit` by splatting
    `{python_name: value}` into them. Their keyword parameters are a
    hand-written copy of the three models, so `describe()` advertised fields
    that always failed — see the comment on `_SETTERS`. A `setter` is now a
    statement about ONE field, never about a kind, and the check those verbs
    carried (`_check_lines` on appliesTo) lives at the choke point where a
    clear, a value and every other field of the row all pass through it.

    Returns write-time advisories (currency's "nothing was converted"), which
    are statements about what the write did NOT do — not validator diagnostics,
    because re-reading the file will not reproduce them.
    """
    address = edit_address(entry, target, index)
    if entry.setter is None:
        return edit.set_field(program, entry.kind, entry.field, value, address, index)
    edit.rename_line(program, str(target), str(value))
    return []


# --- describe() ------------------------------------------------------------------


def describe(kind: str | None = None) -> dict[str, Any]:
    """The whole contract, as data: kinds, fields, types, denied fields with
    their reasons, the value rules and the error codes.

    A tool, not a resource, and the ONLY place the field set is published. No
    tool description may reprint it — that is the second table again, in prose,
    and prose is where the first one rotted.
    """
    if kind is not None and kind not in SURFACE:
        raise KeyError(f"no kind {kind!r}; kinds are {', '.join(KINDS)}")
    wanted = KINDS if kind is None else (kind,)
    kinds: dict[str, Any] = {}
    for name in wanted:
        fields: dict[str, Any] = {}
        for field, entry in SURFACE[name].items():
            payload: dict[str, Any] = {
                "type": entry.type,
                "required": entry.required,
                "clearable": entry.clearable,
            }
            if entry.values is not None:
                payload["values"] = list(entry.values)
            if entry.min_length is not None or entry.max_length is not None:
                # Published, or the caller learns the bound only by tripping it.
                payload["length"] = [entry.min_length, entry.max_length]
            if entry.minimum is not None or entry.maximum is not None:
                # Same reason, for the numbers. `layer.limit` has no bound and
                # publishes none, which is the honest answer: a negative limit
                # is a draft state the validator reports, not a write the
                # surface refuses.
                payload["bounds"] = [entry.minimum, entry.maximum]
            if entry.guard is not None:
                payload["guard"] = entry.guard
            fields[field] = payload
        kinds[name] = {
            "target": list(TARGET[name]),
            "row_guard": ROW_GUARD[name],
            "fields": fields,
            "denied": {
                key.split(".", 1)[1]: reason
                for key, reason in DENIED.items()
                if key.split(".", 1)[0] == name
            },
        }
    return {"kinds": kinds, "value_rules": VALUE_RULES, "error_codes": list(ERROR_CODES)}
