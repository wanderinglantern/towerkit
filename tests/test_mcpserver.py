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

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from towerkit import edit, mcpparity, mcpsurface
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
    _program_edit_field,
    _program_list,
    _program_read,
    _program_restack,
    _program_revert_write,
    _program_view,
    _retention_add,
    _retention_edit,
    _retention_remove,
    _sublimit_add,
    _sublimit_edit,
    _sublimit_remove,
    _write,
    build_server,
    file_sha256,
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
            _program_restack(programs, "atomic-2026")

    def test_list_does_not_arm_the_write_guard(self, roots) -> None:
        """program_list returns summaries, never the structure a write is
        reasoned from — merely listing must not license a write to a file
        this session has never actually read."""
        programs = Programs(roots)
        _program_list(programs)
        with pytest.raises(ValueError, match="read"):
            _program_restack(programs, "atomic-2026")

    def test_a_write_refuses_when_the_file_moved(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed on disk"):
            _program_restack(programs, "atomic-2026")
        assert path.read_text("utf-8") == before.decode() + " "  # untouched by us

    def test_a_write_lands_canonically_and_re_arms_the_guard(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _program_restack(programs, "atomic-2026")
        assert out["write_ref"].startswith("TKW-")
        path = roots[0] / "atomic-2026.json"
        from towerkit.model import dumps_program

        assert path.read_text("utf-8") == dumps_program(load_program(path))
        _program_restack(programs, "atomic-2026")  # guard re-armed; no raise

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

    def test_a_bak_sidecar_beside_a_program_does_not_confuse_list_or_write(
        self, roots
    ) -> None:
        """dump_program's `.bak` sidecar (see towerkit.atomicio) is invisible
        to `*.json` globs by construction, but this pins it down end to end
        on the MCP path: list must not surface it as a phantom program, and
        a write→revert round trip must still work with it sitting there."""
        from towerkit.atomicio import backup_path

        path = roots[0] / "atomic-2026.json"
        bak = backup_path(path)
        bak.write_bytes(path.read_bytes())

        programs = Programs(roots)
        names = {p["name"] for p in _program_list(programs)["programs"]}
        assert names == {"atomic-2026", "private/secret-2026"}

        _program_read(programs, "atomic-2026")
        before = path.read_bytes()
        ref = _program_restack(programs, "atomic-2026")["write_ref"]
        _program_revert_write(programs, ref)
        assert path.read_bytes() == before


class TestRevert:
    def test_revert_restores_byte_identically(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        ref = _program_restack(programs, "atomic-2026")["write_ref"]
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
        ref = _program_restack(programs, "private/secret-2026")["write_ref"]
        assert (roots[0] / "private" / ".mcp-snapshots" / f"{ref}.meta.json").exists()
        _program_revert_write(programs, ref)
        assert path.read_bytes() == before

    def test_revert_refuses_after_a_later_edit(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        ref = _program_restack(programs, "atomic-2026")["write_ref"]
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
            _program_restack(programs, "atomic-2026")
        assert (snapdir / "MCP-abc.json").exists()
        assert len(list(snapdir.glob("TKW-*.meta.json"))) == 3

    def test_revert_runs_the_post_write_hook_and_reports_resync(
        self, roots, monkeypatch
    ) -> None:
        """Same as any other successful write: a downstream projection cache
        must be told the file changed again, and the caller must be able to
        see whether that happened."""
        monkeypatch.delenv("TOWERKIT_POST_WRITE_CMD", raising=False)
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        ref = _program_restack(programs, "atomic-2026")["write_ref"]
        out = _program_revert_write(programs, ref)
        assert out["resync"] == "not configured"

    def test_revert_refuses_a_ref_that_is_not_this_servers_own(self, roots) -> None:
        """.mcp-snapshots/ is shared with bookkit, which prefixes its refs
        MCP-. Handed one of those, this tool must refuse rather than restore
        a pre-image belonging to another tool's writes."""
        programs = Programs(roots)
        with pytest.raises(ValueError, match="TKW-"):
            _program_revert_write(programs, "MCP-20260101T000000-abcd")

    def test_revert_refuses_a_corrupt_pre_image_and_leaves_the_file_alone(
        self, roots
    ) -> None:
        """A pre-image truncated by a disk-full write (Important 2 in the
        review) must never be laid down over a valid program — the sha
        guard alone would let it through, since only the *post*-write sha
        is checked."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        ref = _program_restack(programs, "atomic-2026")["write_ref"]
        before = path.read_bytes()
        snap = roots[0] / ".mcp-snapshots" / f"{ref}.json"
        snap.write_bytes(b"not a program at all")
        with pytest.raises(ValueError, match="not a loadable program"):
            _program_revert_write(programs, ref)
        assert path.read_bytes() == before


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

    def test_line_move_rejects_a_delta_other_than_one(self, roots) -> None:
        """edit.move_line swaps two array elements, which only equals a
        'move' when |delta| == 1. A larger delta would silently swap the
        line with something two-or-more columns away instead of moving it
        one step, so the MCP edge refuses it rather than passing it
        through."""
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        line_id = read["lines"][0]["id"]
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        for bad in (0, 2, -2):
            with pytest.raises(ValueError, match="delta"):
                _line_move(programs, "atomic-2026", line_id, bad)
        assert path.read_bytes() == before

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
        assert _program_restack(programs, "atomic-2026")["resync"] == "not configured"

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
        out = _program_restack(programs, "atomic-2026")
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("atomic-2026.json")

    def test_a_failing_hook_never_undoes_the_write(self, roots, monkeypatch) -> None:
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD", f'{sys.executable} -c "raise SystemExit(3)"'
        )
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        out = _program_restack(programs, "atomic-2026")
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
        out = _program_restack(programs, "atomic-2026")  # must not raise
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
            "program_restack",
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
            "program_edit_field",
            "describe",
        } <= names
        result = await client.call_tool("program_read", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["lines"]
        result = await client.call_tool(
            "line_add", {"name": "atomic-2026", "line_name": "Cyber Protocol"}
        )
        assert not result.is_error
        assert result.structured_content["added"] == "cyber-protocol"
        result = await client.call_tool("program_restack", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["write_ref"].startswith("TKW-")
        result = await client.call_tool("describe", {"kind": "layer"})
        assert not result.is_error
        assert "attach" in result.structured_content["kinds"]["layer"]["fields"]

        # A refusal over the wire. The SDK gives an error result exactly one
        # channel — a flat text string — so the code has to travel inside the
        # message, and this is the assertion that it survives the trip.
        result = await client.call_tool(
            "program_edit_field",
            {
                "name": "atomic-2026",
                "kind": "layer",
                "field": "statutory",
                "value": True,
                "expecting": False,
                "target": "xs-1",
            },
        )
        assert result.is_error
        assert "[denied_field]" in result.content[0].text


# --- the cross-process deadlock ----------------------------------------------


class TestCrossProcessSha:
    """The regression `docs/bugs/2026-08-14-mcp-occ-cross-server-stale-sha.md`
    specified and nobody wrote.

    Two `Programs` instances are a faithful model of the two-process bug:
    `Programs.seen` is a plain per-INSTANCE dict, and the bug's root cause is
    that bookkit runs towerkit as a library and therefore consults a DIFFERENT
    map — one no towerkit write ever arms. No subprocess is needed to
    reproduce that; a second instance is the same thing.
    """

    def test_a_stale_second_reader_can_write_with_expect_sha(self, roots) -> None:
        first, second = Programs(roots), Programs(roots)
        path = roots[0] / "atomic-2026.json"
        _program_read(second, "atomic-2026")  # the second reader's own baseline
        _program_read(first, "atomic-2026")

        _line_add(first, "atomic-2026", "Marine")  # the file moves under `second`

        # (i) the staleness is REAL. Without this the rest passes trivially if
        # restack ever becomes a no-op — the "passed for the wrong reason" trap.
        assert second.seen[str(path)] != file_sha256(path)

        # (ii) the deadlock, reproduced.
        with pytest.raises(ValueError, match="changed on disk") as caught:
            _line_add(second, "atomic-2026", "Cyber")
        assert caught.value.code == "stale_sha"

        # (iii) a WRONG token is still refused. Without this case the whole
        # test is invisible to a mutation that makes the branch accept
        # anything at all.
        with pytest.raises(ValueError) as caught:
            _line_add(second, "atomic-2026", "Cyber", expect_sha="0" * 64)
        assert caught.value.code == "stale_sha"

        # (iv) the fix: a sha the caller read for itself is authoritative.
        out = _line_add(second, "atomic-2026", "Cyber", expect_sha=file_sha256(path))
        assert out["write_ref"].startswith("TKW-")
        # Read the FILE, not `_program_read`: reading through the tool would
        # arm the session map itself and defeat (v) below.
        assert "cyber" in [ln.id for ln in load_program(path).lines]

        # (v) and it ARMED the session map, so the next write needs no token.
        # This is the difference between breaking the deadlock once and
        # breaking it permanently.
        _line_add(second, "atomic-2026", "Quake")

        # (vi) OCC was not globally disabled — `first` is the stale one now.
        with pytest.raises(ValueError, match="changed on disk"):
            _program_restack(first, "atomic-2026")

    def test_expect_sha_does_not_need_a_prior_read_in_this_session(self, roots) -> None:
        """The literal bookkit case, which the test above does not cover: it
        has `second` read first. A caller holding towerkit as a library reads
        the file itself and never touches these tools, so `not_read` must not
        fire on the expect_sha branch at all."""
        fresh = Programs(roots)
        path = roots[0] / "atomic-2026.json"
        assert fresh.seen == {}, "nothing has been read through this instance"

        out = _line_add(fresh, "atomic-2026", "Cyber", expect_sha=file_sha256(path))

        assert out["write_ref"].startswith("TKW-")

    def test_a_malformed_expect_sha_is_bad_value_not_stale_sha(self, roots) -> None:
        """Telling a client to go and re-read when it actually sent garbage is
        how a retry loop starts. The refusal has to say which mistake it was."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        for bad in ("", "not-a-sha", "0" * 63, "z" * 64):
            with pytest.raises(ValueError) as caught:
                _program_restack(programs, "atomic-2026", expect_sha=bad)
            assert caught.value.code == "bad_value", bad
            assert "program_read" in str(caught.value)

    def test_an_empty_expect_sha_is_not_the_same_as_omitting_it(self, roots) -> None:
        """Only `None` is "omitted". Silently downgrading an asserted token to
        session authority is the silent authority the field exists to remove,
        so `''` is refused even though the session map would have allowed the
        write."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")  # the map WOULD permit this write
        with pytest.raises(ValueError) as caught:
            _program_restack(programs, "atomic-2026", expect_sha="")
        assert caught.value.code == "bad_value"

    def test_a_sha_is_taken_case_insensitively_and_stripped(self, roots) -> None:
        programs = Programs(roots)
        path = roots[0] / "atomic-2026.json"
        _program_restack(programs, "atomic-2026", expect_sha=f"  {file_sha256(path).upper()}\n")


# --- the read, derived --------------------------------------------------------


def _advertised(cls) -> set[str]:
    """The keys the FILE uses: the alias where a field has one."""
    return {info.alias or name for name, info in cls.model_fields.items()}


@pytest.fixture()
def furnished(roots):
    """atomic-2026 with the collections the sample leaves empty — a named
    limit, a layer period, saved render settings — so every model in the tree
    is actually present to be read back."""
    from towerkit.model import NamedLimit, Period, RenderSettings, dump_program

    path = roots[0] / "atomic-2026.json"
    program = load_program(path)
    program.render = RenderSettings(theme="themes/marsh.json")
    layer = program.layers[0]
    layer.named_limits.append(NamedLimit(name="Each Accident", amount=1_000_000))
    layer.period = Period(start=program.period.start, end=program.period.end)
    dump_program(program, path)
    return roots


class TestDerivedRead:
    def test_program_read_returns_every_model_field(self, furnished) -> None:
        """DERIVED, so it cannot go lossy again. The hand-written projection
        this replaces returned 7 of 17 `Layer` fields — `notes` was writable
        on four tools and readable on none — and nothing failed when the model
        grew past it.

        Mutation drill (2026-08-19): made `_readable` skip the field named
        'notes'. This failed naming layer.notes. Restored.
        """
        from towerkit.model import (
            Layer,
            Line,
            NamedLimit,
            Participant,
            Period,
            Program,
            RenderSettings,
            Retention,
            Sublimit,
        )

        read = _program_read(Programs(furnished), "atomic-2026")
        layer = next(ly for ly in read["layers"] if ly["namedLimits"])
        missing: list[str] = []
        for label, cls, payload in [
            ("program", Program, read),
            ("period", Period, read["period"]),
            ("render", RenderSettings, read["render"]),
            ("line", Line, read["lines"][0]),
            ("layer", Layer, layer),
            ("layer.period", Period, layer["period"]),
            ("named_limit", NamedLimit, layer["namedLimits"][0]),
            ("participant", Participant, layer["participants"][0]),
            ("retention", Retention, read["retentions"][0]),
            ("sublimit", Sublimit, read["sublimits"][0]),
        ]:
            missing += [f"{label}.{f}" for f in sorted(_advertised(cls) - set(payload))]
        assert not missing, f"program_read has gone lossy again: {missing}"

    def test_every_tool_that_returns_a_period_speaks_the_file_s_own_keys(
        self, roots
    ) -> None:
        """One naming convention, not three. The old read mixed aliases for
        layer fields, the python name for a share, and invented `from`/`to`
        keys for a period — so a caller could not tell what to send back.

        This guarded `program_read` alone until 2026-08-19 while its docstring
        condemned the convention generally, and `_period` was still shipping
        `from`/`to` on `program_list` and `program_clone_renewal`. A caller
        that read a period off the list got two keys no tool accepts: the
        writable fields are `period.start` and `period.end`. The convention is
        extended rather than the docstring narrowed — a read that teaches a
        vocabulary the write refuses is the defect, wherever it happens.

        Mutation drill (2026-08-19): restored `{"from": ..., "to": ...}` in
        `_period`. Failed with `AssertionError: program_list period keys are
        {'from', 'to'}` — and, with the summary left alone, on the clone.
        """
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        assert set(read["period"]) == {"start", "end"}

        listed = next(
            row for row in _program_list(programs)["programs"] if row["name"] == "atomic-2026"
        )
        assert set(listed["period"]) == {"start", "end"}, (
            f"program_list period keys are {set(listed['period'])}"
        )

        cloned = _program_clone_renewal(programs, "atomic-2026", "atomic-2027")
        assert set(cloned["period"]) == {"start", "end"}, (
            f"program_clone_renewal period keys are {set(cloned['period'])}"
        )
        # The clone's own `from` is the SOURCE PROGRAM, not a date — it is the
        # word doing honest work here, which is part of why the period had to
        # stop borrowing it.
        assert cloned["from"] == "atomic-2026"

    def test_one_function_builds_every_period_a_tool_returns(self) -> None:
        """The convention above holds only while `_period` is the sole
        producer. A tool that formatted the dates inline would satisfy no test
        here and would drift on the next rename — the same shape as the
        no-renderer-re-derives-the-pending-predicate ban.

        Mutation drill (2026-08-19): inlined `{"start": program.period.start
        .isoformat(), ...}` into `_program_summary`. Failed with `2 places
        format a period`. Restored.
        """
        source = (SRC / "mcpserver.py").read_text("utf-8")
        places = [
            f"{number}: {text.strip()}"
            for number, text in enumerate(source.splitlines(), start=1)
            if "period.start.isoformat()" in text
        ]
        assert len(places) == 1, (
            f"{len(places)} places format a period; `_period` is the only one "
            f"allowed to:\n  " + "\n  ".join(places)
        )

    def test_rows_with_no_id_carry_the_index_the_edit_tools_address(self, roots) -> None:
        """Derived from the ABSENCE of an id field, not from a list of
        collections, so a new id-less collection carries its index too."""
        read = _program_read(Programs(roots), "atomic-2026")
        assert read["retentions"][0]["index"] == 0
        assert read["sublimits"][0]["index"] == 0
        assert [p["index"] for p in read["layers"][0]["participants"]] == [0]
        assert "index" not in read["lines"][0], "a line has an id; it needs no index"
        assert "index" not in read["layers"][0]


# --- the table cannot come back ----------------------------------------------

SRC = Path(__file__).parent.parent / "src" / "towerkit"


def _distinctive_field_names() -> set[str]:
    """Advertised names no ordinary sentence contains: an alias, a dotted
    path, `share_bps`, `$schema`. Plain lowercase words like 'name', 'notes'
    and 'limit' are excluded — they are English, and matching them would fail
    on prose that is not a table at all."""
    names = _every_advertised_name()
    return {name for name in names if not (name.isalpha() and name.islower())}


def _every_advertised_name() -> set[str]:
    names = {field for fields in mcpsurface.SURFACE.values() for field in fields}
    return names | {key.split(".", 1)[1] for key in mcpsurface.DENIED}


class TestNoSecondFieldTable:
    def test_mcpserver_keeps_no_second_field_table(self) -> None:
        """The whole point of the derivation. `mcpserver.py` may know verbs;
        it may not know field names, because the moment it does, a field
        arrives at the connector only when someone remembers to edit two
        files, and nobody remembered for four features running.

        Mutation drill (2026-08-19): restored one line of the old hand-written
        projection (`"appliesTo": list(ly.applies_to),`) to `_program_read`.
        This failed naming appliesTo. Restored.
        """
        names = _distinctive_field_names()
        assert len(names) >= 10, "the derivation stopped producing names; this passes vacuously"
        source = (SRC / "mcpserver.py").read_text("utf-8")
        # Not a substring search: `program.period.start` is ATTRIBUTE ACCESS on
        # a loaded model, which is the module doing its job, while a key
        # `"period.start"` in a dict literal is the table coming back. The
        # lookbehind is what tells them apart.
        named = sorted(
            name
            for name in names
            if re.search(rf"(?<![\w.$]){re.escape(name)}(?!\w)", source)
        )
        assert not named, (
            f"mcpserver.py names {named} — the field table is coming back; "
            f"derive it from the model, or ask mcpsurface"
        )

    async def test_no_tool_description_reprints_the_derived_field_table(self, roots) -> None:
        """The second table again, in prose — and prose is where the first one
        rotted, because two descriptions drift and neither is checked. A
        description names the RULE ("call describe"); it never lists fields.

        A run, not a count: `retention_add` legitimately says the word `type`
        and the word `vehicle` a sentence apart. Three in a row is a list.
        """
        from mcp.client import Client

        names = _every_advertised_name()
        distinctive = _distinctive_field_names()
        links = {"and", "or"}
        server = build_server(roots)
        async with Client(server) as client:
            offenders = []
            for tool in (await client.list_tools()).tools:
                run, loud, flagged = 0, False, False
                for word in re.findall(r"[A-Za-z_$][\w$]*(?:\.[\w$]+)*", tool.description or ""):
                    if word in names:
                        run += 1
                        # A run of three ordinary English nouns is a sentence:
                        # line_remove really does say "layers, retentions,
                        # sublimits go with it". A table gives itself away by
                        # spelling at least one of them the way the FILE does.
                        loud = loud or word in distinctive
                        flagged = flagged or (run >= 3 and loud)
                    elif word not in links:
                        run, loud = 0, False
                if flagged:
                    offenders.append(tool.name)
        assert not offenders, f"{offenders} reprint the field set — cite describe() instead"

    async def test_every_tool_has_a_docstring(self, roots) -> None:
        """A tool with no description is a tool the model guesses at."""
        from mcp.client import Client

        async with Client(build_server(roots)) as client:
            bare = [
                t.name
                for t in (await client.list_tools()).tools
                if not (t.description or "").strip()
            ]
        assert not bare


# --- compare-and-set ----------------------------------------------------------


class TestRefusalsNameCallableThings:
    """A refusal is only worth the retry it makes possible.

    Both tests here derive the registered set FROM THE BUILT SERVER. A
    hand-listed set is the same second table this whole pass exists to delete:
    it would have said `layer_statutory` was fine, because whoever wrote the
    refusal believed it.
    """

    async def test_no_refusal_names_a_tool_the_server_does_not_register(
        self, roots
    ) -> None:
        """`edit.py` told callers to `layer_statutory(layer_id=..., statutory=
        false)`. The server registers 23 tools and that is not one of them —
        it is Phase 2 — so a client that followed the instruction got "unknown
        tool" and was exactly where it started. `mcpsurface.GUARDS` said the
        opposite in the next file over, which is the two-descriptions drift the
        module was written to prevent.

        What counts as naming a tool: an identifier with an underscore,
        followed by `(`, whose first word is a prefix the server actually uses
        (`layer_`, `program_`, …) or a kind on the write surface. That is wide
        enough to catch `layer_statutory(` and `participant_add(`, and it
        skips keyword arguments (`layer_id=`) and private helpers
        (`_check_lines`), which are not calls a client makes.

        Mutation drill (2026-08-19): put `layer_statutory(layer_id=...,
        statutory=false)` back into `_guard_limit` — the exact text this branch
        shipped with. Failed with `AssertionError: ['layer_statutory'] — a
        refusal naming an unregistered tool refuses the retry too. Name a call
        that works, or say there is none.` Restored.
        """
        from mcp.client import Client

        async with Client(build_server(roots)) as client:
            registered = {tool.name for tool in (await client.list_tools()).tools}
        assert len(registered) > 20, "the tool list came back empty; this would pass blind"
        shapes = (
            {name.split("_")[0] for name in registered}
            | set(mcpsurface.KINDS)
            | {kind.split("_")[0] for kind in mcpsurface.KINDS}
        )

        named = {
            word
            for text in _every_refusal()
            for word in re.findall(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\s*\(", text)
            if word.split("_")[0] in shapes
        }
        assert named, "no refusal named any call at all; the corpus stopped working"
        assert named <= registered, (
            f"{sorted(named - registered)} — a refusal naming an unregistered tool "
            f"refuses the retry too. Name a call that works, or say there is none."
        )

    async def test_the_tool_a_statutory_refusal_used_to_name_is_still_unbuilt(
        self, roots
    ) -> None:
        """The guard above is only meaningful while `layer_statutory` really is
        absent. When Phase 2 registers it, this fails and the refusal text can
        go back to naming it — which is the sequence, not an accident.

        Mutation drill (2026-08-19): registered a stub `layer_statutory` tool.
        Failed with `assert 'layer_statutory' not in {'describe',
        'layer_follows', ...}`. Restored.
        """
        from mcp.client import Client

        async with Client(build_server(roots)) as client:
            registered = {tool.name for tool in (await client.list_tools()).tools}
        assert "layer_statutory" not in registered
        assert "layer_statutory" in mcpparity.DEFERRED_MUTATIONS["set_statutory"]


def _every_refusal() -> list[str]:
    """Every guard refusal a client can be handed, live, plus the same rules in
    the form `describe()` publishes them.

    The live half is raised rather than read out of the source: a message is an
    f-string and only the raised text is the text a client sees.
    """
    program = load_program(SAMPLE)
    edit.set_follows_underlying(program, "xs-1", True)
    edit.heal_follows(program)
    edit.set_statutory(program, "primary-el", True)

    refusals: list[str] = []
    for kind, field, value, target in (
        ("layer", "attach", 1_000_000, "xs-1"),
        ("layer", "attach", 1_000_000, "primary-el"),
        ("layer", "limit", 1_000_000, "primary-el"),
        ("layer", "states", ["NY"], "umbrella"),
    ):
        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, kind, field, value, target)
        refusals.append(str(caught.value))
    assert len(refusals) == 4
    return refusals + list(mcpsurface.GUARDS.values()) + list(mcpsurface.DENIED.values())


def _paste_back(message: str) -> object:
    """The `expecting=` literal from a refusal, decoded the way a client
    decodes it — `json.loads` and nothing else.

    A refusal reaches the model as text, so the only way it can act on
    `pass expecting=X` is to send X as JSON. Extracting X and then EDITING it
    (stripping quotes, say) tests a string no refusal ever printed, and that is
    how `expecting='$2,000,000'` shipped: the tests removed the quotes the
    client had no way to know were not part of the value.
    """
    found = re.search(r'expecting=("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\S+?)(?: |$)', message)
    assert found is not None, message
    return json.loads(found.group(1))


class TestEditField:
    def test_a_field_is_set_when_expecting_matches(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _program_edit_field(
            programs, "atomic-2026", "layer", "premium", "2.2m", 2_100_000, target="xs-1"
        )
        assert out["write_ref"].startswith("TKW-")
        after = _program_read(programs, "atomic-2026")
        assert next(ly for ly in after["layers"] if ly["id"] == "xs-1")["premium"] == 2_200_000

    def test_a_mismatch_names_a_value_that_round_trips(self, roots) -> None:
        """bookkit's 2026-08-18 finding, and it cost real debugging there: a
        refusal that prints raw cents gets a model to write 100x the amount on
        the retry. The literal this refusal offers is fed straight back in.

        `_paste_back` is what makes that claim honest. This test used to
        `.strip("'")` the offered literal before retrying, which is the one
        edit a real client cannot make — it does not know the quotes are
        towerkit's rather than part of the value. Stripped, it proved the
        stripped form works; unstripped, `expecting='$27,000,000'` came back
        as `cannot parse money value: "'$27,000,000'"`.

        Mutation drills (2026-08-19), three of them because the message has
        two independent halves and both are load-bearing:

        - `mismatch_message` printing the bare int: `assert 'attach is
          $27,000,000, ...' in '... attach is 27000000, ...'`.
        - `render_value` multiplying money by 100 (the cents regression this
          test is named for): `... attach is $2,700,000,000, not the
          $200,000,000 you expected ...`.
        - `expecting_literal`'s single-quote branch restored:
          `json.decoder.JSONDecodeError: Expecting value: line 1 column 1
          (char 0)` from `_paste_back`.

        All restored. The first two were invisible to the assertion this test
        shipped with — `"$27,000,000" in message` is satisfied by the offered
        literal alone, so it could not see `render_value` regress at all.
        """
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "attach", "30m", "2m", target="xs-1"
            )
        message = str(caught.value)
        assert caught.value.code == "stale_value"
        # The PRINTED value and the OFFERED literal are two different branches
        # (`render_value` and `expecting_literal`), so the printed one is
        # asserted in place rather than by a bare `"$27,000,000" in message` —
        # which the offered literal satisfies on its own and which therefore
        # could not see `render_value` regress at all.
        assert "attach is $27,000,000, not the $2,000,000 you expected" in message
        assert "2700000000" not in message

        _program_edit_field(
            programs, "atomic-2026", "layer", "attach", "30m",
            _paste_back(message), target="xs-1",
        )
        after = _program_read(programs, "atomic-2026")
        assert next(ly for ly in after["layers"] if ly["id"] == "xs-1")["attach"] == 30_000_000

    def test_no_enum_repr_leaks_into_a_refusal(self, roots) -> None:
        """`<Placement.BOUND: 'bound'>` is not something a client can pass
        back, so a refusal printing it refuses the retry too. The bare value
        it prints instead is fed straight back in.

        Mutation drill (2026-08-19): restored `expecting_literal`'s
        single-quote branch. Failed with `json.decoder.JSONDecodeError:
        Expecting value: line 1 column 1 (char 0)`. Restored. The test could
        not have seen that before, because it re-typed "bound" by hand instead
        of sending what the message offered.
        """
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "program", "placement", "proposed", "proposed"
            )
        message = str(caught.value)
        assert "Placement." not in message and "<" not in message
        assert "'bound'" in message

        # The value the message OFFERS, not the value we happen to know is
        # right: typing "bound" here by hand tested the enum branch of
        # `render_value` and nothing about whether the offer is sendable.
        _program_edit_field(
            programs, "atomic-2026", "program", "placement", "proposed",
            _paste_back(message),
        )
        assert _program_read(programs, "atomic-2026")["placement"] == "proposed"

    def test_a_participant_share_is_written_through_the_generic_setter(
        self, roots
    ) -> None:
        """The capability `describe()` had advertised since the surface shipped
        and that failed on every call.

        `participant.carrier` and `.share_bps` were published as writable with
        no denial reason, and the write died inside `edit.set_field` on
        "'participant' is not addressable by set_field; use its verbs" —
        a private function name, the wrong error code (`guard_refused` for what
        was really an addressing gap), and a pointer to verbs that exist in no
        phase. No test in the suite performed a participant write, which is
        exactly why it shipped.

        Mutation drill (2026-08-19): restored `_entity`'s
        `raise ValueError(f"{kind!r} is not addressable by set_field")`. Failed
        with `towerkit.edit.Refusal: [guard_refused] refused — nothing written:
        'participant' is not addressable by set_field; use its verbs`.
        Restored.
        """
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")

        out = _program_edit_field(
            programs, "atomic-2026", "participant", "share_bps", 4_000, 5_000,
            target="xs-1", index=0, expecting_row="Chubb",
        )

        assert out["wrote"] == "atomic-2026"
        shares = next(
            ly for ly in _program_read(programs, "atomic-2026")["layers"]
            if ly["id"] == "xs-1"
        )["participants"]
        assert [(p["carrier"], p["share_bps"]) for p in shares] == [
            ("Chubb", 4_000),
            ("Zurich", 2_500),
            ("AXA XL", 2_500),
        ]

    def test_a_participant_write_is_guarded_by_the_carrier_it_read(
        self, roots
    ) -> None:
        """An index is a position in a list the caller read a moment ago, and a
        participant list reorders whenever a share is added. The row guard is
        the same one retentions and sublimits have; the refusal names the
        carrier that IS at that index, so the retry has somewhere to go.

        Mutation drill (2026-08-19): made `_row_guard`'s comparison
        unreachable. Failed with `Failed: DID NOT RAISE ValueError`. Restored.
        """
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "participant", "share_bps", 4_000, 5_000,
                target="xs-1", index=0, expecting_row="Swiss Re",
            )
        assert caught.value.code == "stale_value"
        assert "'Chubb'" in str(caught.value)

    def test_an_over_signed_participant_edit_lands_and_says_so(self, roots) -> None:
        """A tower under construction is invalid by construction, so the sum is
        a diagnostic and not a refusal — and `describe()` says which, because a
        caller who expected a veto would otherwise never learn there was none.

        Mutation drill (2026-08-19): added a `("participant", "share_bps")`
        guard to `edit._GUARDS` that refuses. Failed with
        `towerkit.edit.Refusal: [guard_refused] refused — nothing written:
        over-signed` where the write was expected. Restored: the veto belongs
        with the add verb, in Phase 2, and this test is what would notice it
        arriving early.
        """
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")

        out = _program_edit_field(
            programs, "atomic-2026", "participant", "share_bps", 6_000, 5_000,
            target="xs-1", index=0, expecting_row="Chubb",
        )

        assert out["wrote"] == "atomic-2026"
        assert any(d["code"] == "layer-oversigned" for d in out["errors"])
        assert "not vetoed" in mcpsurface.describe("participant")[
            "kinds"
        ]["participant"]["fields"]["share_bps"]["guard"]

    def test_a_denied_field_says_denied_and_says_why(self, roots) -> None:
        """'that field does not exist' and 'you may not write that field' are
        different problems, and a client retries them differently."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "statutory", True, False, target="xs-1"
            )
        assert caught.value.code == "denied_field"
        assert "limit == 0" in str(caught.value)

    def test_an_unknown_field_points_at_describe(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "attatch", 1, 1, target="xs-1"
            )
        assert caught.value.code == "no_such_target"
        assert "describe" in str(caught.value)

    def test_an_unknown_target_names_what_is_there(self, roots) -> None:
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "name", "X", "Y", target="ghost"
            )
        assert caught.value.code == "no_such_target"
        assert "xs-1" in str(caught.value)

    def test_an_edit_py_guard_refuses_through_the_generic_setter(self, roots) -> None:
        """The risk the design names: a generic setter walks past every
        conditional rule unless the rule is a guard inside `edit.py`. It is,
        and this is the proof the generic path inherits it."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "states", ["NY"], [], target="xs-1"
            )
        assert caught.value.code == "guard_refused"
        assert path.read_bytes() == before

    def test_a_bool_is_not_coerced(self, roots) -> None:
        """pydantic would turn "true" into True on assignment, and these five
        decide what a saved chart prints."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "program", "render.showTotals", "true", True
            )
        assert caught.value.code == "bad_value"

    def test_a_missing_render_is_created_from_its_defaults_and_says_so(self, roots) -> None:
        """The caller is now answerable for values they never sent, so the
        response lists every default that was written, not just the one they
        asked for."""
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        assert read["render"] is None
        out = _program_edit_field(
            programs, "atomic-2026", "program", "render.showTotals", False, True
        )
        assert "created render with defaults" in out["note"]
        assert "showPremiums=true" in out["note"]
        after = _program_read(programs, "atomic-2026")
        assert after["render"]["showTotals"] is False
        assert after["render"]["showPremiums"] is True

    def test_a_layer_period_refuses_rather_than_inventing_an_expiry(self, roots) -> None:
        """`Period` needs both dates and Phase 1 has no call that sets both.
        Seeding it from the program period is deliberately NOT done: it would
        write an expiry the caller never supplied into the field the Schedule
        of Insurance reads."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "layer", "period.start", "1/1/26", None, target="xs-1"
            )
        assert caught.value.code == "no_such_target"
        assert "start and end must be set together" in str(caught.value)

    def test_currency_is_written_and_warns_that_no_figure_moved(self, roots) -> None:
        """Silence was the failure mode: the write does far less than it looks
        like it does."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        out = _program_edit_field(programs, "atomic-2026", "program", "currency", "GBP", "USD")
        assert _program_read(programs, "atomic-2026")["currency"] == "GBP"
        assert [d["code"] for d in out["advisories"]] == ["currency-not-converted"]
        # An advisory is a statement about what the write did NOT do. Re-reading
        # the file will not reproduce it, which is why it must never be merged
        # into the validator's warnings.
        assert "currency" not in str(out["warnings"])

    def test_an_id_less_row_needs_its_row_guard_and_honours_it(self, roots) -> None:
        """An index is a position in a list the caller read a moment ago, and
        nothing about the position says the list has not moved. The guard is
        required, because an optional guard on an id-less row is not a guard.
        """
        programs = Programs(roots)
        read = _program_read(programs, "atomic-2026")
        lines = read["retentions"][0]["appliesTo"]

        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs, "atomic-2026", "retention", "amount", "1m", 250_000, index=0
            )
        assert caught.value.code == "bad_value"

        with pytest.raises(ValueError) as caught:
            _program_edit_field(
                programs,
                "atomic-2026",
                "retention",
                "amount",
                "1m",
                250_000,
                index=0,
                expecting_row=["ghost-line"],
            )
        assert caught.value.code == "stale_value"

        _program_edit_field(
            programs,
            "atomic-2026",
            "retention",
            "amount",
            "1m",
            250_000,
            index=0,
            expecting_row=lines,
        )
        assert _program_read(programs, "atomic-2026")["retentions"][0]["amount"] == 1_000_000

    def test_a_line_rename_through_the_generic_setter_cascades_the_id(self, roots) -> None:
        """Denying `line.id` is only coherent if setting `line.name` runs the
        id cascade — a plain assignment would strand every appliesTo on the
        old slug."""
        programs = Programs(roots)
        _program_read(programs, "atomic-2026")
        _program_edit_field(
            programs,
            "atomic-2026",
            "line",
            "name",
            "General Liability X",
            "General Liability",
            target="gl",
        )
        after = _program_read(programs, "atomic-2026")
        assert "general-liability-x" in [ln["id"] for ln in after["lines"]]
        assert not any("gl" in ly["appliesTo"] for ly in after["layers"])

    def test_every_refusal_code_is_one_the_contract_publishes(self, roots) -> None:
        """A code a client cannot look up is a bare ValueError with extra
        punctuation. Each case is pinned to the code it must produce, because
        a set-membership assertion alone passes when every branch returns the
        same code."""
        programs = Programs(roots)
        cases = [
            ("not_read", {"kind": "layer", "field": "premium", "value": "1m",
                          "expecting": 2_100_000, "target": "xs-1"}),
        ]
        for code, kwargs in cases:
            with pytest.raises(ValueError) as caught:
                _program_edit_field(programs, "atomic-2026", **kwargs)
            assert caught.value.code == code, kwargs

        _program_read(programs, "atomic-2026")
        after_read = [
            ("denied_field", {"kind": "layer", "field": "statutory", "value": True,
                              "expecting": False, "target": "xs-1"}),
            ("no_such_target", {"kind": "layer", "field": "attatch", "value": 1,
                                "expecting": 1, "target": "xs-1"}),
            ("stale_value", {"kind": "layer", "field": "attach", "value": "1m",
                             "expecting": "2m", "target": "xs-1"}),
            ("bad_value", {"kind": "layer", "field": "attach", "value": 1.5,
                           "expecting": 27_000_000, "target": "xs-1"}),
            ("no_such_program", {"kind": "layer", "field": "attach", "value": "1m",
                                 "expecting": "2m", "target": "xs-1", "name": "ghost"}),
            ("outside_roots", {"kind": "layer", "field": "attach", "value": "1m",
                               "expecting": "2m", "target": "xs-1", "name": "../out"}),
        ]
        for code, kwargs in after_read:
            name = kwargs.pop("name", "atomic-2026")
            with pytest.raises(ValueError) as caught:
                _program_edit_field(programs, name, **kwargs)
            assert caught.value.code == code, kwargs
            assert caught.value.code in mcpsurface.ERROR_CODES
            assert str(caught.value).startswith(f"[{code}] ")


