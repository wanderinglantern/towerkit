"""One definition per rule.

Structural mutation lives in towerkit.edit, so the TUI and the MCP server
cannot drift apart. This is the same shape as bookkit's no-raw-SQL-in-tui
test: cheap, mechanical, and it fails the moment someone reaches past the
API instead of extending it."""

from __future__ import annotations

import subprocess
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

REPO = Path(__file__).parent.parent

TUI = REPO / "src" / "towerkit" / "tui"

BANNED = (
    ".lines.append(", ".layers.append(", ".retentions.append(", ".sublimits.append(",
    ".lines.pop(", ".layers.pop(", ".retentions.pop(", ".sublimits.pop(",
    ".lines.remove(", ".layers.remove(", ".retentions.remove(", ".sublimits.remove(",
    # a layer's own repeating collection, held to the same rule: the
    # named-limits grid adds and deletes rows through edit.add_named_limit /
    # edit.remove_named_limit, so the MCP server inherits the same meaning
    ".named_limits.append(", ".named_limits.pop(", ".named_limits.remove(",
    'setattr(p, "lines"', 'setattr(p, "layers"',
    'setattr(p, "retentions"', 'setattr(p, "sublimits"',
    "p.lines =", "p.layers =", "p.retentions =", "p.sublimits =",
)


def test_tui_never_mutates_program_collections_directly() -> None:
    offenders = []
    for path in sorted(TUI.rglob("*.py")):
        for number, text in enumerate(path.read_text("utf-8").splitlines(), start=1):
            for pattern in BANNED:
                if pattern in text:
                    offenders.append(f"{path.relative_to(TUI.parent)}:{number}: {pattern}")
    assert not offenders, (
        "structural mutation belongs in towerkit.edit, not the TUI:\n"
        + "\n".join(offenders)
    )


def test_schema_copies_are_identical() -> None:
    """validate.py loads the PACKAGED schema via resources.files('towerkit');
    the root copy is the published reference. A field added to only one makes
    runtime validation disagree with model.py, and no other test would catch
    it — the canonical round-trip never goes through jsonschema."""
    import json

    root = json.loads((REPO / "schema" / "program.schema.json").read_text("utf-8"))
    packaged = json.loads(
        (REPO / "src" / "towerkit" / "schema" / "program.schema.json").read_text("utf-8")
    )
    assert root == packaged


def test_snapshot_dirs_are_ignored_wherever_they_land() -> None:
    """MCP snapshots are verbatim copies of program files, and programs
    hold real client data. `snapshot()` writes beside the program, so the
    directory appears at whatever depth the program sits at — a
    path-anchored `programs/.mcp-snapshots/` rule missed every nested one
    (only `programs/private/` was covered, by an unrelated rule)."""
    candidates = [
        ".mcp-snapshots/x.json",
        "programs/.mcp-snapshots/x.json",
        "programs/private/.mcp-snapshots/x.json",
        "programs/acme/.mcp-snapshots/x.json",
    ]
    missed = [
        path
        for path in candidates
        if subprocess.run(
            ["git", "check-ignore", "-q", path], cwd=REPO
        ).returncode
        != 0
    ]
    assert not missed, f"client-data snapshots not gitignored: {missed}"


RENDER = REPO / "src" / "towerkit" / "render"

# The one predicate every renderer must quote rather than re-derive. "Is this
# layer pending?" chooses between "To be placed" and a partially-open
# remainder — different claims about the world, so two renderers that decide
# it separately can disagree about a fact rather than about a fit.
_PENDING_INLINE = "signed_bps == 0"


def test_no_renderer_re_derives_the_pending_predicate() -> None:
    """labels.is_pending is the single authority; a renderer spelling the
    comparison inline is how the graphic and the panel drift apart.

    This is the same shape as the ban above: it fails the moment someone
    reaches past the API instead of calling it. labels.py itself is exempt —
    that is where the definition lives.
    """
    offenders = []
    for path in sorted(RENDER.rglob("*.py")):
        if path.name == "labels.py":
            continue
        for number, text in enumerate(path.read_text("utf-8").splitlines(), start=1):
            stripped = text.strip()
            if stripped.startswith("#"):
                continue
            if _PENDING_INLINE in text:
                offenders.append(f"{path.relative_to(REPO)}:{number}: {stripped}")
    assert not offenders, (
        "re-derives labels.is_pending instead of calling it:\n  " + "\n  ".join(offenders)
    )


def test_validate_never_imports_edit() -> None:
    """edit.py now imports validate.py — the guards need `Diagnostic` and the
    advisory severity. That direction is fine and the reverse is a cycle, so
    the import has to stay one-way. Nothing enforces this but a test: an
    innocent `from .edit import slugify` inside a validator would break every
    surface at import time, and no other test imports the two in isolation."""
    source = (REPO / "src" / "towerkit" / "validate.py").read_text("utf-8")
    offenders = [
        f"{number}: {text.strip()}"
        for number, text in enumerate(source.splitlines(), start=1)
        if "import edit" in text or "from .edit" in text
    ]
    assert not offenders, "validate.py must not import edit.py:\n  " + "\n  ".join(offenders)


MCPSERVER = REPO / "src" / "towerkit" / "mcpserver.py"


def test_the_mcp_server_writes_no_model_attribute_directly() -> None:
    """Same rule as the TUI ban above, on the other surface.

    The server materialised a missing `program.render` with a bare
    `setattr(entity, entry.path[0], container)` — and `program.render` is on
    the DENYLIST. So the one object no caller may set wholesale was being
    constructed inside a surface, in a branch whose central rule is that writes
    live in `edit.py`, and the TUI (which creates the same containers) did not
    inherit the auto-creation semantics. It is `edit.set_container` now.

    `setattr` is banned outright rather than pattern-matched against the
    denylist: a surface has no business writing a model attribute at all, and
    "which attribute" is a judgement a grep cannot make. `getattr` stays —
    reading is what a read tool does.
    """
    offenders = [
        f"mcpserver.py:{number}: {text.strip()}"
        for number, text in enumerate(MCPSERVER.read_text("utf-8").splitlines(), start=1)
        if "setattr(" in text and not text.strip().startswith("#")
    ]
    assert not offenders, (
        "writes belong in towerkit.edit, where all three surfaces inherit the "
        "guards — not in the MCP server:\n  " + "\n  ".join(offenders)
    )


