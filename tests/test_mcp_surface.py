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

import json
import re
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import Annotated

import pytest
from conftest import model_field
from pydantic import BaseModel, Field

from towerkit import edit, mcpsurface
from towerkit.model import (
    MONEY,
    Layer,
    Line,
    Money,
    NamedLimit,
    Participant,
    Period,
    Placement,
    Program,
    RenderSettings,
    Retention,
    Sublimit,
    _Model,
)
from towerkit.money import BPS_SCALE

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
    container missing its hand reason is still denied — but per FIELD since
    Grant's F8 call, never a raise that stops the server: the rule itself
    becomes the recorded reason, and the container's children stay
    writable."""

    class ProgramWithAudit(Program):
        audit: Period | None = None

    undescribable: dict[str, str] = {}
    surface = mcpsurface.build_surface(
        {**mcpsurface.KIND_MODELS, "program": ProgramWithAudit},
        undescribable=undescribable,
    )
    assert "add it to DENIED" in undescribable["program.audit"]
    assert "audit.start" in surface["program"], "the children still expand"


def test_a_model_two_levels_deep_is_loud_at_build_time() -> None:
    """Depth is exactly one. A silent skip makes a field unreachable and
    nothing says so — loud stays; per-field since F8, so one deep field
    cannot stop the server."""

    class Outer(_Model):
        inner: Period = Period(start=date(2026, 1, 1), end=date(2027, 1, 1))

    class ProgramWithOuter(Program):
        outer: Outer | None = None

    denied = {**mcpsurface.DENIED, "program.outer": "test container"}
    undescribable: dict[str, str] = {}
    surface = mcpsurface.build_surface(
        {**mcpsurface.KIND_MODELS, "program": ProgramWithOuter},
        denied=denied,
        undescribable=undescribable,
    )
    assert "one level" in undescribable["program.outer.inner"]
    assert "insured" in surface["program"], "the rest of the surface survives"


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
    constraint at all. Only the tag tells them apart.

    Asked about fields the surface has NEVER SEEN, on purpose. Until 2026-08-19
    this test asked only about today's five money fields, and a hand-written
    set of their NAMES — the second table `mcpsurface` exists to kill, and the
    exact mutation this docstring named — reproduced every one of them and left
    the test green. A name-keyed table can only be caught by a name it has no
    row for, and by a name whose row is wrong for this kind.

    Mutation drill (2026-08-19): replaced the tag check in `_classify` with
    `if where.rsplit(".", 1)[1] in {"attach", "limit", "premium", "amount",
    "aggregate"}`. Failed with `RuntimeError: layer.brokerFee is an int the
    surface cannot name: tag it model.MONEY if it is dollars, bound it to 10000
    if it is basis points` — the tag was ignored — and, with brokerFee removed
    to reach the second half, with `assert 'money' == 'share_bps'` on
    `layer.amount`. Restored.
    """
    assert mcpsurface.SURFACE["layer"]["attach"].type == "money"
    assert mcpsurface.SURFACE["layer"]["limit"].type == "money"
    assert mcpsurface.SURFACE["layer"]["premium"].type == "money"  # Optional[Money]
    assert mcpsurface.SURFACE["retention"]["aggregate"].type == "money"
    assert mcpsurface.SURFACE["participant"]["share_bps"].type == "share_bps"
    # The tag is the fact, and it is the model's, not the surface's.
    assert MONEY in Layer.model_fields["attach"].metadata

    class LayerWithABrokerFee(Layer):
        # Money under a name no money list has a row for.
        broker_fee: Money | None = Field(alias="brokerFee", default=None)
        # And the trap the other way round: `amount` IS the money field on
        # `sublimit` and on `named_limit`, and here it is basis points. A table
        # keyed by field name gets this one backwards while agreeing with the
        # tag-derived surface everywhere else.
        amount: int = Field(default=0, le=BPS_SCALE)

    surface = mcpsurface.build_surface(
        {**mcpsurface.KIND_MODELS, "layer": LayerWithABrokerFee}
    )
    assert surface["layer"]["brokerFee"].type == "money", "the MONEY tag was not read"
    assert surface["layer"]["amount"].type == "share_bps", "the NAME was read, not the tag"
    # And it is money all the way through the parser, not just in the label.
    assert mcpsurface.parse_value(surface["layer"]["brokerFee"], "$5,000,000") == 5_000_000


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


