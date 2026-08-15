"""The structural edit API — one definition of every mutation, shared by
the TUI editor and the MCP server."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from towerkit import edit
from towerkit.model import RetentionType, load_program

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


class TestAdopt:
    def test_adopt_replaces_all_four_collections(self) -> None:
        program = _sample()
        source = _sample()
        edit.add_line(source, "Cyber Liability", abbr="CYB")
        edit.add_layer(source, [source.lines[0].id])
        edit.add_retention(source, [source.lines[0].id], "sir", 1)
        edit.add_sublimit(source, "Flood", 1, [source.lines[0].id])

        edit.adopt(program, source)

        assert program.lines == source.lines
        assert program.layers == source.layers
        assert program.retentions == source.retentions
        assert program.sublimits == source.sublimits


class TestRetentionsAndSublimits:
    def test_add_retention_appends_and_returns_it(self) -> None:
        program = _sample()
        before = len(program.retentions)
        retention = edit.add_retention(
            program, [program.lines[0].id], "sir", 500_000, aggregate=2_000_000
        )
        assert len(program.retentions) == before + 1
        assert retention.type is RetentionType.SIR
        assert retention.amount == 500_000
        assert program.retentions[-1] is retention

    def test_edit_retention_leaves_unnamed_fields_alone(self) -> None:
        program = _sample()
        edit.add_retention(program, [program.lines[0].id], "deductible", 100_000)
        index = len(program.retentions) - 1
        edit.edit_retention(program, index, amount=250_000)
        assert program.retentions[index].amount == 250_000
        assert program.retentions[index].type is RetentionType.DEDUCTIBLE

    def test_edit_retention_out_of_range_raises(self) -> None:
        program = _sample()
        with pytest.raises(IndexError):
            edit.edit_retention(program, 99, amount=1)

    def test_remove_retention_by_index(self) -> None:
        program = _sample()
        edit.add_retention(program, [program.lines[0].id], "sir", 1)
        before = len(program.retentions)
        edit.remove_retention(program, before - 1)
        assert len(program.retentions) == before - 1

    def test_add_and_edit_sublimit(self) -> None:
        program = _sample()
        sublimit = edit.add_sublimit(program, "Flood", 5_000_000, [program.lines[0].id])
        index = len(program.sublimits) - 1
        assert sublimit.name == "Flood"
        edit.edit_sublimit(program, index, name="Flood & Quake", amount=10_000_000)
        assert program.sublimits[index].name == "Flood & Quake"
        assert program.sublimits[index].amount == 10_000_000


class TestLayers:
    def test_add_layer_stacks_on_top_and_names_by_ordinal(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        top = edit.suggested_attach(program, [line_id])
        layer = edit.add_layer(program, [line_id])
        assert layer.attach == top
        assert layer.limit == 5_000_000
        assert layer.participants == []

    def test_remove_layer(self) -> None:
        program = _sample()
        layer = edit.add_layer(program, [program.lines[0].id])
        edit.remove_layer(program, layer.id)
        assert not any(ly.id == layer.id for ly in program.layers)

    def test_remove_unknown_layer_raises(self) -> None:
        program = _sample()
        with pytest.raises(KeyError):
            edit.remove_layer(program, "nope")

    def test_set_applies_to_rejects_an_unknown_line(self) -> None:
        program = _sample()
        layer = program.layers[0]
        with pytest.raises(KeyError):
            edit.set_applies_to(program, layer.id, ["ghost"])

    def test_set_applies_to_rejects_an_empty_list(self) -> None:
        program = _sample()
        layer = program.layers[0]
        with pytest.raises(ValueError):
            edit.set_applies_to(program, layer.id, [])

    def test_follows_underlying_heals_its_attachment(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        layer = edit.add_layer(program, [line_id])
        edit.set_applies_to(program, layer.id, [line_id])
        edit.set_follows_underlying(program, layer.id, True)
        edit.heal_follows(program)
        expected = max(program.underlying_tops(layer).values(), default=0)
        assert layer.attach == expected

    def test_restack_closes_gaps(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        stack = [ly for ly in program.layers_for_line(line_id) if ly.limit > 0]
        stack[-1].attach += 25_000_000  # blow a gap
        edit.restack(program)
        healed = [ly for ly in program.layers_for_line(line_id) if ly.limit > 0]
        healed.sort(key=lambda ly: ly.attach)
        assert healed[0].attach == 0
        for below, above in zip(healed, healed[1:], strict=False):
            assert above.attach == below.top


class TestDurableWrites:
    def test_dump_program_never_truncates_on_a_failed_write(
        self, tmp_path, monkeypatch
    ) -> None:
        target = tmp_path / "atomic-2026.json"
        shutil.copy(SAMPLE, target)
        before = target.read_bytes()

        def _enospc(_fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", _enospc)

        from towerkit.model import dump_program

        program = _sample()
        program.insured = "Changed Co"  # a real mutation: refused, not merely a no-op match

        with pytest.raises(OSError):
            dump_program(program, target)

        assert target.read_bytes() == before
        assert load_program(target).insured != "Changed Co"

    def test_a_save_keeps_the_previous_contents_aside(self, tmp_path) -> None:
        from towerkit.atomicio import backup_path
        from towerkit.model import dump_program

        target = tmp_path / "atomic-2026.json"
        shutil.copy(SAMPLE, target)
        original = target.read_bytes()

        program = _sample()
        program.insured = "Changed Co"
        dump_program(program, target)

        assert load_program(target).insured == "Changed Co"
        assert backup_path(target).read_bytes() == original
