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

from towerkit import edit, mcpsurface
from towerkit.model import Period, RenderSettings, load_program
from towerkit.validate import WARNING, Diagnostic

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
        only follows-underlying would open a hole the editor had closed.

        Mutation drill (2026-08-19): deleted the `if layer.statutory` branch
        from `_guard_attach`. Failed with `DID NOT RAISE
        <class 'towerkit.edit.GuardRefused'>`. Restored.
        """
        program = _sample()
        layer = _statutory_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "attach", 5_000_000, "primary-el")

        assert layer.attach == 0
        message = str(caught.value)
        assert "statutory implies attach == 0" in message
        # The fix, quoted from the one source rather than restated here — see
        # `TestOneSourceForTheStatutoryFix` below.
        assert edit.STATUTORY_FLAG_FIX in message

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

    def test_the_refusal_names_something_a_caller_can_actually_do(self) -> None:
        """`layer.statutory` is denied to the generic setter, so a caller told
        only "no" has no move at all — and this refusal used to name
        `layer_statutory(layer_id=..., statutory=false)`, which the server does
        not register and will not until Phase 2. Naming an unbuilt tool refuses
        the retry as surely as saying nothing: the client calls it, gets
        "unknown tool", and is back where it started. So it names the editor,
        which works today, and says plainly that there is no call.

        Mutation drill (2026-08-19): replaced `STATUTORY_FLAG_FIX` in
        `_guard_limit` with the old `layer_statutory(...)` sentence. Failed on
        `assert edit.STATUTORY_FLAG_FIX in message`. Restored.
        """
        program = _sample()
        _statutory_layer(program)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "limit", 1_000_000, "primary-el")

        message = str(caught.value)
        assert "statutory implies limit == 0" in message
        assert edit.STATUTORY_FLAG_FIX in message
        assert "towerctl edit" in message

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
        message = str(caught.value)
        assert "dollar-limited layer cannot carry them" in message
        # Two escapes, and BOTH have to be things a Phase 1 client can do:
        # `notes` is a writable field on the same layer, and the flag is the
        # editor's. This assertion read `layer_statutory(...)` until 2026-08-19.
        assert "notes" in message
        assert edit.STATUTORY_FLAG_FIX in message

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


class TestOneSourceForTheStatutoryFix:
    """The refusal a client SEES and the guard text `describe()` PUBLISHES are
    two descriptions of one rule, and two descriptions drift.

    They had. `edit.py` told callers to call `layer_statutory(...)`, a tool the
    server does not register; `mcpsurface.GUARDS['layer.limit']` said the
    opposite — "Phase 1 has no verb for it" — and a test in
    `test_mcp_surface.py` banned the string from `describe()` outright. The
    module that exists to stop exactly this had it inside itself.

    So there is one string now, `edit.STATUTORY_FLAG_FIX`, and both sides
    interpolate it. These tests fail if either side paraphrases.
    """

    def test_the_live_refusal_and_the_published_guard_share_one_string(self) -> None:
        """Mutation drill (2026-08-19): put the old paraphrase back into
        `GUARDS['layer.limit']` ("Phase 1 has no verb for it, so that is the
        towerkit editor") in place of the interpolated constant — a drift of
        exactly the size that shipped. Failed with `assert 'The statutory flag
        moves in the towerkit editor ...' in 'Refused on a statutory layer:
        ... Phase 1 has no verb for it, so that is the towerkit editor.'`.
        Restored.
        """
        program = _sample()
        _statutory_layer(program)
        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "layer", "limit", 1_000_000, "primary-el")

        assert edit.STATUTORY_FLAG_FIX in str(caught.value)
        assert edit.STATUTORY_FLAG_FIX in mcpsurface.GUARDS["layer.limit"]
        assert edit.STATUTORY_FLAG_FIX in mcpsurface.GUARDS["layer.states"]
        assert edit.STATUTORY_FLAG_FIX in mcpsurface.GUARDS["layer.attach"]

    def test_the_one_source_names_no_tool(self) -> None:
        """It names `towerctl edit`, which a human can run, and no tool at all.
        `test_mcpserver.TestRefusalsNameCallableThings` holds the general
        form, derived from the built server's tool list; this is the specific
        claim about the sentence every statutory refusal now ends on.

        Mutation drill (2026-08-19): rewrote the constant to say the flag moves
        "via layer_statutory(layer_id=..., statutory=false)". Failed with
        `assert 'layer_statutory' not in 'The statuto...tor refuses.'`.
        Restored.
        """
        assert "layer_statutory" not in edit.STATUTORY_FLAG_FIX
        assert "towerctl edit" in edit.STATUTORY_FLAG_FIX


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

    def test_a_participant_is_addressed_by_its_layer_and_its_index(self) -> None:
        """Both halves, because neither identifies a row on its own: every
        layer has a participant 0, and a carrier appears on several layers.

        The surface advertised `participant.carrier` and `.share_bps` as
        writable from the day it shipped, and `_entity` refused the kind — so
        every one of those writes died on "'participant' is not addressable by
        set_field; use its verbs", naming a private function and verbs that
        exist in no phase. Nothing in the suite performed a participant write,
        which is how it shipped.

        Mutation drill (2026-08-19): made `_entity` return `rows[0]` whatever
        the index. Failed with `At index 0 diff: ('Chubb', 1000) !=
        ('Chubb', 5000)` — the write landed on the wrong row. Restored.

        Two more drills on the same branch: dropping the `index is None` check
        (defaulting to 0) failed the next test with `DID NOT RAISE ValueError`,
        and dropping `_at` for a modulo failed the one after with `DID NOT
        RAISE IndexError`.
        """
        program = _sample()
        layer = next(ly for ly in program.layers if ly.id == "xs-1")

        edit.set_field(program, "participant", "share_bps", 1_000, "xs-1", index=2)

        assert [(p.carrier, p.share_bps) for p in layer.participants] == [
            ("Chubb", 5_000),
            ("Zurich", 2_500),
            ("AXA XL", 1_000),
        ]

    def test_a_participant_write_with_no_index_says_which_index_it_wants(self) -> None:
        """A caller that sent only the layer id gets told the address, and how
        many rows there are to choose from — not a stack trace.

        Mutation drill (2026-08-19): defaulted the missing index to 0. Failed
        with `Failed: DID NOT RAISE ValueError`. Restored.
        """
        program = _sample()
        with pytest.raises(ValueError) as caught:
            edit.set_field(program, "participant", "carrier", "Ace", "xs-1")
        message = str(caught.value)
        assert "layer id and index" in message
        assert "'xs-1' has 3" in message

    def test_an_out_of_range_participant_index_raises_index_error(self) -> None:
        """The same code the server turns into `no_such_target`, so a caller
        that guessed a row is told what is there.

        Mutation drill (2026-08-19): replaced `_at` with `rows[index % len]`.
        Failed with `Failed: DID NOT RAISE IndexError`. Restored.
        """
        with pytest.raises(IndexError):
            edit.set_field(_sample(), "participant", "carrier", "Ace", "xs-1", index=9)

    def test_an_unknown_kind_names_the_kinds_that_exist(self) -> None:
        """The branch that used to say "use its verbs" about a kind the
        surface publishes. It is now reachable only by a genuine typo, and it
        answers one.

        Mutation drill (2026-08-19): shortened the refusal to "unknown".
        Failed with `assert "no such kind 'carrier'" in 'unknown'`. Restored.
        """
        with pytest.raises(ValueError) as caught:
            edit.set_field(_sample(), "carrier", "name", "Ace", "xs-1")
        message = str(caught.value)
        assert "no such kind 'carrier'" in message
        assert "participant" in message


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


class TestADottedWriteTakesTheSameGuardPath:
    """Defect 6, found 2026-08-19 by a verifier and not by a test.

    `set_field`'s dotted branch resolved the container, did the `setattr` and
    RETURNED — before the `_GUARDS` lookup, and without ever consulting
    `_ADVISORIES`. Nothing was exploitable, because no dotted key was in either
    table. What was broken was the module's CONTRACT: a key absent from
    `_GUARDS` is set with no guard, which is exactly what makes a field added
    to `model.py` writable with no edit here. That contract was silently false
    for every nested scalar, and `period.start`, `period.end` and the render
    flags are where the next cross-field rule (start before end) has to live.
    A guard registered there would have been dead on arrival with nothing in
    the suite failing.

    So the guard tables are exercised on a dotted key here, rather than the
    fixed code being taken on trust. The guard is registered by the TEST — a
    real one would be a change to `_GUARDS` and would carry its own test — and
    it is registered through `monkeypatch.setitem` so the table is restored
    whatever the assertion does.
    """

    def test_a_guard_registered_on_a_dotted_key_fires(self, monkeypatch) -> None:
        """Mutation drill (2026-08-19): restored the early `return []` in the
        dotted branch. Failed with `Failed: DID NOT RAISE
        GuardRefused`. Restored."""
        program = _sample()
        was = program.period.start

        def refuse_a_start_after_the_end(program, entity, value) -> None:
            if value >= entity.period.end:
                raise edit.GuardRefused(
                    f"a period starting {value} ends before it begins "
                    f"({entity.period.end})"
                )

        monkeypatch.setitem(edit._GUARDS, ("program", "period.start"), refuse_a_start_after_the_end)

        with pytest.raises(edit.GuardRefused) as caught:
            edit.set_field(program, "program", "period.start", date(2028, 1, 1))
        assert "ends before it begins" in str(caught.value)
        assert program.period.start == was, "refused, and the write landed anyway"

    def test_the_dotted_guard_key_is_the_CANONICAL_path(self, monkeypatch) -> None:
        """`render.showTotals` and `render.show_totals` address one field, so
        they must reach one guard key — otherwise a caller spelling it the
        python way walks past the rule, which is the bug `_resolve` already
        fixed for the undotted half.

        Mutation drill (2026-08-19): keyed the lookup on the raw `field`
        argument instead of the canonical join. Failed with `Failed: DID NOT
        RAISE GuardRefused` on the python spelling.
        """
        seen: list[object] = []

        def refuse(program, entity, value) -> None:
            seen.append(value)
            raise edit.GuardRefused("no")

        monkeypatch.setitem(edit._GUARDS, ("program", "render.showTotals"), refuse)

        for spelling in ("render.showTotals", "render.show_totals"):
            program = _sample()
            program.render = RenderSettings()
            with pytest.raises(edit.GuardRefused):
                edit.set_field(program, "program", spelling, False)
        assert seen == [False, False], "one of the two spellings missed the guard"

    def test_an_advisory_on_a_dotted_key_comes_back(self, monkeypatch) -> None:
        """The other table the early return skipped. An advisory says what the
        write did NOT do, and a dotted write returning `[]` unconditionally
        would swallow it.

        Mutation drill (2026-08-19): restored the early `return []`. Failed
        with `assert [] == ['nested-advisory']`.
        """

        def advise(program, value) -> list:
            return [Diagnostic(WARNING, "nested-advisory", "said something", ("program", None))]

        monkeypatch.setitem(edit._ADVISORIES, ("program", "period.end"), advise)

        program = _sample()
        out = edit.set_field(program, "program", "period.end", date(2027, 6, 30))
        assert [d.code for d in out] == ["nested-advisory"]
        assert program.period.end == date(2027, 6, 30), "the write itself was lost"


class TestSetContainer:
    """Defect 7. The server materialised a missing `program.render` with a bare
    `setattr` INSIDE the surface — and `program.render` is a denied field, so
    the one object no caller may set wholesale was being constructed by the one
    module the denylist is supposed to keep out of that business. The behaviour
    was right; the location was not, and the TUI creates the same containers
    without inheriting anything the server decided.

    The semantics now have one definition, and it is checked here rather than
    only through the tool that calls it.
    """

    def test_it_materialises_a_missing_container(self) -> None:
        program = _sample()
        assert program.render is None
        edit.set_container(program, "program", "render.showTotals", RenderSettings())
        assert program.render is not None
        assert program.render.show_totals is True

    def test_it_refuses_to_replace_a_container_that_is_already_there(self) -> None:
        """Auto-creation replaces NOTHING. An existing render or period is the
        caller's data, and overwriting it with a defaults object is exactly the
        sibling-blanking write the container denylist exists to prevent — a
        caller who set `cellPremiums` last week would find it back to false.

        Mutation drill (2026-08-19): deleted the `is not None` check. Failed
        with `Failed: DID NOT RAISE ValueError`. Restored.
        """
        program = _sample()
        program.render = RenderSettings(cellPremiums=True)
        with pytest.raises(ValueError, match="already set"):
            edit.set_container(program, "program", "render.showTotals", RenderSettings())
        assert program.render.cell_premiums is True, "a sibling was blanked"

    def test_it_refuses_a_field_that_is_not_a_dotted_path(self) -> None:
        """There is no "create an empty render": a container is materialised
        only on the way to a scalar inside it, which is what makes the note the
        caller gets back ("you changed showTotals") true.

        Mutation drill (2026-08-19): dropped the `if not rest` branch. Failed
        with `Failed: DID NOT RAISE ValueError`. Restored.
        """
        program = _sample()
        with pytest.raises(ValueError, match="dotted path"):
            edit.set_container(program, "program", "render", RenderSettings())

    def test_it_addresses_a_layer_the_way_every_other_write_does(self) -> None:
        """`layer.period` is the other container, and it hangs off a row —
        so the helper takes the same address as `set_field` rather than
        assuming the program.

        Mutation drill (2026-08-19): made `set_container` resolve the program
        instead of the addressed row. Failed with `ValueError: layer.period is
        already set; it is denied to a wholesale write ...` — the program's own
        period, which is required and therefore always there, standing in for
        the layer's, which is optional and was not.
        """
        program = _sample()
        layer = next(ly for ly in program.layers if ly.id == "umbrella")
        assert layer.period is None
        period = Period(start=date(2026, 1, 1), end=date(2027, 1, 1))
        edit.set_container(program, "layer", "period.start", period, "umbrella")
        assert layer.period is not None
        assert layer.period.end == date(2027, 1, 1)
