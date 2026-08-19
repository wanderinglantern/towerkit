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
import shlex
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ValidationError

from . import edit, mcpsurface
from .atomicio import atomic_write_bytes, atomic_write_text
from .dates import parse_flexible_date
from .edit import Refusal
from .model import Period, Placement, Program, dumps_program, load_program, loads_program
from .money import parse_money
from .validate import Diagnostic, validate_program

# Re-exported: a client that imports towerkit as a library (bookkit does)
# catches the exception object and branches on `.code`, and this is the module
# it already imports.
__all__ = ["Refusal", "build_server", "serve"]

DEFAULT_ROOT = Path("programs")
# 100, raised from 20 on 2026-08-19: a session that edits a tower field by
# field burns through twenty writes in an afternoon, and a pruned pre-image is
# an undo that silently is not there.
#
# NOT zero, ever: the prune below slices `images[:-SNAPSHOT_KEEP]`, and
# `[:-0]` is `[:0]` — the empty list — so a cap of 0 would keep every snapshot
# forever instead of keeping none. The constant now reads as tunable, so the
# trap is worth saying out loud.
SNAPSHOT_KEEP = 100
_SNAPDIR = ".mcp-snapshots"
_PREFIX = "TKW-"
HOOK_ENV = "TOWERKIT_POST_WRITE_CMD"
HOOK_TIMEOUT = 30


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_hook(path: Path) -> str:
    """Best-effort notification that a program file changed.

    towerkit does not know what is downstream — bookkit sets this to
    `bookctl sync --path {path}` so its projection cache follows. It NEVER
    fails the write: by the time this runs the file on disk is already
    correct, and rolling a good write back over a hook failure would be a
    worse lie than a stale cache. So: no shell (the command comes from an
    env var — split it with shlex, never build a shell string), a timeout,
    and every exception the subprocess can throw is caught and reported in
    the outcome instead of raised.

    The template is split BEFORE `{path}` is substituted into each part, on
    purpose: splitting after substitution would break a path containing
    spaces ('/Users/x/My Programs/y.json') into multiple argv entries, and
    would let a crafted path inject extra arguments."""
    template = os.environ.get(HOOK_ENV)
    if not template:
        return "not configured"
    try:
        command = [part.replace("{path}", str(path)) for part in shlex.split(template)]
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=HOOK_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return f"failed: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return f"failed: exit {done.returncode}" + (f" — {detail[-1]}" if detail else "")
    return "ok"


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
            raise Refusal(
                "outside_roots",
                f"{name!r} resolves outside the program roots "
                f"({', '.join(str(r) for r in self.roots)})",
            )
        for root in self.roots:
            if not self._inside((root / f"{name}.json").resolve()):
                raise Refusal(
                    "outside_roots",
                    f"{name!r} resolves outside the program roots "
                    f"({', '.join(str(r) for r in self.roots)})",
                )
        raise Refusal(
            "no_such_program", f"no program {name!r} — available: {', '.join(self.names())}"
        )

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
    # backup=False: the snapshot directory is its own history, and must not
    # accumulate `.bak` sidecars of its own snapshots.
    atomic_write_bytes(snapdir / f"{ref}.json", pre_image, backup=False)
    atomic_write_text(
        snapdir / f"{ref}.meta.json",
        json.dumps({"path": str(path), "post_sha256": file_sha256(path)}),
        backup=False,
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
        raise Refusal(
            "no_snapshot",
            f"no snapshot for {ref} — it may have been pruned "
            f"(the last {SNAPSHOT_KEEP} writes are kept)",
        )
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if str(path) != meta["path"]:
        raise Refusal(
            "no_such_target", f"{ref} was a write to {meta['path']}, not this file"
        )
    if file_sha256(path) != meta["post_sha256"]:
        raise Refusal(
            "stale_sha",
            f"the file has changed since {ref} wrote it — a newer edit (the "
            f"towerkit editor, bookkit, or a later write) would be lost; revert "
            f"newer writes first",
        )
    pre_image = image.read_bytes()
    try:
        loads_program(pre_image.decode("utf-8"))  # refuse a pre-image that cannot load
    except (
        ValidationError,
        ValueError,
        KeyError,
        IndexError,
        RuntimeError,
        UnicodeDecodeError,
    ) as exc:
        # `no_snapshot` is the least wrong of the published codes and it is
        # not right: the snapshot EXISTS, it is unusable. The caller's move is
        # the same either way — revert a different ref — so the message does
        # the work rather than a tenth code being invented for one raise site.
        raise Refusal(
            "no_snapshot",
            f"snapshot {ref} is not a loadable program — refusing to restore: {exc}",
        ) from exc
    _atomic_write_bytes(path, pre_image)


def _atomic_write(path: Path, text: str) -> None:
    """Delegates to `towerkit.atomicio` — one definition of a safe write,
    shared with the TUI and the CLI."""
    atomic_write_text(path, text)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Bytes-oriented sibling, used by `restore()`. The pre-image is
    already-canonical UTF-8 JSON read straight off disk; a text round-trip
    risks re-encoding it, so this writes the exact bytes."""
    atomic_write_bytes(path, data)


def _write(
    programs: Programs,
    name: str,
    summary: str,
    mutate: Callable[[Program], None],
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """One design write: drift check → load → mutate → hard-validate →
    canonical dump → atomic replace → snapshot → re-arm the guard.

    Two tiers of validation, on purpose. HARD: the mutation must produce a
    program that still models and still round-trips through the canonical
    dump — a file towerkit cannot load is never written. SOFT: semantic
    diagnostics do not block. A tower under construction is invalid by
    construction (a new line is line-empty until it carries a layer, a stack
    being built passes through line-gap), so errors come back in the result
    and the caller keeps building.

    `expect_sha`, supplied, is AUTHORITATIVE and the in-session map is not
    consulted at all — which is the whole cross-process fix. bookkit runs
    towerkit as a library in its own process with its own `Programs.seen`, so
    it can hand over a sha it read itself but can never arm this session's
    map; consulting the map on that branch is what deadlocked every write it
    made. `expect_sha=""` is not "omitted": only `None` is, because silently
    downgrading an asserted token to session authority is the same silent
    authority the field exists to remove.

    Note that a successful `expect_sha` write still calls `programs.note()`,
    so the caller's NEXT write needs no token. That is the difference between
    breaking the deadlock once and breaking it permanently.
    """
    path = programs.resolve(name)
    actual = file_sha256(path)
    if expect_sha is not None:
        token = expect_sha.strip().lower()
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            # bad_value, NOT stale_sha: telling a client to go and re-read
            # when it actually sent garbage is how a retry loop starts.
            raise Refusal(
                "bad_value",
                f"expect_sha must be a 64-character sha256 hex digest as returned by "
                f"program_read — got {expect_sha!r}",
            )
        if token != actual:
            raise Refusal(
                "stale_sha",
                f"{name} does not match expect_sha — the file changed since that read "
                f"(the towerkit editor, bookkit, or another tool) — call "
                f"program_read({name!r}) and retry with the sha it returns",
            )
    else:
        known = programs.seen.get(str(path))
        if known is None:
            raise Refusal(
                "not_read",
                f"{name} has not been read in this session — call program_read({name!r}) "
                f"first so there is a baseline to compare against, or pass expect_sha "
                f"from a read done elsewhere",
            )
        if actual != known:
            raise Refusal(
                "stale_sha",
                f"{name} changed on disk since it was read (the towerkit editor, "
                f"bookkit, or another tool wrote it) — call program_read({name!r}) and "
                f"retry with what it returns; if you read it through another process, "
                f"pass that read's sha as expect_sha",
            )

    pre_image = path.read_bytes()
    program = load_program(path)
    try:
        mutate(program)
        edit.heal_follows(program)
        text = dumps_program(program)
        loads_program(text)  # proves the file we are about to write is loadable
    except Refusal:
        # FIRST, and the order is mandatory: `Refusal` IS a `ValueError`, so
        # the clause below would swallow a coded refusal raised inside
        # `mutate` and re-emit it double-tagged as
        # "[guard_refused] refused — nothing written: [stale_value] …".
        raise
    except (ValidationError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        raise Refusal("guard_refused", f"refused — nothing written: {exc}") from exc

    _atomic_write(path, text)
    ref = write_ref()
    snapshot(path, ref, pre_image)
    programs.note(path)
    diags = validate_program(program)
    return {
        "wrote": name,
        "summary": summary,
        "write_ref": ref,
        "resync": run_hook(path),
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


def _readable(model: BaseModel) -> dict[str, Any]:
    """Every field a model declares, keyed the way the FILE keys it.

    DERIVED, not listed. The hand-written projection this replaces returned
    7 of 17 `Layer` fields: `notes` was writable on four tools and readable on
    none, and the three detail fields, the named limits, the states, the policy
    number and the per-layer period were invisible to every reader. A read that
    enumerates fields goes lossy the next time the model grows, silently,
    which is precisely what happened.

    Nothing is dropped for being empty. `program_to_jsonable` omits a None —
    that is the canonical FILE format, where an absent key means the default —
    but a reader deciding what to write next has to be able to tell "not set"
    from "I was not told", so every key is present and a None comes back null.
    """
    out: dict[str, Any] = {}
    for name, info in type(model).model_fields.items():
        out[info.alias or name] = _readable_value(getattr(model, name))
    return out


def _readable_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _readable(value)
    if isinstance(value, list):
        rows = [_readable_value(item) for item in value]
        if value and isinstance(value[0], BaseModel) and "id" not in type(value[0]).model_fields:
            # A row with no id is addressed by its position, so the position
            # is part of what the read reports. Derived from the absence of an
            # `id` field rather than listed, so a new id-less collection
            # carries its index too.
            return [{"index": i, **row} for i, row in enumerate(rows)]
        return rows
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return value


def _program_read(programs: Programs, name: str) -> dict[str, Any]:
    """The full structure of one program. Read before you write.

    Rows with no id — retentions, sublimits, participants, named limits —
    carry the `index` the edit tools address them by."""
    path = programs.resolve(name)
    program = load_program(path)
    sha = programs.note(path)
    return {"name": name, "sha": sha, **_readable(program)}


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


def _program_restack(
    programs: Programs, name: str, expect_sha: str | None = None
) -> dict[str, Any]:
    """Reseat every layer on top of what its lines already carry — one call
    heals gaps and overlaps after limit edits."""
    return _write(programs, name, "restacked the tower", edit.restack, expect_sha)


def _guard_index(items: list[Any], index: int, what: str) -> None:
    if not 0 <= index < len(items):
        raise Refusal(
            "no_such_target",
            f"no {what} at index {index} — there are {len(items)}; re-read and retry",
        )


def _line_add(
    programs: Programs,
    name: str,
    line_name: str,
    abbr: str | None = None,
    group: str | None = None,
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Add a coverage line — a new column in the tower. It will report
    line-empty until a layer covers it."""
    added: list[str] = []

    def do(program: Program) -> None:
        added.append(edit.add_line(program, line_name, abbr, group).id)

    out = _write(programs, name, f"added line {line_name!r}", do, expect_sha)
    return {**out, "added": added[0]}


def _line_edit(
    programs: Programs,
    name: str,
    line_id: str,
    line_name: str | None = None,
    abbr: str | None = None,
    group: str | None = None,
    expect_sha: str | None = None,
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

    out = _write(programs, name, f"edited line {line_id}", do, expect_sha)
    return {**out, "line_id": result[0]}


def _line_move(
    programs: Programs, name: str, line_id: str, delta: int, expect_sha: str | None = None
) -> dict[str, Any]:
    """Move a line left (-1) or right (+1). Array order is column order.

    `delta` must be exactly -1 or +1: `edit.move_line` is a swap of two
    array elements, which only means "move" when the swap is between
    neighbours. A larger delta would swap the line with something two or
    more columns away and silently produce the wrong order, so it is
    refused here rather than passed through."""
    if abs(delta) != 1:
        raise Refusal("bad_value", f"delta must be -1 or +1 (a single step), got {delta}")

    def do(program: Program) -> None:
        edit.move_line(program, line_id, delta)

    return _write(programs, name, f"moved line {line_id} by {delta}", do, expect_sha)


def _line_remove(
    programs: Programs, name: str, line_id: str, expect_sha: str | None = None
) -> dict[str, Any]:
    """Remove a line. Anything left covering nothing — layers, retentions,
    sublimits — goes with it."""

    def do(program: Program) -> None:
        edit.remove_line(program, line_id)

    return _write(programs, name, f"removed line {line_id}", do, expect_sha)


def _retention_add(
    programs: Programs,
    name: str,
    applies_to: list[str],
    type: str,
    amount: str,
    aggregate: str | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
    expect_sha: str | None = None,
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

    out = _write(programs, name, f"added a {type} retention", do, expect_sha)
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
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Edit the retention at `index` from a program_read. Retentions have no
    ids, so `expecting_lines` guards against the list having shifted: it
    must match what the read reported."""

    def do(program: Program) -> None:
        _guard_index(program.retentions, index, "retention")
        actual = list(program.retentions[index].applies_to)
        if actual != list(expecting_lines):
            raise Refusal(
                "stale_value",
                f"retention {index} now applies to {actual}, not "
                f"expecting_lines={list(expecting_lines)} — call program_read({name!r}) "
                f"and retry against what it returns",
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

    return _write(programs, name, f"edited retention {index}", do, expect_sha)


def _retention_remove(
    programs: Programs,
    name: str,
    index: int,
    expecting_lines: list[str],
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Remove the retention at `index`, guarded by the lines it covers."""

    def do(program: Program) -> None:
        _guard_index(program.retentions, index, "retention")
        actual = list(program.retentions[index].applies_to)
        if actual != list(expecting_lines):
            raise Refusal(
                "stale_value",
                f"retention {index} now applies to {actual}, not "
                f"expecting_lines={list(expecting_lines)} — call program_read({name!r}) "
                f"and retry against what it returns",
            )
        edit.remove_retention(program, index)

    return _write(programs, name, f"removed retention {index}", do, expect_sha)


def _sublimit_add(
    programs: Programs,
    name: str,
    sublimit_name: str,
    amount: str,
    applies_to: list[str],
    notes: str | None = None,
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """A named cap inside the tower — flood, quake, a per-location limit."""
    index: list[int] = []

    def do(program: Program) -> None:
        edit.add_sublimit(program, sublimit_name, parse_money(amount), applies_to, notes)
        index.append(len(program.sublimits) - 1)

    out = _write(programs, name, f"added sublimit {sublimit_name!r}", do, expect_sha)
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
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Edit the sublimit at `index`, guarded by the name the read reported."""

    def do(program: Program) -> None:
        _guard_index(program.sublimits, index, "sublimit")
        actual = program.sublimits[index].name
        if actual != expecting_name:
            raise Refusal(
                "stale_value",
                f"sublimit {index} is now {actual!r}, not "
                f"expecting_name={expecting_name!r} — call program_read({name!r}) and "
                f"retry against what it returns",
            )
        edit.edit_sublimit(
            program,
            index,
            name=sublimit_name,
            amount=parse_money(amount) if amount else None,
            applies_to=applies_to,
            notes=notes,
        )

    return _write(programs, name, f"edited sublimit {index}", do, expect_sha)


def _sublimit_remove(
    programs: Programs,
    name: str,
    index: int,
    expecting_name: str,
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Remove the sublimit at `index`, guarded by its name."""

    def do(program: Program) -> None:
        _guard_index(program.sublimits, index, "sublimit")
        actual = program.sublimits[index].name
        if actual != expecting_name:
            raise Refusal(
                "stale_value",
                f"sublimit {index} is now {actual!r}, not "
                f"expecting_name={expecting_name!r} — call program_read({name!r}) and "
                f"retry against what it returns",
            )
        edit.remove_sublimit(program, index)

    return _write(programs, name, f"removed sublimit {index}", do, expect_sha)


def _layer_remove(
    programs: Programs, name: str, layer_id: str, expect_sha: str | None = None
) -> dict[str, Any]:
    """Remove a layer from the stack. Expect line-gap until you call
    program_restack."""

    def do(program: Program) -> None:
        edit.remove_layer(program, layer_id)

    return _write(programs, name, f"removed layer {layer_id}", do, expect_sha)


def _layer_lines(
    programs: Programs,
    name: str,
    layer_id: str,
    line_ids: list[str],
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Set which coverage lines a layer spans — how wide its block is."""

    def do(program: Program) -> None:
        edit.set_applies_to(program, layer_id, line_ids)

    return _write(
        programs, name, f"set {layer_id} to cover {', '.join(line_ids)}", do, expect_sha
    )


def _layer_follows(
    programs: Programs,
    name: str,
    layer_id: str,
    follows: bool,
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Mark a layer as following the underlying: its attachment becomes
    derived state, reseating itself on the highest underlying top. This is
    how a shared umbrella sits over columns with differing limits."""

    def do(program: Program) -> None:
        edit.set_follows_underlying(program, layer_id, follows)

    return _write(
        programs, name, f"set follows-underlying={follows} on {layer_id}", do, expect_sha
    )


# --- compare-and-set over the derived surface ---------------------------------
#
# Which LIST a row lives in. Addressing, not a field table: the collection
# cannot be spelled from the kind name without inventing a pluraliser, and a
# wrong guess would write to the wrong list. Participants and named limits
# hang off a LAYER, which is why `mcpsurface.TARGET` says they take a target
# as well as an index.

_COLLECTION: dict[str, str] = {
    "retention": "retentions",
    "sublimit": "sublimits",
    "participant": "participants",
    "named_limit": "named_limits",
}


def _by_id(rows: list[Any], target: str | None, what: str) -> Any:
    row = next((row for row in rows if row.id == target), None)
    if row is None:
        raise Refusal(
            "no_such_target",
            f"no {what} {target!r} — {what}s are "
            f"{', '.join(repr(r.id) for r in rows) or '(none)'}",
        )
    return row


def _addressed(program: Program, kind: str, target: str | None, index: int | None) -> Any:
    """The row a write lands on, or `no_such_target`.

    Every refusal here names what IS there, because a caller that guessed an
    id has nothing to guess again from."""
    needs = mcpsurface.TARGET.get(kind)
    if needs is None:
        raise Refusal(
            "no_such_target", f"no kind {kind!r}; kinds are {', '.join(mcpsurface.KINDS)}"
        )
    if "target" in needs and not target:
        raise Refusal("bad_value", f"a {kind} write needs target; see describe({kind!r})")
    if "index" in needs and index is None:
        raise Refusal("bad_value", f"a {kind} write needs index; see describe({kind!r})")
    if kind == "program":
        return program
    if kind == "line":
        return _by_id(program.lines, target, "line")
    if kind == "layer":
        return _by_id(program.layers, target, "layer")
    owner = _by_id(program.layers, target, "layer") if "target" in needs else program
    rows = getattr(owner, _COLLECTION[kind])
    _guard_index(rows, int(index or 0), kind.replace("_", " "))
    return rows[int(index or 0)]


def _current(entity: Any, entry: mcpsurface.Entry) -> Any:
    value: Any = entity
    for step in entry.path:
        value = getattr(value, step)
    return value


def _row_guard(kind: str, entity: Any, who: str, expecting_row: object) -> None:
    """The `expecting_lines` / `expecting_name` idea under one name.

    An index is a position in a list the caller read a moment ago, and nothing
    about the position says the list has not moved. It is REQUIRED for a kind
    that has one: an optional guard on an id-less row is not a guard."""
    field = mcpsurface.ROW_GUARD[kind]
    if field is None:
        return
    entry = mcpsurface.resolve(kind, field)
    current = _current(entity, entry)
    if expecting_row is None:
        raise Refusal(
            "bad_value",
            f"{kind} rows have no id, so expecting_row is required — pass the "
            f"{field} program_read reported for this one "
            f"({mcpsurface.expecting_literal(entry, current)})",
        )
    try:
        expected = mcpsurface.parse_expecting(entry, expecting_row)
    except mcpsurface.BadValue as exc:
        raise Refusal("bad_value", f"expecting_row: {exc}") from exc
    if not mcpsurface.compare_values(entry, expected, current):
        raise Refusal(
            "stale_value",
            f"{who} {field} is {mcpsurface.render_value(entry, current)}, not the "
            f"{mcpsurface.render_value(entry, expected)} in expecting_row — the list "
            f"moved under the index; call program_read and retry against it",
        )


def _program_edit_field(
    programs: Programs,
    name: str,
    kind: str,
    field: str,
    value: object,
    expecting: object,
    target: str | None = None,
    index: int | None = None,
    expecting_row: object = None,
    expect_sha: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set one field, over the whole derived surface.

    The check ORDER is part of the contract, because it decides which code
    comes back: resolve the program, gate on the sha, address the row, check
    the row guard, refuse a denied field, resolve the field, materialise a
    defaultable parent, parse `expecting`, parse `value`, compare, then let
    `edit.py`'s guards have it. Answering "you may not write that" to a caller
    who actually misspelled a field name sends them somewhere useless.
    """
    notes: list[str] = []
    advisories: list[Diagnostic] = []
    who = mcpsurface.subject(kind, target, index)

    def do(program: Program) -> None:
        entity = _addressed(program, kind, target, index)
        _row_guard(kind, entity, who, expecting_row)

        reason = mcpsurface.denied_reason(kind, field)
        if reason is not None:
            raise Refusal("denied_field", f"{kind}.{field} is not settable here. {reason}")
        try:
            entry = mcpsurface.resolve(kind, field)
        except KeyError as exc:
            raise Refusal(
                "no_such_target", f"{exc.args[0]} — call describe({kind!r}) for the field set"
            ) from exc

        if entry.container is not None and getattr(entity, entry.path[0]) is None:
            if not mcpsurface.container_defaultable(entry):
                raise Refusal(
                    "no_such_target", mcpsurface.missing_container_message(entry, who)
                )
            # Materialised from defaults, and the response says which ones were
            # written: the caller is now answerable for values they never sent.
            # `expecting` therefore compares against the DEFAULT, because an
            # absent container is exactly what its defaults mean.
            container, note = mcpsurface.create_container(entry)
            setattr(entity, entry.path[0], container)
            notes.append(note)
        current = _current(entity, entry)

        try:
            expected = mcpsurface.parse_expecting(entry, expecting)
        except mcpsurface.BadValue as exc:
            raise Refusal("bad_value", f"expecting: {exc}") from exc
        try:
            parsed = mcpsurface.parse_value(entry, value)
        except mcpsurface.BadValue as exc:
            raise Refusal("bad_value", str(exc)) from exc
        if not mcpsurface.compare_values(entry, expected, current):
            raise Refusal(
                "stale_value", mcpsurface.mismatch_message(entry, who, current, expected)
            )
        advisories.extend(mcpsurface.apply(program, entry, target, index, parsed))

    out = _write(programs, name, f"set {field} on {who}", do, expect_sha)
    # Advisories are what the write did NOT do (currency converts no figure).
    # They are not validator diagnostics — re-reading the file will not
    # reproduce them — so they never merge into `warnings`.
    return {**out, "advisories": _diag(advisories), "note": notes[0] if notes else None}


def _program_revert_write(programs: Programs, write_ref: str) -> dict[str, Any]:
    """Undo one write by restoring its pre-image — only while the file still
    holds exactly what that write produced.

    `snapshot()` writes beside the program file (`path.parent / _SNAPDIR`),
    so for a nested name like `private/secret-2026` the snapshot lands in
    `<root>/private/.mcp-snapshots/`, not `<root>/.mcp-snapshots/`. Walk each
    root for a `.mcp-snapshots` dir at any depth rather than assuming one at
    the top.

    `.mcp-snapshots/` is shared with bookkit's MCP, which prefixes its own
    write refs `MCP-`. Refuse anything not carrying this server's `TKW-`
    prefix before ever globbing for it — otherwise a bookkit ref handed to
    this tool would restore bookkit's pre-image through towerkit's write
    path."""
    if not write_ref.startswith(_PREFIX):
        raise Refusal(
            "bad_value",
            f"{write_ref!r} is not a towerkit write ref (expected a {_PREFIX!r} "
            f"prefix) — this tool only reverts its own writes",
        )
    for root in programs.roots:
        for meta_file in root.rglob(f"{_SNAPDIR}/{write_ref}.meta.json"):
            target = Path(json.loads(meta_file.read_text())["path"])
            restore(target, write_ref)
            programs.note(target)
            return {
                "reverted": write_ref,
                "file": str(target),
                "resync": run_hook(target),
            }
    raise Refusal("no_snapshot", f"no snapshot for {write_ref} under the program roots")


def _parse_date(value: str, field: str) -> date:
    parsed = parse_flexible_date(value)
    if parsed is None:
        raise Refusal("bad_value", f"can't read {value!r} as a date for {field}")
    return parsed


def _program_create(
    programs: Programs,
    name: str,
    insured: str,
    program: str,
    placement: str,
    period_from: str,
    period_to: str,
    lines: list[str],
) -> dict[str, Any]:
    """Start a program from nothing: an insured, a period, and its coverage
    lines. This does not go through `_write` — a brand-new file has by
    definition never been read this session, and `_write` refuses exactly
    that — so it does its own write, mirroring `_write`'s invariants by
    hand: resolve through the sandbox, refuse an existing file, hard-gate on
    a canonical dump that re-loads, write atomically, then note() the sha so
    the caller can edit the file it just created without a redundant read.
    It will report line-empty for every line until layers cover them — that
    is expected, keep building."""
    path = programs.resolve(name, must_exist=False)
    if path.exists():
        raise Refusal("exists", f"{name} already exists — edit it, or pick another name")
    fresh = Program(
        insured=insured,
        program=program,
        placement=Placement(placement),
        period=Period(
            start=_parse_date(period_from, "period_from"),
            end=_parse_date(period_to, "period_to"),
        ),
        lines=[],
    )
    for line_name in lines:
        edit.add_line(fresh, line_name)
    text = dumps_program(fresh)
    loads_program(text)  # proves the file we are about to write is loadable
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, text)
    programs.note(path)
    diags = validate_program(fresh)
    return {
        "created": name,
        "file": str(path),
        "lines": [ln.id for ln in fresh.lines],
        "resync": run_hook(path),
        "errors": _diag(diags.errors),
        "warnings": _diag(diags.warnings),
    }


def _program_clone_renewal(programs: Programs, source: str, dest: str) -> dict[str, Any]:
    """Copy a program forward a year as a proposed renewal — the most common
    starting point for next year's design. Same do-it-by-hand write as
    `_program_create`, for the same reason: `dest` has never been read."""
    source_path = programs.resolve(source)
    dest_path = programs.resolve(dest, must_exist=False)
    if dest_path.exists():
        raise Refusal("exists", f"{dest} already exists — pick another name")
    clone = load_program(source_path).clone_as_renewal()
    text = dumps_program(clone)
    loads_program(text)  # proves the file we are about to write is loadable
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest_path, text)
    programs.note(dest_path)
    diags = validate_program(clone)
    return {
        "created": dest,
        "from": source,
        "file": str(dest_path),
        "period": _period(clone),
        "resync": run_hook(dest_path),
        "errors": _diag(diags.errors),
        "warnings": _diag(diags.warnings),
    }


def build_server(roots: list[Path] | None = None) -> MCPServer:
    programs = Programs(roots)
    server = MCPServer(
        name="towerkit",
        instructions=(
            "Design insurance towers in towerkit program files: coverage lines, "
            "retentions, sublimits, and the shape of the layer stack. Read a "
            "program (program_read) before writing to it — writes refuse against "
            "a file this session has not read, and against one that changed since. "
            "Every write also takes an optional expect_sha: pass the sha from a read "
            "done in another process and it is used instead of this session's, which "
            "is how a caller that holds towerkit as a library writes without reading "
            "through these tools first. "
            "describe() publishes what is settable and how values are written; it is "
            "the only place that list exists, so ask it rather than guessing. "
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

    @server.tool()
    async def describe(kind: str | None = None) -> dict[str, Any]:
        """What program_edit_field can set, and how each value is written.

        Kinds, their targets, every settable field with its type, the fields
        that are refused and why, the rules for money, dates, enums, booleans,
        lists and shares, and the error codes a refusal carries. Pass a kind
        to narrow it. This is the ONLY place that list is published — no tool
        description repeats it, because a second copy is how the first one
        went stale."""
        return mcpsurface.describe(kind)


def _register_write_tools(server: MCPServer, programs: Programs) -> None:
    @server.tool()
    async def program_restack(name: str, expect_sha: str | None = None) -> dict[str, Any]:
        """Reseat every layer on top of what its lines already carry —
        one call heals gaps and overlaps after limit edits."""
        return _program_restack(programs, name, expect_sha)

    @server.tool()
    async def program_edit_field(
        name: str,
        kind: str,
        field: str,
        value: str | int | bool | list[str] | None,
        expecting: str | int | bool | list[str] | None,
        target: str | None = None,
        index: int | None = None,
        expecting_row: str | int | bool | list[str] | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """Set one field on one thing, only if it still holds what you expect.

        Call describe() for the kinds, the fields each one has and how their
        values are written; this description deliberately names none of them.

        `expecting` is required and is compared after both sides are parsed,
        so any accepted spelling of a value matches the stored one. Send JSON
        null to expect a field that is unset. A mismatch comes back naming the
        current value in a form you can paste straight back into `expecting`.

        `target` is the line or layer id — the LAYER id for a row that hangs
        off one. `index` addresses a row that has no id, and `expecting_row`
        is what that row's guard field held when you read it, which is what
        proves the list has not moved under the index."""
        return _program_edit_field(
            programs,
            name,
            kind,
            field,
            value,
            expecting,
            target,
            index,
            expecting_row,
            expect_sha,
        )

    @server.tool()
    async def program_revert_write(write_ref: str) -> dict[str, Any]:
        """Undo one write by restoring its pre-image — only while the file
        still holds exactly what that write produced."""
        return _program_revert_write(programs, write_ref)

    @server.tool()
    async def line_add(
        name: str,
        line_name: str,
        abbr: str | None = None,
        group: str | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """Add a coverage line — a new column in the tower. It will report
        line-empty until a layer covers it."""
        return _line_add(programs, name, line_name, abbr, group, expect_sha)

    @server.tool()
    async def line_edit(
        name: str,
        line_id: str,
        line_name: str | None = None,
        abbr: str | None = None,
        group: str | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """Rename a line (the id follows the name, cascading everywhere it is
        referenced), set its abbreviation, or set its coverage group."""
        return _line_edit(programs, name, line_id, line_name, abbr, group, expect_sha)

    @server.tool()
    async def line_move(
        name: str, line_id: str, delta: int, expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Move a line left (-1) or right (+1). Array order is column order.
        `delta` must be -1 or +1 — a single step; anything else is refused."""
        return _line_move(programs, name, line_id, delta, expect_sha)

    @server.tool()
    async def line_remove(
        name: str, line_id: str, expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Remove a line. Anything left covering nothing — layers, retentions,
        sublimits — goes with it."""
        return _line_remove(programs, name, line_id, expect_sha)

    @server.tool()
    async def retention_add(
        name: str,
        applies_to: list[str],
        type: str,
        amount: str,
        aggregate: str | None = None,
        vehicle: str | None = None,
        notes: str | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """What the insured pays below the tower. `type` is deductible, sir,
        or captive; a captive needs a named vehicle. Money in dollars
        ('500k', '$1,000,000')."""
        return _retention_add(
            programs, name, applies_to, type, amount, aggregate, vehicle, notes, expect_sha
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
        expect_sha: str | None = None,
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
            expect_sha,
        )

    @server.tool()
    async def retention_remove(
        name: str, index: int, expecting_lines: list[str], expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Remove the retention at `index`, guarded by the lines it covers."""
        return _retention_remove(programs, name, index, expecting_lines, expect_sha)

    @server.tool()
    async def sublimit_add(
        name: str,
        sublimit_name: str,
        amount: str,
        applies_to: list[str],
        notes: str | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """A named cap inside the tower — flood, quake, a per-location limit."""
        return _sublimit_add(
            programs, name, sublimit_name, amount, applies_to, notes, expect_sha
        )

    @server.tool()
    async def sublimit_edit(
        name: str,
        index: int,
        expecting_name: str,
        sublimit_name: str | None = None,
        amount: str | None = None,
        applies_to: list[str] | None = None,
        notes: str | None = None,
        expect_sha: str | None = None,
    ) -> dict[str, Any]:
        """Edit the sublimit at `index`, guarded by the name the read reported."""
        return _sublimit_edit(
            programs,
            name,
            index,
            expecting_name,
            sublimit_name,
            amount,
            applies_to,
            notes,
            expect_sha,
        )

    @server.tool()
    async def sublimit_remove(
        name: str, index: int, expecting_name: str, expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Remove the sublimit at `index`, guarded by its name."""
        return _sublimit_remove(programs, name, index, expecting_name, expect_sha)

    @server.tool()
    async def layer_remove(
        name: str, layer_id: str, expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Remove a layer from the stack. Expect line-gap until you call
        program_restack."""
        return _layer_remove(programs, name, layer_id, expect_sha)

    @server.tool()
    async def layer_lines(
        name: str, layer_id: str, line_ids: list[str], expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Set which coverage lines a layer spans — how wide its block is."""
        return _layer_lines(programs, name, layer_id, line_ids, expect_sha)

    @server.tool()
    async def layer_follows(
        name: str, layer_id: str, follows: bool, expect_sha: str | None = None
    ) -> dict[str, Any]:
        """Mark a layer as following the underlying: its attachment becomes
        derived state, reseating itself on the highest underlying top. This
        is how a shared umbrella sits over columns with differing limits."""
        return _layer_follows(programs, name, layer_id, follows, expect_sha)

    @server.tool()
    async def program_create(
        name: str,
        insured: str,
        program: str,
        placement: str,
        period_from: str,
        period_to: str,
        lines: list[str],
    ) -> dict[str, Any]:
        """Start a program from nothing: an insured, a period, and its
        coverage lines. It will report line-empty for every line until
        layers cover them — that is expected, keep building. `placement` is
        bound or proposed. Numeric dates are month/day/year."""
        return _program_create(
            programs, name, insured, program, placement, period_from, period_to, lines
        )

    @server.tool()
    async def program_clone_renewal(source: str, dest: str) -> dict[str, Any]:
        """Copy a program forward a year as a proposed renewal — the most
        common starting point for next year's design."""
        return _program_clone_renewal(programs, source, dest)


def serve(roots: list[Path] | None = None) -> None:
    build_server(roots).run()  # stdio transport is the SDK's default