def test_an_int_the_surface_cannot_classify_denies_the_field_not_the_server() -> None:
    """Grant's F8 decision (2026-08-20): loud stays, TOTAL goes. This used to
    RAISE — and SURFACE is built at module scope, so one innocent count
    field added to a model bricked all 23 tools and `towerctl mcp` would
    not start until it was tagged. The field now lands in the undescribable
    table with the same instructive message, and every classifiable field
    keeps working."""

    class LineWithCount(Line):
        count: int = 0

    undescribable: dict[str, str] = {}
    surface = mcpsurface.build_surface(
        {**mcpsurface.KIND_MODELS, "line": LineWithCount}, undescribable=undescribable
    )
    assert "count" not in surface["line"]
    assert "name" in surface["line"], "the classifiable fields must survive"
    assert "tag it model.MONEY" in undescribable["line.count"], (
        "the instructive message must survive the degrade"
    )


def test_an_undescribable_field_refuses_with_its_reason(monkeypatch) -> None:
    """The auto-denial must reach the caller the same way a hand denial
    does: `denied_reason` serves it, so `program_edit_field` answers
    `denied_field` with the instructive message instead of `no_such_target`."""
    monkeypatch.setitem(
        mcpsurface.UNDESCRIBABLE, "line.count", "count is an int the surface cannot name"
    )
    assert mcpsurface.denied_reason("line", "count") == (
        "count is an int the surface cannot name"
    )


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
    """The fixture is deliberately NOT in alphabetical order.

    It was `["NJ", "NY"]` until 2026-08-19 — already sorted — so the one way
    this invariant actually breaks, a `sorted()` slipped into `_parse_list`,
    left the test green. A verbatim-order assertion whose input is sorted
    asserts nothing about order.

    Mutation drill (2026-08-19): wrapped `_parse_list`'s return in `sorted()`.
    Failed with `assert ['CT', 'NJ', 'NY'] == ['NY', 'CT', 'NJ']`. Restored.
    """
    entry = mcpsurface.SURFACE["layer"]["states"]
    assert mcpsurface.parse_value(entry, ["NY", "CT", "NJ"]) == ["NY", "CT", "NJ"]
    assert mcpsurface.parse_value(entry, "NY, CT, NJ") == ["NY", "CT", "NJ"]


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
    for shown in (mcpsurface.render_value(entry, Placement.BOUND),
                  mcpsurface.expecting_literal(entry, Placement.BOUND),
                  mcpsurface.mismatch_message(entry, "program", Placement.BOUND, "proposed")):
        assert "Placement." not in shown and "<" not in shown


def test_the_mismatch_message_can_be_pasted_straight_back() -> None:
    """The literal is JSON, and the value it decodes to is the one the field
    takes. It used to be `expecting='$5,000,000'` — single quotes, which are
    not JSON — so a client that did as it was told sent the seven characters
    `'$5,00...'` and got `cannot parse money value: "'$5,000,000'"` back. This
    test proved nothing about that, because it re-typed the value by hand
    instead of decoding the literal the message actually offers.

    Mutation drill (2026-08-19): restored the old `f"'{render_value(...)}'"`
    branch in `expecting_literal`. Failed on the message equality with
    `- expecting="$5,000,000"` / `+ expecting='$5,000,000'`. Restored. (The
    two tests below take the same mutation the other way round, through
    `json.loads`, so the quoting is checked as syntax and not just as text.)
    """
    entry = mcpsurface.SURFACE["layer"]["attach"]
    message = mcpsurface.mismatch_message(entry, "layer 'xs-1'", 5_000_000, 2_000_000)
    assert message == (
        "layer 'xs-1' attach is $5,000,000, not the $2,000,000 you expected — "
        'pass expecting="$5,000,000" to overwrite it, or call program_read.'
    )
    assert mcpsurface.parse_expecting(entry, json.loads(_offered(message))) == 5_000_000


def _offered(message: str) -> str:
    """The `expecting=` literal EXACTLY as the message prints it, quotes and
    all. Nothing is stripped: the whole point is that what the client is shown
    is what the client can send, and a test that tidied the literal up first
    would be testing a string no refusal ever emitted."""
    found = re.search(r'expecting=("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\S+?)(?: |$)', message)
    assert found is not None, message
    return found.group(1)


