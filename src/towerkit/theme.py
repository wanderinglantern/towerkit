"""Theme loading and carrier colour assignment.

Colour is three separate concerns in three separate places:
- chrome:          the house brand (background, ink, grid, accents)
- carrierPalette + carriers: market identity — categorical, pinned or assigned
- retentionFills:  one tonal family, never a carrier colour
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


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


@dataclass(frozen=True)
class Theme:
    name: str
    chrome: Chrome
    carrier_palette: tuple[str, ...]
    pinned_carriers: dict[str, str] = field(default_factory=dict)
    retention_fills: dict[str, str] = field(default_factory=dict)

    def carrier_colours(self, carriers: list[str]) -> dict[str, str]:
        """Pinned colours win; the rest draw from the palette in first-
        appearance order, so adding a carrier never recolours existing ones."""
        out: dict[str, str] = {}
        in_use = {
            self.pinned_carriers[c] for c in carriers if c in self.pinned_carriers
        }
        cursor = 0
        for carrier in carriers:
            pinned = self.pinned_carriers.get(carrier)
            if pinned is not None:
                out[carrier] = pinned
                continue
            # skip palette entries a pinned or earlier carrier already wears
            for _ in range(len(self.carrier_palette)):
                candidate = self.carrier_palette[cursor % len(self.carrier_palette)]
                cursor += 1
                if candidate not in in_use:
                    break
            out[carrier] = candidate
            in_use.add(candidate)
        return out

    def retention_fill(self, retention_type: str) -> str:
        return self.retention_fills.get(retention_type, "#DDD8C9")


def _theme_from_jsonable(data: dict) -> Theme:
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
