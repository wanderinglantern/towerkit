"""One definition per rule.

Structural mutation lives in towerkit.edit, so the TUI and the MCP server
cannot drift apart. This is the same shape as bookkit's no-raw-SQL-in-tui
test: cheap, mechanical, and it fails the moment someone reaches past the
API instead of extending it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    from towerkit.schemagen import derive_property

    wrong = []
    for where, key, model, name, info, existing in _reconcilable_properties():
        wanted = derive_property(model, name, info).get("enum")
        if wanted is None and "enum" not in existing:
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
