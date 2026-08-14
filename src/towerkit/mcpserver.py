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
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .model import Program, load_program
from .validate import Diagnostic, validate_program

DEFAULT_ROOT = Path("programs")


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


def serve(roots: list[Path] | None = None) -> None:
    build_server(roots).run()  # stdio transport is the SDK's default
