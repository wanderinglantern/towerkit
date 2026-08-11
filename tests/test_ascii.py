"""ASCII preview: structural checks, driven only by the shared geometry."""

from pathlib import Path

from towerkit.model import load_program
from towerkit.render.ascii import UNPLACED, ZERO, ansi256, render_ascii
from towerkit.theme import load_theme

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def render(colour: bool = False, **kw) -> str:
    program = load_program(SAMPLE)
    theme = load_theme()
    return render_ascii(program, theme, colour=colour, **kw)


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
