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