# --- the schema is the model's, not a fourth table ---------------------------
#
# `schema/program.schema.json` enumerates every JSON key by hand and forbids
# additional properties at nine sites, so a field added to `model.py` and
# forgotten there makes the file towerkit's OWN writer just produced fail
# `towerctl validate` — while the MCP write that produced it answers
# `errors: []`, because `validate_program` does not run the schema check.
#
# Reproduced 2026-08-19 by putting `broker_ref: str | None = Field(
# alias="brokerRef", default=None)` on `Layer`: the MCP write reported no
# errors, `towerctl validate` exited 1 with "Additional properties are not
# allowed ('brokerRef' was unexpected)", and the whole suite stayed green.
# `test_schema_copies_are_identical` above cannot see it — it compares the two
# COPIES to each other, and they go wrong together.


def _schema_document() -> dict:
    import json

    return json.loads((REPO / "schema" / "program.schema.json").read_text("utf-8"))


def _model_and_schema_keys() -> list[tuple[str, set[str], set[str]]]:
    """(where, model keys, schema keys) for every object the schema describes.

    The model side is DERIVED — `model.disk_fields` walks `model_fields` and
    asks `_disk_key` what the file calls each one, so an alias (`policyNumber`,
    `appliesTo`, `$schema`) and the one renamed field (`share`) come out right
    without anything here knowing about them.
    """
    from towerkit.model import disk_fields
    from towerkit.schemagen import SCHEMA_MODELS, properties_at

    doc = _schema_document()
    return [
        (
            pointer or "$",
            {key for key, _, _ in disk_fields(model)},
            set(properties_at(doc, pointer)),
        )
        for pointer, model in SCHEMA_MODELS.items()
    ]


def test_every_model_field_has_a_schema_property() -> None:
    """A field on a model and not in the schema. THE defect: the file
    towerkit writes stops validating and only the CLI ever says so.

    Mutation drill (2026-08-19): added `broker_ref: str | None = Field(
    alias="brokerRef", default=None)` to `Layer`. Failed with
    `AssertionError: model fields with no schema property: $defs/layer:
    brokerRef — add them with tools/sync_schema.py, or the file towerkit
    writes stops validating`. Restored.
    """
    missing = [
        f"{where}: {key}"
        for where, model_keys, schema_keys in _model_and_schema_keys()
        for key in sorted(model_keys - schema_keys)
    ]
    assert not missing, (
        "model fields with no schema property: "
        + ", ".join(missing)
        + " — add them with tools/sync_schema.py, or the file towerkit writes "
        "stops validating"
    )


def test_every_schema_property_has_a_model_field() -> None:
    """The other direction. A property the model dropped is a key the schema
    still accepts and nothing can write — and, if it is in a `required` list,
    a key every file is refused for lacking.

    Mutation drill (2026-08-19): added `"brokerRef": {"type": "string"}` to
    the `layer` $def in schema/program.schema.json. Failed with
    `AssertionError: schema properties with no model field: $defs/layer:
    brokerRef — remove them with tools/sync_schema.py; the schema may not
    accept a key model.py has no home for`. Restored.
    """
    orphans = [
        f"{where}: {key}"
        for where, model_keys, schema_keys in _model_and_schema_keys()
        for key in sorted(schema_keys - model_keys)
    ]
    assert not orphans, (
        "schema properties with no model field: "
        + ", ".join(orphans)
        + " — remove them with tools/sync_schema.py; the schema may not accept a "
        "key model.py has no home for"
    )


def test_the_checked_in_schema_is_what_the_generator_produces() -> None:
    """A no-op regeneration, on BOTH copies.

    Stronger than the two tests above together: it also pins property ORDER to
    model declaration order (which is the order the canonical serialiser writes
    the file in), and it proves the repair is mechanical — a failure here has a
    one-command fix rather than a hand edit that may go wrong in the same way
    a second time.

    Mutation drill (2026-08-19): moved the `"currency"` property above
    `"placement"` in schema/program.schema.json. Failed with
    `AssertionError: schema/program.schema.json is not what tools/sync_schema.py
    produces — run it`. Restored.
    """
    import json

    from towerkit.schemagen import dumps_schema, sync_document

    wanted = dumps_schema(sync_document(_schema_document()))
    for path in (
        REPO / "schema" / "program.schema.json",
        REPO / "src" / "towerkit" / "schema" / "program.schema.json",
    ):
        assert path.read_text("utf-8") == wanted, (
            f"{path.relative_to(REPO)} is not what tools/sync_schema.py produces — run it"
        )
    # The generator reads the root copy, so a root copy that is its own fixed
    # point proves nothing on its own: assert the document it produced still
    # parses as the schema jsonschema will load.
    assert json.loads(wanted)["$id"] == "https://towerkit.dev/schema/program.schema.json"


def test_every_schema_def_is_mapped_to_a_model_or_declared_a_scalar() -> None:
    """`SCHEMA_MODELS` is the one declared thing in `schemagen`, so an
    unmapped `$def` is an object nothing above ever compares against a model.

    Mutation drill (2026-08-19): added a `"broker"` $def to
    schema/program.schema.json. Failed with `SchemaDerivationError: $defs
    ['broker'] map to no model and are not declared scalars — add them to
    SCHEMA_MODELS or SCALAR_DEFS, or nothing ever checks them`. Restored.
    """
    from towerkit.schemagen import SCALAR_DEFS, SCHEMA_MODELS, sync_document

    doc = _schema_document()
    sync_document(doc)  # raises on an unmapped $def
    mapped = {p.removeprefix("$defs/") for p in SCHEMA_MODELS if p.startswith("$defs/")}
    assert set(doc["$defs"]) == mapped | set(SCALAR_DEFS)


