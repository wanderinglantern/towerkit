"""Theme loading and carrier colour assignment.

Colour is three separate concerns in three separate places:
- chrome:          the house brand (background, ink, grid, accents)
- carrierPalette + carriers: market identity — categorical, pinned or assigned
- retentionFills:  one tonal family, never a carrier colour
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from importlib import resources
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints


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
class SoiStyle:
    """Schedule of Insurance workbook styling. Defaults mirror the reviewed
    sample workbook, not the chart chrome — SOIs circulate as Excel files
    with their own established look."""

    header_fill: str = "#003865"
    header_text: str = "#FFFFFF"
    body_text: str = "#3D3C37"
    band_fill: str = "#F7F3EE"
    border: str = "#B9B6B1"
    font: str = "Noto Sans"
    size: int = 11

    @property
    def effective_header_text(self) -> str:
        return contrast_text(self.header_fill, self.header_text, self.body_text)


@dataclass(frozen=True)
class Theme:
    name: str
    chrome: Chrome
    carrier_palette: tuple[str, ...]
    pinned_carriers: dict[str, str] = field(default_factory=dict)
    retention_fills: dict[str, str] = field(default_factory=dict)
    soi: SoiStyle = field(default_factory=SoiStyle)

    def carrier_colours(self, carriers: list[str]) -> dict[str, str]:
        """Walk the palette in order — the brand sequence starts in the blues
        and only then riffs outward — assigning by first appearance. No
        carrier list to maintain; explicit pins in a theme still win. For a
        renewal comparison, call once with the union of both programs'
        carriers so a carrier keeps its colour across the two towers."""
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


_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _accepted_types(annotation: Any) -> tuple[type, ...]:
    if get_origin(annotation) in (Union, UnionType):
        return tuple(arg for arg in get_args(annotation) if isinstance(arg, type))
    return (annotation,) if isinstance(annotation, type) else ()


def _slot_problems(prefix: str, obj: Any) -> list[str]:
    """One walk, one verdict per slot — colour, type, or optional.

    Round eight ran a colour walk and a type walk in parallel, and their
    skip rules had to exactly partition the field set; round nine found
    `titleFont=12` falling through both (non-hex default, so not a colour
    slot; `None` default, so the type walk skipped it) and crashing
    matplotlib. Now: a #RRGGBB `str` default marks a colour slot; every
    other slot's accepted types come from its ANNOTATION — which also
    covers a future `default_factory` field, whose `f.default` is MISSING.
    Nothing here is a list of field names."""
    problems: list[str] = []
    hints = get_type_hints(type(obj))
    for f in fields(obj):
        value = getattr(obj, f.name)
        where = f"{prefix}.{f.name}"
        if isinstance(f.default, str) and _HEX_COLOUR.match(f.default):
            if not isinstance(value, str) or not _HEX_COLOUR.match(value):
                problems.append(f"{where}: {value!r} is not a #RRGGBB colour")
            continue
        accepted = _accepted_types(hints[f.name])
        if value is None and type(None) in accepted:
            continue
        concrete = tuple(t for t in accepted if t is not type(None))
        wanted = "/".join(t.__name__ for t in concrete)
        if int in concrete and isinstance(value, bool):
            # bool IS an int; a True size is always a mistake.
            problems.append(f"{where}: {value!r} is not a {wanted}")
        elif int in concrete and isinstance(value, float):
            # 11.5 renders and exports fine (round nine's cost audit);
            # refusing it made a workable theme validate dirty.
            continue
        elif not isinstance(value, concrete):
            problems.append(f"{where}: {value!r} is not a {wanted}")
    return problems


def theme_problems(theme: Theme) -> list[str]:
    """Every colour a renderer would be handed, checked against #RRGGBB.

    The contract is towerkit's OWN, not matplotlib's tolerance:
    `relative_luminance` parses exactly six hex digits, so `"white"` renders
    a chart fine and crashes the SOI's contrast pick — and `"notahex"`
    crashes everything. Round seven (2026-08-20) produced exactly that:
    a theme that LOADED clean, validated clean, and killed `towerctl render`
    with a raw matplotlib ValueError. A theme that loads is not a theme that
    renders; this is the missing half."""
    problems: list[str] = []

    def check(where: str, value: object) -> None:
        if not isinstance(value, str) or not _HEX_COLOUR.match(value):
            problems.append(f"{where}: {value!r} is not a #RRGGBB colour")

    problems.extend(_slot_problems("chrome", theme.chrome))
    problems.extend(_slot_problems("soi", theme.soi))
    for i, value in enumerate(theme.carrier_palette):
        check(f"carrierPalette[{i}]", value)
    for carrier, value in theme.pinned_carriers.items():
        check(f"carriers[{carrier}]", value)
    for kind, value in theme.retention_fills.items():
        check(f"retentionFills[{kind}]", value)
    return problems


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
    soi_raw = data.get("soi", {})
    soi = SoiStyle(
        header_fill=soi_raw.get("headerFill", SoiStyle.header_fill),
        header_text=soi_raw.get("headerText", SoiStyle.header_text),
        body_text=soi_raw.get("bodyText", SoiStyle.body_text),
        band_fill=soi_raw.get("bandFill", SoiStyle.band_fill),
        border=soi_raw.get("border", SoiStyle.border),
        font=soi_raw.get("font", SoiStyle.font),
        size=soi_raw.get("size", SoiStyle.size),
    )
    return Theme(
        name=data.get("name", "unnamed"),
        chrome=chrome,
        carrier_palette=palette,
        pinned_carriers=dict(data.get("carriers", {})),
        retention_fills=dict(data.get("retentionFills", {})),
        soi=soi,
    )


def load_theme(path: Path | str | None = None) -> Theme:
    """Load a theme file; None gives the built-in default."""
    if path is None:
        text = resources.files("towerkit").joinpath("themes/default.json").read_text("utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return _theme_from_jsonable(json.loads(text))


def available_themes(extra_dir: Path | None = None) -> list[Path]:
    """Every theme file reachable RIGHT NOW: ./themes (user's, wins on name
    clash), an optional extra directory, then the packaged set — so the
    picker finds marsh.json no matter which directory launched towerctl
    (e.g. the jump from bookkit)."""
    candidates: list[Path] = [Path("themes")]
    if extra_dir is not None:
        candidates.append(extra_dir)
    packaged = Path(str(resources.files("towerkit").joinpath("themes")))
    candidates.append(packaged)
    seen: dict[str, Path] = {}
    for directory in candidates:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            # paths stay as found: ./themes entries remain RELATIVE, keeping
            # the stored render.theme portable between machines (the existing
            # program-file contract); packaged entries are absolute by nature
            seen.setdefault(path.stem, path)
    return list(seen.values())