def test_a_refused_literal_pasted_back_ends_the_loop() -> None:
    """The text case end to end, in the shape the client experiences it:
    refusal in, literal out, literal back in, no refusal.

    Mutation drill (2026-08-19): restored the single-quote branch. Failed with
    `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
    — and with the decode taken out, the second `mismatch_message` came back
    byte-identical to the first, which is the loop itself. Restored.
    """
    entry = mcpsurface.SURFACE["program"]["currency"]
    first = mcpsurface.mismatch_message(entry, "program", "USD", "ZZZ")
    retried = json.loads(_offered(first))
    assert mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, retried), "USD")


def test_expecting_is_compared_on_the_normalised_value() -> None:
    """Mutation drill (2026-08-19): made `compare_values` compare the RAW wire
    values. This test failed on the '5m' case. Restored."""
    entry = mcpsurface.SURFACE["layer"]["attach"]
    for wire in ("5m", "$5,000,000", 5_000_000, "5000000"):
        assert mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, wire), 5_000_000)
    assert not mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, "2m"), 5_000_000)


def test_null_and_the_empty_string_are_two_different_expectations() -> None:
    """`null` expects UNSET; `""` expects the empty string, which is a value a
    text field can hold. They used to be the same question and it had no
    answer: `parse_expecting` refused `""` outright while the mismatch message
    for a field holding `""` offered `pass expecting=""`, so the two refusals
    named each other and the field could never be written.

    Mutation drill (2026-08-19): restored the `raise BadValue("send null, not
    an empty string, …")` branch in `parse_expecting`. Failed with
    `towerkit.mcpsurface.BadValue: send null, not an empty string, to expect
    policyNumber to be unset` on the first assertion. Restored.
    """
    entry = mcpsurface.SURFACE["layer"]["policyNumber"]
    assert mcpsurface.parse_expecting(entry, None) is None
    assert mcpsurface.compare_values(entry, None, None)
    # Neither one answers for the other, and the mismatch says which is which.
    assert mcpsurface.parse_expecting(entry, "") == ""
    assert not mcpsurface.compare_values(entry, mcpsurface.parse_expecting(entry, ""), None)
    assert not mcpsurface.compare_values(entry, None, "")
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


def test_a_participant_is_published_writable_and_says_what_it_does_not_check() -> None:
    """Both fields were published with no denial reason from the day the
    surface shipped, and every write failed inside `edit.set_field` — an
    advertised capability that always refused. They are writable now, and the
    one thing a caller could reasonably expect and not get — a veto on the sum
    — is stated rather than left to be discovered.

    Mutation drill (2026-08-19): renamed the `participant.share_bps` key in
    `GUARDS` so no entry picked it up. Failed with `KeyError: 'guard'` on the
    share_bps payload. Restored.
    """
    fields = mcpsurface.describe("participant")["kinds"]["participant"]["fields"]
    assert set(fields) == {"carrier", "share_bps"}
    assert mcpsurface.denied_reason("participant", "carrier") is None
    assert mcpsurface.denied_reason("participant", "share_bps") is None
    assert "not vetoed" in fields["share_bps"]["guard"]
    assert "layer-oversigned" in fields["share_bps"]["guard"]


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
    """`appliesTo` reaches `_check_lines`, and it reaches it at the CHOKE
    POINT rather than through a kind-wide setter.

    It used to get there because `_SETTERS` routed `retention.*` and
    `sublimit.*` into `edit.edit_retention` / `edit.edit_sublimit` — whose
    keyword parameters are a hand-written copy of the two models, so every
    other field of both kinds paid for this one check with a `TypeError` the
    moment a model grew. The check is a rule about ONE field now
    (`edit._NORMALISERS`), so it is asserted by DRIVING it, not by reading a
    setter name off the entry.

    Mutation drill (2026-08-19): removed the `("retention", "appliesTo")` row
    from `edit._NORMALISERS`. Failed with `DID NOT RAISE <class 'KeyError'>` —
    `['nope']` was written into the file. Restored. Deleting the
    `("sublimit", "appliesTo")` row failed the sublimit half the same way, and
    taking the `_NORMALISERS` call out of `set_field` failed both.
    """
    for kind in ("retention", "sublimit"):
        assert mcpsurface.SURFACE[kind]["appliesTo"].setter is None
    assert mcpsurface.SURFACE["named_limit"]["amount"].setter is None

    program = Program(
        insured="Atomic", program="Casualty", placement="bound",
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        retentions=[Retention(appliesTo=["gl"], type="sir", amount=100_000)],
        sublimits=[Sublimit(name="Flood", amount=1, appliesTo=["gl"])],
    )
    for kind in ("retention", "sublimit"):
        entry = mcpsurface.SURFACE[kind]["appliesTo"]
        with pytest.raises(KeyError, match="unknown line"):
            mcpsurface.apply(program, entry, None, 0, ["nope"])
    assert program.retentions[0].applies_to == ["gl"], "nothing was written"
    assert program.sublimits[0].applies_to == ["gl"]

    # And the rest of `_check_lines`: duplicates dropped, order kept. This is
    # what makes it a NORMALISER and not a guard, and the only reason the
    # value cannot simply be checked and passed through.
    program.lines.append(Line(id="auto", name="Auto"))
    mcpsurface.apply(
        program, mcpsurface.SURFACE["retention"]["appliesTo"], None, 0, ["auto", "gl", "auto"]
    )
    assert program.retentions[0].applies_to == ["auto", "gl"]


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