# --- the derivable facts INSIDE a property, not just the property set --------
#
# The tests above derive the property SET. Three facts inside it were left
# hand-maintained, cross-checked by nothing, and each drifts in BOTH
# directions — proved 2026-08-19 with one-line mutations that all left
# `tools/sync_schema.py --check` printing "schema is in sync with model.py":
#
# - `required` was FILTERED and never added to. A new required field on a
#   model was not required by the schema; making `Layer.name` optional left
#   the schema still demanding it.
# - An EXISTING property was never re-derived, so retyping `Layer.limit` left
#   the schema describing the old type.
# - An enum's VALUE LIST was never re-derived, so adding one member to
#   `Placement` — a one-line change nobody would call a schema change — made
#   `describe()` advertise a value `program_edit_field` wrote with
#   `errors: []` and `towerctl validate` then exited 1 on.
#
# These four ask the checked-in document the question directly, rather than
# through `sync_document`, so a bug in the reconciliation fails here by name
# instead of hiding behind its own output.


def _reconcilable_properties():
    """(where, key, model field info, the checked-in property) for every
    property `schemagen` derives a type for.

    Skips the two it deliberately does not: a child written INLINE as its own
    mapped object (`render`), and a field whose disk form is converted
    (`Participant.share_bps`), both of which are hand-authored entire.
    """
    from towerkit.model import disk_fields, disk_form_is_derived
    from towerkit.schemagen import SCHEMA_MODELS, properties_at

    doc = _schema_document()
    out = []
    for pointer, model in SCHEMA_MODELS.items():
        properties = properties_at(doc, pointer)
        for key, name, info in disk_fields(model):
            child = f"{pointer}/properties/{key}" if pointer else f"properties/{key}"
            if child in SCHEMA_MODELS or not disk_form_is_derived(model, name):
                continue
            if key not in properties:
                continue  # reported by test_every_model_field_has_a_schema_property
            out.append((pointer or "$", key, model, name, info, properties[key]))
    return out


def _required_lists():
    """(where, pointer, model, the model's required keys, the schema's)."""
    from towerkit.model import disk_fields
    from towerkit.schemagen import SCHEMA_MODELS, _at

    doc = _schema_document()
    out = []
    for pointer, model in SCHEMA_MODELS.items():
        node = _at(doc, pointer)
        out.append(
            (
                pointer or "$",
                pointer,
                model,
                [key for key, _, info in disk_fields(model) if info.is_required()],
                list(node.get("required", [])),
            )
        )
    return out


def test_every_required_model_field_is_required_by_the_schema() -> None:
    """A field with no default is a key every file must carry, and a schema
    that does not say so passes files `model.py` refuses to load.

    Mutation drill (2026-08-19): added `mandate: str = Field(min_length=1)` to
    `Layer`. Failed with `AssertionError: the model requires these and the
    schema does not: $defs/layer: mandate — run tools/sync_schema.py`.
    Restored.
    """
    missing = [
        f"{where}: {key}"
        for where, _pointer, _model, model_required, schema_required in _required_lists()
        for key in model_required
        if key not in schema_required
    ]
    assert not missing, (
        "the model requires these and the schema does not: "
        + ", ".join(missing)
        + " — run tools/sync_schema.py"
    )


def test_no_schema_required_entry_is_optional_on_the_model() -> None:
    """The other direction, and the one that needs a human. A schema that
    demands a key the model defaults REFUSES a file `model.py` loads happily —
    which is either a leftover from making the field optional, or a deliberate
    file-format rule, and only a person can say which. `STRICTER_THAN_MODEL`
    is where the deliberate ones are written down, with the reason.

    Mutation drill (2026-08-19): gave `Layer.name` a default
    (`Field(min_length=1, default="unnamed")`). Failed with `AssertionError:
    the schema requires these and the model makes them optional: $defs/layer:
    name — make the model field required, or declare it in
    schemagen.STRICTER_THAN_MODEL with the reason`. Restored.
    """
    from towerkit.schemagen import STRICTER_THAN_MODEL

    stray = [
        f"{where}: {key}"
        for where, pointer, _model, model_required, schema_required in _required_lists()
        for key in schema_required
        if key not in model_required and key not in STRICTER_THAN_MODEL.get(pointer, frozenset())
    ]
    assert not stray, (
        "the schema requires these and the model makes them optional: "
        + ", ".join(stray)
        + " — make the model field required, or declare it in "
        "schemagen.STRICTER_THAN_MODEL with the reason"
    )


def _derivable_shape(node: dict) -> dict:
    """Only the keywords `derive_property` emits, with an enum's VALUES left
    out — those are the next test's subject, and a property that changed BOTH
    should say so once per fact."""
    from towerkit.schemagen import DERIVED_KEYWORDS

    shape = {}
    for key, value in node.items():
        if key not in DERIVED_KEYWORDS:
            continue
        if key == "enum":
            shape[key] = "<values checked separately>"
        elif key == "items" and isinstance(value, dict):
            shape[key] = _derivable_shape(value)
        else:
            shape[key] = value
    return shape


def test_every_schema_property_type_matches_the_model() -> None:
    """A property the schema already knew was never re-derived, so a retyped
    model field left the schema describing the old type — and the schema is
    what decides whether the file towerkit just wrote is valid.

    Mutation drill (2026-08-19): changed `Layer.limit` from
    `Annotated[int, MONEY]` to `str`. Failed with `AssertionError: schema
    properties whose type no longer matches the model: $defs/layer: limit is
    {'type': 'integer'} and the model says {'type': 'string'} — run
    tools/sync_schema.py`. Restored.
    """
    from towerkit.schemagen import derive_property

    wrong = [
        f"{where}: {key} is {_derivable_shape(existing)} and the model says "
        f"{_derivable_shape(derive_property(model, name, info))}"
        for where, key, model, name, info, existing in _reconcilable_properties()
        if _derivable_shape(existing) != _derivable_shape(derive_property(model, name, info))
    ]
    assert not wrong, (
        "schema properties whose type no longer matches the model: "
        + ", ".join(wrong)
        + " — run tools/sync_schema.py"
    )


