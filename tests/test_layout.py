"""Properties of the layout (§3.3): runs split exactly, allocations tile the
span with bit-identical shared edges, rectangles never overlap."""

from datetime import date
from pathlib import Path

from towerkit.layout import GUTTER, build_layout
from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Placement,
    Program,
    load_program,
)

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def make_program(lines: list[str], layers: list[Layer]) -> Program:
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id=lid, name=lid.upper()) for lid in lines],
        layers=layers,
    )


def layer(id: str, applies: list[str], attach: int, limit: int, shares) -> Layer:
    return Layer(
        id=id,
        name=id,
        applies_to=applies,
        attach=attach,
        limit=limit,
        participants=[Participant(carrier=c, share_bps=b) for c, b in shares],
    )


class TestColumns:
    def test_equal_width_fixed_gutter(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        widths = {col.x1 - col.x0 for col in tower.columns}
        assert widths == {1.0}
        gaps = {
            b.x0 - a.x1 for a, b in zip(tower.columns, tower.columns[1:], strict=False)
        }
        assert gaps == {GUTTER}


class TestRuns:
    def test_contiguous_lines_are_one_run(self) -> None:
        program = make_program(
            ["a", "b", "c"],
            [layer("l1", ["a", "b", "c"], 0, 1_000_000, [("X", 10_000)])],
        )
        tower = build_layout(program)
        assert len(tower.layers[0].outlines) == 1

    def test_non_contiguous_lines_split_into_exact_runs(self) -> None:
        program = make_program(
            ["a", "b", "c", "d"],
            [layer("l1", ["a", "b", "d"], 0, 1_000_000, [("X", 10_000)])],
        )
        tower = build_layout(program)
        outlines = tower.layers[0].outlines
        assert len(outlines) == 2
        cols = {c.line_id: c for c in tower.columns}
        # run edges are exactly the column edges — no arithmetic drift
        assert outlines[0].x0 == cols["a"].x0 and outlines[0].x1 == cols["b"].x1
        assert outlines[1].x0 == cols["d"].x0 and outlines[1].x1 == cols["d"].x1

    def test_applies_to_order_does_not_matter(self) -> None:
        p1 = make_program(
            ["a", "b", "c"], [layer("l", ["c", "a", "b"], 0, 1, [("X", 10_000)])]
        )
        p2 = make_program(
            ["a", "b", "c"], [layer("l", ["a", "b", "c"], 0, 1, [("X", 10_000)])]
        )
        assert build_layout(p1).layers[0].outlines == build_layout(p2).layers[0].outlines


class TestAllocation:
    def rects_for(self, tower, layer_id):
        return [
            rect
            for block in tower.participants
            if block.layer_id == layer_id
            for rect in block.rects
        ]

    def assert_tiles_exactly(self, tower, layer_id) -> None:
        """The exactness property: sorted rects chain with bit-identical
        edges and cover each run edge-to-edge."""
        block = next(b for b in tower.layers if b.layer_id == layer_id)
        rects = sorted(self.rects_for(tower, layer_id), key=lambda r: r.x0)
        assert rects, layer_id
        by_run: dict[tuple[float, float], list] = {}
        for rect in rects:
            for outline in block.outlines:
                if outline.x0 <= rect.x0 and rect.x1 <= outline.x1:
                    by_run.setdefault((outline.x0, outline.x1), []).append(rect)
                    break
            else:
                raise AssertionError(f"rect {rect} outside every run of {layer_id}")
        assert len(by_run) == len(block.outlines), "every run must carry allocation"
        for (x0, x1), run_rects in by_run.items():
            assert run_rects[0].x0 == x0  # exact ==, not approx
            assert run_rects[-1].x1 == x1
            for a, b in zip(run_rects, run_rects[1:], strict=False):
                assert a.x1 == b.x0, "adjacent rects must share a bit-identical edge"

    def assert_no_overlap(self, tower, layer_id) -> None:
        rects = sorted(self.rects_for(tower, layer_id), key=lambda r: r.x0)
        for a, b in zip(rects, rects[1:], strict=False):
            assert a.x1 <= b.x0

    def test_simple_split(self) -> None:
        program = make_program(
            ["a"], [layer("l", ["a"], 0, 1_000_000, [("X", 6_000), ("Y", 4_000)])]
        )
        tower = build_layout(program)
        self.assert_tiles_exactly(tower, "l")
        self.assert_no_overlap(tower, "l")
        assert len(tower.participants) == 2

    def test_participant_straddles_run_boundary(self) -> None:
        # 3 columns, layer on a and c (non-contiguous): X takes 60% — more
        # than column a — so X must straddle into c's run.
        program = make_program(
            ["a", "b", "c"],
            [layer("l", ["a", "c"], 0, 1_000_000, [("X", 6_000), ("Y", 4_000)])],
        )
        tower = build_layout(program)
        x_block = next(b for b in tower.participants if b.carrier == "X")
        assert len(x_block.rects) == 2, "60% of two columns must split at the run boundary"
        self.assert_tiles_exactly(tower, "l")
        self.assert_no_overlap(tower, "l")

    def test_share_boundary_exactly_on_run_boundary(self) -> None:
        # 50/50 across two non-contiguous columns: the share edge lands
        # exactly on the run edge; no sliver, no gutter-crossing rect.
        program = make_program(
            ["a", "b", "c"],
            [layer("l", ["a", "c"], 0, 1_000_000, [("X", 5_000), ("Y", 5_000)])],
        )
        tower = build_layout(program)
        x_block = next(b for b in tower.participants if b.carrier == "X")
        y_block = next(b for b in tower.participants if b.carrier == "Y")
        cols = {c.line_id: c for c in tower.columns}
        assert [(r.x0, r.x1) for r in x_block.rects] == [(cols["a"].x0, cols["a"].x1)]
        assert [(r.x0, r.x1) for r in y_block.rects] == [(cols["c"].x0, cols["c"].x1)]

    def test_unplaced_capacity_gets_a_block(self) -> None:
        program = make_program(
            ["a"], [layer("l", ["a"], 0, 100_000_000, [("X", 8_000)])]
        )
        tower = build_layout(program)
        unplaced = [b for b in tower.participants if b.carrier is None]
        assert len(unplaced) == 1
        assert unplaced[0].share_bps == 2_000
        self.assert_tiles_exactly(tower, "l")

    def test_thirds_tile_exactly(self) -> None:
        program = make_program(
            ["a", "b"],
            [layer("l", ["a", "b"], 0, 1_000_000,
                   [("X", 3_334), ("Y", 3_333), ("Z", 3_333)])],
        )
        tower = build_layout(program)
        self.assert_tiles_exactly(tower, "l")
        self.assert_no_overlap(tower, "l")

    def test_every_sample_layer_tiles_exactly(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        for block in tower.layers:
            self.assert_tiles_exactly(tower, block.layer_id)
            self.assert_no_overlap(tower, block.layer_id)


class TestJoinedGutters:
    def test_same_tower_monoline_bands_meet_edge_to_edge(self) -> None:
        # An umbrella spans a+b, so the a-b gutter closes: the two monoline
        # primaries extend half a gutter each and share a bit-identical edge.
        program = make_program(
            ["a", "b", "c"],
            [
                layer("prim-a", ["a"], 0, 2_000_000, [("X", 10_000)]),
                layer("prim-b", ["b"], 0, 2_000_000, [("Y", 10_000)]),
                layer("prim-c", ["c"], 0, 2_000_000, [("Z", 10_000)]),
                layer("umb", ["a", "b"], 2_000_000, 5_000_000, [("U", 10_000)]),
            ],
        )
        tower = build_layout(program)
        prim_a = next(b for b in tower.layers if b.layer_id == "prim-a")
        prim_b = next(b for b in tower.layers if b.layer_id == "prim-b")
        prim_c = next(b for b in tower.layers if b.layer_id == "prim-c")
        assert prim_a.outlines[0].x1 == prim_b.outlines[0].x0  # gutter closed
        # b-c gutter stays open: c is a separate tower
        assert prim_b.outlines[0].x1 < prim_c.outlines[0].x0

    def test_spanning_layer_extent_unchanged_at_tower_edges(self) -> None:
        program = make_program(
            ["a", "b", "c"],
            [
                layer("prim-a", ["a"], 0, 1, [("X", 10_000)]),
                layer("prim-b", ["b"], 0, 1, [("Y", 10_000)]),
                layer("umb", ["a", "b"], 1, 5, [("U", 10_000)]),
            ],
        )
        tower = build_layout(program)
        umb = next(b for b in tower.layers if b.layer_id == "umb")
        cols = {c.line_id: c for c in tower.columns}
        # the tower's outer silhouette is still the nominal column edges
        assert umb.outlines[0].x0 == cols["a"].x0
        assert umb.outlines[0].x1 == cols["b"].x1

    def test_retentions_follow_the_same_rule(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        # gl/al/el are one tower (umbrella): their retention blocks touch
        rects = sorted(
            (r.rects[0] for r in tower.retentions), key=lambda rect: rect.x0
        )
        assert rects[0].x1 == rects[1].x0
        assert rects[1].x1 == rects[2].x0
        # but the pl retention does not touch el's
        assert rects[2].x1 < rects[3].x0


class TestVertical:
    def test_same_attachment_same_height_across_layers(self) -> None:
        # The umbrella's top and the 1st excess's bottom are the same dollars,
        # so they are the same y — in every column, by construction.
        tower = build_layout(load_program(SAMPLE))
        umbrella = next(b for b in tower.layers if b.layer_id == "umbrella")
        xs1 = next(b for b in tower.layers if b.layer_id == "xs-1")
        assert umbrella.y1 == xs1.y0

    def test_retentions_below_zero(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        assert tower.retentions
        for block in tower.retentions:
            for rect in block.rects:
                assert rect.y1 == 0.0 and rect.y0 < 0

    def test_biggest_retention_is_deepest(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        deepest = min(r.rects[0].y0 for r in tower.retentions)
        captive = next(r for r in tower.retentions if r.type == "captive")
        assert captive.rects[0].y0 == deepest


class TestDrafts:
    def test_zero_limit_layer_is_skipped_not_fatal(self) -> None:
        program = make_program(
            ["a"],
            [
                layer("ok", ["a"], 0, 1_000_000, [("X", 10_000)]),
                layer("draft", ["a"], 1_000_000, 0, []),
            ],
        )
        tower = build_layout(program)
        assert [b.layer_id for b in tower.layers] == ["ok"]

    def test_empty_program_layout(self) -> None:
        tower = build_layout(
            make_program(["a"], [])
        )
        assert tower.layers == ()
