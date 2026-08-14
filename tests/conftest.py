"""Shared fixtures. SAMPLE is the committed multi-line program used across
the suite — real scaffolded data, never hand-built dicts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


@pytest.fixture()
def sample_copy(tmp_path: Path) -> Path:
    """A writable copy of SAMPLE inside a programs/ directory."""
    target = tmp_path / "programs"
    target.mkdir()
    shutil.copy(SAMPLE, target / "atomic-2026.json")
    return target / "atomic-2026.json"
