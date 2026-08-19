"""The derived write surface — one table, computed from `model.py`.

The bug this file guards is not a crash. It is a field that shipped in
`model.py` and never reached the connector, because reaching it needed
somebody to remember a second file. Every test here fails when the surface
stops being a DERIVATION and starts being a list.

Mutation evidence is required before any of these is claimed to protect
something (CLAUDE.md, 2026-08-14). The drills run for this file are recorded
in the docstrings of the tests that needed them — the absent-by-default ones.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from towerkit import mcpsurface
from towerkit.model import (
    MONEY,
    Layer,
    Line,
    Money,
    NamedLimit,
    Participant,
    Period,
    Program,
    RenderSettings,
    Retention,
    Sublimit,
    _Model,
)

REPO = Path(__file__).parent.parent
SRC = REPO / "src" / "towerkit"

# Every model reachable from a kind, including the two that are NOT kinds and
# are addressed only as dotted children. Built here BY HAND on purpose: this
# is the independent statement of what the model contains, and the whole point
# of test 2 is that it disagrees with the surface when the surface goes stale.
NESTED_MODELS = {"period": Period, "render": RenderSettings}


def advertised(cls: type[BaseModel]) -> list[str]:
    """The names the surface promises: the JSON alias where there is one."""
    return [info.alias or name for name, info in cls.model_fields.items()]


# --- the point ---------------------------------------------------------------


def test_a_new_model_field_becomes_writable_with_no_mcp_edit() -> None:
    """THE point of the module. A field added to a model is writable, and
    neither `mcpsurface.py` nor `mcpserver.py` mentions it."""

    class LayerWithBrokerFields(Layer):
        broker_note: str | None = Field(alias="brokerNote", default=None)
        broker_fee: Money | None = Field(alias="brokerFee", default=None)

    surface = mcpsurface.build_surface(
        {**mcpsurface.KIND_MODELS, "layer": LayerWithBrokerFields}
    )

    assert "brokerNote" in surface["layer"], "a new model field must be writable"
    note = surface["layer"]["brokerNote"]
    assert note.type == "text"
    assert note.clearable and not note.required
    assert note.path == ("broker_note",), "the setter must use the PYTHON name"

    # And the money one, which is only knowable from the MONEY tag.
    fee = surface["layer"]["brokerFee"]
    assert fee.type == "money"
    assert mcpsurface.parse_value(fee, "5m") == 5_000_000

    # The write lands on a real object through the parser and the python name.
    layer = LayerWithBrokerFields(
        id="xs-1", name="1st Excess", appliesTo=["gl"], attach=0, limit=5_000_000
    )
    setattr(layer, note.path[0], mcpsurface.parse_value(note, "call Marsh"))
    assert layer.broker_note == "call Marsh"

    for module in ("mcpsurface.py", "mcpserver.py"):
        text = (SRC / module).read_text("utf-8")
        for token in ("brokerNote", "broker_note", "brokerFee", "broker_fee"):
            assert token not in text, f"{module} names {token}; the surface is not derived"


# --- the two halves of coverage ----------------------------------------------


def test_every_model_field_is_writable_or_denied_with_a_reason() -> None:
    """Half one: every field of every KIND.

    Mutation drill (2026-08-19): added `spare: str | None = None` to `Layer`
    and made `build_surface` skip any field named 'spare'. This test failed
    naming `layer.spare`; `test_a_denied_field_stays_denied` did not. Restored.
    """
    orphans = []
    for kind, cls in mcpsurface.KIND_MODELS.items():
        for field in advertised(cls):
            if field in mcpsurface.SURFACE[kind]:
                continue
            if f"{kind}.{field}" in mcpsurface.DENIED:
                continue
            orphans.append(f"{kind}.{field}")
    assert not orphans, (
        "these model fields are neither writable nor denied with a reason — "
        "add them to DENIED or let the derivation reach them: " + ", ".join(orphans)
    )


def test_every_nested_model_field_is_reachable_by_a_dotted_path() -> None:
    """Half two, and the half that rots.

    Without it, adding `RenderSettings.showGroups` produces a field that is
    neither writable nor denied and half one notices nothing — `render` itself
    is denied, so the container looks accounted for.

    Mutation drill (2026-08-19): added `show_groups: bool = Field(
    alias='showGroups', default=False)` to `RenderSettings`. This test failed
    naming `program.render.showGroups`; half one passed. Restored.
    """
    unreachable = []
    for container, cls in NESTED_MODELS.items():
        for kind, owner in mcpsurface.KIND_MODELS.items():
            if container not in advertised(owner):
                continue
            for field in advertised(cls):
                if f"{container}.{field}" not in mcpsurface.SURFACE[kind]:
                    unreachable.append(f"{kind}.{container}.{field}")
    assert not unreachable, (
        "a nested scalar is addressed by dotted path and these are not reachable: "
        + ", ".join(unreachable)
    )


def test_a_denied_field_stays_denied() -> None:
    """The inverse half: nothing on the denylist may be writable.

    Mutation drill (2026-08-19): removed the `if key in denied: continue` line
    from `build_surface`. This test failed naming six leaked fields, starting
    with `program.$schema`. Restored.
    """
    leaked = [key for key in mcpsurface.DENIED if key.split(".", 1)[1] in
              mcpsurface.SURFACE[key.split(".", 1)[0]]]
    assert not leaked, "denied fields are writable: " + ", ".join(leaked)


def test_every_denied_entry_carries_a_reason() -> None:
    """`describe` prints these to the model. A blank one is a refusal with no
    fix in it, which is the refusal shape this pass exists to remove."""
    thin = [key for key, reason in mcpsurface.DENIED.items() if len(reason.split()) < 6]
    assert not thin, "denied without a usable reason: " + ", ".join(thin)


def test_denied_names_only_fields_that_exist() -> None:
    """A stale denylist row silently freezes nothing and hides a rename."""
    for key in mcpsurface.DENIED:
        kind, field = key.split(".", 1)
        assert kind in mcpsurface.KIND_MODELS, f"{key} names an unknown kind"
        assert field in advertised(mcpsurface.KIND_MODELS[kind]), (
            f"{key} names a field {kind} does not have"
        )


def test_the_denylist_is_exactly_the_reviewed_fifteen() -> None:
    """Grant reviewed this list on 2026-08-19 and struck `program.currency`
    out. A sixteenth row appearing without a review is a field quietly
    withdrawn from the connector."""
    assert set(mcpsurface.DENIED) == {
        "program.$schema",
        "program.period",
        "program.render",
        "program.lines",
        "program.layers",
        "program.retentions",
        "program.sublimits",
        "line.id",
        "layer.id",
        "layer.period",
        "layer.followsUnderlying",
        "layer.appliesTo",
        "layer.namedLimits",
        "layer.statutory",
        "layer.participants",
    }
    assert "program.currency" not in mcpsurface.DENIED


def test_applies_to_is_denied_on_a_layer_and_writable_on_a_retention() -> None:
    """DENIED is keyed kind.field. A derivation keying it on the bare field
    name would freeze two writable fields and no other test would see it."""
    assert "appliesTo" not in mcpsurface.SURFACE["layer"]
    assert "appliesTo" in mcpsurface.SURFACE["retention"]
    assert "appliesTo" in mcpsurface.SURFACE["sublimit"]


# --- containers and depth ----------------------------------------------------


def test_a_nested_model_is_denied_and_expanded_by_rule() -> None:
    """The three container rows come from the RULE, not from the list. A
    container missing its reason is refused at import rather than advertised
    as a wholesale set that can blank its siblings."""

    class ProgramWithAudit(Program):
        audit: Period | None = None

    with pytest.raises(RuntimeError, match="audit"):
        mcpsurface.build_surface({**mcpsurface.KIND_MODELS, "program": ProgramWithAudit})


def test_a_model_two_levels_deep_is_loud_at_build_time() -> None:
    """Depth is exactly one. A silent skip makes a field unreachable and
    nothing says so — the canonical serialiser's drop guard raises for the
    same reason."""

    class Outer(_Model):
        inner: Period = Period(start=date(2026, 1, 1), end=date(2027, 1, 1))

    class ProgramWithOuter(Program):
        outer: Outer | None = None

    denied = {**mcpsurface.DENIED, "program.outer": "test container"}
    with pytest.raises(RuntimeError, match="one level"):
        mcpsurface.build_surface(
            {**mcpsurface.KIND_MODELS, "program": ProgramWithOuter}, denied=denied
        )


def test_dotted_children_carry_the_container_and_the_python_path() -> None:
    start = mcpsurface.SURFACE["program"]["period.start"]
    assert start.type == "date"
    assert start.required
    assert start.container == "period"
    assert start.path == ("period", "start")
    totals = mcpsurface.SURFACE["program"]["render.showTotals"]
    assert totals.type == "bool"
    assert totals.path == ("render", "show_totals"), "setattr by alias raises in pydantic"


def test_a_defaultable_container_is_materialised_and_the_note_lists_what_it_wrote() -> None:
    entry = mcpsurface.SURFACE["program"]["render.showTotals"]
    assert mcpsurface.container_defaultable(entry)
    created, note = mcpsurface.create_container(entry)
    assert isinstance(created, RenderSettings)
    assert note == (
        "created render with defaults (showTotals=true, showPremiums=true, "
        "cellPremiums=false, cellDates=false, soiSchematic=false); you changed showTotals"
    )


def test_a_layer_period_cannot_be_defaulted_and_says_why() -> None:
    """Seeding it from the program period would write an end date the caller
    never supplied into the field that feeds the Schedule of Insurance."""
    entry = mcpsurface.SURFACE["layer"]["period.start"]
    assert not mcpsurface.container_defaultable(entry)
    assert mcpsurface.missing_container_message(entry, "layer 'xs-1'") == (
        "layer 'xs-1' has no period; start and end must be set together"
    )


# --- value types are derived, not listed -------------------------------------


def test_money_is_derived_from_the_tag_not_from_a_second_list() -> None:
    """`attach` and `share_bps` are both `int` with `ge=0`; `limit` has no
    constraint at all. Only the tag tells them apart."""
    assert mcpsurface.SURFACE["layer"]["attach"].type == "money"
    assert mcpsurface.SURFACE["layer"]["limit"].type == "money"
    assert mcpsurface.SURFACE["layer"]["premium"].type == "money"  # Optional[Money]
    assert mcpsurface.SURFACE["retention"]["aggregate"].type == "money"
    assert mcpsurface.SURFACE["participant"]["share_bps"].type == "share_bps"
    # The tag is the fact, and it is the model's, not the surface's.
    assert MONEY in Layer.model_fields["attach"].metadata


def test_every_advertised_type_is_derived_for_the_whole_surface() -> None:
    expected = {
        ("program", "insured"): "text",
        ("program", "placement"): "enum",
        ("program", "currency"): "text",
        ("program", "period.end"): "date",
        ("program", "render.theme"): "text",
        ("program", "render.soiSchematic"): "bool",
        ("line", "abbr"): "text",
        ("layer", "policyNumber"): "text",
        ("layer", "states"): "list_of_strings",
        ("layer", "premiumDetail"): "text",
        ("retention", "type"): "enum",
        ("retention", "appliesTo"): "list_of_strings",
        ("sublimit", "amount"): "money",
        ("named_limit", "amount"): "money",
        ("participant", "carrier"): "text",
    }
    for (kind, field), kind_type in expected.items():
        assert mcpsurface.SURFACE[kind][field].type == kind_type, f"{kind}.{field}"


def test_an_int_the_surface_cannot_classify_is_loud() -> None:
    """A misclassified type is the mistake this module exists to prevent, so
    an unknown one refuses at build time instead of being advertised as text."""

    class LineWithCount(Line):
        count: int = 0

    with pytest.raises(RuntimeError, match="count"):
        mcpsurface.build_surface({**mcpsurface.KIND_MODELS, "line": LineWithCount})


def test_enums_publish_their_values() -> None:
    assert mcpsurface.SURFACE["program"]["placement"].values == ("bound", "proposed")
    assert mcpsurface.SURFACE["retention"]["type"].values == ("deductible", "sir", "captive")


# --- the wire lexicon --------------------------------------------------------


@pytest.mark.parametrize(
    "wire,parsed",
    [("5m", 5_000_000), ("250k", 250_000), ("$5,000,000", 5_000_000),
     ("5000000", 5_000_000), (5_000_000, 5_000_000)],
)
def test_money_accepts_every_form_the_rule_advertises(wire: object, parsed: int) -> None:
    entry = mcpsurface.SURFACE["layer"]["attach"]
    assert mcpsurface.parse_value(entry, wire) == parsed


def test_money_refuses_a_float_because_fractional_dollars_are_not_money() -> None:
    entry = mcpsurface.SURFACE["layer"]["attach"]
    with pytest.raises(mcpsurface.BadValue, match="whole dollars"):
        mcpsurface.parse_value(entry, 5_000_000.5)


def test_a_bool_is_json_true_or_false_and_nothing_else() -> None:
    """pydantic would coerce 'true' and 1 to True on assignment, and the five
    render booleans decide what a saved chart prints."""
    entry = mcpsurface.SURFACE["program"]["render.showTotals"]
    assert mcpsurface.parse_value(entry, False) is False
    for refused in ("true", "yes", 1, 0, "false"):
        with pytest.raises(mcpsurface.BadValue, match="true or false"):
            mcpsurface.parse_value(entry, refused)


def test_a_bool_is_not_smuggled_in_as_money_or_a_share() -> None:
    """`bool` is a subclass of `int`; an unguarded isinstance check reads
    `True` as one dollar."""
    with pytest.raises(mcpsurface.BadValue):
        mcpsurface.parse_value(mcpsurface.SURFACE["layer"]["attach"], True)
    with pytest.raises(mcpsurface.BadValue):
        mcpsurface.parse_value(mcpsurface.SURFACE["participant"]["share_bps"], True)


def test_dates_take_iso_exactly_and_numeric_forms_as_mdy() -> None:
    entry = mcpsurface.SURFACE["program"]["period.start"]
    assert mcpsurface.parse_value(entry, "2026-06-01") == date(2026, 6, 1)
    assert mcpsurface.parse_value(entry, "6/1/2026") == date(2026, 6, 1)
    with pytest.raises(mcpsurface.BadValue):
        mcpsurface.parse_value(entry, 20260601)


def test_states_also_take_one_comma_separated_string() -> None:
    """The flag is on the ENTRY, not on the type: a second writable list[str]
    must not inherit the ambiguity by accident."""
    entry = mcpsurface.SURFACE["layer"]["states"]
    assert entry.accepts_comma_string
    assert mcpsurface.parse_value(entry, "NY, NJ") == ["NY", "NJ"]
    assert mcpsurface.parse_value(entry, ["NY", "NJ"]) == ["NY", "NJ"]
    assert not mcpsurface.SURFACE["retention"]["appliesTo"].accepts_comma_string
    with pytest.raises(mcpsurface.BadValue, match="array"):
        mcpsurface.parse_value(mcpsurface.SURFACE["retention"]["appliesTo"], "gl, cyber")


def test_states_order_is_verbatim() -> None:
    entry = mcpsurface.SURFACE["layer"]["states"]
    assert mcpsurface.parse_value(entry, ["NJ", "NY"]) == ["NJ", "NY"]


def test_an_enum_takes_exactly_one_lowercase_string() -> None:
    entry = mcpsurface.SURFACE["program"]["placement"]
    assert mcpsurface.parse_value(entry, "bound") == "bound"
    with pytest.raises(mcpsurface.BadValue, match="bound"):
        mcpsurface.parse_value(entry, "BOUND")


def test_null_clears_an_optional_field_and_a_required_one_refuses_both() -> None:
    notes = mcpsurface.SURFACE["layer"]["notes"]
    assert mcpsurface.parse_value(notes, None) is None
    assert mcpsurface.parse_value(notes, "") is None, "empty is None, so clearing drops the key"
    name = mcpsurface.SURFACE["layer"]["name"]
    for refused in (None, ""):
        with pytest.raises(mcpsurface.BadValue):
            mcpsurface.parse_value(name, refused)


def test_currency_is_writable_but_not_clearable() -> None:
    """It is `str = 'USD'`, not `str | None` — so `null` is a bad value, not
    a clear, and the model gets told which."""
    entry = mcpsurface.SURFACE["program"]["currency"]
    assert not entry.clearable and not entry.required
    with pytest.raises(mcpsurface.BadValue):
        mcpsurface.parse_value(entry, None)


def test_share_bps_is_basis_points_and_bounded() -> None:
    entry = mcpsurface.SURFACE["participant"]["share_bps"]
    assert mcpsurface.parse_value(entry, 3500) == 3500
    for refused in (-1, 10_001, "35%"):
        with pytest.raises(mcpsurface.BadValue):
            mcpsurface.parse_value(entry, refused)


# --- refusal rendering: the half that has to be pasteable --------------------


def test_a_refusal_prints_the_current_value_in_the_lexicon_the_field_accepts() -> None:
    money = mcpsurface.SURFACE["layer"]["attach"]
    assert mcpsurface.render_value(money, 5_000_000) == "$5,000,000"
    assert mcpsurface.render_value(mcpsurface.SURFACE["layer"]["premium"], None) == "null"
    assert mcpsurface.render_value(mcpsurface.SURFACE["program"]["period.start"],
                                   date(2026, 6, 1)) == "2026-06-01"
    assert mcpsurface.render_value(mcpsurface.SURFACE["program"]["render.showTotals"],
                                   True) == "true"
    assert mcpsurface.render_value(mcpsurface.SURFACE["layer"]["states"],
                                   ["NY", "NJ"]) == '["NY", "NJ"]'
    assert mcpsurface.render_value(mcpsurface.SURFACE["layer"]["name"],
                                   "1st Excess") == "'1st Excess'"
    assert mcpsurface.render_value(mcpsurface.SURFACE["participant"]["share_bps"],
                                   3500) == "35% (3500 bps)"


def test_money_is_never_rendered_compact_or_in_cents() -> None:
    """`$5M` does not round-trip through `parse_money` for every amount, and
    a model handed cents writes 100x the figure on the retry."""
    entry = mcpsurface.SURFACE["layer"]["attach"]
    shown = mcpsurface.render_value(entry, 2_340_000)
    assert shown == "$2,340,000"
    assert mcpsurface.parse_value(entry, shown) == 2_340_000


def test_no_enum_repr_leaks_into_a_refusal() -> None:
    """`f'{Placement.BOUND!r}'` is `<Placement.BOUND: 'bound'>`, which no
    client can pass back — so a refusal printing it refuses the retry too.

    Mutation drill (2026-08-19): changed `render_value`'s enum branch to
    `f"{value!r}"`. This test failed. Restored. (A negative assertion, so it
    is exactly the shape that passes for the wrong reason.)
    """
    entry = mcpsurface.SURFACE["program"]["placement"]
    from towerkit.model import Placement

    for shown in (mcpsurface.render_value(entry, Placement.BOUND),
                  mcpsurface.expecting_literal(entry, Placement.BOUND),
                  mcpsurface.mismatch_message(entry, "program", Placement.BOUND, "proposed")):
        assert "Placement." not in shown and "<" not in shown


def test_the_mismatch_message_can_be_pasted_straight_back() -> None:
    entry = mcpsurface.SURFACE["layer"]["attach"]
    message = mcpsurface.mismatch_message(entry, "layer 'xs-1'", 5_000_000, 2_000_000)
    assert message == (
        "layer 'xs-1' attach is $5,000,000, not the $2,000,000 you expected — "
        "pass expecting='$5,000,000' to overwrite it, or call program_read."
    )
    assert mcpsurface.parse_expecting(entry, "$5,000,000") == 5_000_000


def test_expecting_is_compared_on_the_normalised_value() -> None:
    """Mutation drill (2026-08-19): made `compare_values` compare the RAW wire
    values. This test failed on the '5m' case. Restored."""
    entry = mcpsurface.SURFACE["layer"]["attach"]
    for wire in ("5m", "$5,000,000", 5_000_000, "5000000"):
        assert mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, wire), 5_000_000)
    assert not mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, "2m"), 5_000_000)


def test_expecting_null_is_the_only_way_to_guard_a_field_that_is_none() -> None:
    entry = mcpsurface.SURFACE["layer"]["policyNumber"]
    assert mcpsurface.parse_expecting(entry, None) is None
    assert mcpsurface.compare_values(entry, None, None)
    with pytest.raises(mcpsurface.BadValue, match="null"):
        mcpsurface.parse_expecting(entry, "")
    assert not mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, "None"), None)


def test_expecting_null_works_for_a_field_that_cannot_be_cleared() -> None:
    """A required field can still hold None on a half-built draft object; the
    guard has to be expressible either way or it becomes opt-out."""
    entry = mcpsurface.SURFACE["program"]["currency"]
    assert mcpsurface.parse_expecting(entry, None) is None


def test_a_list_comparison_is_order_sensitive() -> None:
    entry = mcpsurface.SURFACE["layer"]["states"]
    assert not mcpsurface.compare_values(entry, ["NJ", "NY"], ["NY", "NJ"])


# --- describe() --------------------------------------------------------------


def test_describe_reads_like_the_file_not_like_an_alphabet() -> None:
    layer = mcpsurface.describe("layer")["kinds"]["layer"]["fields"]
    order = list(layer)
    assert order[:4] == ["name", "policyNumber", "period.start", "period.end"]
    assert order != sorted(order), "canonical file order, never alphabetical"


def test_describe_publishes_kinds_in_declaration_order() -> None:
    assert list(mcpsurface.describe()["kinds"]) == list(mcpsurface.KINDS)


def test_describe_for_one_kind_is_the_same_shape_as_for_all() -> None:
    one = mcpsurface.describe("layer")
    every = mcpsurface.describe()
    assert set(one) == set(every)
    assert one["kinds"]["layer"] == every["kinds"]["layer"]
    assert set(one["kinds"]) == {"layer"}


def test_describe_publishes_the_row_guard_and_the_target_per_kind() -> None:
    kinds = mcpsurface.describe()["kinds"]
    assert kinds["program"]["target"] == [] and kinds["program"]["row_guard"] is None
    assert kinds["layer"]["target"] == ["target"]
    assert kinds["retention"]["target"] == ["index"]
    assert kinds["retention"]["row_guard"] == "appliesTo"
    assert kinds["sublimit"]["row_guard"] == "name"
    assert kinds["participant"]["target"] == ["target", "index"]
    assert kinds["participant"]["row_guard"] == "carrier"
    assert kinds["named_limit"]["row_guard"] == "name"


def test_describe_carries_every_denied_reason_for_its_kind() -> None:
    denied = mcpsurface.describe("layer")["kinds"]["layer"]["denied"]
    assert set(denied) == {
        "id", "period", "followsUnderlying", "appliesTo", "namedLimits",
        "statutory", "participants",
    }
    assert "over-signing" in denied["participants"]


def test_describe_carries_the_guard_text_where_there_is_a_guard() -> None:
    fields = mcpsurface.describe("layer")["kinds"]["layer"]["fields"]
    assert "follows-underlying" in fields["attach"]["guard"]
    assert "statutory" in fields["limit"]["guard"]
    assert "statutory" in fields["states"]["guard"].lower()
    assert "guard" not in fields["notes"]


def test_describe_names_no_tool_the_client_cannot_call() -> None:
    """A refusal that names a call which does not exist refuses the retry
    too. Phase 2 verbs are named as Phase 2, never as something to try now."""
    text = repr(mcpsurface.describe())
    for phase_two in ("layer_statutory(", "layer_add(", "participant_add("):
        assert phase_two not in text


def test_layer_period_is_advertised_with_its_phase_1_limitation() -> None:
    """It is enumerated and it always refuses on a layer with no period,
    because no Phase 1 call creates one. Saying so is what stops the model
    retrying it forever; `mcpparity` carries it as DEFERRED for the same
    reason."""
    start = mcpsurface.describe("layer")["kinds"]["layer"]["fields"]["period.start"]
    assert "both" in start["guard"]


def test_describe_error_codes_include_the_compare_and_set_mismatch() -> None:
    """The spec's list has no code for it and `stale_sha` would be a lie —
    nothing about the file hash is wrong."""
    codes = mcpsurface.describe()["error_codes"]
    assert "stale_value" in codes
    assert set(codes) >= {
        "stale_sha", "not_read", "outside_roots", "no_such_program", "no_such_target",
        "denied_field", "guard_refused", "bad_value", "exists", "no_snapshot",
    }


# --- one owner for the prose -------------------------------------------------


def test_value_rules_have_one_owner() -> None:
    """bookkit shipped two tool descriptions saying money was cents and
    dollars respectively. The sentences live in `VALUE_RULES` and nowhere
    else, so `describe`'s output and a tool description cannot disagree.

    Mutation drill (2026-08-19): pasted the money rule verbatim into a comment
    in `model.py`. This test failed naming `money in model.py`. Restored.
    """
    assert mcpsurface.describe()["value_rules"] is mcpsurface.VALUE_RULES
    owner = SRC / "mcpsurface.py"
    duplicates = []
    for name, rule in mcpsurface.VALUE_RULES.items():
        for path in sorted(SRC.rglob("*.py")):
            if path == owner:
                continue
            if rule in path.read_text("utf-8"):
                duplicates.append(f"{name} in {path.relative_to(SRC)}")
    assert not duplicates, "value rules are reprinted elsewhere: " + ", ".join(duplicates)


def test_every_advertised_type_has_exactly_one_rule() -> None:
    used = {entry.type for fields in mcpsurface.SURFACE.values() for entry in fields.values()}
    assert used <= set(mcpsurface.VALUE_RULES)
    assert "clearing" in mcpsurface.VALUE_RULES


def test_the_money_rule_says_dollars_and_never_cents() -> None:
    rule = mcpsurface.VALUE_RULES["money"]
    assert "cents" in rule and "Whole dollars" in rule
    assert "$5,000,000" in rule


# --- setter dispatch ---------------------------------------------------------


def test_renaming_a_line_goes_through_the_verb_that_cascades_the_id() -> None:
    """Denying `line.id` is only coherent if setting `line.name` runs the
    cascade. A plain setattr strands every appliesTo on the old slug."""
    assert mcpsurface.SURFACE["line"]["name"].setter == "rename_line"

    program = Program(
        insured="Atomic", program="Casualty", placement="bound",
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        layers=[Layer(id="p", name="Primary", appliesTo=["gl"], attach=0, limit=1)],
    )
    mcpsurface.apply(program, mcpsurface.SURFACE["line"]["name"], "gl", None, "Cyber")
    assert program.lines[0].id == "cyber"
    assert program.layers[0].applies_to == ["cyber"], "the cascade must follow the rename"


def test_a_layer_name_is_a_plain_write_because_layer_ids_are_opaque() -> None:
    """`add_layer` uses `unique_id(program, 'layer')`, never the name."""
    assert mcpsurface.SURFACE["layer"]["name"].setter is None


def test_the_guarded_layer_fields_route_through_the_choke_point() -> None:
    """`edit.set_field` runs the guard table. A named setter for `states`
    would walk straight past `_guard_states`."""
    for field in ("attach", "limit", "states"):
        assert mcpsurface.SURFACE["layer"][field].setter is None


def test_retention_and_sublimit_writes_check_the_line_ids() -> None:
    """`appliesTo` reaches `_check_lines` only through `edit_retention` /
    `edit_sublimit`; a bare setattr writes unknown line ids into the file."""
    assert mcpsurface.SURFACE["retention"]["appliesTo"].setter == "edit_retention"
    assert mcpsurface.SURFACE["sublimit"]["appliesTo"].setter == "edit_sublimit"
    assert mcpsurface.SURFACE["named_limit"]["amount"].setter == "edit_named_limit"

    program = Program(
        insured="Atomic", program="Casualty", placement="bound",
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        retentions=[Retention(appliesTo=["gl"], type="sir", amount=100_000)],
    )
    entry = mcpsurface.SURFACE["retention"]["appliesTo"]
    with pytest.raises(KeyError, match="unknown line"):
        mcpsurface.apply(program, entry, None, 0, ["nope"])


def test_every_setter_named_by_an_entry_exists_in_edit_py() -> None:
    """The entry stores a NAME; a typo would surface as an AttributeError at
    write time, on a client's write."""
    from towerkit import edit

    for fields in mcpsurface.SURFACE.values():
        for entry in fields.values():
            if entry.setter is not None:
                assert callable(getattr(edit, entry.setter)), entry.setter


