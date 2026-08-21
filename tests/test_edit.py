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


class TestLayerDetailFields:
    """`states`, `namedLimits`, `premiumDetail` — the setters the editor calls
    and the MCP server will.

    `namedLimits` and `premiumDetail` enforce no validator rule: every refusal
    (duplicates, prose-versus-structure, a premium detail on a priced layer)
    belongs to validate.py, and a setter that quietly repaired the input would
    delete the refusal instead of delivering it.

    `states` is the one exception, and it is a GUARD rather than a repair: on a
    dollar-limited layer the field means nothing at all, so the write is
    refused outright at `edit.set_field`. The guard and its refusal live in
    tests/test_edit_guards.py; what is asserted here is that the setter still
    replaces wholesale and keeps order on a layer that can carry states.
    """

    def test_parse_states_splits_trims_and_drops_the_typing_artefacts(self) -> None:
        assert edit.parse_states("NY, NJ") == ["NY", "NJ"]
        assert edit.parse_states(" NY ,NJ , ") == ["NY", "NJ"]
        assert edit.parse_states("") == []
        assert edit.parse_states("  ,  ") == []

    def test_parse_states_normalises_what_it_knows_and_keeps_the_rest(self) -> None:
        """A RECOGNISED jurisdiction is stored as its USPS code; anything else
        travels on verbatim for `validate` to name.

        This reverses the older rule that upper-casing "would rewrite files
        nobody edited" — that reasoning belongs to LOADING, and parse_states
        never runs on load. It only ever sees text a person just typed, and
        storing "ny" there is what made the validator's own upper-cased
        comparison necessary. "Ontario" is untouched, which is the half of the
        old rule that was always right: a parser must not invent a coverage
        fact out of a near-miss.
        """
        assert edit.parse_states("ny, Ontario") == ["NY", "Ontario"]

    def test_parse_states_keeps_duplicates_for_the_validator_to_refuse(self) -> None:
        assert edit.parse_states("NY, NY") == ["NY", "NY"]

    def test_set_states_replaces_wholesale_and_keeps_order(self) -> None:
        """On a STATUTORY layer — the only place the field has a meaning. The
        umbrella this used to run against is dollar-limited, and the write is
        now refused there (tests/test_edit_guards.py)."""
        program = _sample()
        layer = edit.set_statutory(program, "primary-el", True)
        edit.set_states(program, "primary-el", ["NY", "CT", "NJ"])
        assert layer.states == ["NY", "CT", "NJ"]
        edit.set_states(program, "primary-el", [])
        assert layer.states == []

    def test_set_states_is_refused_on_a_dollar_limited_layer(self) -> None:
        """The tier change, stated where the old policy used to be: this is no
        longer an editable-but-flagged draft, it is a blocked write."""
        program = _sample()
        with pytest.raises(edit.GuardRefused):
            edit.set_states(program, "umbrella", ["NY"])
        assert next(ly for ly in program.layers if ly.id == "umbrella").states == []

    def test_set_states_refuses_an_unknown_layer(self) -> None:
        with pytest.raises(KeyError):
            edit.set_states(_sample(), "no-such-layer", ["NY"])

    def test_set_premium_detail_empties_to_none(self) -> None:
        """Empty is None, never `""` — an empty string is a key in the file,
        and the canonical dump only drops None. A caller that hands over raw
        input (the MCP server will) must get the same answer the form does."""
        program = _sample()
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        edit.set_premium_detail(program, "umbrella", "Included with Part A")
        assert layer.premium_detail == "Included with Part A"
        for empty in ("", "   ", None):
            edit.set_premium_detail(program, "umbrella", empty)
            assert layer.premium_detail is None, empty

    def test_named_limits_append_in_order_and_are_never_sorted(self) -> None:
        program = _sample()
        edit.add_named_limit(program, "umbrella", "Each Accident", 1_000_000)
        edit.add_named_limit(program, "umbrella", "Disease Each Employee", 500_000)
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        assert [(nl.name, nl.amount) for nl in layer.named_limits] == [
            ("Each Accident", 1_000_000),
            ("Disease Each Employee", 500_000),
        ]

    def test_edit_named_limit_leaves_alone_what_it_is_not_given(self) -> None:
        program = _sample()
        edit.add_named_limit(program, "umbrella", "Each Accident", 1_000_000)
        edit.edit_named_limit(program, "umbrella", 0, amount=2_000_000)
        edit.edit_named_limit(program, "umbrella", 0, name="Each Occurrence")
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        assert (layer.named_limits[0].name, layer.named_limits[0].amount) == (
            "Each Occurrence",
            2_000_000,
        )

    def test_remove_named_limit_takes_the_one_it_is_pointed_at(self) -> None:
        program = _sample()
        for name in ("A", "B", "C"):
            edit.add_named_limit(program, "umbrella", name, 1)
        edit.remove_named_limit(program, "umbrella", 1)
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        assert [nl.name for nl in layer.named_limits] == ["A", "C"]

    def test_an_out_of_range_index_is_refused_not_silently_ignored(self) -> None:
        program = _sample()
        edit.add_named_limit(program, "umbrella", "Each Accident", 1_000_000)
        with pytest.raises(IndexError):
            edit.edit_named_limit(program, "umbrella", 1, name="x")
        with pytest.raises(IndexError):
            edit.remove_named_limit(program, "umbrella", 1)
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        assert len(layer.named_limits) == 1
