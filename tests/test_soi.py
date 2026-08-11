"""SOI mapping and theming: pure logic, no Excel here."""

from towerkit.theme import SoiStyle, _theme_from_jsonable, load_theme


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
