"""Shared renderer plumbing: deterministic output, real provenance, saving.

Determinism is a product requirement (§6): two identical runs must produce
byte-identical SVG. That means a fixed hashsalt for element ids, embedded
TrueType (not Type 3) in PDF, and no wall-clock metadata anywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from matplotlib.figure import Figure

from .. import __version__  # noqa: E402
from ..theme import Theme  # noqa: E402


def rc_params(theme: Theme) -> dict[str, Any]:
    return {
        # plain strings everywhere: '$', '_' and '^' in carrier names and
        # notes must never be interpreted as mathtext
        "text.parse_math": False,
        "svg.hashsalt": "towerkit",  # stable SVG element ids
        "pdf.fonttype": 42,  # embed TrueType, not Type 3
        "svg.fonttype": "path",
        "font.family": theme.chrome.font,
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


_METADATA: dict[str, dict[str, Any]] = {
    # scrub every timestamp matplotlib would otherwise embed
    "svg": {"Date": None},
    "pdf": {"CreationDate": None},
    "png": {"Software": "towerkit"},
}


def save_figure(fig: Figure, out_dir: Path, stem: str, formats: list[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        if fmt not in _METADATA:
            raise ValueError(f"unsupported format {fmt!r} (svg, pdf, png)")
        path = out_dir / f"{stem}.{fmt}"
        fig.savefig(path, format=fmt, metadata=_METADATA[fmt], dpi=200)
        written.append(path)
    return written
