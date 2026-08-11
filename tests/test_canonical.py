"""Canonical serialisation: load + save with no edits produces zero diff."""

from pathlib import Path

from towerkit.model import (
    Layer,
    Participant,
    Program,
    dumps_program,
    load_program,
    loads_program,
)

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def test_sample_round_trips_byte_identical() -> None:
    original = SAMPLE.read_text(encoding="utf-8")
    assert dumps_program(loads_program(original)) == original


def test_double_round_trip_is_fixed_point() -> None:
    once = dumps_program(load_program(SAMPLE))
    assert dumps_program(loads_program(once)) == once


def test_shares_survive_round_trip_including_thirds() -> None:
    program = load_program(SAMPLE)
    xs_pr = next(layer for layer in program.layers if layer.id == "xs-pr")
    assert [p.share_bps for p in xs_pr.participants] == [3334, 3333, 3333]
    reloaded = loads_program(dumps_program(program))
    xs_pr2 = next(layer for layer in reloaded.layers if layer.id == "xs-pr")
    assert [p.share_bps for p in xs_pr2.participants] == [3334, 3333, 3333]


def test_money_is_never_float_in_output() -> None:
    text = dumps_program(load_program(SAMPLE))
    for line in text.splitlines():
        for key in ('"attach"', '"limit"', '"premium"', '"amount"', '"aggregate"'):
            if key in line:
                value = line.split(":", 1)[1].strip().rstrip(",")
                assert "." not in value, f"money serialised as float: {line.strip()}"


def test_full_share_serialises_as_integer_one() -> None:
    program = Program.model_validate(
        {
            "insured": "X",
            "program": "Y",
            "placement": "bound",
            "period": {"start": "2026-01-01", "end": "2027-01-01"},
            "lines": [{"id": "gl", "name": "GL"}],
            "layers": [
                Layer(
                    id="l1",
                    name="Primary",
                    applies_to=["gl"],
                    attach=0,
                    limit=1_000_000,
                    participants=[Participant(carrier="Zurich", share_bps=10_000)],
                ).model_dump(by_alias=True)
            ],
        }
    )
    assert '"share": 1\n' in dumps_program(program) or '"share": 1,' in dumps_program(program)


def test_unicode_survives() -> None:
    program = load_program(SAMPLE)
    program2 = loads_program(dumps_program(program))
    assert program2.insured == program.insured
