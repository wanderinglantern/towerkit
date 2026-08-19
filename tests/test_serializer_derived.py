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

import json
import shutil
from pathlib import Path

import pytest
from conftest import model_field
from pydantic import Field

from towerkit.model import (
    Layer,
    Money,
    dumps_program,
    load_program,
    loads_program,
    money_disk_keys,
)

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


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

        # Decoded, not matched as a substring: '"brokerFee": 25000' is IN
        # '"brokerFee": 25000.0', and re-reading through pydantic coerces the
        # float back to an int losslessly — so both of the obvious assertions
        # pass while the FILE holds a float. The file is what a broker keeps.
        written = json.loads(text)["layers"][0]["brokerFee"]
        assert isinstance(written, int) and not isinstance(written, bool), (
            f"money reached the file as {type(written).__name__}: {written!r}"
        )
        assert written == 25_000

        assert loads_program(text).layers[0].broker_fee == 25_000


def test_a_new_money_field_joins_the_never_a_float_guard() -> None:
    """`money_disk_keys` is DERIVED, and this is the test that dies when it is
    not.

    The canonical never-a-float guard used to carry its own five-key list, so
    it was blind to every money field added after it was written — which is how
    `brokerFee` reached the file as `25000.0` with the whole suite green. A
    hand-written table in `tests/` rots exactly the way one in `src/` does.
    """
    assert "brokerFee" not in money_disk_keys()
    with model_field(Layer, "broker_fee", Money | None, Field(alias="brokerFee", default=None)):
        assert "brokerFee" in money_disk_keys(), (
            "a money field the derivation has never seen is not covered — "
            "money_disk_keys has become a list again"
        )


def test_a_new_model_field_is_writable_over_mcp_with_no_connector_edit(tmp_path) -> None:
    """The verifier's reproduction, end to end. `_program_edit_field` returned
    a write_ref and an empty `errors` list for a value that reached neither the
    file nor the next `program_read`.

    Mutation drill (2026-08-19), for the changed assertion: put
    `validate_program` back in `mcpserver._written_diagnostics`. Failed with
    `AssertionError: assert [] == ['schema']`. Restored.
    """
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
            # NOT `errors == []`. This test adds the field to the MODEL only,
            # so the packaged `program.schema.json` — which forbids additional
            # properties — has never heard of `brokerRef`, and `_write` now runs
            # the schema pass and says so. That report is the 2026-08-19 repair,
            # not a regression: a real field addition runs
            # `tools/sync_schema.py` (and `test_conventions.py` fails until it
            # does), which is the half a monkeypatched model cannot do. What
            # this test is about is that the write LANDS with no connector edit,
            # so the assertion is that nothing else went wrong.
            assert [d["code"] for d in out["errors"]] == ["schema"]
            assert "brokerRef" in out["errors"][0]["message"]

            assert '"brokerRef"' in (root / "atomic-2026.json").read_text(encoding="utf-8")
            after = mcpserver._program_read(programs, "atomic-2026")
            assert after["layers"][0]["brokerRef"] == "atomic-2026"
