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
from .money import parse_money
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


def _guard_index(items: list[Any], index: int, what: str) -> None:
    if not 0 <= index < len(items):
        raise ValueError(
            f"no {what} at index {index} — there are {len(items)}; re-read and retry"
        )


def _line_add(
    programs: Programs,
    name: str,
    line_name: str,
    abbr: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    """Add a coverage line — a new column in the tower. It will report
    line-empty until a layer covers it."""
    added: list[str] = []

    def do(program: Program) -> None:
        added.append(edit.add_line(program, line_name, abbr, group).id)

    out = _write(programs, name, f"added line {line_name!r}", do)
    return {**out, "added": added[0]}


def _line_edit(
    programs: Programs,
    name: str,
    line_id: str,
    line_name: str | None = None,
    abbr: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    """Rename a line (the id follows the name, cascading everywhere it is
    referenced), set its abbreviation, or set its coverage group."""
    result: list[str] = []

    def do(program: Program) -> None:
        current = line_id
        if line_name is not None:
            current = edit.rename_line(program, current, line_name).id
        if group is not None:
            edit.set_line_group(program, current, group or None)
        if abbr is not None:
            line = next((ln for ln in program.lines if ln.id == current), None)
            if line is None:
                raise KeyError(f"no line {current!r}")
            line.abbr = abbr or None
        result.append(current)

    out = _write(programs, name, f"edited line {line_id}", do)
    return {**out, "line_id": result[0]}


def _line_move(programs: Programs, name: str, line_id: str, delta: int) -> dict[str, Any]:
    """Move a line left (-1) or right (+1). Array order is column order."""

    def do(program: Program) -> None:
        edit.move_line(program, line_id, delta)

    return _write(programs, name, f"moved line {line_id} by {delta}", do)


def _line_remove(programs: Programs, name: str, line_id: str) -> dict[str, Any]:
    """Remove a line. Anything left covering nothing — layers, retentions,
    sublimits — goes with it."""

    def do(program: Program) -> None:
        edit.remove_line(program, line_id)

    return _write(programs, name, f"removed line {line_id}", do)


def _retention_add(
    programs: Programs,
    name: str,
    applies_to: list[str],
    type: str,
    amount: str,
    aggregate: str | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """What the insured pays below the tower. `type` is deductible, sir, or
    captive; a captive needs a named vehicle. Money in dollars
    ('500k', '$1,000,000')."""
    index: list[int] = []

    def do(program: Program) -> None:
        edit.add_retention(
            program,
            applies_to,
            type,
            parse_money(amount),
            aggregate=parse_money(aggregate) if aggregate else None,
            vehicle=vehicle,
            notes=notes,
        )
        index.append(len(program.retentions) - 1)

    out = _write(programs, name, f"added a {type} retention", do)
    return {**out, "index": index[0]}


def _retention_edit(
    programs: Programs,
    name: str,
    index: int,
    expecting_lines: list[str],
    applies_to: list[str] | None = None,
    type: str | None = None,
    amount: str | None = None,
    aggregate: str | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edit the retention at `index` from a program_read. Retentions have no
    ids, so `expecting_lines` guards against the list having shifted: it
    must match what the read reported."""

    def do(program: Program) -> None:
        _guard_index(program.retentions, index, "retention")
        actual = list(program.retentions[index].applies_to)
        if actual != list(expecting_lines):
            raise ValueError(
                f"retention {index} now applies to {actual}, not "
                f"expecting_lines={list(expecting_lines)} — re-read and retry"
            )
        edit.edit_retention(
            program,
            index,
            applies_to=applies_to,
            type=type,
            amount=parse_money(amount) if amount else None,
            aggregate=parse_money(aggregate) if aggregate else None,
            vehicle=vehicle,
            notes=notes,
        )

    return _write(programs, name, f"edited retention {index}", do)


def _retention_remove(
    programs: Programs, name: str, index: int, expecting_lines: list[str]
) -> dict[str, Any]:
    """Remove the retention at `index`, guarded by the lines it covers."""

    def do(program: Program) -> None:
        _guard_index(program.retentions, index, "retention")
        actual = list(program.retentions[index].applies_to)
        if actual != list(expecting_lines):
            raise ValueError(
                f"retention {index} now applies to {actual}, not "
                f"expecting_lines={list(expecting_lines)} — re-read and retry"
            )
        edit.remove_retention(program, index)

    return _write(programs, name, f"removed retention {index}", do)


def _sublimit_add(
    programs: Programs,
    name: str,
    sublimit_name: str,
    amount: str,
    applies_to: list[str],
    notes: str | None = None,
) -> dict[str, Any]:
    """A named cap inside the tower — flood, quake, a per-location limit."""
    index: list[int] = []

    def do(program: Program) -> None:
        edit.add_sublimit(program, sublimit_name, parse_money(amount), applies_to, notes)
        index.append(len(program.sublimits) - 1)

    out = _write(programs, name, f"added sublimit {sublimit_name!r}", do)
    return {**out, "index": index[0]}


def _sublimit_edit(
    programs: Programs,
    name: str,
    index: int,
    expecting_name: str,
    sublimit_name: str | None = None,
    amount: str | None = None,
    applies_to: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edit the sublimit at `index`, guarded by the name the read reported."""

    def do(program: Program) -> None:
        _guard_index(program.sublimits, index, "sublimit")
        actual = program.sublimits[index].name
        if actual != expecting_name:
            raise ValueError(
                f"sublimit {index} is now {actual!r}, not "
                f"expecting_name={expecting_name!r} — re-read and retry"
            )
        edit.edit_sublimit(
            program,
            index,
            name=sublimit_name,
            amount=parse_money(amount) if amount else None,
            applies_to=applies_to,
            notes=notes,
        )

    return _write(programs, name, f"edited sublimit {index}", do)


def _sublimit_remove(
    programs: Programs, name: str, index: int, expecting_name: str
) -> dict[str, Any]:
    """Remove the sublimit at `index`, guarded by its name."""

    def do(program: Program) -> None:
        _guard_index(program.sublimits, index, "sublimit")
        actual = program.sublimits[index].name
        if actual != expecting_name:
            raise ValueError(
                f"sublimit {index} is now {actual!r}, not "
                f"expecting_name={expecting_name!r} — re-read and retry"
            )
        edit.remove_sublimit(program, index)

    return _write(programs, name, f"removed sublimit {index}", do)


def _layer_remove(programs: Programs, name: str, layer_id: str) -> dict[str, Any]:
    """Remove a layer from the stack. Expect line-gap until you restack."""

    def do(program: Program) -> None:
        edit.remove_layer(program, layer_id)

    return _write(programs, name, f"removed layer {layer_id}", do)


def _layer_lines(
    programs: Programs, name: str, layer_id: str, line_ids: list[str]
) -> dict[str, Any]:
    """Set which coverage lines a layer spans — how wide its block is."""

    def do(program: Program) -> None:
        edit.set_applies_to(program, layer_id, line_ids)

    return _write(programs, name, f"set {layer_id} to cover {', '.join(line_ids)}", do)


def _layer_follows(
    programs: Programs, name: str, layer_id: str, follows: bool
) -> dict[str, Any]:
    """Mark a layer as following the underlying: its attachment becomes
    derived state, reseating itself on the highest underlying top. This is
    how a shared umbrella sits over columns with differing limits."""

    def do(program: Program) -> None:
        edit.set_follows_underlying(program, layer_id, follows)

    return _write(programs, name, f"set followsUnderlying={follows} on {layer_id}", do)


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

    @server.tool()
    async def line_add(
        name: str, line_name: str, abbr: str | None = None, group: str | None = None
    ) -> dict[str, Any]:
        """Add a coverage line — a new column in the tower. It will report
        line-empty until a layer covers it."""
        return _line_add(programs, name, line_name, abbr, group)

    @server.tool()
    async def line_edit(
        name: str,
        line_id: str,
        line_name: str | None = None,
        abbr: str | None = None,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Rename a line (the id follows the name, cascading everywhere it is
        referenced), set its abbreviation, or set its coverage group."""
        return _line_edit(programs, name, line_id, line_name, abbr, group)

    @server.tool()
    async def line_move(name: str, line_id: str, delta: int) -> dict[str, Any]:
        """Move a line left (-1) or right (+1). Array order is column order."""
        return _line_move(programs, name, line_id, delta)

    @server.tool()
    async def line_remove(name: str, line_id: str) -> dict[str, Any]:
        """Remove a line. Anything left covering nothing — layers, retentions,
        sublimits — goes with it."""
        return _line_remove(programs, name, line_id)

    @server.tool()
    async def retention_add(
        name: str,
        applies_to: list[str],
        type: str,
        amount: str,
        aggregate: str | None = None,
        vehicle: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """What the insured pays below the tower. `type` is deductible, sir,
        or captive; a captive needs a named vehicle. Money in dollars
        ('500k', '$1,000,000')."""
        return _retention_add(
            programs, name, applies_to, type, amount, aggregate, vehicle, notes
        )

    @server.tool()
    async def retention_edit(
        name: str,
        index: int,
        expecting_lines: list[str],
        applies_to: list[str] | None = None,
        type: str | None = None,
        amount: str | None = None,
        aggregate: str | None = None,
        vehicle: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Edit the retention at `index` from a program_read. Retentions have
        no ids, so `expecting_lines` guards against the list having shifted:
        it must match what the read reported."""
        return _retention_edit(
            programs,
            name,
            index,
            expecting_lines,
            applies_to,
            type,
            amount,
            aggregate,
            vehicle,
            notes,
        )

    @server.tool()
    async def retention_remove(
        name: str, index: int, expecting_lines: list[str]
    ) -> dict[str, Any]:
        """Remove the retention at `index`, guarded by the lines it covers."""
        return _retention_remove(programs, name, index, expecting_lines)

    @server.tool()
    async def sublimit_add(
        name: str,
        sublimit_name: str,
        amount: str,
        applies_to: list[str],
        notes: str | None = None,
    ) -> dict[str, Any]:
        """A named cap inside the tower — flood, quake, a per-location limit."""
        return _sublimit_add(programs, name, sublimit_name, amount, applies_to, notes)

    @server.tool()
    async def sublimit_edit(
        name: str,
        index: int,
        expecting_name: str,
        sublimit_name: str | None = None,
        amount: str | None = None,
        applies_to: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Edit the sublimit at `index`, guarded by the name the read reported."""
        return _sublimit_edit(
            programs, name, index, expecting_name, sublimit_name, amount, applies_to, notes
        )

    @server.tool()
    async def sublimit_remove(
        name: str, index: int, expecting_name: str
    ) -> dict[str, Any]:
        """Remove the sublimit at `index`, guarded by its name."""
        return _sublimit_remove(programs, name, index, expecting_name)

    @server.tool()
    async def layer_remove(name: str, layer_id: str) -> dict[str, Any]:
        """Remove a layer from the stack. Expect line-gap until you restack."""
        return _layer_remove(programs, name, layer_id)

    @server.tool()
    async def layer_lines(name: str, layer_id: str, line_ids: list[str]) -> dict[str, Any]:
        """Set which coverage lines a layer spans — how wide its block is."""
        return _layer_lines(programs, name, layer_id, line_ids)

    @server.tool()
    async def layer_follows(name: str, layer_id: str, follows: bool) -> dict[str, Any]:
        """Mark a layer as following the underlying: its attachment becomes
        derived state, reseating itself on the highest underlying top. This
        is how a shared umbrella sits over columns with differing limits."""
        return _layer_follows(programs, name, layer_id, follows)


def serve(roots: list[Path] | None = None) -> None:
    build_server(roots).run()  # stdio transport is the SDK's default
