"""Shared renderer plumbing: deterministic output, real provenance, saving.

Determinism is a product requirement (§6): two identical runs must produce
byte-identical SVG. That means a fixed hashsalt for element ids, embedded
TrueType (not Type 3) in PDF, and no wall-clock metadata anywhere.
"""

from __future__ import annotations

import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

from matplotlib import font_manager
from matplotlib.figure import Figure

from .. import __version__
from ..theme import Theme

# Bundled Noto Sans/Serif (SIL OFL, see fonts/OFL.txt): registered explicitly
# so themed output is byte-identical on any machine, with or without system
# font access.
for _entry in resources.files("towerkit").joinpath("fonts").iterdir():
    if _entry.name.endswith(".ttf"):
        with resources.as_file(_entry) as _font_path:
            font_manager.fontManager.addfont(str(_font_path))


def rc_params(theme: Theme) -> dict[str, Any]:
    return {
        # plain strings everywhere: '$', '_' and '^' in carrier names and
        # notes must never be interpreted as mathtext
        "text.parse_math": False,
        "svg.hashsalt": "towerkit",  # stable SVG element ids
        "pdf.fonttype": 42,  # embed TrueType, not Type 3
        "svg.fonttype": "path",
        # an explicit family list enables per-glyph fallback: the bundled
        # Noto LGC subset has no arrows (→), DejaVu does
        "font.family": [theme.chrome.font, "DejaVu Sans"],
        "figure.facecolor": theme.chrome.background,
        "savefig.facecolor": theme.chrome.background,
    }


def provenance() -> str:
    """The actual git state, never a hardcoded SHA. No timestamps."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=5,
        )
        if sha.returncode != 0:
            return f"towerkit {__version__} · unversioned"
        rev = sha.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=5,
        )
        marker = "+dirty" if dirty.stdout.strip() else ""
        return f"towerkit {__version__} · {rev}{marker}"
    except (OSError, subprocess.TimeoutExpired):
        return f"towerkit {__version__} · unversioned"


def _metadata(fmt: str) -> dict[str, Any]:
    """Timestamps scrubbed; provenance (version, git SHA, dirty marker) is
    embedded here instead of drawn on the chart."""
    stamp = provenance()
    table: dict[str, dict[str, Any]] = {
        "svg": {"Date": None, "Creator": stamp},
        "pdf": {"CreationDate": None, "Creator": stamp},
        "png": {"Software": stamp},
    }
    return table[fmt]


def save_figure(fig: Figure, out_dir: Path, stem: str, formats: list[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        if fmt not in ("svg", "pdf", "png"):
            raise ValueError(f"unsupported format {fmt!r} (svg, pdf, png)")
        path = out_dir / f"{stem}.{fmt}"
        fig.savefig(path, format=fmt, metadata=_metadata(fmt), dpi=200)
        written.append(path)
    return written
