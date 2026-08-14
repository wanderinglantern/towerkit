"""towerctl mcp --connector-info / --check.

The design assistant's connector is configured by hand in a panel that takes
one value per field, so these helpers produce the values and then say whether
they will actually work.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from towerkit import connector


def test_command_is_the_console_script_beside_the_interpreter() -> None:
    """`towerctl` is not on PATH — install.sh builds ./.venv inside the
    checkout — and the panel's launcher inherits no shell environment.
    """
    assert connector.fields([Path("/tmp/programs")]).command == str(
        Path(sys.executable).parent / "towerctl"
    )


def test_roots_are_pinned_as_separate_comma_separated_arguments(tmp_path: Path) -> None:
    """The panel splits Arguments on commas, and --programs takes nargs="+".

    The README documented these space-separated, which arrives as one argv
    element and leaves the server on its ./programs default.
    """
    one, two = tmp_path / "a", tmp_path / "b"

    got = connector.fields([one, two])

    assert got.arguments == ["mcp", "--programs", str(one), str(two)]


def _fake_bookctl(tmp_path: Path, stdout: str, code: int = 0) -> Path:
    """A real executable named bookctl, so the subprocess path is exercised
    rather than mocked away."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "bookctl"
    # echo, not cat: these tests set PATH to bindir alone, so the script can
    # only use shell builtins.
    script.write_text(f"#!/bin/sh\necho '{stdout}'\nexit {code}\n")
    script.chmod(0o755)
    return bindir


def test_fields_refuses_without_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emitting the ./programs default would produce a connector that starts
    cleanly and serves an empty shelf — the failure that looks like success.
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(connector.NoRoots):
        connector.fields([])


def test_roots_fall_back_to_bookkits_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grant configures roots once with `bookctl roots`; retyping them into a
    second connector is exactly what he asked to avoid (2026-08-14).
    """
    import json

    programs = tmp_path / "programs"
    programs.mkdir()
    bindir = _fake_bookctl(tmp_path, json.dumps({"roots": [str(programs)]}))
    monkeypatch.setenv("PATH", str(bindir))

    got = connector.fields()

    assert got.arguments == ["mcp", "--programs", str(programs.resolve())]


def test_a_broken_bookctl_degrades_to_NoRoots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """towerkit must stay installable and usable without bookkit, so every
    failure of the borrow — absent, erroring, or printing junk — is just
    "no roots", never a crash.
    """
    bindir = _fake_bookctl(tmp_path, "not json at all", code=0)
    monkeypatch.setenv("PATH", str(bindir))

    with pytest.raises(connector.NoRoots):
        connector.fields()


def test_check_fails_on_a_root_that_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    report = connector.check([tmp_path / "gone"])

    root = next(c for c in report.checks if c.label.startswith("root"))
    assert root.ok is False
    assert "gone" in root.detail
    assert report.ok is False


def test_check_counts_the_programs_it_can_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing but wrong directory is the quiet failure: the server starts
    and the assistant reports a book with nothing in it."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    programs = tmp_path / "programs"
    programs.mkdir()
    (programs / "atomic-2027.json").write_text("{}")
    (programs / "endeavour-2026.json").write_text("{}")

    report = connector.check([programs])

    root = next(c for c in report.checks if c.label.startswith("root"))
    assert root.ok is True
    assert "2" in root.detail


def test_check_flags_a_root_holding_no_programs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    empty = tmp_path / "empty-root"
    empty.mkdir()

    report = connector.check([empty])

    root = next(c for c in report.checks if c.label.startswith("root"))
    assert root.ok is False


def test_check_confirms_startup_writes_nothing_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout is the MCP wire; one stray print and the connector dies on a
    protocol parse error the panel reports as a generic failure."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    programs = tmp_path / "programs"
    programs.mkdir()
    (programs / "atomic-2027.json").write_text("{}")

    report = connector.check([programs])

    stdout = next(c for c in report.checks if c.label == "stdout")
    assert stdout.ok is True


def test_cli_connector_info_prints_pasteable_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from towerkit.cli import main

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    programs = tmp_path / "programs"
    programs.mkdir()
    (programs / "atomic-2027.json").write_text("{}")

    assert main(["mcp", "--connector-info", "--programs", str(programs)]) == 0

    out = capsys.readouterr().out
    assert "towerkit" in out
    assert str(Path(sys.executable).parent / "towerctl") in out
    assert f"mcp, --programs, {programs.resolve()}" in out
    assert "both" in out


def test_cli_connector_info_refuses_rather_than_emit_a_silent_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from towerkit.cli import main

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert main(["mcp", "--connector-info"]) == 2
    assert "programs" in capsys.readouterr().err


def test_cli_check_exit_code_reflects_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from towerkit.cli import main

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    programs = tmp_path / "programs"
    programs.mkdir()

    assert main(["mcp", "--check", "--programs", str(programs)]) == 1
    assert "no .json programs" in capsys.readouterr().out

    (programs / "atomic-2027.json").write_text("{}")
    assert main(["mcp", "--check", "--programs", str(programs)]) == 0


def test_bookctl_is_found_via_the_BOOKCTL_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch for any layout the guesses do not cover."""
    import json

    programs = tmp_path / "programs"
    programs.mkdir()
    bindir = _fake_bookctl(tmp_path, json.dumps({"roots": [str(programs)]}))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("BOOKCTL", str(bindir / "bookctl"))

    assert connector.fields().arguments == ["mcp", "--programs", str(programs.resolve())]


def test_bookctl_is_found_in_the_sibling_bookkit_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layout that actually exists on Grant's machines: ~/Developer/towerkit
    and ~/Developer/bookkit side by side, each with its own ./.venv. bookctl is
    on no PATH — that is the whole reason this command exists — so looking only
    there found nothing and refused.
    """
    import json

    programs = tmp_path / "programs"
    programs.mkdir()
    bindir = _fake_bookctl(tmp_path, json.dumps({"roots": [str(programs)]}))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("BOOKCTL", raising=False)
    monkeypatch.setattr(connector, "_sibling_repo_bookctl", lambda: bindir / "bookctl")

    assert connector.fields().arguments == ["mcp", "--programs", str(programs.resolve())]


def test_the_refusal_says_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"no program roots" alone sent Grant back to ask what to type."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("BOOKCTL", raising=False)

    with pytest.raises(connector.NoRoots) as caught:
        connector.fields()

    message = str(caught.value)
    assert "--programs" in message
    assert "BOOKCTL" in message
