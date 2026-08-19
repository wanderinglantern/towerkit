"""The conditional cross-field guards on `edit.set_field`.

Always-derived fields are denied to the generic setter outright. These four
are the CONDITIONAL ones — `attach` is free on an ordinary layer and derived
on a follows-underlying one, `limit` is free until the statutory flag is set,
`states` is a coverage fact on a statutory layer and a stray note anywhere
else — and a generic setter walks past every one of them unless the rule
lives here, in `edit.py`, where all three surfaces inherit it.

Every test in this file was mutation-verified: the guard was broken, the test
was watched failing by name, and the guard restored. A guard test that has
not been mutated is a guard test that may be passing because the guard was
never reached.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from towerkit import edit
from towerkit.model import load_program
from towerkit.validate import WARNING

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def _sample():
    return load_program(SAMPLE)


def _follows_layer(program):
    """`xs-1` made to follow the umbrella beneath it, healed as the session
    would heal it. Its attachment is now derived state."""
    edit.set_follows_underlying(program, "xs-1", True)
    edit.heal_follows(program)
    return next(ly for ly in program.layers if ly.id == "xs-1")


def _statutory_layer(program):
    """A statutory bar on the EL column, made through the verb that owns the
    invariant rather than by three assignments in the test."""
    return edit.set_statutory(program, "primary-el", True)


class TestAttachOnAFollowsUnderlyingLayer:
    """Guard A. This rule existed on no surface before: the write landed and
    `heal_follows` recomputed it away on the next line, so the number snapped
    back with nothing said. The guard turns a silent revert into a refusal."""

    def test_attach_is_refused_on_a_follows_underlying_layer(self) -> None:
        program = _sample()
        layer = _follows_layer(program)
        seated = layer.attach

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "attach", 1_000_000, "xs-1")

        assert layer.attach == seated, "a refused write must write nothing"
        assert caught.value.code == "guard_refused"

    def test_the_refusal_names_the_fix_and_the_derived_value(self) -> None:
        """A refusal that only says no costs the caller a round trip. This one
        names both escapes — change the underlying limit, or clear the flag —
        and prints the seat as money a caller could hand back."""
        program = _sample()
        _follows_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "attach", 1_000_000, "xs-1")

        message = str(caught.value)
        assert "DERIVED" in message
        assert "layer_follows(layer_id='xs-1', follows=false)" in message
        assert "$27,000,000" in message, message

    def test_attach_is_refused_on_a_statutory_layer(self) -> None:
        """The second half of guard A, and the one the spec's table omits: the
        TUI already blocked attach on a statutory layer, so a guard covering
        only follows-underlying would open a hole the editor had closed."""
        program = _sample()
        layer = _statutory_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "attach", 5_000_000, "primary-el")

        assert layer.attach == 0
        assert "layer_statutory(layer_id='primary-el', statutory=false)" in str(caught.value)

    def test_an_ordinary_layer_still_takes_an_attachment(self) -> None:
        """The guard is CONDITIONAL. If this ever fails the guard has stopped
        reading the flag and started refusing the field."""
        program = _sample()
        edit.set_field(program, "layer", "attach", 3_000_000, "xs-pl")
        assert next(ly for ly in program.layers if ly.id == "xs-pl").attach == 3_000_000

    def test_heal_follows_is_exempt_from_its_own_guard(self) -> None:
        """`heal_follows` is the DERIVER of the value guard A protects. If it
        ever routed through the choke point it would refuse itself and every
        follows-underlying layer would stop healing."""
        program = _sample()
        layer = _follows_layer(program)
        edit.set_field(program, "layer", "limit", 30_000_000, "umbrella")
        edit.heal_follows(program)
        assert layer.attach == 32_000_000


class TestLimitOnAStatutoryLayer:
    """Guard B. `statutory ⇒ limit == 0` is the invariant the whole statutory
    design rests on: it is what keeps the bar out of every dollar total by
    construction."""

    def test_limit_is_refused_on_a_statutory_layer(self) -> None:
        program = _sample()
        layer = _statutory_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "limit", 1_000_000, "primary-el")

        assert layer.limit == 0, "a refused write must write nothing"
        assert caught.value.code == "guard_refused"

    def test_the_refusal_names_the_verb_that_clears_the_flag(self) -> None:
        """`layer.statutory` is denied to the generic setter, so a caller told
        only "no" has no move at all. The refusal has to name the verb."""
        program = _sample()
        _statutory_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "limit", 1_000_000, "primary-el")

        message = str(caught.value)
        assert "layer_statutory(layer_id='primary-el', statutory=false)" in message
        assert "statutory implies limit == 0" in message

    def test_an_ordinary_layer_still_takes_a_limit(self) -> None:
        program = _sample()
        edit.set_field(program, "layer", "limit", 7_000_000, "primary-pl")
        assert next(ly for ly in program.layers if ly.id == "primary-pl").limit == 7_000_000


class TestStatesOnADollarLimitedLayer:
    """Guard C. A TIER CHANGE, not a relocation: `validate.py` flagged this
    softly and kept the draft editable, and now the write is blocked. It is
    the one layer-detail field that earns it — states are a coverage FACT, and
    on a dollar-limited layer the field does not mean anything at all."""

    def test_states_are_refused_on_a_dollar_limited_layer(self) -> None:
        program = _sample()
        layer = next(ly for ly in program.layers if ly.id == "umbrella")

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "states", ["NY", "NJ"], "umbrella")

        assert layer.states == [], "a refused write must write nothing"
        assert "dollar-limited layer cannot carry them" in str(caught.value)
        assert "layer_statutory(layer_id='umbrella', statutory=true)" in str(caught.value)

    def test_set_states_is_guarded_by_delegating_to_the_choke_point(self) -> None:
        """The named setter is a thin wrapper, not a second door. If it grew
        its own body the TUI would keep writing what MCP refuses."""
        program = _sample()
        with pytest.raises(edit.GuardRefused):
            edit.set_states(program, "umbrella", ["NY"])

    def test_clearing_states_is_always_allowed(self) -> None:
        """Otherwise a layer that lost its statutory flag could never be
        tidied up — the guard would strand exactly the data it exists to
        keep off dollar-limited layers."""
        program = _sample()
        layer = _statutory_layer(program)
        edit.set_states(program, "primary-el", ["NY", "NJ"])
        edit.set_statutory(program, "primary-el", False)

        edit.set_states(program, "primary-el", [])
        assert layer.states == []

    def test_states_land_on_a_statutory_layer_and_keep_their_order(self) -> None:
        program = _sample()
        layer = _statutory_layer(program)
        edit.set_states(program, "primary-el", ["NY", "CT", "NJ"])
        assert layer.states == ["NY", "CT", "NJ"]

    def test_the_guard_does_not_re_check_the_codes_themselves(self) -> None:
        """`MONOPOLISTIC_STATES` stays owned by validate.py. The guard decides
        whether the FIELD may be written at all; whether OH is cover-able is a
        different question, and a second copy of that list here is the exact
        bug this whole pass exists to kill."""
        program = _sample()
        layer = _statutory_layer(program)
        edit.set_states(program, "primary-el", ["OH", "Ontario"])
        assert layer.states == ["OH", "Ontario"]


class TestCurrencyAdvisory:
    """Guard D is not a refusal. Setting `currency` is allowed and converts
    nothing, and silence about that was the failure mode."""

    def test_currency_is_writable(self) -> None:
        program = _sample()
        edit.set_field(program, "program", "currency", "EUR")
        assert program.currency == "EUR"

    def test_the_write_carries_a_warning_that_no_figure_was_converted(self) -> None:
        program = _sample()
        total = program.total_limit()

        advisories = edit.set_field(program, "program", "currency", "EUR")

        assert len(advisories) == 1
        advisory = advisories[0]
        assert advisory.severity == WARNING
        assert advisory.code == "currency-not-converted"
        assert advisory.ref == ("program", None)
        assert "NO figure was converted" in advisory.message
        assert "EUR" in advisory.message
        assert program.total_limit() == total, "the advisory is a statement, not a conversion"

    def test_the_advisory_says_the_label_does_not_move_either(self) -> None:
        """towerkit hard-codes USD into every money format, so `currency`
        reaches no chart, no SOI and no CLI — only the file and the read. An
        advisory that mentioned only unconverted figures would still be silent
        about the larger half."""
        program = _sample()
        (advisory,) = edit.set_field(program, "program", "currency", "EUR")
        assert "US dollars wherever it renders" in advisory.message

    def test_an_unguarded_field_returns_no_advisories(self) -> None:
        """A key absent from both tables is set with no guard and says
        nothing — that is what makes a new model field writable with no edit
        to this section."""
        program = _sample()
        assert edit.set_field(program, "program", "notes", "renewal pending") == []
        assert program.notes == "renewal pending"


class TestSetFieldPlumbing:
    """The choke point itself. Guards are only worth what the path through
    them is worth."""

    def test_fields_are_addressed_by_their_file_name_or_their_attribute(self) -> None:
        program = _sample()
        edit.set_field(program, "layer", "policyNumber", "GL-123", "primary-gl")
        edit.set_field(program, "layer", "limits_detail", "Per occurrence", "primary-gl")
        layer = next(ly for ly in program.layers if ly.id == "primary-gl")
        assert layer.policy_number == "GL-123"
        assert layer.limits_detail == "Per occurrence"

    def test_a_nested_scalar_is_addressed_by_a_dotted_path(self) -> None:
        program = _sample()
        edit.set_field(program, "program", "period.end", "2027-06-30")
        assert program.period.end == date(2027, 6, 30)
        assert program.period.start == _sample().period.start, "a sibling was blanked"

    def test_an_unknown_target_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            edit.set_field(_sample(), "layer", "limit", 1, "no-such-layer")

    def test_an_unknown_field_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            edit.set_field(_sample(), "layer", "colour", "blue", "umbrella")

    def test_a_bad_value_raises_rather_than_writing_it(self) -> None:
        """`validate_assignment` is on, so the model refuses a negative
        attachment. The choke point must let that through, not swallow it."""
        program = _sample()
        with pytest.raises(ValueError):
            edit.set_field(program, "layer", "attach", -1, "xs-pl")
        assert next(ly for ly in program.layers if ly.id == "xs-pl").attach == 5_000_000

    def test_a_guard_refusal_is_a_value_error(self) -> None:
        """The MCP server catches `(ValidationError, ValueError, KeyError,
        IndexError, RuntimeError)` and writes nothing. A refusal type outside
        that tuple would escape as a traceback and the file would be spared
        only by luck."""
        program = _sample()
        _statutory_layer(program)
        with pytest.raises(ValueError):
            edit.set_field(program, "layer", "limit", 1, "primary-el")

    def test_retentions_and_sublimits_are_addressed_by_index(self) -> None:
        program = _sample()
        edit.set_field(program, "retention", "amount", 250_000, 0)
        assert program.retentions[0].amount == 250_000
        edit.set_field(program, "sublimit", "name", "Named Storm", 0)
        assert program.sublimits[0].name == "Named Storm"

    def test_an_out_of_range_index_raises_index_error(self) -> None:
        with pytest.raises(IndexError):
            edit.set_field(_sample(), "retention", "amount", 1, 99)


class TestSetStatutory:
    """The flag that OWNS the invariant, moved out of the TUI. It was a
    closure inside a screen — the last copy of `statutory ⇒ limit == 0`
    living on a surface."""

    def test_setting_the_flag_forces_the_whole_invariant(self) -> None:
        program = _sample()
        edit.set_follows_underlying(program, "xs-1", True)
        layer = edit.set_statutory(program, "xs-1", True)
        assert (layer.statutory, layer.limit, layer.attach) == (True, 0, 0)
        assert layer.follows_underlying is False

    def test_clearing_the_flag_leaves_the_zeros_alone(self) -> None:
        """Dropping the flag states the layer is no longer statutory; it does
        not invent a limit. The user says what the limit is next."""
        program = _sample()
        edit.set_statutory(program, "primary-el", True)
        layer = edit.set_statutory(program, "primary-el", False)
        assert (layer.statutory, layer.limit) == (False, 0)

    def test_clearing_the_flag_leaves_stale_states_for_the_validator(self) -> None:
        """Deliberate asymmetry: guard C blocks WRITING states onto a
        dollar-limited layer, and clearing the flag does not delete the ones
        already there. Silently dropping them would destroy typed data to
        satisfy a rule the validator already reports by name
        (`states-non-statutory`)."""
        program = _sample()
        edit.set_statutory(program, "primary-el", True)
        edit.set_states(program, "primary-el", ["NY"])
        layer = edit.set_statutory(program, "primary-el", False)
        assert layer.states == ["NY"]

    def test_an_unknown_layer_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            edit.set_statutory(_sample(), "no-such-layer", True)
