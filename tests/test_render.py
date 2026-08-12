"""Renderer tests. Determinism first — it is the premise of the project."""

from pathlib import Path

import pytest

from towerkit.model import load_program
from towerkit.render.mpl_program import render_program
from towerkit.theme import load_theme

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


@pytest.fixture(scope="module")
def program():
    return load_program(SAMPLE)


@pytest.fixture(scope="module")
def theme():
    return load_theme(REPO / "themes" / "marsh.json")


class TestDeterminism:
    def test_two_renders_are_byte_identical_svg(self, program, theme, tmp_path) -> None:
        a = render_program(program, theme, tmp_path / "a", "tower", ["svg"])[0]
        b = render_program(program, theme, tmp_path / "b", "tower", ["svg"])[0]
        assert a.read_bytes() == b.read_bytes()

    def test_two_renders_are_byte_identical_pdf(self, program, theme, tmp_path) -> None:
        a = render_program(program, theme, tmp_path / "a", "tower", ["pdf"])[0]
        b = render_program(program, theme, tmp_path / "b", "tower", ["pdf"])[0]
        assert a.read_bytes() == b.read_bytes()

    def test_no_wall_clock_in_svg(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "tower", ["svg"])[0]
        text = out.read_text()
        assert "<dc:date>" not in text