# --- the advertised capability, DRIVEN ----------------------------------------
#
# `describe()` is the only published statement of what can be written, and
# until 2026-08-19 three whole kinds could not honour it. `mcpsurface._SETTERS`
# routed every field of `retention`, `sublimit` and `named_limit` through
# `edit.edit_retention`, `edit.edit_sublimit` and `edit.edit_named_limit` as
# `**{python_name: value}` — and those three functions' keyword parameters are
# a hand-written enumeration of the three models. A field added to `Retention`
# was advertised `{'type': 'text', 'required': False, 'clearable': True}` and
# came back, over a real client, as `edit_retention() got an unexpected keyword
# argument 'broker_ref'`.
#
# Nothing in the suite could see it. Every test above INSPECTS the surface —
# the entry's type, its setter name, its bounds — and an entry is a promise,
# not a capability. So these two DRIVE it: every field of every kind is
# written, and the value is read back off the model.


def _full_program() -> Program:
    """One row of every kind, so every advertised field has somewhere to land —
    including the two containers (`program.render`, `layer.period`), which are
    reachable only when the parent object exists."""
    return Program(
        insured="Atomic Corp",
        program="Casualty",
        placement="bound",
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        render=RenderSettings(),
        lines=[Line(id="gl", name="General Liability"), Line(id="auto", name="Auto")],
        layers=[
            Layer(
                id="primary",
                name="Primary",
                appliesTo=["gl"],
                attach=0,
                limit=5_000_000,
                period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
                participants=[Participant(carrier="Zurich", share_bps=BPS_SCALE)],
                namedLimits=[NamedLimit(name="Sexual Abuse", amount=1_000_000)],
            )
        ],
        retentions=[Retention(appliesTo=["gl"], type="sir", amount=250_000)],
        sublimits=[Sublimit(name="Flood", amount=25_000_000, appliesTo=["gl"])],
    )


def _within(entry: mcpsurface.Entry, value: int) -> bool:
    low, high = entry.minimum, entry.maximum
    return (low is None or value >= low) and (high is None or value <= high)


def _holdable(entry: mcpsurface.Entry, line_ids: list[str]) -> list[object]:
    """Every value this entry's ADVERTISED type and its OWN published bounds
    say the field can hold — generated, never listed.

    A hand-written case list cannot fail for a value nobody thought of, which
    is exactly how two holes in the offered `expecting` literal survived a
    green suite: the empty string (which `parse_expecting` refused while
    `expecting_literal` offered it) and a negative amount (which
    `expecting_literal` offered as '-$5' and `parse_money` refused). Both are
    generated here from what the surface itself publishes.
    """
    if entry.type == "text":
        low, high = entry.min_length or 0, entry.max_length
        sample = "O'Neill \"1st\" Excess"
        if high is not None:
            sample = sample[:high]
        sample += "x" * max(0, low - len(sample))
        return ([""] if low == 0 else []) + [sample]
    if entry.type == "money":
        # -5 only where the MODEL allows one: `layer.limit` carries no `ge` on
        # purpose, and `attach` carries `ge=0`.
        return [value for value in (0, 2_340_000, -5) if _within(entry, value)]
    if entry.type == "share_bps":
        return [value for value in (0, 3_500, BPS_SCALE) if _within(entry, value)]
    if entry.type == "date":
        return [date(2026, 6, 1), date(2027, 1, 1)]
    if entry.type == "enum":
        return list(entry.values or ())
    if entry.type == "bool":
        return [True, False]
    return [[], list(line_ids)]


