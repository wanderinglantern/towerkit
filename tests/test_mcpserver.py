"""towerctl mcp — the design surface.

Tools are exercised two ways: directly against the module-level `_program_*`
helpers (fast, precise), and over a real in-memory protocol round-trip
(proves the wiring the SDK does for us).

Why the helpers and not `server._tool_manager.get_tool(name).fn`: the
installed SDK (mcp==2.0.0) does expose that accessor, and it does return a
callable — but the tools are registered `async def` (required: the SDK runs
a *sync* tool body on a worker thread via `anyio.to_thread.run_sync`, and an
`async def` wrapper is what keeps this server's state on the event-loop
thread that owns it). `.fn` on an async tool is a coroutine *function*:
calling it from a plain sync `def test_...` hands back an unawaited
coroutine object, not a result dict. So each tool wrapper is a one-line
`async def` shim over a module-level `_program_list` / `_program_read` /
`_program_view` / `_program_check` helper, and the unit tests below call
those helpers directly. The protocol round-trip test at the bottom is the
proof that registration and wiring work end to end.

The SDK is mcp==2.0.0: the server class is `MCPServer` at
`mcp.server.mcpserver.MCPServer`, and the in-process client is
`mcp.client.Client`, which takes the server instance. pyproject sets
asyncio_mode = "auto", so bare `async def test_` functions are collected
without a marker.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from towerkit.mcpserver import (
    Programs,
    _program_check,
    _program_list,
    _program_read,
    _program_revert_write,
    _program_view,
    _restack,
    _write,
    build_server,
)
from towerkit.model import load_program

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


@pytest.fixture()
def roots(tmp_path):
    programs = tmp_path / "programs"
    (programs / "private").mkdir(parents=True)
    shutil.copy(SAMPLE, programs / "atomic-2026.json")
    shutil.copy(SAMPLE, programs / "private" / "secret-2026.json")
    return [programs]


class TestResolution:
    def test_list_finds_programs_in_roots_and_private(self, roots) -> None:
        programs = Programs(roots)
        names = {p["name"] for p in _program_list(programs)["programs"]}
        assert names == {"atomic-2026", "private/secret-2026"}

    def test_read_reports_structure_and_a_sha(self, roots) -> None:
        programs = Programs(roots)
        out = _program_read(programs, "atomic-2026")
        assert out["insured"]
        assert [ln["id"] for ln in out["lines"]]
        assert out["layers"][0]["id"]
        assert len(out["sha"]) == 64
        assert out["retentions"][0]["index"] == 0

    def test_escaping_the_roots_is_refused(self, roots) -> None:
        programs = Programs(roots)
        for bad in ("../outside", "/etc/passwd", "private/../../escape"):
            with pytest.raises(ValueError, match="outside"):
                _program_read(programs, bad)

    def test_unknown_name_names_what_is_available(self, roots) -> None:
        programs = Programs(roots)
        with pytest.raises(ValueError, match="atomic-2026"):
            _program_read(programs, "nope")


class TestSeeing:
    def test_view_returns_an_ascii_tower_without_escape_codes(self, roots) -> None:
        programs = Programs(roots)
        art = _program_view(programs, "atomic-2026")["view"]
        assert "\x1b[" not in art
        assert art.count("\n") > 5

    def test_check_reports_diagnostics_with_refs(self, roots) -> None:
        programs = Programs(roots)
        out = _program_check(programs, "atomic-2026")
        assert out["errors"] == []
        assert any("placed" in w["message"] for w in out["warnings"])
        assert out["warnings"][0]["ref"]


class TestWriteCycle:
    def test_a_write_needs_a_read_first(self, roots) -> None:
        programs = Programs(roots)
        with pytest.raises(ValueError, match="read"):
            _restack(programs, "atomic-2026")

    def test_list_does_not_arm_the_write_guard(self, roots) -> None:
        """program_list returns summaries, never the structure a write is
        reasoned from — merely listing must not license a write to a file
        this session has never actually read."""
        programs = Programs(roots)
        _program_list(programs)
        with pytest.raises(ValueError, match="read"):
            _restack(programs, "atomic-2026")

    def test_a_write_refuses_when_the_file_moved(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed on disk"):
            _restack(programs, "atomic-2026")
        assert path.read_text("utf-8") == before.decode() + " "  # untouched by us

    def test_a_write_lands_canonically_and_re_arms_the_guard(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _restack(programs, "atomic-2026")
        assert out["write_ref"].startswith("TKW-")
        path = roots[0] / "atomic-2026.json"
        from towerkit.model import dumps_program

        assert path.read_text("utf-8") == dumps_program(load_program(path))
        _restack(programs, "atomic-2026")  # guard re-armed; no raise

    def test_a_model_invalid_write_is_refused_and_the_file_is_untouched(
        self, roots
    ) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()

        def poison(program):
            program.layers[0].applies_to = []  # min_length=1 → ValidationError

        with pytest.raises(ValueError, match="refused"):
            _write(programs, "atomic-2026", "poison", poison)
        assert path.read_bytes() == before


class TestRevert:
    def test_revert_restores_byte_identically(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        ref = _restack(programs, "atomic-2026")["write_ref"]
        _program_revert_write(programs, ref)
        assert path.read_bytes() == before

    def test_revert_finds_snapshots_for_a_nested_program(self, roots) -> None:
        """snapshot() writes beside the program file, so a nested name like
        private/secret-2026 puts its snapshots under
        <root>/private/.mcp-snapshots/, not <root>/.mcp-snapshots/. The
        revert lookup must find them there too."""
        programs = Programs(roots)
        _program_read(programs, "private/secret-2026")
        path = roots[0] / "private" / "secret-2026.json"
        before = path.read_bytes()
        ref = _restack(programs, "private/secret-2026")["write_ref"]
        assert (roots[0] / "private" / ".mcp-snapshots" / f"{ref}.meta.json").exists()
        _program_revert_write(programs, ref)
        assert path.read_bytes() == before

    def test_revert_refuses_after_a_later_edit(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        ref = _restack(programs, "atomic-2026")["write_ref"]
        path = roots[0] / "atomic-2026.json"
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed since"):
            _program_revert_write(programs, ref)

    def test_snapshots_share_the_directory_with_bookkit_and_prune_only_their_own(
        self, roots
    ) -> None:
        programs = Programs(roots)
        snapdir = roots[0] / ".mcp-snapshots"
        snapdir.mkdir(exist_ok=True)
        (snapdir / "MCP-abc.json").write_text("{}")  # bookkit's, not ours
        _program_read(programs, "atomic-2026")
        for _ in range(3):
            _restack(programs, "atomic-2026")
        assert (snapdir / "MCP-abc.json").exists()
        assert len(list(snapdir.glob("TKW-*.meta.json"))) == 3


async def test_tools_are_registered_and_callable_over_the_protocol(roots) -> None:
    from mcp.client import Client

    server = build_server(roots)
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {
            "program_list",
            "program_read",
            "program_view",
            "program_check",
            "restack",
            "program_revert_write",
        } <= names
        result = await client.call_tool("program_read", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["lines"]
        result = await client.call_tool("restack", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["write_ref"].startswith("TKW-")
