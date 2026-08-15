"""ASCII preview: structural checks, driven only by the shared geometry."""

from datetime import date
from pathlib import Path

from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Placement,
    Program,
    load_program,
)
from towerkit.render.ascii import UNPLACED, ZERO, ansi256, render_ascii
from towerkit.theme import load_theme

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def render(colour: bool = False, **kw) -> str:
    program = load_program(SAMPLE)
    theme = load_theme()
    return render_ascii(program, theme, colour=colour, **kw)


def _wc_program(*layers: Layer) -> Program:
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=list(layers),
    )


def test_no_statutory_cover_reserves_no_extra_row() -> None:
    """The chevron band must cost nothing when absent. Nothing else in this
    file pins an absolute row index — every other test locates rows
    dynamically or checks substring presence — so without this, an
    unconditionally reserved top row would silently insert a blank row at
    the top of every chart (grid sizing compensates so total line count
    stays 26, which is why this checks row 0's content, not the line count).

    Row 0 of the SAMPLE render is fully covered by the top layer, so it must
    never be blank; an unconditionally reserved chevron row renders row 0 as
    an empty string (nothing drawn into it, then rstripped away).
    """
    out = render(height=26)
    lines = out.splitlines()
    assert len(lines) == 26
    assert lines[0] != ""
    assert "top" in lines[0]


def test_statutory_draws_a_caret_row() -> None:
    program = _wc_program(
        Layer(
            id="wc-stat", name="Workers Compensation", applies_to=["wc"],
            attach=0, limit=0, statutory=True,
            participants=[Participant(carrier="Travelers", share_bps=10_000)],
        )
    )
    out = render_ascii(program, load_theme(), colour=False)
    assert "^" in out.splitlines()[0]


def test_no_caret_row_without_statutory_cover() -> None:
    program = _wc_program(
        Layer(
            id="gl-primary", name="Primary", applies_to=["wc"],
            attach=0, limit=5_000_000,
            participants=[Participant(carrier="A", share_bps=10_000)],
        )
    )
    assert "^" not in render_ascii(program, load_theme(), colour=False)


class TestStructure:
    def test_heavy_zero_line_present(self) -> None:
        out = render()
        zero_rows = [line for line in out.splitlines() if line.count(ZERO) > 20]
        assert len(zero_rows) == 1

    def test_retention_blocks_below_zero_line(self) -> None:
        lines = render().splitlines()
        zero_idx = next(i for i, line in enumerate(lines) if ZERO * 10 in line)
        below = "\n".join(lines[zero_idx + 1 :])
        above = "\n".join(lines[:zero_idx])
        assert "▒" in below
        assert "▒" not in above  # retention never floats up into the tower

    def test_unplaced_capacity_is_hatched(self) -> None:
        assert UNPLACED in render()  # 3rd Excess is 80% placed

    def test_not_to_scale_caveat_when_compressed(self) -> None:
        assert "(not to scale)" in render()
        assert "(not to scale)" not in render(gamma=1.0)

    def test_column_labels_present(self) -> None:
        out = render()
        for label in ("GL", "AL", "EL", "PL", "CY", "PR"):
            assert label in out

    def test_zero_dollar_label(self) -> None:
        assert "$0" in render()

    def test_deterministic(self) -> None:
        assert render() == render()
        assert render(colour=True) == render(colour=True)

    def test_plain_mode_has_no_escapes(self) -> None:
        assert "\x1b" not in render(colour=False)

    def test_colour_mode_uses_256_palette(self) -> None:
        assert "\x1b[38;5;" in render(colour=True)


class TestAnsiQuantisation:
    def test_primaries(self) -> None:
        assert ansi256("#000000") == 232 or ansi256("#000000") == 16
        assert ansi256("#FF0000") == 196
        assert ansi256("#00FF00") == 46
        assert ansi256("#0000FF") == 21

    def test_grey_hits_grey_ramp(self) -> None:
        assert 232 <= ansi256("#808080") <= 255


class TestContrastText:
    def test_light_text_on_dark_fills(self) -> None:
        from towerkit.theme import contrast_text

        assert contrast_text("#000F47", "#FFF", "#000") == "#FFF"  # midnight
        assert contrast_text("#C53532", "#FFF", "#000") == "#FFF"  # danger red

    def test_dark_text_on_light_fills(self) -> None:
        from towerkit.theme import contrast_text

        assert contrast_text("#82BAFF", "#FFF", "#000") == "#000"  # sky blue
        assert contrast_text("#FFBF00", "#FFF", "#000") == "#000"  # gold
        assert contrast_text("#CEECFF", "#FFF", "#000") == "#000"


class TestErrorHighlighting:
    def test_error_layer_renders_in_danger_red(self) -> None:
        from towerkit.model import load_program
        from towerkit.render.ascii import DANGER, ansi256, render_ascii
        from towerkit.theme import load_theme

        program = load_program(SAMPLE)
        out = render_ascii(
            program, load_theme(), colour=True,
            error_layers=frozenset({"umbrella"}),
        )
        assert f"\x1b[38;5;{ansi256(DANGER)}m" in out

    def test_error_line_tints_the_gap_background(self) -> None:
        from towerkit.model import load_program
        from towerkit.render.ascii import DANGER, ansi256, render_ascii
        from towerkit.theme import load_theme

        program = load_program(SAMPLE)
        # seed a gap on gl and highlight the line, as the editor would
        umbrella = next(ly for ly in program.layers if ly.id == "umbrella")
        umbrella.attach = 5_000_000
        out = render_ascii(
            program, load_theme(), colour=True, error_lines=frozenset({"gl"})
        )
        assert f"\x1b[38;5;{ansi256(DANGER)}m" in out

    def test_clean_render_has_no_danger_colour(self) -> None:
        # marsh palette has no reds, so the danger index cannot collide the
        # way the default theme's #E45756 does under 256-colour quantisation
        from towerkit.model import load_program
        from towerkit.render.ascii import DANGER, ansi256, render_ascii
        from towerkit.theme import load_theme

        marsh = load_theme(Path(__file__).parent.parent / "themes" / "marsh.json")
        assert all(ansi256(c) != ansi256(DANGER) for c in marsh.carrier_palette)
        out = render_ascii(load_program(SAMPLE), marsh, colour=True)
        assert f"\x1b[38;5;{ansi256(DANGER)}m" not in out