class TestOutput:
    def test_all_formats_written(self, program, theme, tmp_path) -> None:
        written = render_program(program, theme, tmp_path, "t", ["svg", "pdf", "png"])
        assert [p.suffix for p in written] == [".svg", ".pdf", ".png"]
        assert all(p.stat().st_size > 1000 for p in written)

    def test_unknown_format_rejected(self, program, theme, tmp_path) -> None:
        with pytest.raises(ValueError, match="unsupported format"):
            render_program(program, theme, tmp_path, "t", ["bmp"])

    def test_dollar_and_special_chars_survive(self, theme, tmp_path) -> None:
        # The old prototype's mathtext escaping broke on $, _ and ^ in names.
        program = load_program(SAMPLE)
        mutated = program.model_copy(deep=True)
        mutated.insured = "Lloyd's $yndicate_1 ^Alpha Ltd"
        out = render_program(mutated, theme, tmp_path, "t", ["svg"])[0]
        assert "$yndicate_1 ^Alpha" in out.read_text()

    def test_provenance_is_real_not_hardcoded(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        text = out.read_text()
        assert "a3f19c2" not in text  # the prototype's fake SHA must be gone
        assert "towerkit 0.1" in text

    def test_no_scale_caveat_drawn(self, program, theme, tmp_path) -> None:
        # Removed at the user's request (review round 3); see DECISIONS.md.
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "NOT TO SCALE" not in out.read_text()


class TestTotalsToggle:
    def test_totals_shown_by_default(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "Total limit" in out.read_text()

    def test_no_totals_omits_the_header_line(self, program, theme, tmp_path) -> None:
        out = render_program(
            program, theme, tmp_path, "t", ["svg"], show_totals=False
        )[0]
        assert "Total limit" not in out.read_text()


class TestLongCarrierNames:
    def test_long_name_wraps_instead_of_degrading_to_initial(self, theme, tmp_path) -> None:
        from datetime import date

        from towerkit.model import Layer, Line, Participant, Period, Placement, Program

        program = Program(
            insured="X",
            program="Y",
            placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(
                    id="p",
                    name="Primary",
                    applies_to=["gl"],
                    attach=0,
                    limit=5_000_000,
                    participants=[
                        Participant(
                            carrier="Indian Harbor Insurance Company", share_bps=10_000
                        )
                    ],
                )
            ],
        )
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "Indian Harbor" in out.read_text()


class TestLayerNoteFootnotes:
    def test_notes_render_as_footnotes_with_markers(self, program, theme, tmp_path) -> None:
        # the sample's 3rd Excess carries a note
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        text = out.read_text()
        assert "¹ 3rd Excess: 20% open at renewal" in text
        # the marked title itself renders as paths (halo path-effects), so it
        # leaves no text comment to assert on — verified visually


class TestCellPremiums:
    def test_cell_premiums_show_per_carrier_figures(self, program, theme, tmp_path) -> None:
        out = render_program(
            program, theme, tmp_path, "t", ["svg"], cell_premiums=True
        )[0]
        text = out.read_text()
        # AIG 60% of the $3.9M umbrella premium
        assert "$2.34M" in text

    def test_off_by_default(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "$2.34M" not in out.read_text()

    def test_suppressed_when_premiums_hidden(self, program, theme, tmp_path) -> None:
        out = render_program(
            program, theme, tmp_path, "t", ["svg"],
            show_premiums=False, cell_premiums=True,
        )[0]
        assert "$2.34M" not in out.read_text()


class TestLayerTerms:
    def test_primary_shows_limit_only_no_xs_zero(self) -> None:
        from towerkit.render.mpl_program import layer_terms

        assert layer_terms(0, 5_000_000) == "$5M"
        assert layer_terms(2_000_000, 25_000_000) == "$25M xs $2M"


class TestCellDates:
    def test_policy_term_between_share_and_premium(self, program, theme, tmp_path) -> None:
        out = render_program(
            program, theme, tmp_path, "t", ["svg"],
            cell_premiums=True, cell_dates=True,
        )[0]
        text = out.read_text()
        # program period on most layers; property runs on its own inception
        assert "1/1/26 – 1/1/27" in text
        assert "4/1/26 – 4/1/27" in text

    def test_off_by_default(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "1/1/26 – 1/1/27" not in out.read_text()


class TestPendingLayers:
    def make_pending_program(self):
        from datetime import date

        from towerkit.model import Layer, Line, Participant, Period, Placement, Program

        return Program(
            insured="X", program="Y", placement=Placement.PROPOSED,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(id="p", name="Primary", applies_to=["gl"], attach=0,
                      limit=5_000_000,
                      participants=[Participant(carrier="Zurich", share_bps=8_000)]),
                Layer(id="x", name="1st Excess", applies_to=["gl"],
                      attach=5_000_000, limit=10_000_000, participants=[]),
            ],
        )

    def test_pending_layer_says_to_be_placed_with_dashed_outline(self, theme, tmp_path) -> None:
        out = render_program(self.make_pending_program(), theme, tmp_path, "t", ["svg"])[0]
        text = out.read_text()
        assert "To be placed" in text
        assert "stroke-dasharray" in text  # the dashed pending outline
        # the partially-open primary keeps its distinct treatment
        assert "20% open" in text


class TestLayerNameReliability:
    def test_name_survives_narrow_lead_share_with_extras_on(self, theme, tmp_path) -> None:
        from datetime import date

        from towerkit.model import Layer, Line, Participant, Period, Placement, Program

        # lead carrier holds only 10% — the old leftmost-heading rule starved
        # the title; it must ride the widest cell and keep rendering
        program = Program(
            insured="X", program="Y", placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id=f"l{i}", name=f"Line {i}") for i in range(6)],
            layers=[
                *[
                    Layer(id=f"p{i}", name=f"Primary {i}", applies_to=[f"l{i}"],
                          attach=0, limit=1_000_000,
                          participants=[Participant(carrier="Zurich", share_bps=10_000)])
                    for i in range(6)
                ],
                Layer(
                    id="xs", name="Umbrella Excess", applies_to=[f"l{i}" for i in range(6)],
                    attach=1_000_000, limit=25_000_000, premium=1_000_000,
                    participants=[
                        Participant(carrier="Small Lead Co", share_bps=1_000),
                        Participant(carrier="AIG", share_bps=9_000),
                    ],
                ),
            ],
        )
        out = render_program(
            program, theme, tmp_path, "t", ["svg"], cell_premiums=True, cell_dates=True
        )[0]
        assert "Umbrella Excess" in out.read_text()


def test_available_themes_includes_packaged_from_any_cwd(tmp_path, monkeypatch) -> None:
    from towerkit.theme import available_themes

    monkeypatch.chdir(tmp_path)  # no ./themes here — like the jump from bookkit
    stems = {path.stem for path in available_themes()}
    assert "marsh" in stems and "default" in stems


def test_available_themes_cwd_overrides_packaged(tmp_path, monkeypatch) -> None:
    from towerkit.theme import available_themes

    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "marsh.json").write_text('{"name": "local marsh"}')
    monkeypatch.chdir(tmp_path)
    marsh = next(path for path in available_themes() if path.stem == "marsh")
    # relative on purpose — stored render.theme paths stay portable
    assert marsh == Path("themes") / "marsh.json"
