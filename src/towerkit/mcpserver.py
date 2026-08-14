"""towerctl mcp — the design surface over towerkit's program files.

Tower DESIGN lives here: lines, retentions, sublimits, and the shape of the
stack. Book facts — premiums firming, markets binding, dates moving — belong
to bookkit's server, which edits these same files through towerkit.

stdout is protocol. Never print.

Tool wrappers are `async def` with synchronous bodies: the SDK runs a *sync*
tool callable on a worker thread via `anyio.to_thread.run_sync`, and this
server's last-seen-sha state should stay on the event-loop thread that owns
it. Each wrapper just calls a module-level `_program_*` helper — the bodies
do no I/O worth yielding for, and the helpers are what the unit tests call
directly (see tests/test_mcpserver.py for why: the installed SDK's
`_tool_manager.get_tool(name).fn` returns a coroutine function for an async
tool, so calling it synchronously hands back an unawaited coroutine, not a
result — no good for a plain `def test_...`). The protocol round-trip test
is the proof that registration and wiring work end to end.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from . import edit
from .model import Program, dumps_program, load_program, loads_program
from .validate import Diagnostic, validate_program

DEFAULT_ROOT = Path("programs")
SNAPSHOT_KEEP = 20
_SNAPDIR = ".mcp-snapshots"
_PREFIX = "TKW-"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Programs:
    """Name resolution, the sandbox, and what this session has last seen.

    A name is a file stem relative to a root — 'atomic-2027' or
    'private/endeavour-2026'. Anything resolving outside the roots is
    refused: this server is not a general file writer."""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = [Path(r).resolve() for r in (roots or [DEFAULT_ROOT])]
        self.seen: dict[str, str] = {}

    def resolve(self, name: str, must_exist: bool = True) -> Path:
        if name.endswith(".json"):
            name = name[: -len(".json")]
        for root in self.roots:
            candidate = (root / f"{name}.json").resolve()
            if not self._inside(candidate):
                continue
            if candidate.exists() or not must_exist:
                return candidate
        if not must_exist:
            raise ValueError(
                f"{name!r} resolves outside the program roots "
                f"({', '.join(str(r) for r in self.roots)})"
            )
        for root in self.roots:
            if not self._inside((root / f"{name}.json").resolve()):
                raise ValueError(
                    f"{name!r} resolves outside the program roots "
                    f"({', '.join(str(r) for r in self.roots)})"
                )
        raise ValueError(f"no program {name!r} — available: {', '.join(self.names())}")

    def _inside(self, candidate: Path) -> bool:
        # `.resolve()` on both `roots` (in __init__) and every `candidate` collapses
        # symlinks before this check — a symlink inside a root that points outside
        # it resolves to its real (outside) path first, so `is_relative_to` catches
        # it here. Drop either `.resolve()` call and this defence silently reopens.
        return any(candidate.is_relative_to(root) for root in self.roots)

    def names(self) -> list[str]:
        out: list[str] = []
        for root in self.roots:
            for path in sorted(root.rglob("*.json")):
                if ".mcp-snapshots" in path.parts:
                    continue
                out.append(path.relative_to(root).with_suffix("").as_posix())
        return out

    def note(self, path: Path) -> str:
        """Record the sha this session has now seen for a file, and return it."""
        sha = file_sha256(path)
        self.seen[str(path)] = sha
        return sha


def write_ref() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{_PREFIX}{stamp}-{secrets.token_hex(2)}"


def _snapdir(path: Path) -> Path:
    return path.parent / _SNAPDIR


def snapshot(path: Path, ref: str, pre_image: bytes) -> None:
    """Record the pre-image and the post-write sha. Called AFTER a successful
    write, so a refused write leaves no debris. The directory is shared with
    bookkit's snapshots; each side globs only its own prefix."""
    snapdir = _snapdir(path)
    snapdir.mkdir(exist_ok=True)
    (snapdir / f"{ref}.json").write_bytes(pre_image)
    (snapdir / f"{ref}.meta.json").write_text(
        json.dumps({"path": str(path), "post_sha256": file_sha256(path)})
    )
    images = sorted(
        (p for p in snapdir.glob(f"{_PREFIX}*.json") if not p.name.endswith(".meta.json")),
        key=lambda p: p.stat().st_mtime,
    )
    for stale in images[:-SNAPSHOT_KEEP]:
        stale.unlink(missing_ok=True)
        (snapdir / f"{stale.stem}.meta.json").unlink(missing_ok=True)


