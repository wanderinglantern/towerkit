"""SOI mapping and theming: pure logic, no Excel here."""

import datetime

from towerkit.model import Layer, Placement, Program
from towerkit.soi import (
    PROGRAM_WIDE,
    SoiRow,
    SoiSection,
    SoiStatus,
    build_soi,
    carrier_text,
    coverage_text,
    default_filename,
    limits_text,
    premium_value,
    retention_text,
    sheet_title,
)
from towerkit.theme import SoiStyle, _theme_from_jsonable, load_theme


def make_program() -> Program:
    """Exercises every SOI mapping rule: a grouped tower with quota share,
    a pending layer, an ungrouped line with a sublimit, a same-group
    umbrella, and a cross-group follows-underlying umbrella."""
    return Program.model_validate(
        {
            "insured": "Atomic Industries, LLC",
            "program": "Casualty",
            "placement": "bound",
            "period": {"start": "2026-01-01", "end": "2027-01-01"},
            "lines": [
                {"id": "gl", "name": "General Liability", "group": "Casualty"},
                {"id": "al", "name": "Auto Liability", "group": "Casualty"},
                {"id": "prop", "name": "Property"},
            ],
            "layers": [
                {
                    "id": "gl-primary", "name": "Primary", "appliesTo": ["gl"],
                    "attach": 0, "limit": 1_000_000, "premium": 50_000,
                    "policyNumber": "GL-123",
                    "period": {"start": "2026-02-01", "end": "2027-02-01"},
                    "participants": [{"carrier": "Zenith", "share_bps": 10_000}],
                },
                {
                    "id": "gl-x1", "name": "1st Excess", "appliesTo": ["gl"],
                    "attach": 1_000_000, "limit": 4_000_000, "premium": 30_000,
                    "participants": [
                        {"carrier": "Alpha Re", "share_bps": 6_000},
                        {"carrier": "Beta Syndicate", "share_bps": 4_000},
                    ],
                },
                {
                    "id": "al-primary", "name": "Primary", "appliesTo": ["al"],
                    "attach": 0, "limit": 1_000_000,
                },
                {
                    "id": "prop-primary", "name": "Primary", "appliesTo": ["prop"],
                    "attach": 0, "limit": 10_000_000, "premium": 80_000,
                    "participants": [{"carrier": "Gamma", "share_bps": 10_000}],
                },
                {
                    "id": "casualty-umbrella", "name": "Umbrella",
                    "appliesTo": ["gl", "al"], "attach": 5_000_000,
                    "limit": 5_000_000, "premium": 20_000,
                    "participants": [{"carrier": "Zenith", "share_bps": 10_000}],
                },
                {
                    "id": "program-umbrella", "name": "Program Umbrella",
                    "appliesTo": ["gl", "prop"], "followsUnderlying": True,
                    "attach": 0, "limit": 25_000_000, "premium": 40_000,
                    "participants": [{"carrier": "Delta", "share_bps": 10_000}],
                },
            ],
            "retentions": [
                {"appliesTo": ["gl", "al"], "type": "sir", "amount": 250_000,
                 "aggregate": 1_000_000},
                {"appliesTo": ["prop"], "type": "deductible", "amount": 100_000},
            ],
            "sublimits": [
                {"name": "Flood", "amount": 5_000_000, "appliesTo": ["prop"]},
            ],
        }
    )


def _statutory_layer(**kw) -> Layer:
    base = dict(
        id="wc-stat", name="Workers Compensation", applies_to=["wc"],
        attach=0, limit=0, statutory=True,
    )
    return Layer(**{**base, **kw})


