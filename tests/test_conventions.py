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
