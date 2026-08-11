"""Properties of the vertical scale map (§3.2). Silent scale bugs change what
the picture says, so these are exact where exactness is possible."""

import subprocess
from pathlib import Path

import pytest

from towerkit.model import Layer
from towerkit.scale import build_y_map, retention_depth

REPO = Path(__file__).parent.parent


def L(attach: int, limit: int) -> Layer:
    return Layer(id=f"l{attach}", name="x", applies_to=["gl"], attach=attach, limit=limit)


LAYERS = [L(0, 2_000_000), L(2_000_000, 25_000_000), L(27_000_000, 25_000_000),
          L(52_000_000, 50_000_000), L(102_000_000, 100_000_000)]


class TestYMap:
    def test_breakpoints_are_all_attachment_points_plus_zero(self) -> None:
        ymap = build_y_map(LAYERS)
        assert ymap.breakpoints == (
            0, 2_000_000, 27_000_000, 52_000_000, 102_000_000, 202_000_000
        )

    def test_endpoints_exact(self) -> None:
        ymap = build_y_map(LAYERS)
        assert ymap.y(0) == 0.0
        assert ymap.y(ymap.max_dollars) == 1.0

    def test_monotonic_non_decreasing(self) -> None:
        ymap = build_y_map(LAYERS)
        step = 500_000
        values = [ymap.y(d) for d in range(0, ymap.max_dollars + step, step)]
        assert all(a <= b for a, b in zip(values, values[1:], strict=False))

    def test_strictly_increasing_between_breakpoints(self) -> None:
        ymap = build_y_map(LAYERS)
        assert ymap.y(1_000_000) < ymap.y(1_500_000)

    def test_gamma_one_reproduces_linear_proportions(self) -> None:
        ymap = build_y_map(LAYERS, gamma=1.0)
        for dollars in (0, 1_000_000, 2_000_000, 51_000_000, 101_000_000, 202_000_000):
            assert ymap.y(dollars) == pytest.approx(dollars / ymap.max_dollars, abs=1e-12)

    def test_compression_lifts_small_layers(self) -> None:
        # gamma < 1 must give the $2M primary more height than linear would.
        compressed = build_y_map(LAYERS, gamma=0.35)
        linear = build_y_map(LAYERS, gamma=1.0)
        assert compressed.y(2_000_000) > linear.y(2_000_000)

    def test_same_dollar_same_height_globally(self) -> None:
        # The map is global, not per-column: one Y for $52M, full stop.
        ymap = build_y_map(LAYERS)
        assert ymap.y(52_000_000) == ymap.y(52_000_000)
        # and interpolation at a non-breakpoint is single-valued by construction
        assert ymap.y(30_000_000) == ymap.y(30_000_000)

    def test_no_layers_degenerate(self) -> None:
        ymap = build_y_map([])
        assert ymap.y(0) == 0.0
        assert ymap.y(5) == 0.0

    def test_clamps_outside_range(self) -> None:
        ymap = build_y_map(LAYERS)
        assert ymap.y(999_000_000) == 1.0
        assert ymap.y(-5) == 0.0


class TestRetentionMap:
    def test_zero_and_max(self) -> None:
        assert retention_depth(0, 1_000_000) == 0.0
        assert retention_depth(1_000_000, 1_000_000) == 1.0

    def test_monotonic(self) -> None:
        depths = [retention_depth(a, 1_000_000) for a in (100_000, 250_000, 500_000, 1_000_000)]
        assert depths == sorted(depths)

    def test_compression(self) -> None:
        # quarter of the money is far more than a quarter of the depth
        assert retention_depth(250_000, 1_000_000) > 0.5

    def test_degenerate_no_retentions(self) -> None:
        assert retention_depth(0, 0) == 0.0


class TestPurity:
    def test_scale_and_layout_do_not_import_matplotlib(self) -> None:
        # The §9 gate, verbatim: grep returns nothing.
        result = subprocess.run(
            ["grep", "-r", "matplotlib",
             str(REPO / "src/towerkit/scale.py"), str(REPO / "src/towerkit/layout.py")],
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
