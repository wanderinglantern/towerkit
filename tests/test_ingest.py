"""ingest: drafts, tower-text parsing, tabular rows, template round-trips."""

from datetime import date

import pytest

from towerkit.ingest import DraftProgram
from towerkit.model import Layer, Line, Participant, Period, Placement
from towerkit.validate import ProgramInvalidError


def _complete_draft() -> DraftProgram:
    draft = DraftProgram(insured="Atomic Industries", program="Property")
    draft.period = Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    draft.lines = [Line(id="cover", name="Property")]
    draft.layers = [
        Layer(
            id="primary", name="Primary", applies_to=["cover"], attach=0,
            limit=10_000_000,
            participants=[Participant(carrier="Chubb", share_bps=10_000)],
        )
    ]
    return draft


def test_complete_draft_becomes_program() -> None:
    program = _complete_draft().to_program()
    assert program.insured == "Atomic Industries"
    assert program.placement is Placement.PROPOSED
    assert program.layers[0].limit == 10_000_000


def test_draft_missing_period_refuses() -> None:
    draft = _complete_draft()
    draft.period = None
    with pytest.raises(ProgramInvalidError) as exc:
        draft.to_program()
    assert any("period" in d.message for d in exc.value.diagnostics.errors)


def test_draft_missing_insured_refuses() -> None:
    draft = _complete_draft()
    draft.insured = "  "
    with pytest.raises(ProgramInvalidError):
        draft.to_program()


def test_draft_runs_full_validation() -> None:
    draft = _complete_draft()
    draft.layers[0] = draft.layers[0].model_copy(update={"applies_to": ["nope"]})
    with pytest.raises(ProgramInvalidError):
        draft.to_program()
