"""ONE POLICY, SEVERAL LAYERS — workers' compensation being the case.

Part A (statutory benefits, no dollar limit) and Part B (employers liability, a
real limit) come on ONE policy from ONE carrier, and the model cannot make them
one layer: `statutory` requires limit 0, so a layer cannot be both. The
schematic draws them apart, correctly, and until 2026-08-21 nothing in the file
said they belonged together.

`policy_group` is a SHARED TOKEN, not a pointer. That choice is what most of
this file is about — it is symmetric by construction, so there is no direction
to get backwards and no dangling reference when a layer is removed.

It is deliberately NOT `policy_number` doing double duty, which was the first
proposal and was wrong: a program being DESIGNED has no policy number yet, and
the relation between the parts is known long before the paper is.
"""

from __future__ import annotations

import json

import pytest
from test_validate import make_program

from towerkit import edit, validate
from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    program_from_jsonable,
    program_to_jsonable,
)


def _wc_pair(**kw):
    """The real shape: Part A statutory, Part B dollar-limited, one program."""
    part_a = Layer(
        id="part-a", name="WC Part A", applies_to=["wc"], attach=0, limit=0,
        statutory=True,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
        **kw.pop("a", {}),
    )
    part_b = Layer(
        id="part-b", name="Employers Liability", applies_to=["el"], attach=0,
        limit=1_000_000,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
        **kw.pop("b", {}),
    )
    return make_program(
        lines=[
            Line(id="wc", name="Workers Compensation"),
            Line(id="el", name="Employers Liability"),
        ],
        layers=[part_a, part_b],
        retentions=[],
    )


class TestWhyItCannotBeOneLayer:
    def test_a_statutory_layer_with_a_limit_is_refused(self) -> None:
        """The reason the link field exists at all. If Part A and Part B could
        be one layer this would be a display problem, not a model one."""
        program = _wc_pair()
        program.layers[0].limit = 1_000_000
        assert "statutory-limit" in {
            d.code for d in validate.validate_program(program).items
        }


class TestLinking:
    def test_linking_puts_both_layers_on_one_token(self) -> None:
        program = _wc_pair()
        group = edit.link_policy(program, "part-a", "part-b")
        assert program.layers[0].policy_group == group
        assert program.layers[1].policy_group == group

    def test_the_token_is_derived_and_unique_within_the_program(self) -> None:
        program = _wc_pair()
        group = edit.link_policy(program, "part-a", "part-b")
        assert group
        assert group not in {ly.id for ly in program.layers}

    def test_it_is_symmetric_so_there_is_no_direction_to_get_backwards(
        self,
    ) -> None:
        """The whole reason for a token over a pointer."""
        one = _wc_pair()
        other = _wc_pair()
        edit.link_policy(one, "part-a", "part-b")
        edit.link_policy(other, "part-b", "part-a")
        assert (
            {ly.policy_group is not None for ly in one.layers}
            == {ly.policy_group is not None for ly in other.layers}
        )
        assert len({ly.policy_group for ly in one.layers}) == 1

    def test_a_third_part_joins_the_existing_policy(self) -> None:
        program = _wc_pair()
        program.layers.append(
            Layer(id="part-c", name="Stop Gap", applies_to=["wc"], attach=0,
                  limit=500_000)
        )
        group = edit.link_policy(program, "part-a", "part-b")
        again = edit.link_policy(program, "part-c", "part-a")
        assert again == group
        assert {ly.policy_group for ly in program.layers} == {group}

    def test_joining_carries_the_joiners_whole_group_with_it(self) -> None:
        """Taking one layer out of a policy to put it in another would break
        the first policy to make the second."""
        program = _wc_pair()
        program.layers.append(
            Layer(id="x1", name="Excess A", applies_to=["el"], attach=1_000_000,
                  limit=4_000_000)
        )
        program.layers.append(
            Layer(id="x2", name="Excess B", applies_to=["el"], attach=5_000_000,
                  limit=5_000_000)
        )
        second = edit.link_policy(program, "x1", "x2")
        assert second

        # x1 has a group of two; part-a has none — so x1's group wins and
        # part-a joins it, and x2 must come along
        edit.link_policy(program, "part-a", "x1")
        groups = {ly.id: ly.policy_group for ly in program.layers}
        assert groups["part-a"] == groups["x1"] == groups["x2"]

    def test_two_populated_policies_refuse_to_merge_silently(self) -> None:
        """Which policy number survives is a decision only a person can make."""
        program = _wc_pair()
        program.layers.append(
            Layer(id="x1", name="Excess A", applies_to=["el"], attach=1_000_000,
                  limit=4_000_000)
        )
        program.layers.append(
            Layer(id="x2", name="Excess B", applies_to=["el"], attach=5_000_000,
                  limit=5_000_000)
        )
        edit.link_policy(program, "part-a", "part-b")
        edit.link_policy(program, "x1", "x2")

        with pytest.raises(edit.Refusal) as refused:
            edit.link_policy(program, "part-a", "x1")
        assert refused.value.code == "policy-group-clash"
        assert "unlink one side first" in str(refused.value)

    def test_a_layer_cannot_be_linked_to_itself(self) -> None:
        program = _wc_pair()
        with pytest.raises(edit.Refusal) as refused:
            edit.link_policy(program, "part-a", "part-a")
        assert refused.value.code == "policy-self-link"

    def test_an_unknown_layer_raises(self) -> None:
        program = _wc_pair()
        with pytest.raises(KeyError):
            edit.link_policy(program, "part-a", "nope")