def test_every_schema_enum_lists_the_models_members() -> None:
    """Adding a member to an enum is a one-line change nobody calls a schema
    change, and the schema is the only thing that then refuses it.

    Reproduced 2026-08-19: with `QUOTED = "quoted"` on `Placement`,
    `describe()` advertised `quoted`, `program_edit_field` wrote it and
    answered `errors: []`, and `towerctl validate` exited 1 with
    `placement: 'quoted' is not one of ['bound', 'proposed']`.

    Mutation drill (2026-08-19): added `QUOTED = "quoted"` to `Placement`.
    Failed with `AssertionError: schema enums that are not the model's
    members: $: placement lists ['bound', 'proposed'] and the model has
    ['bound', 'proposed', 'quoted'] — run tools/sync_schema.py`. Restored.
    """
    from towerkit.schemagen import STRICTER_ENUMS, derive_property

    wrong = []
    for where, key, model, name, info, existing in _reconcilable_properties():
        wanted = derive_property(model, name, info).get("enum")
        if wanted is None and "enum" not in existing:
            continue
        if wanted is None and f"{where}:{key}" in STRICTER_ENUMS:
            # A DECLARED stricter-than-model enum (F9) is the human's half
            # by decision; the generator's stale-declaration check owns it.
            continue
        if existing.get("enum") != wanted:
            wrong.append(
                f"{where}: {key} lists {existing.get('enum')} and the model has {wanted}"
            )
    assert not wrong, (
        "schema enums that are not the model's members: "
        + ", ".join(wrong)
        + " — run tools/sync_schema.py"
    )


def test_a_hand_authored_constraint_the_model_stranded_raises() -> None:
    """The reconciliation overwrites what it DERIVES and refuses what it would
    have to judge. A `minLength` left on a property the model retyped to an
    integer is not a small mess: JSON Schema ignores a keyword that does not
    apply to the instance type, so it reads as a rule and enforces nothing —
    and dropping it silently would discard a bound a human chose. The
    generator says so and lets the maintainer pick.

    Mutation drill (2026-08-19): deleted the `stranded` check from
    `schemagen.reconcile_property`. Failed with `Failed: DID NOT RAISE
    SchemaDerivationError`. Restored.
    """
    from towerkit.schemagen import SchemaDerivationError, reconcile_property

    with pytest.raises(SchemaDerivationError) as caught:
        reconcile_property(
            "$defs/layer:name", {"type": "string", "minLength": 1}, {"type": "integer"}
        )
    assert "minLength" in str(caught.value)
    assert "integer" in str(caught.value)


def test_reconciling_a_property_keeps_the_hand_authored_half_in_place() -> None:
    """The whole bet: derive the type, keep the prose, and keep it WHERE it
    was — `appliesTo` is written `type, minItems, items` while the derivation
    emits `type, items`, so a merge that appended would reorder the file and
    every no-op regeneration would churn.

    Mutation drill (2026-08-19): made `reconcile_property` start from
    `dict(derived)` instead of `dict(existing)`. Failed with `AssertionError:
    assert {'type': 'arr...e': 'string'}} == {'description...inLength': 1}}`,
    and `test_the_checked_in_schema_is_what_the_generator_produces` failed
    with it. Restored.
    """
    from towerkit.schemagen import reconcile_property

    merged = reconcile_property(
        "$defs/layer:states",
        {"description": "Jurisdictions.", "type": "array", "minItems": 1,
         "items": {"type": "string", "minLength": 1}},
        {"type": "array", "items": {"type": "string"}},
    )
    assert merged == {
        "description": "Jurisdictions.",
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "minLength": 1},
    }
    assert list(merged) == ["description", "type", "minItems", "items"]


# --- the GENERATOR's own logic, not only the document it produced ------------
#
# Every test above compares the CHECKED-IN DOCUMENT to the models. That is the
# right question and it is not the whole one: on an already-synced document
# most of `schemagen`'s logic is a no-op, so a mutation to the logic changes
# nothing anybody can see. Seven mutations were applied 2026-08-19 and FOUR
# survived the entire suite AND left `tools/sync_schema.py --check` printing
# "schema is in sync with model.py":
#
# - `_required_for` restored to FILTERING instead of deriving — the exact
#   defect the module was rewritten to kill;
# - `enum` re-derivation skipped;
# - `node["properties"] = {**properties, **existing}`, so the property set is
#   no longer re-derived;
# - the undeclared-`STRICTER_THAN_MODEL` raise removed.
#
# The invariants are not lost — each shows up one step later, the next time
# somebody edits a model. But the state in between is precisely what this
# branch exists to end: the tool says "in sync", pytest says stale, and the
# assertion message tells the maintainer to run the tool that just lied.
#
# So these ask what the generator PRODUCES, from a synthetic model and a
# synthetic document that are deliberately NOT in sync. A change to the logic
# then fails here even while the checked-in schema is perfectly correct.


class _Colour(Enum):
    RED = "red"
    BLUE = "blue"


class _Thing(BaseModel):
    """A model whose only job is to be out of step with `_synthetic_doc`."""

    alpha: str = Field(min_length=1)
    beta: str | None = None
    colour: _Colour = _Colour.RED


def _synthetic_doc(properties: dict, required: list[str] | None = None) -> dict:
    node: dict = {"type": "object", "additionalProperties": False, "properties": properties}
    if required is not None:
        node["required"] = required
    return {"$defs": {"thing": node}}


def _synthetic_sync(monkeypatch, doc: dict) -> dict:
    """`sync_document` over `_Thing` alone, at `$defs/thing`.

    Patching `SCHEMA_MODELS` rather than adding a real model keeps the
    synthetic shape out of the shipped schema: this is a test about the
    derivation, not about towerkit's own document.
    """
    from towerkit import schemagen

    monkeypatch.setattr(schemagen, "SCHEMA_MODELS", {"$defs/thing": _Thing})
    return schemagen.sync_document(doc)["$defs"]["thing"]


