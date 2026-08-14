"""What to type into the design assistant's Add MCP Connector panel, and
whether it will work.

The panel is hand-entry — the host owns its own config and stores env values
encrypted — so nothing here writes a file. These helpers return data; cli.py
prints it.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BOOKCTL_TIMEOUT_S = 10


class NoRoots(Exception):
    """Raised rather than emitting the ./programs default.

    A connector carrying that default starts cleanly and serves an empty
    shelf, which reads as "there are no programs" instead of as an error.
    """


@dataclass(frozen=True)
class ConnectorFields:
    """One field per input in the Add MCP Connector dialog."""

    name: str
    command: str
    arguments: list[str]
    env: dict[str, str]
    mode: str


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CheckReport:
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _find_bookctl() -> str | None:
    """Beside this interpreter first: bookkit's install.sh puts bookctl and
    towerctl in the same venv, and neither lands on PATH."""
    sibling = Path(sys.executable).parent / "bookctl"
    if sibling.is_file():
        return str(sibling)
    return shutil.which("bookctl")


def bookkit_roots() -> list[Path]:
    """Ask bookkit where the program files live.

    A subprocess, not an import: bookkit depends on towerkit, and reversing
    that would make towerkit uninstallable without it. Running bookctl is the
    direction TOWERKIT_POST_WRITE_CMD already established. Every failure —
    absent, non-zero, slow, or not JSON — degrades to "no roots".
    """
    exe = _find_bookctl()
    if exe is None:
        return []
    try:
        done = subprocess.run(
            [exe, "roots", "--json"],
            capture_output=True, text=True, timeout=BOOKCTL_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        return []
    found = payload.get("roots") if isinstance(payload, dict) else None
    if not isinstance(found, list):
        return []
    return [Path(str(r)) for r in found]


def fields(roots: Sequence[Path] | None = None) -> ConnectorFields:
    chosen = list(roots) if roots else bookkit_roots()
    resolved = [Path(r).expanduser().resolve() for r in chosen]
    if not resolved:
        raise NoRoots("no program roots — pass --programs or configure bookctl roots")
    return ConnectorFields(
        name="towerkit",
        command=str(Path(sys.executable).parent / "towerctl"),
        arguments=["mcp", "--programs", *(str(r) for r in resolved)],
        env={},
        mode="both",
    )


def _command() -> CheckResult:
    path = Path(sys.executable).parent / "towerctl"
    if path.is_file() and os.access(path, os.X_OK):
        return CheckResult("command", True, str(path))
    return CheckResult("command", False, f"not an executable file: {path}")


def _root(index: int, path: Path) -> CheckResult:
    label = f"root {index}"
    if not path.is_dir():
        return CheckResult(label, False, f"not a directory: {path}")
    count = len(list(path.rglob("*.json")))
    if not count:
        # The quiet failure this whole command exists to catch: the server
        # starts, and an empty shelf reads as "you have no programs".
        return CheckResult(label, False, f"{path} holds no .json programs")
    return CheckResult(label, True, f"{path} ({count} program file(s))")


def _stdout(roots: list[Path]) -> CheckResult:
    """stdout is the MCP wire; anything printed at startup corrupts it."""
    from . import mcpserver

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        mcpserver.build_server(roots)
    noise = buffer.getvalue()
    if noise:
        return CheckResult("stdout", False, f"startup printed {noise[:80]!r}")
    return CheckResult("stdout", True, "silent at startup")


def check(roots: Sequence[Path] | None = None) -> CheckReport:
    chosen = list(roots) if roots else bookkit_roots()
    resolved = [Path(r).expanduser().resolve() for r in chosen]
    checks = [_command()]
    if not resolved:
        checks.append(CheckResult("roots", False, "none — pass --programs"))
        return CheckReport(checks)
    checks.extend(_root(i, r) for i, r in enumerate(resolved, start=1))
    checks.append(_stdout(resolved))
    return CheckReport(checks)
