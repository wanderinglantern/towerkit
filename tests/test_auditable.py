"""`Layer.auditable` — does the carrier true this policy's premium up at expiry?

Workers' compensation and general liability normally do: the deposit premium is
an estimate against payroll or sales, and the audit at expiry settles the
difference. Property and most excess layers do not. Whether a renewal brings an
audit with it is something a broker needs to see WITHOUT opening the policy,
and until 2026-08-21 the file could not say it at all.

It is a recorded fact and nothing more — no diagram behaviour, no total, no
validation rule. What this file asserts is that it reached every door: the
model, the schema, the derived write surface and the editor. A field that
exists and is unreachable is the "built but not accessible" class, and it has
shipped here before.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
from test_validate import make_program  # the suite's own minimal program

import towerkit
from towerkit import edit, mcpsurface, validate
from towerkit.model import (
    Layer,
    Line,
    Participant,
    program_from_jsonable,
    program_to_jsonable,
)


def _wc_layer(**kw) -> Layer:
    """WC Part A as it really is: statutory, so limit 0 is legal and the
    program validates for reasons that have nothing to do with this field."""
    base = dict(
        id="wc", name="Workers Compensation", applies_to=["wc"],
        attach=0, limit=0, statutory=True,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
    )
    return Layer(**{**base, **kw})


def _wc_program(**kw):
    return make_program(
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=[_wc_layer(**kw)],
        retentions=[],
    )


class TestTheField:
    def test_it_defaults_to_not_auditable(self) -> None:
        assert _wc_layer().auditable is False

    def test_not_auditable_writes_no_key(self) -> None:
        """OMIT_EMPTY, so adding the field changed the shape of no existing
        file — a program nobody has touched writes exactly what it wrote
        before, with no new key and no reordering."""
        assert "auditable" not in json.dumps(program_to_jsonable(_wc_program()))

    def test_auditable_round_trips_through_the_file(self) -> None:
        wire = program_to_jsonable(_wc_program(auditable=True))
        assert wire["layers"][0]["auditable"] is True
        assert program_from_jsonable(wire).layers[0].auditable is True

    def test_the_schema_accepts_it(self) -> None:
        """`additionalProperties: false` at nine sites — a field the schema
        does not know about makes the file towerkit's OWN writer produced fail
        `towerctl validate` while the MCP write reports no errors."""
        schema = json.loads(
            (resources.files("towerkit") / "schema" / "program.schema.json").read_text()
        )
        prop = schema["$defs"]["layer"]["properties"]["auditable"]
        assert prop["type"] == "boolean"
        assert prop.get("description"), "a broker reads this; prose is the human's half"

    def test_a_written_file_still_validates(self) -> None:
        assert not validate.validate_program(_wc_program(auditable=True)).errors


class TestEveryDoorReachesIt:
    def test_the_write_surface_picked_it_up(self) -> None:
        """DERIVED from the model, so this needed no hand-listing — which is
        the point of the seam. Asserted anyway, because the derivation is the
        claim, and bookkit builds its own editors on top of this entry."""
        entry = mcpsurface.SURFACE["layer"]["auditable"]
        assert entry.type == "bool"
        assert entry.required is False

    @pytest.mark.parametrize("flag", [True, False])
    def test_it_writes_through_the_scalar_choke_point(self, flag: bool) -> None:
        """`set_field`, not a setattr — that is what makes the write validated,
        undoable, and the same write every surface makes."""
        program = _wc_program(auditable=not flag)
        edit.set_field(program, "layer", "auditable", flag, "wc")
        assert program.layers[0].auditable is flag

    def test_the_wire_refuses_a_coerced_string(self) -> None:
        """A bool field takes a bool. The surface says so in the field's own
        lexicon rather than storing a truthy "no"."""
        entry = mcpsurface.SURFACE["layer"]["auditable"]
        with pytest.raises(mcpsurface.BadValue):
            mcpsurface.parse_value(entry, "no")

    def test_the_editor_renders_a_checkbox_for_it(self) -> None:
        """towerkit's TUI layer sheet is HAND-BUILT, not derived from SURFACE,
        so a new field is reachable by MCP and invisible in the editor unless
        somebody adds it — the "built but not accessible" class. Both halves
        are needed: the widget, and the handler that commits it."""
        source = (
            Path(towerkit.__file__).parent / "tui" / "screens" / "editor.py"
        ).read_text()
        assert 'id="f-layer-auditable"' in source
        assert 'wid == "f-layer-auditable"' in source

    def test_the_checkbox_sits_with_the_policy_number(self) -> None:
        """Grouping is information (the data-entry rules): auditable is a fact
        about the POLICY, so it belongs beside the policy number and period,
        not among attach/limit/statutory, which are facts about the cover."""
        source = (
            Path(towerkit.__file__).parent / "tui" / "screens" / "editor.py"
        ).read_text()
        policy = source.index('id="f-layer-policy"')
        auditable = source.index('id="f-layer-auditable"')
        applies = source.index('Label("Applies to"')
        assert policy < auditable < applies


class TestItChangesNothingElse:
    def test_a_dollar_limited_layer_may_be_auditable_too(self) -> None:
        """The statutory case is covered above (the WC Part A these helpers
        build). GL is the other audited line and carries a real limit, so the
        field cannot be quietly tied to `statutory` the way `states` is."""
        program = make_program(layers=[
            Layer(
                id="primary", name="Primary", applies_to=["gl"], attach=0,
                limit=2_000_000, auditable=True,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            )
        ])
        assert not validate.validate_program(program).errors
        assert program.layers[0].auditable is True

    def test_it_raises_no_diagnostic_of_its_own(self) -> None:
        codes = {d.code for d in validate.validate_program(_wc_program(auditable=True)).items}
        assert not [c for c in codes if "auditable" in c]
