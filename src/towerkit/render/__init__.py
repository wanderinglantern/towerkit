"""Renderers. Importing this package configures matplotlib safely.

The Agg backend is forced (no display needed), and a macOS 27 beta issue is
worked around: `system_profiler SPFontsDataType` reports no fonts there,
which crashes matplotlib's font enumeration at import. When that happens we
fall back to matplotlib's bundled fonts (DejaVu) — which the default themes
use anyway, and which keep rendered output identical across machines.

ascii.py itself never uses matplotlib — only this package init configures it.
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")

try:
    import matplotlib.font_manager  # noqa: F401
except KeyError:
    os.environ["MPL_IGNORE_SYSTEM_FONTS"] = "1"
    sys.modules.pop("matplotlib.font_manager", None)
    import matplotlib.font_manager  # noqa: F401
