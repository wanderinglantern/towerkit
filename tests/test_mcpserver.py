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
import sys
from pathlib import Path

import pytest

from towerkit.mcpserver import (
    Programs,
    _layer_follows,
    _layer_lines,
    _layer_remove,
    _line_add,
    _line_edit,
    _line_move,
    _line_remove,
    _program_check,
    _program_clone_renewal,
    _program_create,
    _program_list,
    _program_read,
    _program_revert_write,
    _program_view,
    _restack,
    _retention_add,
    _retention_edit,
    _retention_remove,
    _sublimit_add,
    _sublimit_edit,
    _sublimit_remove,
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


class TestDesignTools:
    def test_line_add_lands_and_reports_that_it_is_empty(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _line_add(programs, "atomic-2026", "Cyber", abbr="CYB")
        assert out["added"] == "cyber"
        assert any(d["code"] == "line-empty" for d in out["errors"])
        read = _program_read(programs, "atomic-2026")
        assert "cyber" in [ln["id"] for ln in read["lines"]]

    def test_line_remove_takes_stranded_layers_with_it(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        target = read["lines"][0]["id"]
        solo = [ly["id"] for ly in read["layers"] if ly["appliesTo"] == [target]]
        _line_remove(programs, "atomic-2026", target)
        after = _program_read(programs, "atomic-2026")
        assert target not in [ln["id"] for ln in after["lines"]]
        assert not [ly for ly in after["layers"] if ly["id"] in solo]

    def test_line_edit_with_unknown_line_id_and_only_abbr_refuses_cleanly(
        self, roots
    ) -> None:
        """Regression: an unknown line_id with ONLY abbr set used to look the
        line up via next() with no default, raising a bare StopIteration that
        escaped _write's except clause instead of the standard refused
        message. rename_line/set_line_group guard with KeyError, but their
        checks only ran when line_name or group were also supplied — the
        abbr-only path had no guard at all."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="refused"):
            _line_edit(programs, "atomic-2026", "ghost-line", abbr="XX")
        assert path.read_bytes() == before

    def test_line_edit_renames_and_line_move_reorders(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _line_edit(programs, "atomic-2026", line_id, line_name="General Liability X")
        new_id = out["line_id"]
        after = _program_read(programs, "atomic-2026")
        assert new_id in [ln["id"] for ln in after["lines"]]
        if len(after["lines"]) > 1:
            before_order = [ln["id"] for ln in after["lines"]]
            _line_move(programs, "atomic-2026", new_id, 1)
            moved = _program_read(programs, "atomic-2026")
            moved_order = [ln["id"] for ln in moved["lines"]]
            assert moved_order != before_order or before_order[-1] == new_id

    def test_retention_add_parses_human_money(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _retention_add(programs, "atomic-2026", [line_id], "sir", "500k", aggregate="2m")
        assert out["index"] == len(read["retentions"])
        after = _program_read(programs, "atomic-2026")
        assert after["retentions"][out["index"]]["amount"] == 500_000
        assert after["retentions"][out["index"]]["aggregate"] == 2_000_000

    def test_retention_edit_guards_against_a_shifted_index(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        assert read["retentions"], "fixture must carry a retention"
        with pytest.raises(ValueError, match="expecting"):
            _retention_edit(
                programs, "atomic-2026", 0, amount="1m", expecting_lines=["ghost-line"]
            )

    def test_retention_edit_applies_when_the_guard_matches(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        current = read["retentions"][0]
        _retention_edit(
            programs, "atomic-2026", 0, amount="750k", expecting_lines=current["appliesTo"]
        )
        after = _program_read(programs, "atomic-2026")
        assert after["retentions"][0]["amount"] == 750_000

    def test_retention_remove_guards_and_then_removes(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        current = read["retentions"][0]
        count = len(read["retentions"])
        with pytest.raises(ValueError, match="expecting"):
            _retention_remove(programs, "atomic-2026", 0, expecting_lines=["ghost-line"])
        _retention_remove(
            programs, "atomic-2026", 0, expecting_lines=current["appliesTo"]
        )
        after = _program_read(programs, "atomic-2026")
        assert len(after["retentions"]) == count - 1

    def test_layer_lines_rejects_an_unknown_line_and_writes_nothing(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="refused"):
            _layer_lines(programs, "atomic-2026", read["layers"][0]["id"], ["ghost"])
        assert path.read_bytes() == before

    def test_layer_follows_heals_the_attachment(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        top = max(read["layers"], key=lambda ly: ly["attach"])
        _layer_follows(programs, "atomic-2026", top["id"], True)
        after = _program_read(programs, "atomic-2026")
        healed = next(ly for ly in after["layers"] if ly["id"] == top["id"])
        assert healed["followsUnderlying"] is True

    def test_layer_remove_reports_line_gap(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        layer_id = read["layers"][0]["id"]
        out = _layer_remove(programs, "atomic-2026", layer_id)
        after = _program_read(programs, "atomic-2026")
        assert layer_id not in [ly["id"] for ly in after["layers"]]
        assert isinstance(out["errors"], list)

    def test_sublimit_round_trip(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _sublimit_add(programs, "atomic-2026", "Flood", "5m", [line_id])
        index = out["index"]
        _sublimit_edit(
            programs, "atomic-2026", index, amount="10m", expecting_name="Flood"
        )
        after = _program_read(programs, "atomic-2026")
        assert after["sublimits"][index]["amount"] == 10_000_000
        _sublimit_remove(programs, "atomic-2026", index, expecting_name="Flood")
        assert len(_program_read(programs, "atomic-2026")["sublimits"]) == index

    def test_sublimit_edit_guards_against_a_shifted_index(self, roots) -> None:
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _sublimit_add(programs, "atomic-2026", "Flood", "5m", [line_id])
        with pytest.raises(ValueError, match="expecting"):
            _sublimit_edit(
                programs, "atomic-2026", out["index"], amount="1m", expecting_name="Not Flood"
            )


class TestCreation:
    def test_create_writes_a_canonical_file_and_reports_it_is_empty(self, roots) -> None:
        programs = Programs(roots)
        out = _program_create(
            programs,
            "prospect-2027",
            insured="Prospect Co",
            program="Casualty",
            placement="proposed",
            period_from="1/1/27",
            period_to="1/1/28",
            lines=["General Liability", "Auto Liability"],
        )
        path = roots[0] / "prospect-2027.json"
        assert path.exists()
        from towerkit.model import dumps_program

        assert path.read_text("utf-8") == dumps_program(load_program(path))
        assert {d["code"] for d in out["errors"]} == {"line-empty"}
        assert load_program(path).period.start.isoformat() == "2027-01-01"

    def test_create_refuses_an_existing_file(self, roots) -> None:
        programs = Programs(roots)
        with pytest.raises(ValueError, match="already exists"):
            _program_create(
                programs,
                "atomic-2026",
                insured="X",
                program="Y",
                placement="bound",
                period_from="1/1/26",
                period_to="1/1/27",
                lines=["GL"],
            )

    def test_create_refuses_outside_the_roots(self, roots) -> None:
        programs = Programs(roots)
        with pytest.raises(ValueError, match="outside"):
            _program_create(
                programs,
                "../escape",
                insured="X",
                program="Y",
                placement="bound",
                period_from="1/1/26",
                period_to="1/1/27",
                lines=["GL"],
            )

    def test_create_rejects_an_unreadable_date(self, roots) -> None:
        programs = Programs(roots)
        with pytest.raises(ValueError, match="date"):
            _program_create(
                programs,
                "bad-dates",
                insured="X",
                program="Y",
                placement="bound",
                period_from="whenever",
                period_to="1/1/27",
                lines=["GL"],
            )
        assert not (roots[0] / "bad-dates.json").exists()

    def test_clone_as_renewal_bumps_the_period_and_proposes(self, roots) -> None:
        programs = Programs(roots)
        _program_clone_renewal(programs, "atomic-2026", "atomic-2027")
        source = load_program(roots[0] / "atomic-2026.json")
        clone = load_program(roots[0] / "atomic-2027.json")
        assert clone.period.start.year == source.period.start.year + 1
        assert clone.placement.value == "proposed"
        assert len(clone.layers) == len(source.layers)

    def test_a_created_program_is_immediately_writable(self, roots) -> None:
        """Creation notes the sha, so the caller does not have to read back
        a file it just wrote before editing it."""
        programs = Programs(roots)
        _program_create(
            programs,
            "fresh-2027",
            insured="Fresh",
            program="Property",
            placement="proposed",
            period_from="1/1/27",
            period_to="1/1/28",
            lines=["Property"],
        )
        _line_add(programs, "fresh-2027", "Cyber")  # no raise


class TestResyncHook:
    def test_no_hook_configured_is_reported_not_hidden(self, roots, monkeypatch) -> None:
        monkeypatch.delenv("TOWERKIT_POST_WRITE_CMD", raising=False)
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        assert _restack(programs, "atomic-2026")["resync"] == "not configured"

    def test_the_hook_runs_with_the_path_substituted(
        self, roots, tmp_path, monkeypatch
    ) -> None:
        marker = tmp_path / "hooked.txt"
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD",
            f"{sys.executable} -c "
            f"\"import sys,pathlib; pathlib.Path(r'{marker}').write_text(sys.argv[1])\" {{path}}",
        )
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _restack(programs, "atomic-2026")
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("atomic-2026.json")

    def test_a_failing_hook_never_undoes_the_write(self, roots, monkeypatch) -> None:
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD", f'{sys.executable} -c "raise SystemExit(3)"'
        )
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        out = _restack(programs, "atomic-2026")
        assert out["resync"].startswith("failed")
        assert (roots[0] / ".mcp-snapshots" / f"{out['write_ref']}.meta.json").exists()
        assert load_program(path)  # and the file is still loadable

    def test_a_malformed_template_is_reported_not_raised(self, roots, monkeypatch) -> None:
        """shlex.split raises ValueError on an unbalanced quote. That must not
        escape run_hook: the write already landed by the time the hook runs,
        so a typo'd env var must be reported in `resync`, not raised out of
        the tool call."""
        monkeypatch.setenv("TOWERKIT_POST_WRITE_CMD", 'bad "quote')
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        out = _restack(programs, "atomic-2026")  # must not raise
        assert out["resync"].startswith("failed")
        assert (roots[0] / ".mcp-snapshots" / f"{out['write_ref']}.meta.json").exists()
        assert load_program(path)  # the write still landed

    def test_creation_runs_the_hook_too(self, roots, tmp_path, monkeypatch) -> None:
        marker = tmp_path / "created.txt"
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD",
            f"{sys.executable} -c "
            f"\"import sys,pathlib; pathlib.Path(r'{marker}').write_text(sys.argv[1])\" {{path}}",
        )
        programs = Programs(roots)
        out = _program_create(
            programs,
            "hooked-2027",
            insured="H",
            program="P",
            placement="proposed",
            period_from="1/1/27",
            period_to="1/1/28",
            lines=["GL"],
        )
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("hooked-2027.json")

    def test_clone_renewal_runs_the_hook_too(self, roots, tmp_path, monkeypatch) -> None:
        marker = tmp_path / "cloned.txt"
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD",
            f"{sys.executable} -c "
            f"\"import sys,pathlib; pathlib.Path(r'{marker}').write_text(sys.argv[1])\" {{path}}",
        )
        programs = Programs(roots)
        out = _program_clone_renewal(programs, "atomic-2026", "atomic-2027")
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("atomic-2027.json")


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
            "line_add",
            "line_edit",
            "line_move",
            "line_remove",
            "retention_add",
            "retention_edit",
            "retention_remove",
            "sublimit_add",
            "sublimit_edit",
            "sublimit_remove",
            "layer_remove",
            "layer_lines",
            "layer_follows",
            "program_create",
            "program_clone_renewal",
        } <= names
        result = await client.call_tool("program_read", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["lines"]
        result = await client.call_tool(
            "line_add", {"name": "atomic-2026", "line_name": "Cyber Protocol"}
        )
        assert not result.is_error
        assert result.structured_content["added"] == "cyber-protocol"
        result = await client.call_tool("restack", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["write_ref"].startswith("TKW-")
