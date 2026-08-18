"""The web panel view: geometry in, HTML-ready values out.

Pure input/output in the shape of test_scale.py and test_layout.py — a
Program in, a WebTower out, no plotting and no browser. The thresholds are
exercised at gamma=1.0 so a block's height is an exact fraction of the tower
and the pixel arithmetic is checkable by hand; the agreement tests use the
compressed default.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from towerkit.layout import build_layout
from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Placement,
    Program,
    Retention,
    RetentionType,
    load_program,
)
from towerkit.money import format_money_compact
from towerkit.render.labels import (
    block_premium_label,
    carrier_only_label,
    group_label,
    heading_blocks,
    layer_heading,
    layer_terms,
    participant_label,
    retention_label,
    unplaced_label,
)
from towerkit.render.web import (
    MONEY_MIN_PX,
    NAME_MIN_PX,
    NAME_MIN_PX_SPANNING,
    REF_LINE_MIN_GAP_PX,
    build_web_tower,
    is_pending,
)

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


def make_program(
    lines: list[str],
    layers: list[Layer],
    retentions: list[Retention] | None = None,
    groups: dict[str, str] | None = None,
) -> Program:
    groups = groups or {}
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[
            Line(id=lid, name=lid.upper(), group=groups.get(lid)) for lid in lines
        ],
        layers=layers,
        retentions=retentions or [],
    )


def layer(
    id: str,
    applies: list[str],
    attach: int,
    limit: int,
    shares: list[tuple[str, int]] | None = None,
    premium: int | None = None,
    statutory: bool = False,
) -> Layer:
    return Layer(
        id=id,
        name=id,
        applies_to=applies,
        attach=attach,
        limit=limit,
        premium=premium,
        statutory=statutory,
        participants=[
            Participant(carrier=c, share_bps=b) for c, b in (shares or [])
        ],
    )


def two_layer_program(lines: list[str]) -> Program:
    """At gamma=1.0 the lower layer is exactly 10% of the tower's height, so
    `chart_height_px` × 0.1 is the block's rendered height, to the pixel."""
    return make_program(
        lines,
        [
            layer("low", lines, 0, 1_000_000, [("A", 10_000)], premium=100_000),
            layer("high", lines, 1_000_000, 9_000_000, [("B", 10_000)]),
        ],
    )


def low_block(program: Program, chart_height_px: float):
    web = build_web_tower(program, chart_height_px, gamma=1.0)
    return next(b for b in web.blocks if b.layer_id == "low")


