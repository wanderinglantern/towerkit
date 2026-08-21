"""A buffer is a deliberate uninsured band, not a hole.

Grant, 2026-08-21: "there are situations where there may be a buffer layer in
a tower that is technically uninsured, though. in the diagram, that could be
represented by a 'buffer' layer and not just a gap tho."

towerkit reported `line-gap` for such a band — a FALSE REFUSAL on a structure
that is really placed.
"""

from __future__ import annotations

import json

from test_validate import layer as plain_layer
from test_validate import make_program

from towerkit.model import Layer, Line, Participant, program_to_jsonable
from towerkit.validate import validate_program


def _buffer(**kw) -> Layer:
    base = dict(id="buf", name="Buffer", applies_to=["gl"],
                attach=5_000_000, limit=5_000_000, buffer=True)
    return Layer(**{**base, **kw})


def test_a_buffer_layer_exists_and_defaults_off() -> None:
    assert _buffer().buffer is True
    assert Layer(id="x", name="X", applies_to=["gl"], attach=0,
                 limit=1_000_000).buffer is False


def test_not_a_buffer_writes_no_key() -> None:
    """OMIT_EMPTY: adding the field changes the shape of no existing file."""
    program = make_program()
    assert "buffer" not in json.dumps(program_to_jsonable(program))


def _stack(*layers):
    return make_program(lines=[Line(id="gl", name="General Liability")],
                        layers=list(layers), retentions=[])


def _codes(program) -> set[str]:
    return {d.code for d in validate_program(program).items}


def test_a_buffer_suppresses_the_gap_it_would_otherwise_be() -> None:
    """THE POINT. Primary to $5M, nothing to $10M, excess above — reported as
    a GAP until the band could be declared."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(),
        plain_layer("xs", ["gl"], 10_000_000, 10_000_000),
    )
    assert "line-gap" not in _codes(program)


def test_the_same_stack_without_the_buffer_still_reports_the_gap() -> None:
    """The rule must not have been deleted, only made declarable."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        plain_layer("xs", ["gl"], 10_000_000, 10_000_000),
    )
    assert "line-gap" in _codes(program)


def test_a_buffer_with_a_carrier_on_it_is_refused() -> None:
    """A buffer with a carrier is a LAYER. Calling it a buffer would hide real
    cover from every total."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(participants=[Participant(carrier="Zurich", share_bps=10_000)]),
    )
    assert "buffer-participants" in _codes(program)


def test_a_buffer_with_a_premium_is_refused() -> None:
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(premium=25_000),
    )
    assert "buffer-premium" in _codes(program)