def test_the_generator_adds_a_required_key_the_document_lacks(monkeypatch) -> None:
    """`required` is DERIVED from `FieldInfo.is_required()`, never filtered
    from what the document already says. Filtering was the 2026-08-19 defect:
    nothing ever ADDED, so a new required field on a model was simply not
    required by the schema and `--check` stayed clean.

    The document here says `required: []` and the model says `alpha` has no
    default, so the generator must put it back.

    Mutation drill (2026-08-19): replaced the derivation in
    `schemagen._required_for` with the filter it used to be —
    `wanted = [k for k in list(node.get("required") or []) if k in properties]`.
    Failed with `AssertionError: schemagen.sync_document did not derive
    required from the model: got None`. Restored.
    """
    node = _synthetic_sync(
        monkeypatch,
        _synthetic_doc(
            {
                "alpha": {"type": "string", "minLength": 1},
                "beta": {"type": "string"},
                "colour": {"enum": ["red", "blue"]},
            },
            required=[],
        ),
    )
    assert node.get("required") == ["alpha"], (
        f"schemagen.sync_document did not derive required from the model: "
        f"got {node.get('required')!r}"
    )


def test_the_generator_re_derives_a_stale_enums_values(monkeypatch) -> None:
    """Adding a member to an enum is a one-line change nobody calls a schema
    change, and re-deriving the VALUE LIST is the only thing that catches it.
    `test_every_schema_enum_lists_the_models_members` above proves the
    checked-in document agrees today; this proves the generator would FIX it,
    which is what the assertion message there promises.

    Mutation drill (2026-08-19): removed `"enum"` from
    `schemagen.DERIVED_KEYWORDS` and skipped its re-derivation, by making the
    merge loop `if key == "enum" and "enum" in out: continue`. Failed with
    `AssertionError: schemagen.sync_document did not re-derive the enum:
    ['red'] assert ['red'] == ['red', 'blue']`. Restored.
    """
    node = _synthetic_sync(
        monkeypatch,
        _synthetic_doc(
            {
                "alpha": {"type": "string", "minLength": 1},
                "beta": {"type": "string"},
                "colour": {"description": "Kept.", "enum": ["red"]},
            }
        ),
    )
    assert node["properties"]["colour"]["enum"] == ["red", "blue"], (
        f"schemagen.sync_document did not re-derive the enum: "
        f"{node['properties']['colour'].get('enum')}"
    )
    assert node["properties"]["colour"]["description"] == "Kept."


def test_the_generator_drops_a_property_the_model_no_longer_has(monkeypatch) -> None:
    """The property SET is re-derived, not merged into. A property whose model
    field is gone is a key the schema still accepts and nothing can write.

    Mutation drill (2026-08-19): changed `schemagen.sync_document`'s
    `node["properties"] = properties` to
    `node["properties"] = {**properties, **existing}`. Failed with
    `AssertionError: schemagen.sync_document kept a property the model does
    not have: ['alpha', 'beta', 'colour', 'ghost']`. Restored.
    """
    node = _synthetic_sync(
        monkeypatch,
        _synthetic_doc(
            {
                "alpha": {"type": "string", "minLength": 1},
                "ghost": {"type": "string"},
                "beta": {"type": "string"},
                "colour": {"enum": ["red", "blue"]},
            }
        ),
    )
    assert list(node["properties"]) == ["alpha", "beta", "colour"], (
        f"schemagen.sync_document kept a property the model does not have: "
        f"{list(node['properties'])}"
    )


def test_the_generator_re_derives_a_property_the_document_already_has(monkeypatch) -> None:
    """The other half of the same mutation: an EXISTING property is
    reconciled, not passed through, so a retyped model field is corrected
    rather than left describing the old type — while its prose survives.

    Mutation drill (2026-08-19): changed `schemagen.sync_document`'s
    `node["properties"] = properties` to
    `node["properties"] = {**properties, **existing}`. Failed with
    `AssertionError: schemagen.sync_document did not re-derive an existing
    property: {'description': 'Kept.', 'type': 'integer'}`. Restored.
    """
    node = _synthetic_sync(
        monkeypatch,
        _synthetic_doc(
            {
                "alpha": {"description": "Kept.", "type": "integer"},
                "beta": {"type": "string"},
                "colour": {"enum": ["red", "blue"]},
            }
        ),
    )
    assert node["properties"]["alpha"] == {"description": "Kept.", "type": "string"}, (
        f"schemagen.sync_document did not re-derive an existing property: "
        f"{node['properties']['alpha']}"
    )


def test_an_undeclared_stricter_than_model_required_entry_raises() -> None:
    """A schema that demands a key the model defaults REFUSES a file
    `model.py` loads. It is either a leftover from making the field optional
    or a deliberate file-format rule, and only a person can say which — so the
    generator refuses rather than reconciling it away, and
    `STRICTER_THAN_MODEL` is where the deliberate ones are written down.

    `test_no_schema_required_entry_is_optional_on_the_model` above asks this
    of the checked-in document, where it is a no-op; this asks the generator.

    Mutation drill (2026-08-19): replaced the computation in
    `schemagen._required_for` with `stray = []`. Failed with `Failed: DID NOT
    RAISE <class 'towerkit.schemagen.SchemaDerivationError'>`. Restored.
    """
    from towerkit.schemagen import SchemaDerivationError, _required_for

    properties = {"alpha": {"type": "string"}, "beta": {"type": "string"}}
    with pytest.raises(SchemaDerivationError) as caught:
        _required_for(
            "$defs/thing", "$defs/thing", _Thing, properties, {"required": ["alpha", "beta"]}
        )
    assert "beta" in str(caught.value)
    assert "STRICTER_THAN_MODEL" in str(caught.value)
    # and the declared half is accepted, so the raise is about the DECLARATION
    # and not about strictness itself.
    from towerkit import schemagen

    assert _required_for(
        "$defs/thing", "$defs/thing", _Thing, properties, {"required": ["alpha"]}
    ) == ["alpha"]
    assert schemagen.STRICTER_THAN_MODEL[""] == frozenset(
        # A pin, so this test's premise stays true. `sublimits` joined the other
        # three on 2026-08-19: it had been left out of the schema's root `required`
        # by oversight, not by a distinction.
        {"lines", "layers", "retentions", "sublimits"}
    )


