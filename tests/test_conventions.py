"""One definition per rule.

Structural mutation lives in towerkit.edit, so the TUI and the MCP server
cannot drift apart. This is the same shape as bookkit's no-raw-SQL-in-tui
test: cheap, mechanical, and it fails the moment someone reaches past the
API instead of extending it."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
