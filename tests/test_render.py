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

    def test_not_to_scale_caveat_present(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"])[0]
        assert "NOT TO SCALE" in out.read_text()

    def test_linear_gamma_drops_caveat(self, program, theme, tmp_path) -> None:
        out = render_program(program, theme, tmp_path, "t", ["svg"], gamma=1.0)[0]
        assert "NOT TO SCALE" not in out.read_text()
