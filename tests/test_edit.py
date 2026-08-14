"""The structural edit API — one definition of every mutation, shared by
the TUI editor and the MCP server."""

from __future__ import annotations

from pathlib import Path

import pytest

from towerkit import edit
from towerkit.model import load_program

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def _sample():
    return load_program(SAMPLE)


class TestLines:
    def test_add_line_slugs_its_id_from_the_name(self) -> None:
        program = _sample()
        line = edit.add_line(program, "Cyber Liability", abbr="CYB")
        assert line.id == "cyber-liability"
        assert line.abbr == "CYB"
        assert program.lines[-1] is line

    def test_add_line_does_not_collide_with_an_existing_id(self) -> None:
        program = _sample()
        first = edit.add_line(program, "Excess Casualty")
        second = edit.add_line(program, "Excess Casualty")
        assert first.id == "excess-casualty"
        assert second.id == "excess-casualty-2"

    def test_rename_line_cascades_the_new_id_everywhere(self) -> None:
        program = _sample()
        old = program.lines[0].id
        carriers = [ly.id for ly in program.layers if old in ly.applies_to]
        assert carriers, "fixture must have layers on the first line"
        line = edit.rename_line(program, old, "Marine Cargo")
        assert line.id == "marine-cargo"
        assert not any(old in ly.applies_to for ly in program.layers)
        for layer_id in carriers:
            layer = next(ly for ly in program.layers if ly.id == layer_id)
            assert "marine-cargo" in layer.applies_to

    def test_rename_line_to_its_own_slug_does_not_drift(self) -> None:
        """The self-collision guard: re-committing the same name must not
        walk the id to '-2', '-3' on every edit."""
        program = _sample()
        line = edit.add_line(program, "Cyber")
        again = edit.rename_line(program, line.id, "Cyber")
        assert again.id == "cyber"

    def test_move_line_reorders_columns(self) -> None:
        program = _sample()
        second = program.lines[1].id
        assert edit.move_line(program, second, -1) == 0
        assert program.lines[0].id == second

    def test_move_line_at_the_edge_is_a_no_op(self) -> None:
        program = _sample()
        first = program.lines[0].id
        assert edit.move_line(program, first, -1) == 0
        assert program.lines[0].id == first

    def test_remove_line_drops_layers_it_would_leave_empty(self) -> None:
        """The bug in editor.py's drop_line: it kept the original appliesTo
        when filtering emptied it, stranding a reference to a dead line."""
        program = _sample()
        target = program.lines[0].id
        solo = [ly.id for ly in program.layers if ly.applies_to == [target]]
        assert solo, "fixture must have a single-line layer to strand"
        edit.remove_line(program, target)
        assert target not in program.line_ids()
        assert not any(ly.id in solo for ly in program.layers)
        assert not any(target in ly.applies_to for ly in program.layers)

    def test_remove_line_keeps_shared_layers_and_trims_them(self) -> None:
        program = _sample()
        target = program.lines[0].id
        shared = [
            ly.id for ly in program.layers
            if target in ly.applies_to and len(ly.applies_to) > 1
        ]
        assert shared, "fixture must have a multi-line layer"
        edit.remove_line(program, target)
        for layer_id in shared:
            layer = next(ly for ly in program.layers if ly.id == layer_id)
            assert target not in layer.applies_to

    def test_remove_line_drops_retentions_and_sublimits_left_empty(self) -> None:
        program = _sample()
        target = program.lines[0].id
        edit.remove_line(program, target)
        for retention in program.retentions:
            assert target not in retention.applies_to
            assert retention.applies_to
        for sublimit in program.sublimits:
            assert target not in sublimit.applies_to
            assert sublimit.applies_to

    def test_remove_unknown_line_raises(self) -> None:
        program = _sample()
        with pytest.raises(KeyError):
            edit.remove_line(program, "no-such-line")