def _writable(entry: mcpsurface.Entry, line_ids: list[str]) -> object:
    """One value the MODEL will also take: `appliesTo` is `min_length=1` and
    `name` is `min_length=1`, so the empty forms `_holdable` generates are
    states to EXPECT, not values to write."""
    values = _holdable(entry, line_ids)
    if entry.type in ("text", "list_of_strings"):
        return values[-1]
    if entry.type == "money":
        return next((v for v in (2_340_000, 0) if _within(entry, v)), values[0])
    return values[0]


def _address(kind: str, program: Program) -> tuple[str | None, int | None]:
    """The address for row 0 of a kind, from `mcpsurface.TARGET` — the same
    table `describe()` publishes to the caller."""
    wants = mcpsurface.TARGET[kind]
    target = None
    if "target" in wants:
        target = program.lines[0].id if kind == "line" else program.layers[0].id
    return target, (0 if "index" in wants else None)


def _row(program: Program, kind: str, target: str | None, index: int | None) -> object:
    """Where the row lives, stated INDEPENDENTLY of `edit._entity`: a test that
    reads back through the addressing it is testing cannot notice a write that
    landed on the wrong row."""
    if kind == "program":
        return program
    if kind == "line":
        return next(line for line in program.lines if line.id == target)
    if kind == "retention":
        return program.retentions[index or 0]
    if kind == "sublimit":
        return program.sublimits[index or 0]
    layer = next(ly for ly in program.layers if ly.id == target)
    if kind == "layer":
        return layer
    if kind == "participant":
        return layer.participants[index or 0]
    return layer.named_limits[index or 0]


def _stored(row: object, entry: mcpsurface.Entry) -> object:
    value: object = row
    for name in entry.path:
        value = getattr(value, name)
    return value


def _drive(surface: dict[str, dict[str, mcpsurface.Entry]], program: Program) -> tuple[
    list[str], dict[str, str]
]:
    """Write every field the surface advertises. Returns what landed and what a
    guard refused."""
    written: list[str] = []
    refused: dict[str, str] = {}
    for kind, fields in surface.items():
        for field, entry in fields.items():
            key = f"{kind}.{field}"
            target, index = _address(kind, program)
            value = _writable(entry, program.line_ids())
            try:
                mcpsurface.apply(program, entry, target, index, value)
            except edit.GuardRefused as exc:
                refused[key] = str(exc)
                continue
            # Re-addressed AFTER the write: setting `line.name` re-slugs the id.
            target, index = _address(kind, program)
            stored = _stored(_row(program, kind, target, index), entry)
            assert mcpsurface.compare_values(entry, stored, value), (
                f"{key} advertised writable, the write reported success, and the "
                f"model holds {stored!r} instead of {value!r}"
            )
            # The value that just landed has to be EXPRESSIBLE, or the next
            # compare-and-set against it cannot be written. Checked on real
            # stored state — which is where the enum instances and the parsed
            # dates are, rather than the wire forms `_holdable` produces.
            offered = json.loads(mcpsurface.expecting_literal(entry, stored))
            assert mcpsurface.compare_values(
                entry, mcpsurface.parse_expecting(entry, offered), stored
            ), f"{key} holds a value its own refusal could not offer back"
            written.append(key)
    return written, refused


def test_every_advertised_field_is_actually_writable() -> None:
    """The promise `describe()` makes, kept for all seven kinds.

    Mutation drill (2026-08-19): put the `"retention.*": "edit_retention"`,
    `"sublimit.*": "edit_sublimit"` and `"named_limit.*": "edit_named_limit"`
    rows back in `mcpsurface._SETTERS` with `apply`'s kwargs splat. Failed with
    `TypeError: edit_retention() got an unexpected keyword argument 'vehicle'`
    — an advertised field, on today's model, that the write path cannot take.
    Restored.
    """
    program = _full_program()
    written, refused = _drive(mcpsurface.SURFACE, program)

    total = sum(len(fields) for fields in mcpsurface.SURFACE.values())
    assert len(written) + len(refused) == total
    assert len(written) > 30, "the surface stopped producing fields; this passes vacuously"

    # A refusal is only acceptable where `describe()` warned about it, and on
    # this program exactly one field does: `states` on a dollar-limited layer.
    # An exact set rather than a floor, because a NEW guard on a field callers
    # were told is writable is a contract change someone has to look at.
    assert set(refused) == {"layer.states"}, refused
    for key in refused:
        kind, _, field = key.partition(".")
        assert mcpsurface.SURFACE[kind][field].guard is not None, (
            f"{key} refused the write with nothing in describe() warning it would"
        )


