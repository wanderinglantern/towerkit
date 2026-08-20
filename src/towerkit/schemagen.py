"""The JSON Schema's PROPERTY SET, computed from `model.py`.

`schema/program.schema.json` and its packaged twin are hand-typed
enumerations of every key a program file may contain, with
`additionalProperties: false` at nine sites. That makes them a fourth field
table beside the models — after the MCP write surface, the read projection
and the canonical serialiser, all three of which have since been derived —
and it rotted the same way: a field added to `model.py` and forgotten here
makes the file towerkit's OWN writer just produced fail `towerctl validate`,
while the MCP response says `errors: []`. Reproduced 2026-08-19 with a
`brokerRef` on `Layer`; the whole suite noticed nothing, because
`test_schema_copies_are_identical` compares the two copies to EACH OTHER and
they stay wrong together.

This module does NOT regenerate the document. `model_json_schema()` would,
and would throw away everything the file says that a pydantic model cannot:
the `minLength` on every string, the `format: date`, the money `$def`, the
`required` lists, the descriptions a broker reads, the `$id`. Those are
hand-authored semantics and they stay hand-authored.

What is mechanical is the property SET, its ORDER, each property's TYPE, the
enum VALUE LISTS, and the `required` lists. So:

- a property whose model field is gone is dropped (and dropped from
  `required`, which would otherwise demand a key nothing may write);
- a property that is missing is added with a type derived from the
  annotation, and NO description — prose is the human's half;
- a property that is already there is RECONCILED, not copied through: the
  derived keywords (`type`, `$ref`, `format`, `enum`, `items`) are taken from
  the model, and everything else — `description`, `pattern`, `minLength`,
  `minimum`, `minItems`, `examples` — survives byte for byte in its original
  position;
- `required` is derived from `FieldInfo.is_required()`, plus the keys
  `STRICTER_THAN_MODEL` declares;
- properties are ordered by model declaration order, which is already the
  file's key order (`model.program_to_jsonable` derives it from the same
  `model_fields`), so a new field lands where the file will write it.

Reconciling rather than copying is the 2026-08-19 repair. Only the property
SET was derived before, so three facts drifted in silence, in BOTH
directions, and each was proved with a one-line mutation that left
`sync_schema.py --check` printing "schema is in sync with model.py":

- `required` was only FILTERED, never added to. A new required field on
  `Layer` was not required by the schema; making `Layer.name` optional left
  the schema still demanding it.
- An EXISTING property was never re-derived. Retyping `Layer.limit` from
  money to `str` left the schema saying `{"type": "integer"}`.
- An enum's VALUE LIST was never re-derived. Adding one member to
  `Placement` — which nobody would call a schema change — left the schema
  stale, so `describe()` advertised `quoted`, the MCP write reported
  `errors: []`, and `towerctl validate` exited 1 with `placement: 'quoted'
  is not one of ['bound', 'proposed']`.

Anything this cannot derive raises. A new nested model needs a `$def` with
its own `required` list and its own `additionalProperties`, and guessing one
is how a schema starts accepting files it should refuse.

Where a hand-authored fact CONTRADICTS the model rather than merely adding to
it, this raises too, and the split is deliberate:

- a DERIVED fact is overwritten WHERE THE MODEL HAS SOMETHING TO SAY, in
  silence, because the model IS the source of truth for it — there is no
  judgement to preserve. Where the model says nothing, the human's value
  stays: see the `enum` rule below;
- a hand-authored CONSTRAINT the derived type has stranded — a `minLength` on
  a property that is now an integer, a `required` entry for a field the model
  made optional — raises. Keeping it silently loosens the document, and
  dropping it silently discards a rule a human chose. Both are the failure
  this module exists to end: a schema that passes files it should reject.

Two rules were wrong on 2026-08-19 and are stated here because getting them
wrong is easy and the errors point in opposite directions.

**Stranded means CANNOT BITE, not "sits beside a `$ref` or an `enum`".** The
first cut keyed off `out["type"]`, which is absent for both, so
`{"$ref": "#/$defs/money", "minimum": 1000}` and
`{"enum": [...], "minLength": 1}` were refused and the whole schema was
blocked. Sibling keywords to `$ref` have applied since draft 2019-09, and
`enum` constrains the value set without saying anything about the type.
Checked against `jsonschema.Draft202012Validator`, the validator `validate.py`
loads:

- `{"$ref": "#/$defs/money", "minimum": 1000}` rejects `500` with
  "500 is less than the minimum of 1000";
- `{"enum": ["bound", "proposed"], "minLength": 9}` rejects `"bound"` with
  "'bound' is too short";
- `{"type": "integer", "minLength": 5}` ACCEPTS `1` — the genuinely stranded
  shape, and the only one that should raise.

So the test is on the JSON types the property can still ACCEPT (through its
`type`, through the `$def` its `$ref` names, and through its `enum` members'
own types). A keyword is stranded only when no accepted value is ever of a
type the keyword is defined for; then, and only then, it rejects nothing.

**A derived keyword is overwritten only where the model derives one.** `enum`
is derived from an `Enum` annotation and from nothing else, so on a plain
`str` field the model asserts NOTHING about the value set — and deleting a
hand-authored `{"enum": ["USD", "GBP"]}` there, which the first cut did with
no message at all, silently LOOSENS validation on a broker's file. That is
the exact failure this module exists to end, arriving through the repair.
A hand-authored `enum` is therefore preserved, like `pattern` beside it: it
can only tighten, and the model has no opinion to impose. It is checked for
one thing — that its members are of a type the derived schema accepts —
because an enum of strings under `{"type": "integer"}` accepts nothing at
all, and a property that refuses every file is the mirror failure.

The module is PURE — a document in, a document out. It knows nothing about
where the two copies live on disk: that is repo layout, and repo layout
belongs to `tools/sync_schema.py`, which is the maintenance script that runs
it, and to the tests. `towerctl` is the broker's CLI over their own program
files and has no business writing to this checkout's `schema/` directory.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from datetime import date
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .model import (
    MONEY,
    Layer,
    Line,
    NamedLimit,
    Participant,
    Period,
    Program,
    RenderSettings,
    Retention,
    Sublimit,
    disk_fields,
    disk_form_is_derived,
)

# Where each model is described in the document, as a slash-separated path
# from the root. `""` IS the root — the document describes a `Program`
# directly rather than through a `$def`.
#
# This mapping is small, it is legitimate, and it is the one thing here that
# is declared: no rule can know that the schema calls `NamedLimit` "namedLimit"
# or that `RenderSettings` is written inline rather than as a `$def`. It is
# not a field table — nothing about a FIELD appears in it — and `sync_document`
# refuses to run while a `$def` is missing from it, so a new shape cannot slip
# past by being added to only one side.
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "": Program,
    "properties/render": RenderSettings,
    "$defs/line": Line,
    "$defs/layer": Layer,
    "$defs/namedLimit": NamedLimit,
    "$defs/participant": Participant,
    "$defs/retention": Retention,
    "$defs/sublimit": Sublimit,
    "$defs/period": Period,
}

# `$defs` that describe a VALUE, not a model: the two shared scalar types the
# hand-authored file factors out. Declared so that `sync_document` can insist
# every other `$def` maps to a model — an unmapped one would otherwise be
# silently unchecked, which is the failure this module exists to end.
SCALAR_DEFS: frozenset[str] = frozenset({"money", "share"})

# Keys the FILE requires that the MODEL does not, declared with the reason.
#
# `required` is derived from `FieldInfo.is_required()`, and a schema that
# demands MORE than the model is not automatically a bug: it is a file-format
# rule the model cannot carry. `Program.lines`/`.layers`/`.retentions` all
# default to `[]`, so a program without them LOADS — but every writer towerkit
# has emits all three arrays, and dropping them from `required` would start
# accepting files no towerkit writer produces and the TUI would show as an
# empty tower with no complaint.
#
# It is not a field table: an entry here adds nothing and describes nothing,
# it only records a decision, and an UNDECLARED stricter-than-model entry
# raises rather than being reconciled away. That is what makes making a model
# field optional a choice the maintainer has to make out loud.
STRICTER_THAN_MODEL: dict[str, frozenset[str]] = {
    # All four default to `[]` on the model, so a program missing one LOADS —
    # and every towerkit writer emits all four on every file, so a program
    # missing one is a file no writer produced. The schema holds the stricter
    # line. `sublimits` was absent from this set and from the schema's root
    # `required` until 2026-08-19, which was an oversight rather than a
    # distinction: it has been on `Program` since the first commit, every
    # program file carries it, and the reason above applies to it word for
    # word. Making it consistent tightens `towerctl validate` on a
    # hand-written file that omits the key; nothing towerkit wrote can.
    "": frozenset({"lines", "layers", "retentions", "sublimits"}),
}

# The keywords `derive_property` emits. Everything else in a property is the
# human's half and is preserved, in place, byte for byte.
DERIVED_KEYWORDS: frozenset[str] = frozenset({"$ref", "type", "format", "enum", "items"})

# The derived keywords a human may ALSO author, so that one left in a property
# the model no longer emits it for is kept rather than dropped.
#
# `enum` only. The model derives one from an `Enum` annotation and from nothing
# else, so on a plain `str` field it is stating no opinion, and dropping the
# human's `{"enum": ["USD", "GBP"]}` there loosens the document in silence.
# The other four are type IDENTITY — `type`, `$ref`, `format`, `items` say what
# the value IS, which is the model's answer alone, so one left behind by a
# retyped field is stale and goes.
HAND_AUTHORABLE_KEYWORDS: frozenset[str] = frozenset({"enum"})

# Every type a JSON instance can have. `null` is here for completeness only:
# `_unwrap` peels `| None` and the canonical file omits an absent key rather
# than writing `null`, so no derived property ever permits it.
_JSON_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "array", "object", "null"}
)

# Which JSON types each hand-authored constraint keyword is DEFINED for. A
# keyword is applied only to instances of those types and is inert for every
# other, so one that no accepted value can ever be of the right type for is
# a rule that rejects nothing — see the module docstring for the validator
# evidence, and `_permitted_types` for how "accepted value" is worked out.
_APPLIES_TO: dict[str, frozenset[str]] = {
    "minLength": frozenset({"string"}),
    "maxLength": frozenset({"string"}),
    "pattern": frozenset({"string"}),
    "minimum": frozenset({"integer", "number"}),
    "maximum": frozenset({"integer", "number"}),
    "exclusiveMinimum": frozenset({"integer", "number"}),
    "exclusiveMaximum": frozenset({"integer", "number"}),
    "multipleOf": frozenset({"integer", "number"}),
    "minItems": frozenset({"array"}),
    "maxItems": frozenset({"array"}),
    "uniqueItems": frozenset({"array"}),
    "properties": frozenset({"object"}),
    "additionalProperties": frozenset({"object"}),
    "minProperties": frozenset({"object"}),
    "maxProperties": frozenset({"object"}),
}

# The reverse of `SCHEMA_MODELS` for the `$defs` half only: what to `$ref`
# when a field's type is a model. The root and the inline `render` object have
# no reference to point at, so a NEW field typed `Program` or `RenderSettings`
# raises rather than emitting a dangling `$ref`.
_REF_FOR: dict[type[BaseModel], str] = {
    model: f"#/{pointer}"
    for pointer, model in SCHEMA_MODELS.items()
    if pointer.startswith("$defs/")
}


class SchemaDerivationError(RuntimeError):
    """A field the schema cannot be given a type for without a human."""


def _at(doc: dict[str, Any], pointer: str) -> dict[str, Any]:
    node = doc
    for step in pointer.split("/") if pointer else ():
        child = node.get(step)
        if not isinstance(child, dict):
            raise SchemaDerivationError(
                f"the schema has no object at {pointer!r}; SCHEMA_MODELS names a "
                f"place the document does not have"
            )
        node = child
    return node


def properties_at(doc: Mapping[str, Any], pointer: str) -> dict[str, Any]:
    """The `properties` object at one `SCHEMA_MODELS` pointer.

    Public because the contract tests need the same navigation this module
    does, and a test that walks the document itself is a second copy of the
    pointer convention — the shape of mistake this whole module is about.
    """
    return dict(_at(dict(doc), pointer).get("properties", {}))


def _unwrap(info: FieldInfo) -> tuple[Any, list[Any]]:
    """The annotation with `| None` and `Annotated[...]` peeled off, plus the
    metadata both layers carry.

    `mcpsurface._flatten` peels the same two layers and is deliberately not
    shared: it goes on to answer what the MCP surface may ADVERTISE, which
    refuses a model and refuses a bare `int`, and both are ordinary here. Same
    unwrapping, different question, and merging them would make one module's
    refusal the other's bug.
    """
    annotation: Any = info.annotation
    metadata: list[Any] = list(info.metadata)
    if get_origin(annotation) in (Union, UnionType):
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(inner) != 1:
            raise SchemaDerivationError(f"cannot derive a type for the union {annotation!r}")
        annotation = inner[0]
    nested = getattr(annotation, "__metadata__", None)
    if nested is not None:
        metadata = [*metadata, *nested]
        annotation = annotation.__origin__
    return annotation, metadata


def _subschema(where: str, base: Any, metadata: list[Any]) -> dict[str, Any]:
    """One annotation, as the file would describe it.

    Money before `int`, and `bool` before `int` as well, because `bool` is an
    `int` subclass and a boolean typed `integer` would let `attach: true`
    through the schema untouched.
    """
    if any(item is MONEY for item in metadata):
        # The `money` $def is `{"type": "integer", "minimum": 0}`, so it is the
        # right reference only for a field that carries the bound. `Layer.limit`
        # is `Annotated[int, MONEY]` with NO `ge=0` ON PURPOSE — positivity is a
        # SEMANTIC rule so a draft stays loadable and `validate.py` reports it —
        # and pointing it at the $def would make the schema refuse the very
        # drafts the model was written to accept. The MONEY tag says how to READ
        # the number; the bound says what it may BE, and only the second one is
        # what the $def encodes.
        if _bounded_at_zero(metadata):
            return {"$ref": "#/$defs/money"}
        if _carries_a_bound(metadata):
            # `ge=1` (or any bound the two branches above cannot express) used
            # to fall through to a bare `{"type": "integer"}` — the schema
            # silently looser than the model, which is the one thing this
            # module promises never to emit. Same policy as the nested-model
            # branch: refuse loudly and name the hand-authored repair, because
            # `minimum`/`maximum` are the human's half of the document and
            # survive reconciliation.
            raise SchemaDerivationError(
                f"{where} is Money with a bound the derivation cannot express — "
                f"author `minimum`/`maximum` by hand on the property (hand-authored "
                f"keywords survive reconciliation) or move the bound to ge=0"
            )
        return {"type": "integer"}
    if base is date:
        return {"type": "string", "format": "date"}
    if isinstance(base, type) and issubclass(base, Enum):
        values = [member.value for member in base]
        if not all(isinstance(v, str) for v in values):
            # Stringified, an int-valued enum yields `{"enum": ["1", "2"]}`
            # while the file holds `1` — proven wrong against
            # Draft202012Validator in round six (`1 is not one of ['1', '2']`).
            # Wrong-instead-of-raise is the one failure mode this module
            # promises against.
            raise SchemaDerivationError(
                f"{where} is an enum with non-string values {values!r} — the file "
                f"would hold the raw value, so a stringified enum is a schema that "
                f"rejects the model's own output; make it a StrEnum or teach "
                f"_subschema the value type"
            )
        return {"enum": values}
    if base is bool:
        return {"type": "boolean"}
    if isinstance(base, type) and issubclass(base, BaseModel):
        ref = _REF_FOR.get(base)
        if ref is None:
            raise SchemaDerivationError(
                f"{where} is a {base.__name__} and the schema has no $def for it — "
                f"author the $def (with its required list and additionalProperties) "
                f"and add it to SCHEMA_MODELS; a generated one would accept shapes "
                f"nobody reviewed"
            )
        return {"$ref": ref}
    if get_origin(base) is list:
        (element,) = get_args(base) or (None,)
        if element is None:
            raise SchemaDerivationError(f"{where} is an untyped list")
        return {"type": "array", "items": _subschema(where, *_element(element))}
    if base is int:
        return {"type": "integer"}
    if base is str:
        return {"type": "string"}
    raise SchemaDerivationError(f"{where} has a type the schema cannot be given: {base!r}")


def _bounded_at_zero(metadata: list[Any]) -> bool:
    """Does this field carry `ge=0`?

    Two shapes, the same split `model._has_tag` and `mcpsurface._flatten` pay
    for: a required `Money` lands `Ge(ge=0)` in the metadata directly, while an
    optional `Money | None` wraps it in a `FieldInfo` of its own.
    """
    for item in metadata:
        if getattr(item, "ge", None) == 0:
            return True
        if any(getattr(inner, "ge", None) == 0 for inner in getattr(item, "metadata", ())):
            return True
    return False


def _carries_a_bound(metadata: list[Any]) -> bool:
    """Any `ge`/`le` at all, in either of `_bounded_at_zero`'s two shapes —
    so a bound that ISN'T the ge=0 the money `$def` encodes is detected
    rather than silently dropped."""
    for item in metadata:
        if getattr(item, "ge", None) is not None or getattr(item, "le", None) is not None:
            return True
        if any(
            getattr(inner, "ge", None) is not None or getattr(inner, "le", None) is not None
            for inner in getattr(item, "metadata", ())
        ):
            return True
    return False


def _element(annotation: Any) -> tuple[Any, list[Any]]:
    """A list's element type, with any `Annotated` metadata it carries — so a
    `list[Money]` would still come out as the money `$ref`."""
    nested = getattr(annotation, "__metadata__", None)
    if nested is None:
        return annotation, []
    return annotation.__origin__, list(nested)


def derive_property(model: type[BaseModel], name: str, info: FieldInfo) -> dict[str, Any]:
    """The subschema for one model field, as the FILE holds it.

    Type only. No `description`, no `minLength`, no `minItems`: those are
    judgements about the data, and a generated guess at one is worse than the
    absence a human can see and fill in.
    """
    where = f"{model.__name__}.{name}"
    if not disk_form_is_derived(model, name):
        raise SchemaDerivationError(
            f"{where} is converted on the way to disk (model._DISK_FORM), so its "
            f"annotation does not describe what the file holds — author this "
            f"property by hand"
        )
    return _subschema(where, *_unwrap(info))


_DEFS_PREFIX = "#/$defs/"


def _json_type(value: Any) -> str:
    """The JSON type of one literal — `bool` before `int`, as everywhere else
    here, because `True` is an `int` in python and `true` is not a number in
    JSON."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _permitted_types(
    node: Mapping[str, Any], defs: Mapping[str, Any], seen: frozenset[str] = frozenset()
) -> frozenset[str] | None:
    """The JSON types a value this subschema ACCEPTS can have.

    `None` means unknown — a `$ref` that names no `$def` we were handed — and
    unknown is not "stranded". This check exists to catch a rule that enforces
    nothing; guessing would make it block a schema that is fine, which is
    worse, and that is exactly the 2026-08-19 defect it replaces.

    All three narrowings compose, which is the point: a `$ref` to `money` says
    integer, an `enum` of strings says string, and a property carrying both
    accepts nothing.
    """
    permitted = _JSON_TYPES
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in seen or not ref.startswith(_DEFS_PREFIX):
            return None
        target = defs.get(ref.removeprefix(_DEFS_PREFIX))
        if not isinstance(target, Mapping):
            return None
        inner = _permitted_types(target, defs, seen | {ref})
        if inner is None:
            return None
        permitted &= inner
    kind = node.get("type")
    if isinstance(kind, str):
        permitted &= {kind}
    members = node.get("enum")
    if isinstance(members, list):
        permitted &= {_json_type(member) for member in members}
    return permitted