def test_the_surface_imports_no_plotting_library_and_not_the_server() -> None:
    """`mcpserver` imports the surface; the reverse is a cycle. Plotting is
    the scale.py/layout.py rule extended to the connector spine."""
    text = (SRC / "mcpsurface.py").read_text("utf-8")
    for banned in ("import matplotlib", "from matplotlib", "import mcpserver",
                   "from .mcpserver", "from towerkit.mcpserver"):
        assert banned not in text


# --- addressing --------------------------------------------------------------


def test_resolve_refuses_an_unknown_segment_by_name() -> None:
    assert mcpsurface.resolve("layer", "attach").type == "money"
    with pytest.raises(KeyError, match="attatch"):
        mcpsurface.resolve("layer", "attatch")
    with pytest.raises(KeyError, match="period.middle"):
        mcpsurface.resolve("layer", "period.middle")
    with pytest.raises(KeyError, match="lyaer"):
        mcpsurface.resolve("lyaer", "attach")


def test_a_denied_field_resolves_to_its_reason_not_to_no_such_target() -> None:
    """'that field does not exist' and 'you may not write that field' are
    different problems and a client retries them differently."""
    assert mcpsurface.denied_reason("layer", "statutory") is not None
    assert mcpsurface.denied_reason("layer", "attach") is None