def test_every_stricter_than_model_entry_is_still_a_choice_somebody_made() -> None:
    """An entry here suppresses the refusal above, so a stale one is a
    suppression nobody would notice. Each must name a real disk key of the
    model at that pointer AND a field the model actually makes optional — once
    the model requires it, the derivation covers it and the entry is silently
    excusing nothing.

    Mutation drills (2026-08-19), all three on
    `schemagen.STRICTER_THAN_MODEL[""]`, restored after each:

    - added `"notes"` (optional on the model, absent from the schema's
      `required`) — `AssertionError: ... $: notes is not required by the
      schema`;
    - added `"insured"` (already required by the model, so the entry excuses
      nothing) — `AssertionError: ... $: insured is required by the model, so
      the entry excuses nothing`;
    - added `"ghost"` — `AssertionError: ... $: ghost is not a field of
      Program`.
    """
    from towerkit.model import disk_fields
    from towerkit.schemagen import SCHEMA_MODELS, STRICTER_THAN_MODEL, _at

    doc = _schema_document()
    stale = []
    for pointer, keys in STRICTER_THAN_MODEL.items():
        model = SCHEMA_MODELS[pointer]
        where = pointer or "$"
        required_by_model = {key for key, _, info in disk_fields(model) if info.is_required()}
        known = {key for key, _, _ in disk_fields(model)}
        schema_required = set(_at(doc, pointer).get("required", []))
        for key in sorted(keys):
            if key not in known:
                stale.append(f"{where}: {key} is not a field of {model.__name__}")
            elif key in required_by_model:
                stale.append(
                    f"{where}: {key} is required by the model, so the entry excuses nothing"
                )
            elif key not in schema_required:
                stale.append(f"{where}: {key} is not required by the schema")
    assert not stale, (
        "STRICTER_THAN_MODEL entries the model already requires, or that are not "
        "fields at all: " + ", ".join(stale)
    )


# --- what "stranded" means, checked against the validator validate.py loads --
#
# The first cut keyed the check off `out["type"]`, which is ABSENT for a `$ref`
# property and for an `enum` property, so it refused two legitimate tightenings
# and blocked the whole schema — and its message told the maintainer to delete
# a rule that IS enforced. Sibling keywords to `$ref` have applied since draft
# 2019-09. These tests run the real validator rather than reasoning about it.


def _rejects(prop: dict, value: object) -> list[str]:
    """What `Draft202012Validator` — the class `validate.py` loads — says about
    one value under one property subschema, with the real `$defs` in scope."""
    from jsonschema import Draft202012Validator

    doc = _schema_document()
    schema = {
        "type": "object",
        "properties": {"probe": prop},
        "$defs": doc["$defs"],
    }
    return [error.message for error in Draft202012Validator(schema).iter_errors({"probe": value})]


def test_a_bound_beside_a_ref_is_kept_because_the_validator_applies_it() -> None:
    """`{"$ref": "#/$defs/money", "minimum": 1000}` is a legitimate tightening
    — a floor above the `$def`'s own — and refusing it exits the generator 1
    and blocks every other repair in the document.

    Mutation drill (2026-08-19): restored the old type-keyed predicate —
    `kind = out.get("type")` with `if key in out and kind not in applies` —
    which is `None` for a `$ref` property and so matches nothing. Failed with
    `towerkit.schemagen.SchemaDerivationError: $defs/layer:attach is now a
    #/$defs/money, so every value it accepts is ['integer'], and the
    hand-authored ['minimum'] constrains only ['integer', 'number'] — no value
    this property can accept is ever tested against it`, which is the refusal
    contradicting itself in one sentence. Restored.
    """
    from towerkit.schemagen import reconcile_property

    tightened = {"$ref": "#/$defs/money", "minimum": 1000}
    assert _rejects(tightened, 500) == ["500 is less than the minimum of 1000"], (
        "the premise is gone: this jsonschema no longer applies a sibling of $ref"
    )
    doc = _schema_document()
    merged = reconcile_property(
        "$defs/layer:attach", tightened, {"$ref": "#/$defs/money"}, doc["$defs"]
    )
    assert merged == tightened


def test_a_bound_beside_an_enum_is_kept_because_the_validator_applies_it() -> None:
    """Same defect, second false positive. `enum` says nothing about the
    instance TYPE, so a `minLength` beside it is applied to every string
    member — it narrows the enum, it does not sit inert on it.

    Mutation drill (2026-08-19): restored the old type-keyed predicate —
    `kind = out.get("type")` with `if key in out and kind not in applies` —
    which is `None` for an `enum` property too. Failed with
    `towerkit.schemagen.SchemaDerivationError: $:placement is now an enum, so
    every value it accepts is ['string'], and the hand-authored ['minLength']
    constrains only ['string'] — no value this property can accept is ever
    tested against it`. Restored.
    """
    from towerkit.schemagen import reconcile_property

    tightened = {"minLength": 9, "enum": ["bound", "proposed"]}
    assert _rejects(tightened, "bound") == ["'bound' is too short"], (
        "the premise is gone: this jsonschema no longer applies a sibling of enum"
    )
    merged = reconcile_property(
        "$:placement", tightened, {"enum": ["bound", "proposed"]}, _schema_document()["$defs"]
    )
    assert merged == tightened


def test_a_keyword_no_accepted_value_can_be_tested_against_still_raises() -> None:
    """The narrowing must not have thrown the check away. All three shapes
    below are inert — the validator ACCEPTS the value, so the keyword reads as
    a rule and rejects nothing — and each must still refuse.

    Mutation drill (2026-08-19): made `schemagen._permitted_types` return
    `None` unconditionally. Failed with `Failed: DID NOT RAISE
    SchemaDerivationError` on the first case. Restored.
    """
    from towerkit.schemagen import SchemaDerivationError, reconcile_property

    defs = _schema_document()["$defs"]
    inert = [
        # a minLength the model retyped out from under
        ({"type": "string", "minLength": 1}, {"type": "integer"}, 1, "minLength"),
        # a minLength beside a $ref to an INTEGER $def — the true stranding,
        # as against the `minimum` two tests up, which bites
        ({"$ref": "#/$defs/money", "minLength": 5}, {"$ref": "#/$defs/money"}, 5, "minLength"),
        # a numeric bound beside an enum of strings
        ({"minimum": 5, "enum": ["bound"]}, {"enum": ["bound", "proposed"]}, "bound", "minimum"),
    ]
    for existing, derived, accepted, keyword in inert:
        merged = dict(existing)
        merged.update(derived)
        assert _rejects(merged, accepted) == [], (
            f"{merged} is not inert after all — this case belongs in the kept list"
        )
        with pytest.raises(SchemaDerivationError) as caught:
            reconcile_property("$defs/thing:probe", existing, derived, defs)
        assert keyword in str(caught.value)
        assert "rejects nothing" in str(caught.value)