def restore(path: Path, ref: str) -> None:
    """Put the pre-image back, only while the file still holds exactly what
    that write produced. Anything newer — the TUI, bookkit, a later write —
    makes the pre-image stale and this refuses."""
    snapdir = _snapdir(path)
    image, meta_file = snapdir / f"{ref}.json", snapdir / f"{ref}.meta.json"
    if not image.exists() or not meta_file.exists():
        raise ValueError(
            f"no snapshot for {ref} — it may have been pruned "
            f"(the last {SNAPSHOT_KEEP} writes are kept)"
        )
    meta = json.loads(meta_file.read_text())
    if str(path) != meta["path"]:
        raise ValueError(f"{ref} was a write to {meta['path']}, not this file")
    if file_sha256(path) != meta["post_sha256"]:
        raise ValueError(
            f"the file has changed since {ref} wrote it — a newer edit (the "
            f"towerkit editor, bookkit, or a later write) would be lost; revert "
            f"newer writes first"
        )
    path.write_bytes(image.read_bytes())


def _atomic_write(path: Path, text: str) -> None:
    """Same-directory temp + replace: a crash mid-write leaves the old file,
    never a truncated one."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write(
    programs: Programs,
    name: str,
    summary: str,
    mutate: Callable[[Program], None],
) -> dict[str, Any]:
    """One design write: drift check → load → mutate → hard-validate →
    canonical dump → atomic replace → snapshot → re-arm the guard.

    Two tiers of validation, on purpose. HARD: the mutation must produce a
    program that still models and still round-trips through the canonical
    dump — a file towerkit cannot load is never written. SOFT: semantic
    diagnostics do not block. A tower under construction is invalid by
    construction (a new line is line-empty until it carries a layer, a stack
    being built passes through line-gap), so errors come back in the result
    and the caller keeps building."""
    path = programs.resolve(name)
    known = programs.seen.get(str(path))
    if known is None:
        raise ValueError(
            f"{name} has not been read in this session — call program_read first "
            f"so there is a baseline to compare against"
        )
    if file_sha256(path) != known:
        raise ValueError(
            f"{name} changed on disk since you read it (the towerkit editor, "
            f"bookkit, or another tool) — re-read it and retry"
        )

    pre_image = path.read_bytes()
    program = load_program(path)
    try:
        mutate(program)
        edit.heal_follows(program)
        text = dumps_program(program)
        loads_program(text)  # proves the file we are about to write is loadable
    except (ValidationError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        raise ValueError(f"refused — nothing written: {exc}") from exc

    _atomic_write(path, text)
    ref = write_ref()
    snapshot(path, ref, pre_image)
    programs.note(path)
    diags = validate_program(program)
    return {
        "wrote": name,
        "summary": summary,
        "write_ref": ref,
        "errors": _diag(diags.errors),
        "warnings": _diag(diags.warnings),
    }


def _period(program: Program) -> dict[str, str]:
    return {"from": program.period.start.isoformat(), "to": program.period.end.isoformat()}


def _diag(items: list[Diagnostic]) -> list[dict[str, Any]]:
    return [{"code": d.code, "message": d.message, "ref": f"{d.ref[0]}:{d.ref[1]}"} for d in items]


def _program_summary(name: str, program: Program) -> dict[str, Any]:
    return {
        "name": name,
        "insured": program.insured,
        "program": program.program,
        "placement": program.placement.value,
        "period": _period(program),
        "layers": len(program.layers),
    }


def _program_list(programs: Programs) -> dict[str, Any]:
    """Every program file under the configured roots."""
    out: list[dict[str, Any]] = []
    for name in programs.names():
        path = programs.resolve(name)
        try:
            program = load_program(path)
        except Exception as exc:  # a broken file must not hide the rest
            out.append({"name": name, "error": str(exc)})
            continue
        out.append(_program_summary(name, program))
    return {"programs": out, "roots": [str(r) for r in programs.roots]}


def _program_read(programs: Programs, name: str) -> dict[str, Any]:
    """The full structure of one program. Read before you write.

    Retentions and sublimits have no ids — they carry the `index` that
    the edit tools address them by."""
    path = programs.resolve(name)
    program = load_program(path)
    sha = programs.note(path)
    return {
        "name": name,
        "insured": program.insured,
        "program": program.program,
        "placement": program.placement.value,
        "period": _period(program),
        "currency": program.currency,
        "sha": sha,
        "lines": [
            {"id": ln.id, "name": ln.name, "abbr": ln.abbr, "group": ln.group}
            for ln in program.lines
        ],
        "layers": [
            {
                "id": ly.id,
                "name": ly.name,
                "appliesTo": list(ly.applies_to),
                "attach": ly.attach,
                "limit": ly.limit,
                "premium": ly.premium,
                "followsUnderlying": ly.follows_underlying,
                "participants": [
                    {"carrier": p.carrier, "share_bps": p.share_bps} for p in ly.participants
                ],
            }
            for ly in program.layers
        ],
        "retentions": [
            {
                "index": i,
                "appliesTo": list(r.applies_to),
                "type": r.type.value,
                "amount": r.amount,
                "aggregate": r.aggregate,
                "vehicle": r.vehicle,
            }
            for i, r in enumerate(program.retentions)
        ],
        "sublimits": [
            {"index": i, "name": s.name, "amount": s.amount, "appliesTo": list(s.applies_to)}
            for i, s in enumerate(program.sublimits)
        ],
    }


def _program_view(programs: Programs, name: str) -> dict[str, Any]:
    """Draw the tower as text. Gaps, overlaps, and column shape are
    visible here in a way they are not in a layer list."""
    from .render.ascii import render_ascii
    from .theme import load_theme

    path = programs.resolve(name)
    program = load_program(path)
    programs.note(path)
    diags = validate_program(program)
    return {
        "name": name,
        "view": render_ascii(
            program,
            load_theme(),  # no argument = the built-in default, as tests/test_ascii.py does
            colour=False,
            error_layers=frozenset(
                str(d.ref[1]) for d in diags.errors if d.ref[0] == "layer"
            ),
            error_lines=frozenset(str(d.ref[1]) for d in diags.errors if d.ref[0] == "line"),
        ),
    }


def _program_check(programs: Programs, name: str) -> dict[str, Any]:
    """towerkit's validator on one program: what is wrong and where."""
    path = programs.resolve(name)
    program = load_program(path)
    programs.note(path)
    diags = validate_program(program)
    return {
        "name": name,
        "errors": _diag(diags.errors),
        "warnings": _diag(diags.warnings),
    }


