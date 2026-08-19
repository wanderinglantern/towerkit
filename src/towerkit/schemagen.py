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

What is mechanical is the property SET and its ORDER. So:

- a property whose model field exists is copied through BYTE FOR BYTE;
- a property whose model field is gone is dropped (and dropped from
  `required`, which would otherwise demand a key nothing may write);
- a property that is missing is added with a type derived from the
  annotation, and NO description — prose is the human's half;
- properties are ordered by model declaration order, which is already the
  file's key order (`model.program_to_jsonable` derives it from the same
  `model_fields`), so a new field lands where the file will write it.

Anything this cannot derive raises. A new nested model needs a `$def` with
its own `required` list and its own `additionalProperties`, and guessing one
is how a schema starts accepting files it should refuse.

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
        return {"$ref": "#/$defs/money"}
    if base is date:
        return {"type": "string", "format": "date"}
    if isinstance(base, type) and issubclass(base, Enum):
        return {"enum": [str(member.value) for member in base]}
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


def sync_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    """`doc` with every mapped object's property set brought into line with
    the models, and everything else left exactly as it was."""
    out = copy.deepcopy(dict(doc))

    unmapped = sorted(
        set(out.get("$defs", {})) - SCALAR_DEFS - {p.removeprefix("$defs/") for p in SCHEMA_MODELS}
    )
    if unmapped:
        raise SchemaDerivationError(
            f"$defs {unmapped} map to no model and are not declared scalars — add "
            f"them to SCHEMA_MODELS or SCALAR_DEFS, or nothing ever checks them"
        )

    for pointer, model in SCHEMA_MODELS.items():
        node = _at(out, pointer)
        existing = node.get("properties", {})
        properties: dict[str, Any] = {}
        for key, name, info in disk_fields(model):
            properties[key] = (
                existing[key] if key in existing else derive_property(model, name, info)
            )
        node["properties"] = properties
        required = node.get("required")
        if isinstance(required, list):
            # A `required` entry for a property that no longer exists demands a
            # key `additionalProperties: false` forbids in the same breath —
            # every file invalid, forever. Dropping it is part of dropping the
            # property, not a separate opinion about the required list.
            node["required"] = [key for key in required if key in properties]
    return out


def dumps_schema(doc: Mapping[str, Any]) -> str:
    """The canonical on-disk form of the schema file: 2-space indent, one
    trailing newline — what both checked-in copies already round-trip to."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
