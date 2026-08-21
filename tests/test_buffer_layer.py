"""A buffer is a deliberate uninsured band, not a hole.

Grant, 2026-08-21: "there are situations where there may be a buffer layer in
a tower that is technically uninsured, though. in the diagram, that could be
represented by a 'buffer' layer and not just a gap tho."

towerkit reported `line-gap` for such a band — a FALSE REFUSAL on a structure
that is really placed.
"""

from __future__ import annotations

import json

from test_validate import make_program

from towerkit.model import Layer, program_to_jsonable


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