def test_subject_reads_like_the_thing_the_broker_is_looking_at() -> None:
    assert mcpsurface.subject("layer", "xs-1", None) == "layer 'xs-1'"
    assert mcpsurface.subject("retention", None, 2) == "retention 2"
    assert mcpsurface.subject("participant", "xs-1", 0) == "participant 0 on layer 'xs-1'"
    assert mcpsurface.subject("program", None, None) == "program"


# --- the shape of the whole thing --------------------------------------------


def test_the_writable_counts_match_the_reviewed_contract() -> None:
    """Layer is the field that keeps growing: 10 scalars plus the two dotted
    period entries. A count that drifts without a review is the connector
    going lossy again in the other direction."""
    assert len(mcpsurface.SURFACE["program"]) == 13
    assert len(mcpsurface.SURFACE["line"]) == 3
    assert len(mcpsurface.SURFACE["layer"]) == 12
    assert len(mcpsurface.SURFACE["participant"]) == 2
    assert len(mcpsurface.SURFACE["retention"]) == 6
    assert len(mcpsurface.SURFACE["sublimit"]) == 4
    assert len(mcpsurface.SURFACE["named_limit"]) == 2


def test_period_and_render_are_not_kinds() -> None:
    """They are reached as dotted children only. A kind for them would give
    two ways to set the same scalar, which is how two definitions drift."""
    assert set(mcpsurface.KIND_MODELS) == set(mcpsurface.KINDS)
    assert Period not in mcpsurface.KIND_MODELS.values()
    assert RenderSettings not in mcpsurface.KIND_MODELS.values()
    assert mcpsurface.KIND_MODELS == {
        "program": Program, "line": Line, "layer": Layer, "participant": Participant,
        "retention": Retention, "sublimit": Sublimit, "named_limit": NamedLimit,
    }


def test_the_module_level_surface_is_the_default_build() -> None:
    assert mcpsurface.build_surface() == mcpsurface.SURFACE
    assert NamedLimit  # imported for the KIND_MODELS assertion above