def test_a_field_the_surface_has_never_seen_is_writable_on_every_kind() -> None:
    """The half that cannot pass by reproducing today's field set.

    Adding `brokerRef`/`brokerFee` to every model is what editing `model.py`
    does, and it is the reproduction the verifier ran: `describe` advertised
    `brokerRef: {'type': 'text', 'required': False, 'clearable': True}` on
    `retention` and the write came back `edit_retention() got an unexpected
    keyword argument 'broker_ref'` over a real `mcp.client.Client`.

    Mutation drill (2026-08-19): restored the three `_SETTERS` wildcards and
    `apply`'s splat. Failed with `TypeError: edit_retention() got an unexpected
    keyword argument 'broker_ref'`. Restored. Removing only the `named_limit.*`
    row failed on `retention.brokerRef` and `sublimit.brokerRef` alone, so each
    kind is carrying its own weight here.
    """
    models = [Program, Line, Layer, Participant, Retention, Sublimit, NamedLimit]
    with ExitStack() as stack:
        for cls in models:
            stack.enter_context(
                model_field(cls, "broker_ref", str | None, Field(alias="brokerRef", default=None))
            )
            stack.enter_context(
                model_field(cls, "broker_fee", Money | None, Field(alias="brokerFee", default=None))
            )
        # The surface is a module-level derivation computed at import; editing
        # `model.py` for real recomputes it on the next start, and this is that
        # recomputation. Nothing in `mcpsurface.py` changes.
        surface = mcpsurface.build_surface()
        written, _refused = _drive(surface, _full_program())

    for kind in mcpsurface.KINDS:
        for field in ("brokerRef", "brokerFee"):
            assert f"{kind}.{field}" in written, (
                f"{kind}.{field} is advertised by describe() and the write did not land"
            )


def test_every_offered_literal_is_json_the_field_takes_back() -> None:
    """The whole surface, and every value its own published type and bounds
    say a field can hold — not a case list.

    This was nine hand-written cases, one per type, and it was the same shape
    as the money-key table already killed in `tests/`: it cannot fail for a
    value nobody listed. Two states failed and it passed anyway.

    - A text field holding `""` was PERMANENTLY UNWRITABLE over the protocol,
      on a file `towerctl validate` exits 0 on: `expecting=null` refused with
      `notes is '', not the null you expected — pass expecting=""`, and
      `expecting=""` refused with `send null, not an empty string`. The two
      refusals named each other.
    - `layer.limit` carries no `ge` deliberately, so -5 is a state it can
      hold; the mismatch offered `expecting="-$5"` and `parse_money` refuses a
      negative outright, so only the bare integer worked.

    Mutation drill (2026-08-19): restored `parse_expecting`'s `raise BadValue`
    on `""`. Failed on `program.notes` with `send null, not an empty string, to
    expect notes to be unset`. Restored. Then removed the minus-sign branch
    from `_parse_money`. Failed on `layer.limit` with `cannot parse money
    value: '-$5'`. Restored. (Both mutations leave the old nine-case list
    green, which is the point.)
    """
    line_ids = ["gl", "auto"]
    checked = 0
    for kind, fields in mcpsurface.SURFACE.items():
        for field, entry in fields.items():
            # `null` for every entry, clearable or not: a field can be unset on
            # a half-built draft and the guard has to be expressible either way.
            for value in [*_holdable(entry, line_ids), None]:
                literal = mcpsurface.expecting_literal(entry, value)
                sent = json.loads(literal)  # a refusal that is not JSON is unsendable
                back = mcpsurface.parse_expecting(entry, sent)
                assert mcpsurface.compare_values(entry, back, value), (
                    f"{kind}.{field} holding {value!r} offers expecting={literal}, "
                    f"which comes back as {back!r}"
                )
                checked += 1
    assert checked > 100, "the generator stopped producing values; this passes vacuously"