class TestUnlinking:
    def test_unlinking_takes_one_layer_off_and_leaves_the_rest(self) -> None:
        program = _wc_pair()
        program.layers.append(
            Layer(id="part-c", name="Stop Gap", applies_to=["wc"], attach=0,
                  limit=500_000)
        )
        group = edit.link_policy(program, "part-a", "part-b")
        edit.link_policy(program, "part-c", "part-a")

        edit.unlink_policy(program, "part-c")

        assert program.layers[2].policy_group is None
        assert program.layers[0].policy_group == group
        assert program.layers[1].policy_group == group

    def test_removing_a_layer_strands_nothing(self) -> None:
        """A token has no referent to dangle. A pointer would have needed a
        heal here, and heal_follows is enough of that already.

        The EL line is left uncovered by removing Part B, which validate says
        so about — that is the ordinary consequence of removing a layer and not
        this field's business. What matters is that no diagnostic mentions the
        group, and that the survivor keeps its token."""
        program = _wc_pair()
        edit.link_policy(program, "part-a", "part-b")
        edit.remove_layer(program, "part-b")

        codes = {d.code for d in validate.validate_program(program).items}
        assert not [c for c in codes if c.startswith("policy-group")]
        assert program.layers[0].policy_group  # a policy with one part is fine

    def test_members_ignores_the_unset_field(self) -> None:
        """Unset means "not linked", never "linked to the others that are also
        unset" — the bug an empty-string group would be."""
        program = _wc_pair()
        assert edit.policy_group_members(program, None) == []
        assert edit.policy_group_members(program, "") == []


class TestTheFile:
    def test_an_unlinked_layer_writes_no_key(self) -> None:
        """OMIT_EMPTY: adding the field changed the shape of no existing file."""
        assert "policyGroup" not in json.dumps(program_to_jsonable(_wc_pair()))

    def test_a_linked_pair_round_trips(self) -> None:
        program = _wc_pair()
        group = edit.link_policy(program, "part-a", "part-b")
        wire = program_to_jsonable(program)
        assert wire["layers"][0]["policyGroup"] == group
        back = program_from_jsonable(wire)
        assert {ly.policy_group for ly in back.layers} == {group}

    def test_the_schema_accepts_it_with_prose(self) -> None:
        from importlib import resources

        schema = json.loads(
            (resources.files("towerkit") / "schema" / "program.schema.json").read_text()
        )
        prop = schema["$defs"]["layer"]["properties"]["policyGroup"]
        assert prop["type"] == "string"
        assert prop.get("description")

    def test_the_write_surface_derived_it(self) -> None:
        from towerkit import mcpsurface

        entry = mcpsurface.SURFACE["layer"]["policyGroup"]
        assert entry.type == "text"