class TestDescribe:
    def test_describe_publishes_the_kinds_the_rules_and_the_codes(self) -> None:
        out = mcpsurface.describe()
        assert list(out["kinds"]) == list(mcpsurface.KINDS)
        assert out["kinds"]["layer"]["denied"]["statutory"]
        assert "Never cents" in out["value_rules"]["money"]
        assert "stale_value" in out["error_codes"]
        assert mcpsurface.describe("layer")["kinds"].keys() == {"layer"}


# --- snapshots ----------------------------------------------------------------


class TestSnapshotCap:
    def test_the_cap_prunes_the_oldest_image_and_its_meta(self, roots, monkeypatch) -> None:
        """Pinned at 2 rather than writing 101 programs. The point is that the
        constant is READ at prune time (a baked-in 20 would ignore this
        monkeypatch) and that the sidecar goes with the image — an orphaned
        `.meta.json` is a ref `program_history` would list and revert could
        never honour.

        Mutation drill (2026-08-19): dropped the `.meta.json` unlink. This
        failed on the meta assertion. Restored.
        """
        import towerkit.mcpserver as server

        monkeypatch.setattr(server, "SNAPSHOT_KEEP", 2)
        path = roots[0] / "atomic-2026.json"
        snapdir = roots[0] / ".mcp-snapshots"
        image = path.read_bytes()
        for ref, when in (("TKW-a", 1_000_000), ("TKW-b", 2_000_000), ("TKW-c", None)):
            server.snapshot(path, ref, image)
            if when is not None:
                os.utime(snapdir / f"{ref}.json", (when, when))

        assert not (snapdir / "TKW-a.json").exists()
        assert not (snapdir / "TKW-a.meta.json").exists()
        assert (snapdir / "TKW-b.json").exists()
        assert (snapdir / "TKW-c.json").exists()

    def test_the_cap_is_a_hundred_and_is_never_zero(self) -> None:
        """`images[:-SNAPSHOT_KEEP]` is `[:0]` at zero — the empty list — so a
        cap of 0 keeps every snapshot forever instead of keeping none. Twenty
        was one afternoon of field-by-field edits."""
        import towerkit.mcpserver as server

        assert server.SNAPSHOT_KEEP == 100
