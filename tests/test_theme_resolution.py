"""A stored `render.theme` resolves wherever the process is running from.

Grant, 2026-08-21, on his own book: assigning a program to the SOI schedule
errored with `themes/marsh.json` not found, after a folder move the day before.

The stored value is RELATIVE by contract — program files are portable and an
absolute theme path is a validation error — but it was resolved literally,
against the current working directory. Nothing pins that directory, so moving a
folder broke every stored theme on the machine.

And the damage was not a failed render. bookkit re-validates the whole program
file on every write and `_check_render_theme` errors on a theme it cannot read,
so an unresolvable theme refused EVERY later edit to that program. The file was
wedged, and the theme could not be changed from any UI, because changing it is
itself a write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_validate import make_program

from towerkit.model import RenderSettings
from towerkit.theme import available_themes, load_theme, resolve_theme
from towerkit.validate import Diagnostics, _check_render_theme


def _theme_diags(program) -> Diagnostics:
    """`_check_render_theme` is the check under test and it is NOT part of the
    pure `validate_program` pass — it needs the filesystem, which is exactly
    why it lives beside the file loader. Called directly so these tests do not
    have to write a program file to disk to ask one question about a path."""
    diags = Diagnostics()
    _check_render_theme(program, diags)
    return diags


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A working directory with NO ./themes beside it — the state a moved
    folder leaves every process in."""
    monkeypatch.chdir(tmp_path)
    assert not Path("themes").exists()
    return tmp_path


class TestTheReportedBug:
    def test_a_stored_relative_theme_loads_from_anywhere(self, elsewhere) -> None:
        """THE REGRESSION. `marsh.json` ships packaged, so the file Grant needs
        is on every machine — it was simply never looked for."""
        theme = load_theme("themes/marsh.json")
        assert theme.name

    def test_the_program_is_no_longer_wedged(self, elsewhere) -> None:
        """The consequence that actually bit: an unreadable theme made
        `_check_render_theme` error, and bookkit re-validates on every write,
        so every later edit to the program was refused."""
        program = make_program(render=RenderSettings(theme="themes/marsh.json"))

        assert not _theme_diags(program).errors

    def test_resolve_finds_the_packaged_copy_by_name(self, elsewhere) -> None:
        resolved = resolve_theme("themes/marsh.json")
        assert resolved.is_file()
        assert resolved.stem == "marsh"


class TestWhatMustNotChange:
    def test_a_real_relative_file_still_wins(self, tmp_path, monkeypatch) -> None:
        """A user's OWN ./themes directory is the reason the literal path is
        tried first: a local marsh.json must beat the packaged one, or
        overriding a shipped theme stops working."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "themes").mkdir()
        packaged = json.loads(
            next(p for p in available_themes() if p.stem == "marsh").read_text()
        )
        packaged["name"] = "mine, not the packaged one"
        (tmp_path / "themes" / "marsh.json").write_text(json.dumps(packaged))

        assert load_theme("themes/marsh.json").name == "mine, not the packaged one"
        assert resolve_theme("themes/marsh.json") == Path("themes/marsh.json")

    def test_an_absolute_path_is_still_an_error(self) -> None:
        """Unrelated rule, and correct: a path that renders here and breaks on
        the next machine is worse than one that breaks now."""
        # .resolve(), because available_themes returns ./themes entries as
        # RELATIVE paths when the process runs from a checkout that has one —
        # which is the whole reason the stored contract is relative, and would
        # have made this test assert nothing.
        absolute = next(
            p for p in available_themes() if p.stem == "marsh"
        ).resolve()
        assert absolute.is_absolute()
        program = make_program(render=RenderSettings(theme=str(absolute)))

        codes = {d.code for d in _theme_diags(program).errors}
        assert "render-theme" in codes

    def test_a_name_nothing_matches_still_refuses_and_says_so(
        self, elsewhere
    ) -> None:
        with pytest.raises(FileNotFoundError) as missing:
            resolve_theme("themes/nosuchtheme.json")

        assert "nosuchtheme" in str(missing.value)
        assert "marsh" in str(missing.value), (
            "the refusal does not name a theme that WOULD work"
        )

    def test_the_validator_still_catches_an_unloadable_theme(
        self, elsewhere
    ) -> None:
        program = make_program(render=RenderSettings(theme="themes/nosuchtheme.json"))
        codes = {d.code for d in _theme_diags(program).errors}
        assert "render-theme" in codes

    def test_none_still_means_the_built_in_default(self) -> None:
        assert load_theme(None).name


class TestOneResolutionRule:
    def test_the_renderer_and_the_validator_agree(self, elsewhere) -> None:
        """`_check_render_theme`'s whole claim is that it PREDICTS the
        renderer. A prediction made against a different resolution than the one
        the renderer performs is a prediction about a render nobody will run —
        which is precisely how a file could validate clean and crash, and, once
        the CWD moved, how it could fail validation and render fine."""
        stored = "themes/marsh.json"
        program = make_program(render=RenderSettings(theme=stored))

        validated = not _theme_diags(program).errors
        try:
            load_theme(stored)
            loaded = True
        except Exception:
            loaded = False

        assert validated is loaded

    def test_resolution_does_not_depend_on_the_working_directory(
        self, tmp_path, monkeypatch
    ) -> None:
        """The property the bug violated, stated directly."""
        first = tmp_path / "one"
        second = tmp_path / "two" / "deeper"
        second.mkdir(parents=True)
        first.mkdir()

        monkeypatch.chdir(first)
        here = load_theme("themes/marsh.json").name
        monkeypatch.chdir(second)
        there = load_theme("themes/marsh.json").name

        assert here == there
        assert os.getcwd() == str(second)