class TestSoiStyle:
    def test_default_theme_mirrors_the_sample_workbook(self) -> None:
        soi = load_theme(None).soi
        assert soi.header_fill == "#003865"
        assert soi.header_text == "#FFFFFF"
        assert soi.body_text == "#3D3C37"
        assert soi.band_fill == "#F7F3EE"
        assert soi.border == "#B9B6B1"
        assert soi.font == "Noto Sans"
        assert soi.size == 11

    def test_theme_json_overrides(self) -> None:
        theme = _theme_from_jsonable({"name": "x", "soi": {"headerFill": "#000F47"}})
        assert theme.soi.header_fill == "#000F47"
        assert theme.soi.band_fill == "#F7F3EE"  # untouched defaults survive

    def test_light_header_fill_never_gets_white_text(self) -> None:
        soi = SoiStyle(header_fill="#CEECFF")  # Marsh Sky
        assert soi.effective_header_text == soi.body_text

    def test_dark_header_fill_keeps_declared_text(self) -> None:
        assert SoiStyle().effective_header_text == "#FFFFFF"


class TestCompositionHelpers:
    def test_sole_full_share_carrier_is_plain(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[0]) == "Zenith"

    def test_quota_share_lists_carriers_with_shares(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[1]) == "Alpha Re (60%), Beta Syndicate (40%)"

    def test_no_participants_reads_to_be_placed(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[2]) == "To be placed"

    def test_primary_limits_quoted_by_limit_alone(self) -> None:
        p = make_program()
        assert limits_text(p.layers[2], p) == "$1,000,000"

    def test_excess_limits_use_xs(self) -> None:
        p = make_program()
        assert limits_text(p.layers[1], p) == "$4,000,000 xs $1,000,000"

    def test_follows_underlying_reads_xs_underlying(self) -> None:
        p = make_program()
        assert limits_text(p.layers[5], p).startswith("$25,000,000 xs underlying")

    def test_sublimits_appended_to_composed_limits(self) -> None:
        p = make_program()
        assert limits_text(p.layers[3], p) == "$10,000,000; Sublimit: Flood $5,000,000"

    def test_limits_detail_wins_verbatim(self) -> None:
        p = make_program()
        p.layers[0].limits_detail = "Each Occurrence $1,000,000"
        assert limits_text(p.layers[0], p) == "Each Occurrence $1,000,000"

    def test_primary_retention_composed_with_aggregate(self) -> None:
        p = make_program()
        assert retention_text(p.layers[0], p) == "SIR $250,000; Aggregate $1,000,000"

    def test_excess_retention_blank(self) -> None:
        p = make_program()
        assert retention_text(p.layers[1], p) == ""

    def test_follows_layer_counts_as_excess_for_retention(self) -> None:
        p = make_program()
        assert retention_text(p.layers[5], p) == ""

    def test_retention_detail_wins_verbatim(self) -> None:
        p = make_program()
        p.layers[1].retention_detail = "See policy."
        assert retention_text(p.layers[1], p) == "See policy."

    def test_sole_layer_on_line_shows_line_name(self) -> None:
        # Drop the cross-group umbrella so Property genuinely has one layer.
        p = make_program()
        p.layers = [layer for layer in p.layers if layer.id != "program-umbrella"]
        assert coverage_text(p.layers[3], p) == "Property"

    def test_towered_by_umbrella_line_appends_layer_name(self) -> None:
        # The cross-group umbrella makes Property a two-layer line.
        p = make_program()
        assert coverage_text(p.layers[3], p) == "Property — Primary"

    def test_towered_line_appends_layer_name(self) -> None:
        p = make_program()
        assert coverage_text(p.layers[0], p) == "General Liability — Primary"
        assert coverage_text(p.layers[1], p) == "General Liability — 1st Excess"

    def test_multi_line_layer_uses_layer_name_plus_line_labels(self) -> None:
        p = make_program()
        assert coverage_text(p.layers[4], p) == "Umbrella (GL, AL)"