class TestTheRulesItCarries:
    """A link field with no rule attached is just a note. Parts of one policy
    share its paper, and that is checkable."""

    def _codes(self, program) -> set[str]:
        return {d.code for d in validate.validate_program(program).items}

    def test_one_policy_stating_two_numbers_is_an_error(self) -> None:
        program = _wc_pair(a={"policy_number": "WC-1"}, b={"policy_number": "WC-2"})
        edit.link_policy(program, "part-a", "part-b")
        assert "policy-group-numbers" in self._codes(program)

    def test_one_number_across_the_parts_is_fine(self) -> None:
        program = _wc_pair(a={"policy_number": "WC-1"}, b={"policy_number": "WC-1"})
        edit.link_policy(program, "part-a", "part-b")
        assert "policy-group-numbers" not in self._codes(program)

    def test_a_part_that_has_not_been_numbered_yet_is_fine(self) -> None:
        """A draft is routinely half-filled — only a CONTRADICTION is an error."""
        program = _wc_pair(a={"policy_number": "WC-1"})
        edit.link_policy(program, "part-a", "part-b")
        assert "policy-group-numbers" not in self._codes(program)

    def test_parts_running_on_different_periods_warn(self) -> None:
        program = _wc_pair(
            a={"period": Period(start="2026-01-01", end="2027-01-01")},
            b={"period": Period(start="2026-06-01", end="2027-06-01")},
        )
        edit.link_policy(program, "part-a", "part-b")
        codes = self._codes(program)
        assert "policy-group-periods" in codes
        assert "policy-group-periods" not in {
            d.code for d in validate.validate_program(program).errors
        }, "a half-filled draft must not be refused over it"

    def test_unlinked_layers_carry_no_rules_at_all(self) -> None:
        program = _wc_pair(a={"policy_number": "WC-1"}, b={"policy_number": "WC-2"})
        codes = self._codes(program)
        assert "policy-group-numbers" not in codes
        assert "policy-group-periods" not in codes


class TestEveryDoorReachesIt:
    """A field reachable by MCP and invisible in the editor is the "built but
    not accessible" class, and towerkit's layer sheet is HAND-BUILT."""

    def _source(self) -> str:
        from pathlib import Path

        import towerkit

        return (
            Path(towerkit.__file__).parent / "tui" / "screens" / "editor.py"
        ).read_text()

    def test_the_editor_renders_a_control_for_it(self) -> None:
        source = self._source()
        assert 'id="f-layer-policy-group"' in source
        assert 'wid == "f-layer-policy-group"' in source

    def test_the_control_is_a_picker_and_not_a_text_box(self) -> None:
        """The token is machine-minted and nobody should be typing one; the set
        of things a layer may share a policy with is exactly the other layers.
        Constrained input over an open field, and the picker offers only what
        is storable."""
        source = self._source()
        block = source[source.index('Same policy as') : ]
        assert block[: block.index("id=")].count("Select(") == 1
        assert "Input(" not in block[: block.index("id=")]

    def test_blank_unlinks_rather_than_being_ignored(self) -> None:
        """Every other select on that screen returns early on no-selection.
        Here it is a real answer — "not linked" — and the early return would
        have made unlinking unreachable from the keyboard."""
        source = self._source()
        handler = source[source.index('if wid == "f-layer-policy-group":') :]
        handler = handler[: handler.index("if value is Select.NULL:\n            return")]
        assert "unlink_policy" in handler
        assert "link_policy" in handler

    def test_the_screen_never_uses_the_falsy_BLANK_sentinel(self) -> None:
        """Textual 8.2.8's `Select.BLANK` is literally the bool False; the
        no-selection sentinel is `Select.NULL`. Passing BLANK as a value raises
        at MOUNT and takes the whole layer sheet down with it — 49 tests went
        red on it — and comparing against it in a change handler silently never
        matches. The two selects that predate this one are allow_blank=False,
        so their comparison was unreachable rather than wrong; this one makes
        blank a real answer."""
        from textual.widgets import Select

        assert Select.BLANK is False, "the trap moved — re-read this test"
        # comments stripped: the comment explaining the trap names it, which is
        # the point of the comment
        code = "\n".join(
            line for line in self._source().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "Select.BLANK" not in code
