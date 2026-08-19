"""The serialiser is DERIVED from `model.py`, like the read and the surface.

The branch's whole claim is "add a field to `model.py` and it is writable with
no MCP edit". It was false end to end, and it failed with a success receipt:
`program_to_jsonable` was a THIRD hand-written field table, so a write reached
the in-memory model, the response said `{"wrote": ..., "errors": []}`, and the
value was in neither the file nor the next read.

The guard that was supposed to catch this ran backwards. `_ordered` computed
`set(raw) - set(keys)` over the HAND-BUILT dict, so it could only see a key
added to the hand-written dict — never one missing from it, which is the only
failure that ever actually happens.

These tests add a field to a model at runtime, exactly as editing `model.py`
would, and follow the value all the way to the bytes on disk and back.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from towerkit.model import (
    Layer,
    Money,
    NamedLimit,
    Participant,
    Period,
    Program,
    RenderSettings,
    Retention,
    Sublimit,
    dumps_program,
    load_program,
    loads_program,
)
from towerkit.model import Line as CoverageLine

# Innermost first. A pydantic model compiles its children's schemas INTO its
# own, so rebuilding `Layer` alone leaves `Program` still validating layers
# with the schema it captured at import — the new field would exist on the
# class and be missing from every instance the loader produced.
_REBUILD_ORDER = (
    Period, NamedLimit, Participant, CoverageLine, RenderSettings,
    Retention, Sublimit, Layer, Program,
)

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


@contextmanager
def model_field(
    cls: type[BaseModel], name: str, annotation: object, field: FieldInfo
) -> Iterator[None]:
    """Add a field to a pydantic model for the duration of a test.

    This is what editing `model.py` does, minus the edit: the same
    `__pydantic_fields__` entry, in the same declaration order (last), rebuilt
    the same way. Anything derived from the model sees it; anything
    hand-written does not. Restored in a `finally` because these classes are
    process-global and every other test in the session shares them.
    """
    original = dict(cls.__pydantic_fields__)
    cls.__pydantic_fields__[name] = FieldInfo.from_annotated_attribute(annotation, field)
    _rebuild()
    try:
        yield
    finally:
        cls.__pydantic_fields__.clear()
        cls.__pydantic_fields__.update(original)
        _rebuild()


def _rebuild() -> None:
    for cls in _REBUILD_ORDER:
        cls.model_rebuild(force=True)


def test_a_new_model_field_reaches_the_file_and_comes_back() -> None:
    """THE load-bearing one. A field added to `Layer` must be emitted under its
    alias and survive a re-read. Under the hand-written table it was dropped in
    silence — the write "succeeded" and the file never changed."""
    with model_field(Layer, "broker_ref", str | None, Field(alias="brokerRef", default=None)):
        program = load_program(SAMPLE)
        program.layers[0].broker_ref = "atomic-2026"

        text = dumps_program(program)
        assert '"brokerRef": "atomic-2026"' in text, "the new field never reached the file"

        assert loads_program(text).layers[0].broker_ref == "atomic-2026"


def test_a_new_optional_field_is_omitted_while_unset() -> None:
    """The other half: derived does not mean chatty. An unset optional emits no
    key, so adding a field to the model cannot make every stored program dirty
    on its next save — the followsUnderlying/statutory precedent."""
    original = SAMPLE.read_text(encoding="utf-8")
    with model_field(Layer, "broker_ref", str | None, Field(alias="brokerRef", default=None)):
        assert dumps_program(loads_program(original)) == original


def test_a_new_money_field_round_trips_as_an_integer() -> None:
    """`Money` is `Annotated[int, ...]`; a derivation that dumped through a
    generic JSON encoder could hand back a float and break the "money is never
    a float" contract on a field nobody listed."""
    with model_field(Layer, "broker_fee", Money | None, Field(alias="brokerFee", default=None)):
        program = load_program(SAMPLE)
        program.layers[0].broker_fee = 25_000

        text = dumps_program(program)
        assert '"brokerFee": 25000' in text

        assert loads_program(text).layers[0].broker_fee == 25_000


def test_a_new_model_field_is_writable_over_mcp_with_no_connector_edit(tmp_path) -> None:
    """The verifier's reproduction, end to end. `_program_edit_field` returned
    a write_ref and an empty `errors` list for a value that reached neither the
    file nor the next `program_read`."""
    from towerkit import mcpserver, mcpsurface

    root = tmp_path / "programs"
    root.mkdir()
    shutil.copy(SAMPLE, root / "atomic-2026.json")

    with model_field(Layer, "broker_ref", str | None, Field(alias="brokerRef", default=None)):
        # The surface is a module-level derivation computed at import; editing
        # `model.py` for real recomputes it on the next start, and this is that
        # recomputation. Nothing in `mcpserver.py` or `mcpsurface.py` changes.
        rebuilt = mcpsurface.build_surface()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(mcpsurface, "SURFACE", rebuilt)

            programs = mcpserver.Programs([root])
            mcpserver._program_read(programs, "atomic-2026")
            out = mcpserver._program_edit_field(
                programs,
                "atomic-2026",
                "layer",
                "brokerRef",
                "atomic-2026",
                None,
                target=load_program(root / "atomic-2026.json").layers[0].id,
            )
            assert out["errors"] == []

            assert '"brokerRef"' in (root / "atomic-2026.json").read_text(encoding="utf-8")
            after = mcpserver._program_read(programs, "atomic-2026")
            assert after["layers"][0]["brokerRef"] == "atomic-2026"