def test_an_unresolvable_ref_stands_the_stranded_check_down() -> None:
    """A `$ref` whose `$def` we were not handed makes the permitted types
    UNKNOWN, and unknown is not stranded. Refusing wrongly exits 1 and blocks
    the whole document; keeping a keyword that may be inert costs nothing but
    the keyword. The safe direction is the one that does not block.

    Mutation drill (2026-08-19): made `schemagen._permitted_types` GUESS for
    an unresolved `$ref` — `return frozenset({"integer"})` in place of
    `return None`. Failed with `towerkit.schemagen.SchemaDerivationError:
    $defs/thing:probe is now a #/$defs/nowhere, so every value it accepts is
    ['integer'], and the hand-authored ['minLength'] constrains only
    ['string'] ... so it is a rule that rejects nothing`, refusing a merge it
    has no grounds to judge. Restored.
    """
    from towerkit.schemagen import reconcile_property

    merged = reconcile_property(
        "$defs/thing:probe",
        {"$ref": "#/$defs/nowhere", "minLength": 5},
        {"$ref": "#/$defs/nowhere"},
        {},
    )
    assert merged == {"$ref": "#/$defs/nowhere", "minLength": 5}


# --- a derived keyword is overwritten only where the model derives one -------


def test_a_hand_authored_enum_the_model_has_no_opinion_on_survives() -> None:
    """`enum` is derived from an `Enum` annotation and from NOTHING else, so on
    a plain `str` field the model asserts nothing about the value set. Deleting
    the human's `{"enum": ["USD", "GBP"]}` there — which the first cut did with
    no message at all — silently LOOSENS validation on a broker's file, which
    is the exact failure this module exists to end.

    Preserved rather than refused because it can only TIGHTEN: every value the
    enum admits the derived `type` admits too, so nothing `model.py` loads
    becomes unloadable. `pattern` and `minLength` beside it are kept on the
    same grounds and always were; `enum` was the one that vanished.

    Mutation drill (2026-08-19): removed `HAND_AUTHORABLE_KEYWORDS` from the
    delete loop in `schemagen.reconcile_property`, restoring `if k in
    DERIVED_KEYWORDS and k not in derived`. Failed with `AssertionError: a
    hand-authored enum was silently deleted: {'type': 'string', 'minLength':
    3, 'maxLength': 3}`. Restored.

    Since Grant's F9 call (2026-08-20) the survival requires a DECLARATION
    in STRICTER_ENUMS — kept, but never silently; the undeclared case is
    `test_an_undeclared_hand_authored_enum_raises`.
    """
    from towerkit import schemagen

    existing = {"type": "string", "minLength": 3, "maxLength": 3, "enum": ["USD", "GBP"]}
    declared = {**schemagen.STRICTER_ENUMS, "$:currency": "the book trades in these"}
    try:
        schemagen.STRICTER_ENUMS.update(declared)
        merged = schemagen.reconcile_property("$:currency", existing, {"type": "string"}, {})
    finally:
        schemagen.STRICTER_ENUMS.pop("$:currency", None)
    assert merged == existing, f"a hand-authored enum was silently deleted: {merged}"
    assert _rejects(merged, "XYZ") == ["'XYZ' is not one of ['USD', 'GBP']"]


def test_a_hand_authored_enum_the_model_contradicts_raises() -> None:
    """The one thing a preserved enum is checked for. An enum of strings under
    a model that says `integer` accepts NO value at all — the mirror of the
    loosening above, and the only shape where silence would be worse than a
    refusal.

    Mutation drill (2026-08-19): deleted the `not permitted` branch from
    `schemagen.reconcile_property`. Failed with `Failed: DID NOT RAISE
    <class 'towerkit.schemagen.SchemaDerivationError'>`. Restored.
    """
    from towerkit.schemagen import SchemaDerivationError, reconcile_property

    with pytest.raises(SchemaDerivationError) as caught:
        reconcile_property(
            "$defs/layer:limit",
            {"type": "string", "enum": ["USD", "GBP"]},
            {"type": "integer"},
            {},
        )
    assert "no value at all" in str(caught.value)
    assert "integer" in str(caught.value)
    # a model enum, in contrast, simply WINS — there is no judgement to keep
    assert reconcile_property(
        "$:placement", {"enum": ["bound"]}, {"enum": ["bound", "proposed"]}, {}
    ) == {"enum": ["bound", "proposed"]}