# --- the model's own numeric bounds -------------------------------------------


def test_a_money_field_refuses_what_its_model_bound_refuses_and_names_the_bound() -> None:
    """`attach` is `Money`, which is `Annotated[int, Field(ge=0), MONEY]`, and
    `value=-1` used to reach the client as pydantic's own four-line repr with a
    documentation URL in it — a refusal naming no value that would be
    accepted. This is the defect the `Program.currency` `min_length` fix closed
    for strings, left open on the numbers.

    Both spellings, because they disagreed: the string '-5' was refused
    cleanly by `parse_money` while the integer -5 went straight through to
    pydantic.

    Mutation drill (2026-08-19): made `_bounds` return `(None, None)`. Failed
    with `DID NOT RAISE` on the first assertion. Restored.
    """
    attach = mcpsurface.SURFACE["layer"]["attach"]
    assert (attach.minimum, attach.maximum) == (0, None)
    for wire in (-1, "-$1", "-1"):
        with pytest.raises(mcpsurface.BadValue, match=r"\$0 or more"):
            mcpsurface.parse_value(attach, wire)


def test_a_money_field_with_no_bound_takes_a_negative_in_either_spelling() -> None:
    """`layer.limit` carries no `ge` ON PURPOSE — positivity is a semantic rule
    `validate.py` reports so a draft stays loadable — so -5 is a state the
    field can hold, and every spelling of it has to work or the refusal that
    offers `-$5` is a loop.

    Mutation drill (2026-08-19): removed the minus-sign branch from
    `_parse_money`. Failed with `BadValue: cannot parse money value: '-$5'`.
    Restored.
    """
    limit = mcpsurface.SURFACE["layer"]["limit"]
    assert (limit.minimum, limit.maximum) == (None, None)
    assert mcpsurface.parse_value(limit, -5) == -5
    assert mcpsurface.parse_value(limit, "-$5") == -5
    assert mcpsurface.parse_value(limit, "-5m") == -5_000_000
    assert mcpsurface.render_value(limit, -5) == "-$5"


def test_the_bound_is_read_off_the_model_not_declared_here() -> None:
    """Asked about a field the surface has never seen, with a bound no money
    field in `model.py` carries — the only question a name-keyed table cannot
    answer.

    Mutation drill (2026-08-19): stopped `_entry` carrying the bound onto the
    entry. Failed with `assert (None, None) == (1000, 9000)`. Removing
    `_flatten`'s `_expanded` step instead failed the same assertion, because
    an OPTIONAL money field files its constraints one level deeper. Both
    restored.
    """

    class LayerWithAMinimumFee(Layer):
        broker_fee: Annotated[int, Field(ge=1_000, le=9_000), MONEY] | None = Field(
            alias="brokerFee", default=None
        )

    surface = mcpsurface.build_surface({**mcpsurface.KIND_MODELS, "layer": LayerWithAMinimumFee})
    fee = surface["layer"]["brokerFee"]
    assert (fee.minimum, fee.maximum) == (1_000, 9_000)
    with pytest.raises(mcpsurface.BadValue, match=r"\$1,000 to \$9,000"):
        mcpsurface.parse_value(fee, "500")
    assert mcpsurface.parse_value(fee, "5k") == 5_000
    assert surface["layer"]["brokerFee"].type == "money"


def test_an_optional_money_field_carries_the_same_bound_as_a_required_one() -> None:
    """`Money` and `Money | None` are the same annotation with the same rules;
    pydantic just files their metadata in two different places — flattened into
    `FieldInfo.metadata` when the field is required, left on the inner
    `Annotated`'s own `__metadata__` when it is optional.

    `_flatten` already knew that for the MONEY tag and not for the bounds, so
    `layer.premium` advertised no minimum at all while `layer.attach`, the same
    `Money`, advertised `ge=0`. The type was right and the bound was gone,
    which is why nothing noticed.

    Mutation drill (2026-08-19): removed the `_expanded` FieldInfo expansion
    from `_flatten`. Failed with `assert None == 0`. Restored.
    """
    premium = mcpsurface.SURFACE["layer"]["premium"]
    assert premium.minimum == 0
    with pytest.raises(mcpsurface.BadValue, match=r"\$0 or more"):
        mcpsurface.parse_value(premium, -1)
    assert mcpsurface.parse_value(premium, None) is None, "clearing still works"