class TestPurity:
    def test_web_does_not_import_a_plotting_library(self) -> None:
        """The rule scale.py and layout.py carry. The pre-existing purity
        test (test_scale.py) names only those two files, so render/web.py
        would not have been covered by anything."""
        result = subprocess.run(
            ["grep", "-rn", "matplotlib", str(REPO / "src/towerkit/render/web.py")],
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""


class TestDropThresholds:
    """Heights are literal here on purpose: a block that is exactly 10% of
    the tower at gamma=1.0 renders at chart_height_px/10, so 300px → 30.0px.
    Deriving them from the constants would make these tests agree with any
    constant, which is the one thing they must not do."""

    def test_the_constants_are_the_prototype_s(self) -> None:
        """These are tuned-by-eye numbers from the design prototype, not
        measurements. Recording them is the only way a later edit shows up
        as a decision rather than a drift."""
        assert (NAME_MIN_PX, NAME_MIN_PX_SPANNING, MONEY_MIN_PX) == (30.0, 13.0, 11.0)
        assert REF_LINE_MIN_GAP_PX == 12.0

    def test_single_column_name_appears_exactly_at_thirty_pixels(self) -> None:
        assert low_block(two_layer_program(["gl"]), 300.0).height_px == 30.0
        assert low_block(two_layer_program(["gl"]), 300.0).show_name
        assert not low_block(two_layer_program(["gl"]), 290.0).show_name

    def test_a_spanning_block_gets_the_lower_bar(self) -> None:
        """13px is enough for a block wide enough to hold the text on one
        line; the same height in a single column is not."""
        wide = low_block(two_layer_program(["gl", "al"]), 130.0)
        narrow = low_block(two_layer_program(["gl"]), 130.0)
        assert wide.height_px == narrow.height_px == 13.0
        assert wide.spans_columns == 2 and wide.show_name
        assert narrow.spans_columns == 1 and not narrow.show_name

    def test_money_survives_a_block_too_short_for_a_name(self) -> None:
        block = low_block(two_layer_program(["gl"]), 110.0)
        assert block.height_px == 11.0
        assert not block.show_name
        assert block.show_money
        # too short to say who it is, so it says how big it is
        assert block.lines == (layer_terms(0, 1_000_000),)

    def test_money_drops_below_eleven_pixels(self) -> None:
        block = low_block(two_layer_program(["gl"]), 100.0)
        assert block.height_px == 10.0
        assert not block.show_money
        assert block.lines == ()

    def test_a_named_block_shows_its_own_premium_share(self) -> None:
        program = two_layer_program(["gl"])
        low = next(b for b in build_layout(program, gamma=1.0).layers if b.layer_id == "low")
        assert low_block(program, 300.0).lines == (
            layer_heading(low, follows=False),
            participant_label("A", 10_000),
            block_premium_label(100_000, 10_000),
        )

    def test_the_panel_box_is_not_the_drawing_area(self) -> None:
        """D4's silent failure, made visible: the prototype's outer panel is
        340px while the region the [0,1] coordinates are drawn into is
        ~240px. Passing the outer box makes a block look ~42% taller than it
        renders and UNDER-fires the drop rule — it keeps a name the panel has
        no room for."""
        program = two_layer_program(["gl"])
        assert not low_block(program, 240.0).show_name  # 24px: no room
        assert low_block(program, 340.0).show_name  # 34px: the lie

    def test_a_non_positive_height_is_refused(self) -> None:
        with pytest.raises(ValueError):
            build_web_tower(two_layer_program(["gl"]), 0.0)


class TestReferenceLines:
    def test_labels_and_heights_come_from_the_scale(self) -> None:
        program = load_program(SAMPLE)
        web = build_web_tower(program, 240.0)
        for ref in web.ref_lines:
            assert (ref.dollars, ref.y) in web.layout.ref_lines
            assert ref.label == format_money_compact(ref.dollars)
        assert web.top_dollars == web.layout.ymap.max_dollars

    def test_the_top_of_the_tower_is_always_kept(self) -> None:
        program = load_program(SAMPLE)
        web = build_web_tower(program, 40.0)  # absurdly short: thins hard
        assert web.ref_lines[0].dollars == web.layout.ymap.max_dollars

    def test_crowded_marks_are_thinned(self) -> None:
        """Under gamma < 1 the low breakpoints sit within a few pixels of
        each other. Shorter chart, fewer marks — never more."""
        program = load_program(SAMPLE)
        tall = build_web_tower(program, 600.0).ref_lines
        short = build_web_tower(program, 120.0).ref_lines
        assert len(short) < len(tall)
        kept = {ref.dollars for ref in short}
        assert kept <= {ref.dollars for ref in tall}
        # no two survivors are closer than the gap the constant names
        ys = [ref.y for ref in short]
        gaps = [abs(a - b) for a, b in zip(ys, ys[1:], strict=False)]
        assert all(gap >= REF_LINE_MIN_GAP_PX / 120.0 for gap in gaps)

    def test_the_wider_attachment_wins_a_tie(self) -> None:
        """Two attachments a hair apart. Thinning walks DOWN from the top, so
        the narrow one is reached first and provisionally kept; the wider one
        just below must displace it, because it is the one a reader needs
        named. Without the tie-break the narrow attachment survives and the
        wide one is silently dropped."""
        program = make_program(
            ["gl", "al", "pl"],
            [
                layer("base", ["gl", "al", "pl"], 0, 10_000_000, [("A", 10_000)]),
                layer("wide", ["gl", "al", "pl"], 10_000_000, 90_000_000,
                      [("A", 10_000)]),
                layer("narrow", ["gl"], 10_100_000, 89_900_000, [("A", 10_000)]),
            ],
        )
        kept = {ref.dollars for ref in build_web_tower(program, 200.0).ref_lines}
        assert 10_000_000 in kept, "the wider attachment lost the tie"
        assert 10_100_000 not in kept

    def test_statutory_only_program_has_no_top_of_tower(self) -> None:
        """Statutory cover contributes no breakpoints, so there is no dollar
        scale at all — a '$0 top of tower' would be a nonsense the panel
        would print in the gutter."""
        program = make_program(
            ["wc"], [layer("wc", ["wc"], 0, 0, [("A", 10_000)], statutory=True)]
        )
        web = build_web_tower(program, 240.0)
        assert web.top_dollars is None
        assert web.ref_lines == ()
        assert web.layout.chevrons  # the bar is still drawn, off-scale
        assert web.layers[0].terms == "Statutory"


class TestCaveat:
    def test_present_whenever_the_scale_is_compressed(self) -> None:
        web = build_web_tower(load_program(SAMPLE), 240.0)
        assert web.caveat is not None
        assert "0.35" in web.caveat

    def test_absent_on_a_linear_scale(self) -> None:
        web = build_web_tower(load_program(SAMPLE), 240.0, gamma=1.0)
        assert web.caveat is None


class TestAgreement:
    """R66: the renderers must agree about the FACTS a block asserts. They
    may differ about which candidate string a fitter chose to assert them
    with, so these assert the facts, not fitted strings."""

    def _universe(self, program: Program, tower) -> set[str]:
        """Every string labels.py produces for this program. Built here by
        calling labels.py directly, so a string web.py composed itself
        cannot be in it."""
        follows = {ly.id for ly in program.layers if ly.follows_underlying}
        out: set[str] = set()
        for block in tower.layers:
            out.add(layer_heading(block, follows=block.layer_id in follows))
            out.add(
                layer_terms(block.attach, block.limit, statutory=block.statutory)
            )
        for part in tower.participants:
            owner = next(
                ly for ly in tower.layers if ly.layer_id == part.layer_id
            )
            if part.carrier is None:
                # BOTH pending readings: this test deliberately cannot see a
                # flipped pending predicate — test_pending_is_decided_once
                # is the one that can.
                out.add(unplaced_label(part.share_bps, True))
                out.add(unplaced_label(part.share_bps, False))
            else:
                out.add(participant_label(part.carrier, part.share_bps))
                out.add(carrier_only_label(part.carrier))
                premium = block_premium_label(owner.premium, part.share_bps)
                if premium is not None:
                    out.add(premium)
        return out

    def test_no_block_string_is_composed_here(self) -> None:
        program = load_program(SAMPLE)
        tower = build_layout(program)
        universe = self._universe(program, tower)
        web = build_web_tower(program, 240.0)
        emitted: set[str] = set()
        for block in web.blocks:
            emitted.update(block.name_forms)
            emitted.add(block.terms)
            if block.heading is not None:
                emitted.add(block.heading)
            if block.premium is not None:
                emitted.add(block.premium)
        assert emitted, "no block text at all — the test would pass vacuously"
        assert emitted <= universe, sorted(emitted - universe)

    def test_a_carrier_block_offers_the_narrow_form_too(self) -> None:
        """carrier_only_label is the narrowest non-empty rung the schematic
        already falls through to. The panel cannot measure width, so it hands
        the caller the ladder rather than choosing — narrowing WITHIN it is
        fit, which R66 permits to differ."""
        program = load_program(SAMPLE)
        web = build_web_tower(program, 240.0)
        placed = [b for b in web.blocks if b.carrier is not None]
        assert placed
        for block in placed:
            assert block.name_forms[0] == participant_label(
                block.carrier, block.share_bps
            )
            assert block.name_forms[-1] == carrier_only_label(block.carrier)
        assert any(len(b.name_forms) > 1 for b in placed)

    def test_group_and_retention_text_is_quoted_too(self) -> None:
        program = make_program(
            ["gl", "al"],
            [layer("l", ["gl", "al"], 0, 10_000_000, [("A", 10_000)],
                   premium=500_000)],
            retentions=[
                Retention(
                    type=RetentionType.SIR,
                    amount=250_000,
                    applies_to=["gl"],
                    vehicle=None,
                )
            ],
            groups={"gl": "Casualty", "al": "Casualty"},
        )
        tower = build_layout(program)
        web = build_web_tower(program, 240.0)
        assert [g.label for g in web.groups] == [
            group_label(band) for band in tower.groups
        ]
        assert [r.label for r in web.retentions] == [
            retention_label(r.type, r.amount, r.vehicle) for r in tower.retentions
        ]

    def test_the_heading_goes_where_labels_py_says(self) -> None:
        """heading_blocks gives the layer name to the WIDEST block — a narrow
        lead share must not doom the name. Handing it to the layer's first
        block instead is the divergence this catches."""
        program = make_program(
            ["gl"],
            [layer("l", ["gl"], 0, 10_000_000, [("Small", 2_000), ("Big", 8_000)])],
        )
        tower = build_layout(program)
        owner = heading_blocks(tower.participants)
        assert owner == {"l": 1}, "fixture no longer exercises the rule"
        web = build_web_tower(program, 240.0)
        carrying = [i for i, b in enumerate(web.blocks) if b.heading is not None]
        assert carrying == sorted(owner.values())
        for layer_id, index in owner.items():
            assert web.blocks[index].layer_id == layer_id
            assert web.blocks[index].heading == layer_heading(
                next(ly for ly in tower.layers if ly.layer_id == layer_id),
                follows=False,
            )

    def test_pending_is_decided_once(self) -> None:
        """'To be placed' (nobody signed) and an open remainder are different
        claims about the world. The predicate is is_pending, and nothing may
        re-derive it."""
        program = make_program(
            ["gl"],
            [
                layer("nobody", ["gl"], 0, 5_000_000, []),
                layer("partial", ["gl"], 5_000_000, 5_000_000, [("A", 6_000)]),
            ],
        )
        tower = build_layout(program)
        web = build_web_tower(program, 240.0)
        unplaced = {b.layer_id: b for b in web.blocks if b.carrier is None}
        assert unplaced["nobody"].name == unplaced_label(10_000, pending=True)
        assert unplaced["partial"].name == unplaced_label(4_000, pending=False)
        assert [ly.layer_id for ly in web.layers if ly.pending] == ["nobody"]
        assert is_pending(next(b for b in tower.layers if b.layer_id == "nobody"))

    def test_the_label_rides_the_widest_piece_of_a_stepped_block(self) -> None:
        """A follows-underlying block has a STEPPED bottom, so its pieces are
        different heights. The graphic puts the label on the widest piece
        (`mpl_program._participant_label`); the height that gates the drop
        rule has to be that same piece's, or the two renderers are deciding
        about different parts of one block."""
        program = make_program(
            ["gl", "al"],
            [
                layer("ugl", ["gl"], 0, 10_000_000, [("X", 10_000)]),
                layer("ual", ["al"], 0, 20_000_000, [("X", 10_000)]),
                Layer(
                    id="f",
                    name="f",
                    applies_to=["gl", "al"],
                    attach=20_000_000,
                    limit=30_000_000,
                    follows_underlying=True,
                    participants=[
                        Participant(carrier="A", share_bps=3_000),
                        Participant(carrier="B", share_bps=7_000),
                    ],
                ),
            ],
        )
        tower = build_layout(program)
        stepped = next(p for p in tower.participants if p.carrier == "B")
        heights = {round(r.height, 9) for r in stepped.rects}
        assert len(heights) > 1, "fixture no longer produces a stepped block"
        widest = max(stepped.rects, key=lambda r: r.width)
        assert widest.height != min(stepped.rects, key=lambda r: r.width).height
        block = next(
            b for b in build_web_tower(program, 240.0).blocks if b.carrier == "B"
        )
        assert block.height_px == widest.height * 240.0

    def test_geometry_is_passed_through_not_recomputed(self) -> None:
        """TowerLayout is a frozen dataclass of tuples, so this is exact and
        needs no tolerance."""
        program = load_program(SAMPLE)
        tower = build_layout(program)
        web = build_web_tower(program, 240.0)
        assert web.layout == tower
        assert [b.rects for b in web.blocks] == [
            p.rects for p in tower.participants
        ]
        assert [(ly.y0, ly.y1, ly.outlines) for ly in web.layers] == [
            (b.y0, b.y1, b.outlines) for b in tower.layers
        ]
        assert [r.rects for r in web.retentions] == [
            r.rects for r in tower.retentions
        ]