def test_a_second_pass_over_a_repaired_document_changes_nothing() -> None:
    """`sync_document` must be the fixed point of itself, on a document that
    needed REAL repair and that carries hand-authored keywords the generator
    has to decide about. A second pass that differed would leave `--check`
    reporting stale forever after any repair, and a second pass that RAISED
    would mean the generator refuses its own output.

    Honest note on what the bare fixed-point line protects: no mutation was
    found that breaks `sync_document(once) == once` alone — the function is a
    projection, so the second pass has nothing left to do. What the drills
    below kill are the assertions beside it, which is where this test earns
    its place: they say the two 2026-08-19 preservation rules survive being
    fed back in.

    Mutation drills (2026-08-19), restored after each:

    - the P8 mutation (`if k in DERIVED_KEYWORDS and k not in derived` in
      `schemagen.reconcile_property`, deleting the hand-authored enum):
      `KeyError: 'enum'`;
    - the P7 mutation (the old type-keyed stranded predicate):
      `towerkit.schemagen.SchemaDerivationError: $defs/layer:attach ... the
      hand-authored ['minimum'] constrains only ['integer', 'number']` — it
      refuses to run at all;
    - the P6 `_required_for` filter mutation: `AssertionError: assert None ==
      ['insured', 'program', 'placement', 'period', 'lines', 'layers',
      'retentions']`;
    - the P6 enum mutation (`if key == "enum" and "enum" in out: continue` in
      the merge loop): `AssertionError: assert ['bound'] == ['bound',
      'proposed']`.
    """
    import copy

    from towerkit import schemagen
    from towerkit.schemagen import sync_document

    stale = _schema_document()
    stale["required"] = []                                    # to be re-derived
    stale["properties"]["placement"]["enum"] = ["bound"]       # to be re-derived
    stale["properties"]["currency"]["enum"] = ["USD", "GBP"]   # to be KEPT (declared, F9)
    stale["$defs"]["layer"]["properties"]["attach"]["minimum"] = 1000  # to be KEPT

    schemagen.STRICTER_ENUMS["$:currency"] = "the book trades in exactly these"
    try:
        once = sync_document(stale)
        assert once.get("required") == [
            "insured", "program", "placement", "period",
            "lines", "layers", "retentions", "sublimits",
        ]
        assert once["properties"]["placement"]["enum"] == ["bound", "proposed"]
        assert once["properties"]["currency"]["enum"] == ["USD", "GBP"]
        assert once["$defs"]["layer"]["properties"]["attach"]["minimum"] == 1000

        assert sync_document(copy.deepcopy(once)) == once, "sync_document is not idempotent"
    finally:
        schemagen.STRICTER_ENUMS.pop("$:currency", None)


def test_an_enum_with_non_string_values_raises_rather_than_stringifying() -> None:
    """Round six, proven against `jsonschema.Draft202012Validator`: an
    int-valued Enum came out as `{"enum": ["1", "2"]}` while the file would
    hold `1`, and the validator rejected the file's own value (`1 is not one
    of ['1', '2']`). Emitting a wrong schema instead of raising is the one
    thing `schemagen` promises never to do. Latent — every current enum is a
    StrEnum — so only a shape the generator has never seen can pin it."""
    from enum import Enum as PlainEnum

    from pydantic import BaseModel as PydanticBase

    from towerkit.schemagen import SchemaDerivationError, derive_property

    class Numbered(PlainEnum):
        ONE = 1
        TWO = 2

    class Holder(PydanticBase):
        numbered: Numbered

    with pytest.raises(SchemaDerivationError, match="non-string"):
        derive_property(Holder, "numbered", Holder.model_fields["numbered"])


def test_a_money_bound_the_schema_cannot_express_raises() -> None:
    """Round six: `Annotated[int, Field(ge=1), MONEY]` emitted a bare
    `{"type": "integer"}` — the `ge=1` silently dropped, schema looser than
    model with no message. The module's precedent for constraints it will not
    guess at is the nested-model branch: refuse loudly and name the
    hand-authored repair. Same here — `minimum` is the human's half of the
    document and survives reconciliation, so the raise says to author it."""
    from typing import Annotated as Ann

    from pydantic import BaseModel as PydanticBase
    from pydantic import Field as PydanticField

    from towerkit.model import MONEY as MONEY_TAG
    from towerkit.schemagen import SchemaDerivationError, derive_property

    class Holder(PydanticBase):
        amount: Ann[int, PydanticField(ge=1), MONEY_TAG]

    with pytest.raises(SchemaDerivationError, match="minimum"):
        derive_property(Holder, "amount", Holder.model_fields["amount"])


def test_every_tool_registers_through_the_coded_ring() -> None:
    """Rounds five, six and seven each caught an exception family the ring
    before it missed — TypeError inside the mutation, load_program at the
    perimeter, then OSError/KeyError one ring further out. The sequence ends
    only if the ring is STRUCTURAL: every tool registration goes through
    `_tool`, which codes anything that is not already a Refusal as
    [internal_error]. A tool registered with a bare `server.tool()` would sit
    outside the ring, so no registration site may use it directly."""
    import re as _re

    source = (REPO / "src" / "towerkit" / "mcpserver.py").read_text(encoding="utf-8")
    bare = [
        line.strip()
        for line in source.splitlines()
        if _re.search(r"@server\.tool\(", line)
    ]
    assert bare == [], f"tools registered outside the coded ring: {bare}"
    assert "def _tool(" in source, "the ring itself is gone"


def test_an_undeclared_hand_authored_enum_raises() -> None:
    """Grant's F9 decision (2026-08-20): symmetry with STRICTER_THAN_MODEL.
    A hand-authored `enum` on a property whose model type derives none is
    the schema being stricter than the model — and stricter-than-model is a
    decision someone makes out loud, or the generator refuses. Retyping an
    Enum field to plain `str` used to leave the old enum behind as silently
    "hand-authored", declared nowhere."""
    from towerkit.schemagen import SchemaDerivationError, reconcile_property

    with pytest.raises(SchemaDerivationError, match="STRICTER_ENUMS"):
        reconcile_property(
            "$:currency", {"type": "string", "enum": ["USD", "GBP"]}, {"type": "string"}
        )


def test_a_declared_hand_authored_enum_survives(monkeypatch) -> None:
    from towerkit import schemagen

    monkeypatch.setitem(
        schemagen.STRICTER_ENUMS, "$:currency", "the book trades in exactly these"
    )
    out = schemagen.reconcile_property(
        "$:currency", {"type": "string", "enum": ["USD", "GBP"]}, {"type": "string"}
    )
    assert out["enum"] == ["USD", "GBP"]


def test_a_declaration_for_a_property_without_an_enum_raises() -> None:
    """Both directions, like STRICTER_THAN_MODEL: a stale declaration is as
    wrong as a missing one."""
    from towerkit import schemagen

    problems = schemagen.stale_enum_declarations(
        {"$:ghost": "reason"}, {"$:currency": {"type": "string"}}
    )
    assert problems