def reconcile_property(
    where: str,
    existing: Mapping[str, Any],
    derived: Mapping[str, Any],
    defs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One existing property, with the model's facts imposed on it and the
    human's prose left alone.

    Assignment to a key a dict already holds keeps that key's POSITION, which
    is what makes this byte-for-byte on a document that is already in sync:
    `appliesTo` is written `type, minItems, items` and comes back out in that
    order even though `derive_property` emits `type, items`.

    `defs` is the document's `$defs`, needed only to look through a `$ref` to
    the types it permits. Omitted, an unresolvable `$ref` reads as "unknown"
    and the stranded check stands down for that property — the safe direction,
    since refusing wrongly blocks the whole schema.

    Public because the contract tests ask the same question of the checked-in
    document — a test that re-implemented the merge would be a second copy of
    the rule, which is the shape of mistake this module is about.
    """
    defs = {} if defs is None else defs
    out = dict(existing)
    for key in [
        k
        for k in out
        if k in DERIVED_KEYWORDS - HAND_AUTHORABLE_KEYWORDS and k not in derived
    ]:
        # `format: date` left behind by a field that is no longer a date, a
        # `type` left behind by one that is now a `$ref`. NOT `enum`: the model
        # derives one only from an `Enum` annotation, so its absence from
        # `derived` means the model has no opinion, and the human's survives.
        del out[key]
    for key, value in derived.items():
        if key == "items" and isinstance(out.get("items"), dict) and isinstance(value, Mapping):
            # An element subschema carries prose too — `states` items are
            # `{"type": "string", "minLength": 1}`.
            out["items"] = reconcile_property(f"{where}[]", out["items"], value, defs)
        else:
            out[key] = value

    permitted = _permitted_types(out, defs)
    if permitted is not None and not permitted:
        # Only reachable through a preserved hand-authored `enum` whose members
        # are of a type the model's own `type`/`$ref` refuses: strings under
        # `{"type": "integer"}`. The property would then refuse EVERY file,
        # which is the mirror of the loosening the preservation rule prevents.
        raise SchemaDerivationError(
            f"{where} is now {_describe(out)} and the hand-authored enum "
            f"{out.get('enum')} has no type in common with it, so the property "
            f"would accept no value at all; drop the enum, or change the model back"
        )
    if permitted is not None:
        stranded = sorted(
            key for key, applies in _APPLIES_TO.items() if key in out and not permitted & applies
        )
        if stranded:
            constrains = sorted(set().union(*(_APPLIES_TO[key] for key in stranded)))
            raise SchemaDerivationError(
                f"{where} is now {_describe(out)}, so every value it accepts is "
                f"{sorted(permitted)}, and the hand-authored {stranded} "
                f"{'constrains' if len(stranded) == 1 else 'constrain'} only "
                f"{constrains} — no value this property can accept is ever tested "
                f"against {'it' if len(stranded) == 1 else 'them'}, so it is a rule "
                f"that rejects nothing; drop it, or change the model back"
            )
    return out


def _describe(node: Mapping[str, Any]) -> str:
    """What a reconciled property now IS, for a refusal message.

    `type` before `enum`, so a property carrying both is described by the half
    the MODEL imposed — that is the half the maintainer has to change, and the
    refusal that says "an enum" when the model made it an integer sends them
    to the wrong line.
    """
    if "$ref" in node:
        return f"a {node['$ref']}"
    kind = node.get("type")
    if isinstance(kind, str):
        return f"an {kind}" if kind[:1] in "aeiou" else f"a {kind}"
    if "enum" in node:
        return "an enum"
    return "a schema"


def _required_for(
    where: str, pointer: str, model: type[BaseModel], properties: Mapping[str, Any], node: Any
) -> list[str]:
    """The `required` list this model implies, in declaration order.

    DERIVED, not filtered. Filtering was the 2026-08-19 defect: nothing ever
    ADDED, so a new required field on a model was simply not required by the
    schema and `--check` stayed clean.
    """
    extra = STRICTER_THAN_MODEL.get(pointer, frozenset())
    unknown = sorted(extra - set(properties))
    if unknown:
        raise SchemaDerivationError(
            f"STRICTER_THAN_MODEL declares {unknown} required at {where}, and "
            f"{'it is' if len(unknown) == 1 else 'they are'} not a property there"
        )
    wanted = [key for key, _, info in disk_fields(model) if info.is_required() or key in extra]
    declared = node.get("required")
    if isinstance(declared, list):
        # A declared entry whose property is GONE is dropped without comment —
        # that is part of dropping the property, and the orphan is reported by
        # the property tests. A declared entry whose property is still there
        # and whose model field is OPTIONAL is the contradiction: the schema
        # would refuse a file the model happily loads.
        stray = sorted(set(declared) & set(properties) - set(wanted))
        if stray:
            raise SchemaDerivationError(
                f"{where} requires {stray}, which the model makes optional — the "
                f"schema would refuse a file model.py loads; make the field required "
                f"on the model, or declare it in STRICTER_THAN_MODEL with the reason"
            )
    return wanted


def sync_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    """`doc` with every mapped object's property set, property types, enum
    values and `required` list brought into line with the models, and
    everything else left exactly as it was."""
    out = copy.deepcopy(dict(doc))

    unmapped = sorted(
        set(out.get("$defs", {})) - SCALAR_DEFS - {p.removeprefix("$defs/") for p in SCHEMA_MODELS}
    )
    if unmapped:
        raise SchemaDerivationError(
            f"$defs {unmapped} map to no model and are not declared scalars — add "
            f"them to SCHEMA_MODELS or SCALAR_DEFS, or nothing ever checks them"
        )

    defs = out.get("$defs", {})
    inline = set(SCHEMA_MODELS)
    for pointer, model in SCHEMA_MODELS.items():
        where = pointer or "$"
        node = _at(out, pointer)
        existing = node.get("properties", {})
        properties: dict[str, Any] = {}
        for key, name, info in disk_fields(model):
            child = f"{pointer}/properties/{key}" if pointer else f"properties/{key}"
            if key in existing and (child in inline or not disk_form_is_derived(model, name)):
                # Two properties this module has nothing to say about. A child
                # in SCHEMA_MODELS (`render`) is written INLINE and is
                # reconciled below as a node of its own, so re-deriving it here
                # would only raise — it has no `$def` to `$ref`. A field whose
                # disk form is converted (`Participant.share_bps`) is
                # hand-authored entirely, which `derive_property` says out loud.
                properties[key] = existing[key]
                continue
            derived = derive_property(model, name, info)
            properties[key] = (
                reconcile_property(f"{where}:{key}", existing[key], derived, defs)
                if key in existing
                else derived
            )
        node["properties"] = properties
        required = _required_for(where, pointer, model, properties, node)
        if required:
            node["required"] = required
        else:
            node.pop("required", None)
    return out


def dumps_schema(doc: Mapping[str, Any]) -> str:
    """The canonical on-disk form of the schema file: 2-space indent, one
    trailing newline — what both checked-in copies already round-trip to."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
