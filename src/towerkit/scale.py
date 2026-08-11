"""Vertical scale: one global piecewise-compressed map over the breakpoint set.

The compression is per-interval, not per-layer: every attachment point in the
program becomes a breakpoint, each interval's raw height is (span ** gamma),
and the cumulative sum is normalised to [0, 1]. Because the map is global,
$52M sits at the same height in every column — the property the whole design
hangs on.

Retentions get their own map: $250k retentions and $200M limits cannot share
a scale.

This module imports nothing but the stdlib and model.py — no plotting
library, ever; that is what keeps the geometry unit-testable.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from .model import Layer

DEFAULT_GAMMA = 0.35
RETENTION_EXPONENT = 0.35


@dataclass(frozen=True)
class YMap:
    """Maps dollars → normalised height in [0, 1], piecewise-linear between
    breakpoints whose spacing is compressed by gamma."""

    breakpoints: tuple[int, ...]
    positions: tuple[float, ...]
    gamma: float

    @property
    def max_dollars(self) -> int:
        return self.breakpoints[-1]

    def y(self, dollars: int) -> float:
        if dollars <= 0:
            return 0.0
        if dollars >= self.max_dollars:
            return self.positions[-1]
        idx = bisect_right(self.breakpoints, dollars) - 1
        lo_d, hi_d = self.breakpoints[idx], self.breakpoints[idx + 1]
        lo_y, hi_y = self.positions[idx], self.positions[idx + 1]
        return lo_y + (hi_y - lo_y) * (dollars - lo_d) / (hi_d - lo_d)


def build_y_map(
    layers: Iterable[Layer],
    gamma: float = DEFAULT_GAMMA,
    extra_points: Iterable[int] = (),
) -> YMap:
    """Breakpoints are every attach and every top across all layers, plus 0
    and any extra points (e.g. the stepped bottoms of a follows-underlying
    layer) — still one global map."""
    points = {0, *extra_points}
    for layer in layers:
        points.add(layer.attach)
        points.add(layer.attach + layer.limit)
    breakpoints = tuple(sorted(points))
    if len(breakpoints) == 1:  # no layers: degenerate but well-defined
        return YMap(breakpoints=(0,), positions=(0.0,), gamma=gamma)

    raw = [(b - a) ** gamma for a, b in zip(breakpoints, breakpoints[1:], strict=False)]
    total = sum(raw)
    positions = [0.0]
    running = 0.0
    for height in raw:
        running += height
        positions.append(running / total)
    positions[-1] = 1.0  # exact by construction; pin against float drift
    return YMap(breakpoints=breakpoints, positions=tuple(positions), gamma=gamma)


def retention_depth(
    amount: int, max_retention: int, exponent: float = RETENTION_EXPONENT
) -> float:
    """Depth of a retention block as a fraction of the retention band height."""
    if max_retention <= 0 or amount <= 0:
        return 0.0
    return float((amount / max_retention) ** exponent)
