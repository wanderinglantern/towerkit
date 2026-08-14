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
        # run edges are exactly the columns' drawing extents — no drift
        assert outlines[0].x0 == cols["a"].ex0 and outlines[0].x1 == cols["b"].ex1
        assert outlines[1].x0 == cols["d"].ex0 and outlines[1].x1 == cols["d"].ex1

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
        assert [(r.x0, r.x1) for r in x_block.rects] == [(cols["a"].ex0, cols["a"].ex1)]
        assert [(r.x0, r.x1) for r in y_block.rects] == [(cols["c"].ex0, cols["c"].ex1)]

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


class TestClosedGutters:
    def test_adjacent_monoline_bands_meet_edge_to_edge(self) -> None:
        # Every interior gutter is split between its neighbours, so adjacent
        # bands share a bit-identical edge — no white holes anywhere.
        program = make_program(
            ["a", "b", "c"],
            [
                layer("prim-a", ["a"], 0, 2_000_000, [("X", 10_000)]),
                layer("prim-b", ["b"], 0, 2_000_000, [("Y", 10_000)]),
                layer("prim-c", ["c"], 0, 2_000_000, [("Z", 10_000)]),
            ],
        )
        tower = build_layout(program)
        prim_a = next(b for b in tower.layers if b.layer_id == "prim-a")
        prim_b = next(b for b in tower.layers if b.layer_id == "prim-b")
        prim_c = next(b for b in tower.layers if b.layer_id == "prim-c")
        assert prim_a.outlines[0].x1 == prim_b.outlines[0].x0
        assert prim_b.outlines[0].x1 == prim_c.outlines[0].x0

    def test_program_outer_edges_stay_nominal(self) -> None:
        program = make_program(
            ["a", "b"],
            [layer("umb", ["a", "b"], 0, 5, [("U", 10_000)])],
        )
        tower = build_layout(program)
        umb = next(b for b in tower.layers if b.layer_id == "umb")
        cols = {c.line_id: c for c in tower.columns}
        # only interior gutters close; the chart's outer silhouette is fixed
        assert umb.outlines[0].x0 == cols["a"].x0
        assert umb.outlines[0].x1 == cols["b"].x1

    def test_retentions_close_gutters_too(self) -> None:
        tower = build_layout(load_program(SAMPLE))
        rects = sorted(
            (r.rects[0] for r in tower.retentions), key=lambda rect: rect.x0
        )
        for left, right in zip(rects, rects[1:], strict=False):
            assert left.x1 == right.x0


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


class TestNoRetentionBand:
    def test_band_collapses_without_retentions(self) -> None:
        program = make_program(
            ["a"], [layer("l", ["a"], 0, 1_000_000, [("X", 10_000)])]
        )
        tower = build_layout(program)
        assert tower.retentions == ()
        assert tower.retention_band == 0.0


class TestFollowsUnderlying:
    def make_shared_umbrella(self):
        # two entity GL columns with different primary limits, one shared
        # umbrella following the underlying tops
        return make_program(
            ["a", "b"],
            [
                layer("prim-a", ["a"], 0, 1_000_000, [("X", 10_000)]),
                layer("prim-b", ["b"], 0, 2_000_000, [("Y", 10_000)]),
                Layer(
                    id="umb",
                    name="Shared Umbrella",
                    applies_to=["a", "b"],
                    attach=2_000_000,
                    limit=10_000_000,
                    follows_underlying=True,
                    participants=[Participant(carrier="U", share_bps=10_000)],
                ),
            ],
        )

    def test_stepped_bottom_flat_top(self) -> None:
        tower = build_layout(self.make_shared_umbrella())
        umb = next(b for b in tower.layers if b.layer_id == "umb")
        pieces = sorted(umb.outlines, key=lambda r: r.x0)
        assert len(pieces) == 2
        left, right = pieces
        # column a's bottom sits on its $1M top — lower than b's $2M top
        assert left.y0 < right.y0
        assert left.y1 == right.y1  # flat top
        # bottoms sit exactly on the underlying tops
        assert left.y0 == tower.ymap.y(1_000_000)
        assert right.y0 == tower.ymap.y(2_000_000)

    def test_participant_rects_follow_the_steps(self) -> None:
        tower = build_layout(self.make_shared_umbrella())
        u_block = next(b for b in tower.participants if b.carrier == "U")
        bottoms = sorted({r.y0 for r in u_block.rects})
        assert bottoms == sorted(
            [tower.ymap.y(1_000_000), tower.ymap.y(2_000_000)]
        )
        # pieces still tile the span exactly
        xs = sorted(u_block.rects, key=lambda r: r.x0)
        for a, b in zip(xs, xs[1:], strict=False):
            assert a.x1 == b.x0

    def test_validator_accepts_the_stepped_stack(self) -> None:
        from towerkit.validate import validate_program

        program = self.make_shared_umbrella()
        assert not validate_program(program).errors

    def test_validator_rejects_wrong_follows_attach(self) -> None:
        from towerkit.validate import validate_program

        program = self.make_shared_umbrella()
        umb = next(ly for ly in program.layers if ly.id == "umb")
        umb.attach = 1_500_000
        codes = {d.code for d in validate_program(program).errors}
        assert "layer-follows-attach" in codes