class TestBuildSoi:
    def test_sections_named_groups_then_ungrouped_then_program_wide(self) -> None:
        sections = build_soi(make_program())
        assert [s.label for s in sections] == ["Casualty", None, PROGRAM_WIDE]

    def test_casualty_rows_ordered_line_then_attach(self) -> None:
        casualty = build_soi(make_program())[0]
        assert [r.coverage for r in casualty.rows] == [
            "General Liability — Primary",
            "General Liability — 1st Excess",
            "Umbrella (GL, AL)",          # same-group umbrella stays in its group
            "Auto Liability — Primary",
        ]

    def test_cross_group_layer_lands_program_wide(self) -> None:
        wide = build_soi(make_program())[2]
        assert [r.coverage for r in wide.rows] == ["Program Umbrella (GL, Property)"]

    def test_section_premium_totals(self) -> None:
        sections = build_soi(make_program())
        assert sections[0].premium_total == 100_000  # 50k + 30k + 20k, pending=0
        assert sections[1].premium_total == 80_000
        assert sections[2].premium_total == 40_000

    def test_row_period_falls_back_to_program(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[0].effective == datetime.date(2026, 2, 1)   # layer override
        assert rows[1].effective == datetime.date(2026, 1, 1)   # program period

    def test_insured_and_policy_number(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[0].insured == "Atomic Industries, LLC"
        assert rows[0].policy_number == "GL-123"
        assert rows[1].policy_number == ""


class TestNaming:
    def test_sheet_title_years_from_period(self) -> None:
        assert sheet_title(make_program()) == "Casualty SOI - 26-27"

    def test_sheet_title_truncated_to_31_chars(self) -> None:
        p = make_program()
        p.program = "An Extremely Long Program Name That Overflows"
        title = sheet_title(p)
        assert len(title) <= 31
        assert title.endswith(" SOI - 26-27")

    def test_sheet_title_strips_illegal_chars(self) -> None:
        p = make_program()
        p.program = "Cas[ual]ty: Pro/gram?"
        assert "[" not in sheet_title(p) and ":" not in sheet_title(p)

    def test_default_filename(self) -> None:
        # period years in the filename: renewal-year exports must not
        # clobber each other (26-27 vs 27-28 are distinct files)
        assert (
            default_filename(make_program())
            == "Atomic Industries, LLC - Schedule of Insurance 26-27.xlsx"
        )

    def test_default_filename_replaces_path_hostile_chars(self) -> None:
        p = make_program()
        p.insured = "A/B: C"
        assert "/" not in default_filename(p) and ":" not in default_filename(p)


# --- C1/C2: every row says whether the cover is bound -------------------------


def unbound_with_premium() -> Program:
    """The fixture, with a premium on the layer that has NO participants —
    the exact shape the CFO review flagged: money on a row whose cover does
    not exist yet."""
    p = make_program()
    next(layer for layer in p.layers if layer.id == "al-primary").premium = 25_000
    return p


class TestRowStatus:
    def test_placed_layer_in_a_bound_program_reads_bound(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[0].status is SoiStatus.BOUND

    def test_layer_without_participants_reads_to_be_placed(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[3].coverage == "Auto Liability — Primary"
        assert rows[3].status is SoiStatus.TO_BE_PLACED

    def test_proposed_program_never_says_bound(self) -> None:
        p = make_program()
        p.placement = Placement.PROPOSED
        assert build_soi(p)[0].rows[0].status is SoiStatus.PROPOSED

    def test_shares_that_do_not_close_read_partially_bound(self) -> None:
        p = make_program()
        gl_x1 = next(layer for layer in p.layers if layer.id == "gl-x1")
        gl_x1.participants = gl_x1.participants[:1]  # 60% signed, 40% open
        assert build_soi(p)[0].rows[1].status is SoiStatus.PARTIALLY_BOUND

    def test_status_is_carried_on_the_row_not_the_section_label(self) -> None:
        """C2: the marker belongs to the row. Sections keep their own labels."""
        sections = build_soi(make_program())
        assert sections[0].label == "Casualty"
        assert all(row.status is not None for s in sections for row in s.rows)

    def test_an_unstated_status_is_not_bound(self) -> None:
        """The contract for callers that build SoiRow themselves (bookkit):
        status defaults to None and None never counts as cover in force."""
        row = SoiRow(
            insured="X", coverage="Y", carrier="Z", policy_number="",
            effective=datetime.date(2026, 1, 1), expiration=datetime.date(2027, 1, 1),
            limits="", retention="", premium=10_000,
        )
        assert row.status is None
        assert row.is_bound is False


class TestBoundAndUnboundSubtotals:
    def test_bound_subtotal_excludes_an_unbound_rows_premium(self) -> None:
        casualty = build_soi(unbound_with_premium())[0]
        assert casualty.bound_premium_total == 100_000

    def test_unbound_subtotal_holds_exactly_that_premium(self) -> None:
        casualty = build_soi(unbound_with_premium())[0]
        assert casualty.unbound_premium_total == 25_000

    def test_the_two_subtotals_account_for_every_row(self) -> None:
        casualty = build_soi(unbound_with_premium())[0]
        assert casualty.premium_total == 125_000
        assert casualty.bound_premium_total + casualty.unbound_premium_total == 125_000

    def test_rows_of_unknown_status_land_in_the_unbound_subtotal(self) -> None:
        row = SoiRow(
            insured="X", coverage="Y", carrier="Z", policy_number="",
            effective=datetime.date(2026, 1, 1), expiration=datetime.date(2027, 1, 1),
            limits="", retention="", premium=7_000,
        )
        section = SoiSection(label="L", rows=(row,))
        assert section.bound_premium_total == 0
        assert section.unbound_premium_total == 7_000


# --- C10: the statutory package ----------------------------------------------


class TestStatutoryPackage:
    def test_statutory_reads_state_limits(self) -> None:
        assert limits_text(_statutory_layer(), make_program()) == "Statutory - State Limits"

    def test_limits_detail_still_wins_over_the_phrase(self) -> None:
        layer = _statutory_layer(limits_detail="Benefits as required by NY state law")
        assert limits_text(layer, make_program()) == "Benefits as required by NY state law"

    def test_a_shared_retention_is_stated_once(self) -> None:
        """The SIR spans GL and AL, and both are primaries. It belongs to the
        first row that carries it; a second statement invites the reader to
        conclude there are two retentions."""
        rows = build_soi(make_program())[0].rows
        assert rows[0].retention == "SIR $250,000; Aggregate $1,000,000"
        assert rows[3].retention == ""

    def test_an_unshared_retention_still_prints(self) -> None:
        rows = build_soi(make_program())[1].rows
        assert rows[0].retention == "Deductible $100,000"

    def test_prose_on_the_first_primary_claims_the_shared_retention(self) -> None:
        p = make_program()
        p.layers[0].retention_detail = "Captive retention $500,000 per occurrence"
        rows = build_soi(p)[0].rows
        assert rows[0].retention == "Captive retention $500,000 per occurrence"
        assert rows[3].retention == ""

    def test_prose_on_an_excess_layer_does_not_claim_it(self) -> None:
        """Only a primary states a retention, so prose on an excess layer must
        not silence the primary that actually carries it.

        The excess has to sort AHEAD of that primary for this to be capable of
        failing: the SIR is moved onto AL alone, and the Casualty umbrella
        (excess, spanning GL and AL) is row 2 while the AL primary is row 3."""
        p = make_program()
        p.retentions[0].applies_to = ["al"]
        p.layers[4].retention_detail = "See policy."
        rows = build_soi(p)[0].rows
        assert rows[2].coverage == "Umbrella (GL, AL)"
        assert rows[2].retention == "See policy."
        assert rows[3].coverage == "Auto Liability — Primary"
        assert rows[3].retention == "SIR $250,000; Aggregate $1,000,000"

    def test_zero_premium_reads_included_not_free(self) -> None:
        """$0.00 reads as free cover; a zero premium means it is priced with
        another layer (WC Part B with Part A)."""
        assert premium_value(_row_with_premium(0)) == "Included"

    def test_a_real_premium_stays_a_number(self) -> None:
        assert premium_value(_row_with_premium(50_000)) == 50_000

    def test_an_absent_premium_stays_blank(self) -> None:
        assert premium_value(_row_with_premium(None)) is None


def _row_with_premium(premium: int | None) -> SoiRow:
    return SoiRow(
        insured="X", coverage="Y", carrier="Z", policy_number="",
        effective=datetime.date(2026, 1, 1), expiration=datetime.date(2027, 1, 1),
        limits="", retention="", premium=premium,
    )
