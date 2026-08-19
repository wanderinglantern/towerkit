"""Shared fixtures and the model-injection helper.

SAMPLE is the committed multi-line program used across the suite — real
scaffolded data, never hand-built dicts.

`model_field` lives here rather than in one test file because two of them need
it and it is the ONE way this suite asks "what happens to a field nobody has
written down yet": the serialiser, the schema, the read and the write surface
all claim to be derived, and the only honest test of a derivation is a field
the code has never seen. Two copies of it would drift, and the second copy
would be a hand-written model table by another name.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from towerkit.model import (
    Layer,
    NamedLimit,
    Participant,
    Period,
    Program,
    RenderSettings,
    Retention,
    Sublimit,
)
from towerkit.model import Line as CoverageLine

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"

# Innermost first. A pydantic model compiles its children's schemas INTO its
# own, so rebuilding `Layer` alone leaves `Program` still validating layers
# with the schema it captured at import — the new field would exist on the
# class and be missing from every instance the loader produced.
_REBUILD_ORDER = (
    Period, NamedLimit, Participant, CoverageLine, RenderSettings,
    Retention, Sublimit, Layer, Program,
)


def _rebuild() -> None:
    for cls in _REBUILD_ORDER:
        cls.model_rebuild(force=True)


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


@pytest.fixture()
def sample_copy(tmp_path: Path) -> Path:
    """A writable copy of SAMPLE inside a programs/ directory."""
    target = tmp_path / "programs"
    target.mkdir()
    shutil.copy(SAMPLE, target / "atomic-2026.json")
    return target / "atomic-2026.json"
