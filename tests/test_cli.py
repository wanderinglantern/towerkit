"""End-to-end CLI: named subcommands, never positional sys.argv."""

from pathlib import Path

from towerkit.cli import main

REPO = Path(__file__).parent.parent
OLD = REPO / "programs" / "atomic-2026.json"
NEW = REPO / "programs" / "atomic-2027.json"


class TestRender:
    def test_render_writes_requested_formats(self, tmp_path, capsys) -> None:
        code = main(
            [
                "render", str(OLD),
                "--theme", str(REPO / "themes" / "marsh.json"),
                "--out", str(tmp_path),
                "--format", "svg,png",
            ]
        )
        assert code == 0
        assert (tmp_path / "atomic-2026.svg").exists()
        assert (tmp_path / "atomic-2026.png").exists()
        out = capsys.readouterr().out
        assert "atomic-2026.svg" in out

    def test_render_refuses_invalid_program(self, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(OLD.read_text().replace('"attach": 27000000', '"attach": 30000000'))
        code = main(["render", str(bad), "--out", str(tmp_path)])
        assert code == 1
        assert not (tmp_path / "bad.svg").exists()


class TestCompare:
    def test_compare_writes_comparison(self, tmp_path) -> None:
        code = main(
            ["compare", str(OLD), str(NEW), "--out", str(tmp_path), "--format", "svg"]
        )
        assert code == 0
        assert (tmp_path / "atomic-2026-vs-atomic-2027.svg").exists()


def test_soi_exports_workbook(tmp_path) -> None:
    sample = Path(__file__).parent.parent / "programs" / "atomic-2026.json"
    out = tmp_path / "soi.xlsx"
    assert main(["soi", str(sample), "-o", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 0


def test_soi_default_filename_from_insured(tmp_path, monkeypatch) -> None:
    from towerkit.model import load_program
    from towerkit.soi import default_filename

    sample = Path(__file__).parent.parent / "programs" / "atomic-2026.json"
    monkeypatch.chdir(tmp_path)
    assert main(["soi", str(sample)]) == 0
    assert (tmp_path / default_filename(load_program(sample))).exists()


class TestParser:
    def test_no_command_shows_help(self, capsys) -> None:
        assert main([]) == 2

    def test_version(self, capsys) -> None:
        try:
            main(["--version"])
        except SystemExit as exc:
            assert exc.code == 0
        assert "towerctl" in capsys.readouterr().out
