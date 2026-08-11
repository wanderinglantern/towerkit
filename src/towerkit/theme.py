"""Theme loading and carrier colour assignment.

Colour is three separate concerns in three separate places:
- chrome:          the house brand (background, ink, grid, accents)
- carrierPalette + carriers: market identity — categorical, pinned or assigned
- retentionFills:  one tonal family, never a carrier colour
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Chrome:
    background: str = "#FFFFFF"
    ink: str = "#1A1A2E"
    muted: str = "#6B7280"
    accent: str = "#0F4C81"
    grid: str = "#D7DCE3"
    zero_line: str = "#1A1A2E"
    no_cover: str = "#F2F3F5"
    unplaced: str = "#9AA3AF"
    font: str = "DejaVu Sans"
    title_font: str | None = None  # headlines; falls back to `font`


@dataclass(frozen=True)
class Theme:
    name: str
    chrome: Chrome
    carrier_palette: tuple[str, ...]
    pinned_carriers: dict[str, str] = field(default_factory=dict)
    retention_fills: dict[str, str] = field(default_factory=dict)

    def carrier_colours(self, carriers: list[str]) -> dict[str, str]:
        """Assign palette colours with no carrier list to maintain.

        Each carrier's name hashes to a preferred palette slot (md5 — Python's
        hash() is salted per process), so the same carrier wears the same
        colour in every program on every machine. Within one program,
        collisions probe deterministically to the next free slot. Explicit
        pins in the theme file still win when present."""
        n = len(self.carrier_palette)
        out: dict[str, str] = {}
        taken: set[int] = set()
        in_use = {
            self.pinned_carriers[c] for c in carriers if c in self.pinned_carriers
        }
        for carrier in carriers:
            pinned = self.pinned_carriers.get(carrier)
            if pinned is not None:
                out[carrier] = pinned
                continue
            preferred = self._preferred_slot(carrier)
            slot = preferred
            for offset in range(n):
                slot = (preferred + offset) % n
                if slot not in taken and self.carrier_palette[slot] not in in_use:
                    break
            taken.add(slot)
            out[carrier] = self.carrier_palette[slot]
            in_use.add(self.carrier_palette[slot])
        return out

    def _preferred_slot(self, carrier: str) -> int:
        digest = hashlib.md5(carrier.encode("utf-8")).hexdigest()
        return int(digest, 16) % len(self.carrier_palette)

    def retention_fill(self, retention_type: str) -> str:
        return self.retention_fills.get(retention_type, "#DDD8C9")


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of #RRGGBB, in [0, 1]."""
    hex_colour = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(hex_colour[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_text(face: str, light: str, dark: str) -> str:
    """Pick the readable label colour for a block: light text on dark fills,
    dark text on light fills (sky blues, golds)."""
    return light if relative_luminance(face) < 0.40 else dark


def _theme_from_jsonable(data: dict[str, Any]) -> Theme:
    chrome_raw = data.get("chrome", {})
    chrome = Chrome(
        background=chrome_raw.get("background", Chrome.background),
        ink=chrome_raw.get("ink", Chrome.ink),
        muted=chrome_raw.get("muted", Chrome.muted),
        accent=chrome_raw.get("accent", Chrome.accent),
        grid=chrome_raw.get("grid", Chrome.grid),
        zero_line=chrome_raw.get("zeroLine", Chrome.zero_line),
        no_cover=chrome_raw.get("noCover", Chrome.no_cover),
        unplaced=chrome_raw.get("unplaced", Chrome.unplaced),
        font=chrome_raw.get("font", Chrome.font),
        title_font=chrome_raw.get("titleFont"),
    )
    palette = tuple(data.get("carrierPalette") or ["#4C78A8"])
    return Theme(
        name=data.get("name", "unnamed"),
        chrome=chrome,
        carrier_palette=palette,
        pinned_carriers=dict(data.get("carriers", {})),
        retention_fills=dict(data.get("retentionFills", {})),
    )


def load_theme(path: Path | str | None = None) -> Theme:
    """Load a theme file; None gives the built-in default."""
    if path is None:
        text = resources.files("towerkit").joinpath("themes/default.json").read_text("utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return _theme_from_jsonable(json.loads(text))