def _restack(programs: Programs, name: str) -> dict[str, Any]:
    """Reseat every layer on top of what its lines already carry — one call
    heals gaps and overlaps after limit edits."""
    return _write(programs, name, "restacked the tower", edit.restack)


def _program_revert_write(programs: Programs, write_ref: str) -> dict[str, Any]:
    """Undo one write by restoring its pre-image — only while the file still
    holds exactly what that write produced.

    `snapshot()` writes beside the program file (`path.parent / _SNAPDIR`),
    so for a nested name like `private/secret-2026` the snapshot lands in
    `<root>/private/.mcp-snapshots/`, not `<root>/.mcp-snapshots/`. Walk each
    root for a `.mcp-snapshots` dir at any depth rather than assuming one at
    the top."""
    for root in programs.roots:
        for meta_file in root.rglob(f"{_SNAPDIR}/{write_ref}.meta.json"):
            target = Path(json.loads(meta_file.read_text())["path"])
            restore(target, write_ref)
            programs.note(target)
            return {"reverted": write_ref, "file": str(target)}
    raise ValueError(f"no snapshot for {write_ref} under the program roots")


def build_server(roots: list[Path] | None = None) -> MCPServer:
    programs = Programs(roots)
    server = MCPServer(
        name="towerkit",
        instructions=(
            "Design insurance towers in towerkit program files: coverage lines, "
            "retentions, sublimits, and the shape of the layer stack. Read a "
            "program (program_read) before writing to it — writes refuse against "
            "a file this session has not read, and against one that changed since. "
            "program_view draws the tower; use it to see gaps and overlaps. "
            "Validation errors do NOT block writes: a tower under construction is "
            "invalid by construction, so errors come back in the result and you "
            "keep building. Book facts — premiums, market shares, policy dates — "
            "belong to bookkit's tools, not these."
        ),
    )
    _register_read_tools(server, programs)
    _register_write_tools(server, programs)
    return server


def _register_read_tools(server: MCPServer, programs: Programs) -> None:
    @server.tool()
    async def program_list() -> dict[str, Any]:
        """Every program file under the configured roots."""
        return _program_list(programs)

    @server.tool()
    async def program_read(name: str) -> dict[str, Any]:
        """The full structure of one program. Read before you write.

        Retentions and sublimits have no ids — they carry the `index` that
        the edit tools address them by."""
        return _program_read(programs, name)

    @server.tool()
    async def program_view(name: str) -> dict[str, Any]:
        """Draw the tower as text. Gaps, overlaps, and column shape are
        visible here in a way they are not in a layer list."""
        return _program_view(programs, name)

    @server.tool()
    async def program_check(name: str) -> dict[str, Any]:
        """towerkit's validator on one program: what is wrong and where."""
        return _program_check(programs, name)


def _register_write_tools(server: MCPServer, programs: Programs) -> None:
    @server.tool()
    async def restack(name: str) -> dict[str, Any]:
        """Reseat every layer on top of what its lines already carry —
        one call heals gaps and overlaps after limit edits."""
        return _restack(programs, name)

    @server.tool()
    async def program_revert_write(write_ref: str) -> dict[str, Any]:
        """Undo one write by restoring its pre-image — only while the file
        still holds exactly what that write produced."""
        return _program_revert_write(programs, write_ref)


def serve(roots: list[Path] | None = None) -> None:
    build_server(roots).run()  # stdio transport is the SDK's default