def test_describe_publishes_the_numeric_bounds_it_enforces() -> None:
    """A bound the caller learns only by tripping it is a bound the caller
    trips. `limit` publishes none, which is the honest answer.

    Mutation drill (2026-08-19): removed the `bounds` block from `describe`.
    Failed with `KeyError: 'bounds'`. Restored.
    """
    fields = mcpsurface.describe("layer")["kinds"]["layer"]["fields"]
    assert fields["attach"]["bounds"] == [0, None]
    assert "bounds" not in fields["limit"]
    share = mcpsurface.describe("participant")["kinds"]["participant"]["fields"]["share_bps"]
    assert share["bounds"] == [0, BPS_SCALE]


def test_the_share_range_is_the_models_and_not_a_copy_of_it() -> None:
    """`parse_value` spelled `0 <= value <= BPS_SCALE` in its own body, which
    is `Participant.share_bps`'s `ge`/`le` written down a second time.

    Mutation drill (2026-08-19): changed `Participant.share_bps` to
    `Field(ge=0, le=5_000)` in `model.py`. This test failed on the 9000 case
    with `DID NOT RAISE`; with the copy restored in `parse_value` it would have
    passed while the model refused the write. Restored.
    """
    entry = mcpsurface.SURFACE["participant"]["share_bps"]
    assert (entry.minimum, entry.maximum) == (
        Participant.model_fields["share_bps"].metadata[0].ge,
        Participant.model_fields["share_bps"].metadata[1].le,
    )
    for refused in (-1, BPS_SCALE + 1):
        with pytest.raises(mcpsurface.BadValue, match="0 to 10000"):
            mcpsurface.parse_value(entry, refused)

def test_apply_dispatches_on_the_setter_the_entry_names(monkeypatch) -> None:
    """`_SETTERS` rows name their `edit` function, and `apply` must LOOK AT
    the name. Round six (2026-08-20): with exactly one row it hardcoded
    `edit.rename_line` behind `if entry.setter is None`, so the test above
    passed while the dispatch was fiction — the first person to add a second
    row would have their field silently applied as a line rename. Invisible
    to any test using only today's rows; this one injects a row the surface
    has never seen, the same trick that caught table six."""
    from dataclasses import replace

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        edit,
        "probe_setter",
        lambda program, target, value: calls.append((target, value)),
        raising=False,
    )
    entry = replace(mcpsurface.SURFACE["line"]["name"], setter="probe_setter")
    program = Program(
        insured="Atomic", program="Casualty", placement="bound",
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
    )
    mcpsurface.apply(program, entry, "gl", None, "Cyber")
    assert calls == [("gl", "Cyber")], "apply must call the function the row names"
    assert program.lines[0].name == "General Liability", "rename_line must NOT have run"


def test_an_enum_with_non_string_values_cannot_be_advertised() -> None:
    """`_classify` stringifies member values for the published `values` tuple.
    For an int-valued enum that advertises '1' while `parse_value`'s
    `Kind('1')` lookup can never match it — advertised-but-unwritable, the
    exact defect class this module exists to kill. All current enums are
    StrEnums, so this can only be caught by a shape the surface has never
    seen; raise at derivation time, before describe() ever lies."""
    from enum import Enum

    class Numbered(Enum):
        ONE = 1
        TWO = 2

    with pytest.raises(RuntimeError, match="non-string"):
        mcpsurface._classify("layer.numbered", Numbered, [])


def test_a_hand_denial_outranks_the_derivation_message_at_both_doors(monkeypatch) -> None:
    """Round twelve: a field in BOTH tables answered with the hand reason
    through `denied_reason` and the derivation message through `describe()`
    — two doors, two explanations. DENIED is the human's decision and wins
    at both."""
    key = "layer.id"  # any real DENIED row
    denied_reason_text = mcpsurface.DENIED[key]
    monkeypatch.setitem(mcpsurface.UNDESCRIBABLE, key, "derivation message")
    assert mcpsurface.denied_reason("layer", "id") == denied_reason_text
    listed = mcpsurface.describe("layer")["kinds"]["layer"]["denied"]["id"]
    assert listed == denied_reason_text