class TestGroupBuckets:
    def grouped_program(self):
        program = make_program(
            ["a", "b", "c", "d"],
            [
                layer("pa", ["a"], 0, 1_000_000, [("X", 10_000)]),
                layer("pb", ["b"], 0, 2_000_000, [("Y", 10_000)]),
                layer("pc", ["c"], 0, 1_000_000, [("Z", 10_000)]),
                layer("pd", ["d"], 0, 1_000_000, [("W", 10_000)]),
                Layer(id="umb", name="Alpha Umbrella", applies_to=["a", "b"],
                      attach=2_000_000, limit=10_000_000, premium=1_000_000,
                      follows_underlying=True,
                      participants=[Participant(carrier="U", share_bps=10_000)]),
            ],
        )
        program.lines[0].group = "Project Alpha"
        program.lines[1].group = "Project Alpha"
        program.lines[2].group = "Houston"
        return program

    def test_gutters_flush_within_open_between(self) -> None:
        tower = build_layout(self.grouped_program())
        a, b, c, d = tower.columns
        assert a.ex1 == b.ex0          # inside Project Alpha: flush
        assert b.ex1 == b.x1 and c.ex0 == c.x0  # Alpha → Houston: open gutter
        assert c.ex1 == c.x1 and d.ex0 == d.x0  # Houston → ungrouped: open

    def test_band_extents_and_rollups(self) -> None:
        tower = build_layout(self.grouped_program())
        assert [g.label for g in tower.groups] == ["Project Alpha", "Houston"]
        alpha = tower.groups[0]
        cols = {c.line_id: c for c in tower.columns}
        assert alpha.x0 == cols["a"].x0 and alpha.x1 == cols["b"].x1
        # fully-contained layers count whole; umbrella spans only group lines
        assert alpha.limit == 1_000_000 + 2_000_000 + 10_000_000
        assert alpha.premium == 1_000_000
        assert tower.groups[1].limit == 1_000_000

    def test_straddling_layer_allocates_pro_rata(self) -> None:
        program = self.grouped_program()
        umb = next(ly for ly in program.layers if ly.id == "umb")
        umb.applies_to = ["a", "b", "c", "d"]  # 2 of 4 lines in Alpha
        umb.follows_underlying = False
        umb.attach = 2_000_000
        tower = build_layout(program)
        alpha = tower.groups[0]
        assert alpha.limit == 3_000_000 + 10_000_000 * 2 // 4
        assert alpha.premium == 1_000_000 * 2 // 4


def statutory_layer(id: str, applies: list[str], shares) -> Layer:
    return Layer(
        id=id, name=id, applies_to=applies, attach=0, limit=0, statutory=True,
        participants=[Participant(carrier=c, share_bps=b) for c, b in shares],
    )


class TestStatutory:
    def test_does_not_move_any_other_layer(self) -> None:
        """THE load-bearing invariant. scale.py builds ONE global map over the
        program's breakpoints and the whole design hangs on $52M sitting at the
        same height in every column. A statutory layer must contribute no
        breakpoints, so adding one cannot shift anything else by a single
        float."""
        layers = [
            layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
            layer("gl-excess", ["gl"], 5_000_000, 20_000_000, [("B", 10_000)]),
        ]
        before = build_layout(make_program(["gl"], layers))
        after = build_layout(
            make_program(
                ["gl", "wc"],
                [*layers, statutory_layer("wc-stat", ["wc"], [("C", 10_000)])],
            )
        )
        baseline = {b.layer_id: (b.y0, b.y1) for b in before.layers}
        for block in after.layers:
            if block.layer_id in baseline:
                assert (block.y0, block.y1) == baseline[block.layer_id], block.layer_id

    def test_occupies_the_full_column(self) -> None:
        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("wc-stat", ["wc"], [("C", 10_000)]),
                ],
            )
        )
        stat = next(b for b in tower.layers if b.layer_id == "wc-stat")
        assert (stat.y0, stat.y1) == (0.0, 1.0)
        assert stat.statutory is True

    def test_contributes_no_breakpoints(self) -> None:
        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("wc-stat", ["wc"], [("C", 10_000)]),
                ],
            )
        )
        assert tower.ymap.breakpoints == (0, 5_000_000)

    def test_statutory_only_program_is_drawable(self) -> None:
        """build_y_map([]) returns the degenerate YMap. The bar still draws
        floor to top; there are simply no axis labels."""
        tower = build_layout(
            make_program(["wc"], [statutory_layer("wc-stat", ["wc"], [("C", 10_000)])])
        )
        stat = next(b for b in tower.layers if b.layer_id == "wc-stat")
        assert (stat.y0, stat.y1) == (0.0, 1.0)
        assert len(tower.participants) == 1

    def test_participants_allocate_across_the_bar(self) -> None:
        tower = build_layout(
            make_program(
                ["wc"],
                [statutory_layer("wc-stat", ["wc"], [("A", 6_000), ("B", 4_000)])],
            )
        )
        blocks = [b for b in tower.participants if b.layer_id == "wc-stat"]
        assert [b.carrier for b in blocks] == ["A", "B"]
        assert all(r.y0 == 0.0 and r.y1 == 1.0 for b in blocks for r in b.rects)

    def test_a_stale_limit_on_a_statutory_layer_is_still_off_the_scale(self) -> None:
        """limit == 0 is a SEMANTIC rule, not a schema one, and build_layout
        tolerates draft data — so a layer with the box ticked and a stale
        limit must still contribute no breakpoints. This is the case that
        distinguishes build_y_map(scaled) from build_y_map(drawable); with
        limit == 0 the two are bit-identical and nothing would catch the
        difference."""
        stat = statutory_layer("wc-stat", ["wc"], [("C", 10_000)])
        stat.limit = 7_000_000  # draft: box ticked, limit never cleared
        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]), stat],
            )
        )
        assert tower.ymap.breakpoints == (0, 5_000_000)
        assert next(b for b in tower.layers if b.layer_id == "wc-stat").y1 == 1.0
