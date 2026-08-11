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


# --- parse_tower --------------------------------------------------------------

from towerkit.ingest import parse_tower  # noqa: E402
from towerkit.model import RetentionType  # noqa: E402

PASTE = """
Primary 10M — Chubb 100% — 250,000
15M xs 10M — AXA XL 60%, Sompo 40% — 180k
SIR 500k
"""


def test_parse_tower_layers() -> None:
    draft = parse_tower(PASTE, insured="Atomic Industries", program="Property")
    assert [layer.attach for layer in draft.layers] == [0, 10_000_000]
    assert draft.layers[0].limit == 10_000_000
    assert draft.layers[0].premium == 250_000
    assert draft.layers[1].participants[1].carrier == "Sompo"
    assert draft.layers[1].participants[1].share_bps == 4000
    assert draft.retentions[0].type is RetentionType.SIR
    assert draft.retentions[0].amount == 500_000


def test_parse_tower_single_carrier_means_full_share() -> None:
    draft = parse_tower("Primary 5M — Chubb")
    assert draft.layers[0].participants[0].share_bps == 10_000


def test_parse_tower_synthesizes_line_with_warning() -> None:
    draft = parse_tower("Primary 5M — Chubb", program="Casualty")
    assert draft.lines[0].name == "Casualty"
    assert any(d.code == "paste.line" for d in draft.diagnostics.warnings)


def test_parse_tower_bad_line_is_diagnostic_not_crash() -> None:
    draft = parse_tower("Primary 5M — Chubb\ntotal nonsense here")
    assert len(draft.layers) == 1
    assert any("line 2" in d.message for d in draft.diagnostics.errors)


def test_parse_tower_pipe_and_hyphen_separators() -> None:
    draft = parse_tower("5M xs 5M | Zurich 50%, Allianz 50% | 90k")
    assert draft.layers[0].signed_bps == 10_000
    assert draft.layers[0].premium == 90_000
