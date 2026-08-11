"""TUI tests via Textual's run_test pilot.

The headline gate: a valid program can be created from scratch without
touching a text editor — and saving is canonical, blocked-but-not-silent on
errors, with working undo/redo and over-sign protection.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from towerkit.model import dumps_program, load_program
from towerkit.money import BPS_SCALE
from towerkit.tui.app import TowerkitApp
from towerkit.tui.screens.browser import ProgramBrowser, _bump_stem
from towerkit.tui.screens.editor import EditorScreen
from towerkit.tui.session import EditSession, suggested_attach
from towerkit.tui.widgets.inputs import CarrierSuggester, parse_share_pct

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


@pytest.fixture()
def sample_copy(tmp_path):
    target = tmp_path / "programs"
    target.mkdir()
    shutil.copy(SAMPLE, target / "atomic-2026.json")
    return target / "atomic-2026.json"


class TestSession:
    def test_undo_redo_round_trip(self) -> None:
        session = EditSession.open(SAMPLE)
        original = dumps_program(session.program)
        session.mutate(lambda p: setattr(p, "insured", "Changed Co"))
        assert session.program.insured == "Changed Co"
        assert session.undo()
        assert dumps_program(session.program) == original
        assert session.redo()
        assert session.program.insured == "Changed Co"

    def test_noop_mutation_costs_no_undo_step(self) -> None:
        session = EditSession.open(SAMPLE)
        session.mutate(lambda p: None)
        assert not session.undo()

    def test_save_with_no_edits_is_zero_diff(self, sample_copy) -> None:
        before = sample_copy.read_bytes()
        session = EditSession.open(sample_copy)
        session.save()
        assert sample_copy.read_bytes() == before

    def test_dirty_tracking(self, sample_copy) -> None:
        session = EditSession.open(sample_copy)
        assert not session.dirty
        session.mutate(lambda p: setattr(p, "insured", "X Co"))
        assert session.dirty
        session.save()
        assert not session.dirty

    def test_suggested_attach_is_top_of_stack(self) -> None:
        program = load_program(SAMPLE)
        # gl/al/el stack tops out at $202M
        assert suggested_attach(program, ["gl"]) == 202_000_000
        # pl tower tops out at $25M
        assert suggested_attach(program, ["pl"]) == 25_000_000
        assert suggested_attach(program, ["nope"]) == 0

    def test_add_layer_defaults_contiguous(self) -> None:
        session = EditSession.open(SAMPLE)
        layer = session.add_layer(["pl"])
        assert layer.attach == 25_000_000  # contiguity by construction


class TestInputHelpers:
    def test_share_parsing(self) -> None:
        assert parse_share_pct("35") == 3_500
        assert parse_share_pct("33.33") == 3_333
        assert parse_share_pct("100%") == BPS_SCALE
        assert parse_share_pct("0") == 0
        assert parse_share_pct("101") is None
        assert parse_share_pct("33.333") is None  # sub-bps precision rejected
        assert parse_share_pct("abc") is None

    @pytest.mark.asyncio
    async def test_carrier_suggester_prefix_and_fuzzy(self) -> None:
        suggester = CarrierSuggester(["Swiss Re", "Sompo", "Munich Re"])
        assert await suggester.get_suggestion("Swi") == "Swiss Re"
        assert await suggester.get_suggestion("swiss re corporate") == "Swiss Re"
        assert await suggester.get_suggestion("zzz") is None


class TestBrowser:
    def test_bump_stem(self) -> None:
        assert _bump_stem("atomic-2026") == "atomic-2027"
        assert _bump_stem("prog") == "prog-renewal"

    @pytest.mark.asyncio
    async def test_browser_lists_programs_with_badges(self, tmp_path, monkeypatch) -> None:
        programs = tmp_path / "programs"
        programs.mkdir()
        shutil.copy(SAMPLE, programs / "atomic-2026.json")
        monkeypatch.chdir(tmp_path)
        app = TowerkitApp()
        async with app.run_test(size=(120, 40)):
            browser = app.screen
            assert isinstance(browser, ProgramBrowser)
            table = browser.query_one("#programs")
            assert table.row_count == 1
            row = table.get_row_at(0)
            assert row[1] == "atomic-2026.json"
            assert row[2] == "Atomic Industries, Inc."
            assert "⚠" in row[0]  # the deliberate 80%-placed warning

    @pytest.mark.asyncio
    async def test_open_editor_from_browser(self, tmp_path, monkeypatch) -> None:
        programs = tmp_path / "programs"
        programs.mkdir()
        shutil.copy(SAMPLE, programs / "atomic-2026.json")
        monkeypatch.chdir(tmp_path)
        app = TowerkitApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EditorScreen)


class TestEditor:
    @pytest.mark.asyncio
    async def test_editor_shows_structure_and_diagnostics(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)):
            editor = app.screen
            assert isinstance(editor, EditorScreen)
            tree = editor.query_one("#structure")
            labels = [str(n.label) for n in editor._walk(tree.root)]
            assert any("Layers (14)" in label for label in labels)
            assert any("Umbrella" in label for label in labels)
            diags = editor.query_one("#diagnostics")
            assert len(diags.children) == 1  # the 80%-placed warning

    @pytest.mark.asyncio
    async def test_over_signing_blocked_at_input(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            share = editor.query_one("#p-share-0")
            share.value = "90"  # AIG 60→90 with Berkley at 40 would be 130%
            editor._commit_participant(share, "p-share-0")
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.signed_bps == BPS_SCALE  # unchanged: block, don't flag
            assert share.value == "60"  # display restored

    @pytest.mark.asyncio
    async def test_money_field_commit_updates_program(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            attach = editor.query_one("#f-layer-attach")
            attach.value = "2.5m"
            editor._commit_input(attach)
            await pilot.pause()
            assert editor._layer("umbrella").attach == 2_500_000
            # the gap this creates shows up immediately in diagnostics
            assert any(
                d.code == "line-gap" for d in editor.session.diagnostics().errors
            )

    @pytest.mark.asyncio
    async def test_undo_key(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.session.mutate(lambda p: setattr(p, "insured", "Other Co"))
            await pilot.press("u")
            await pilot.pause()
            assert editor.session.program.insured == "Atomic Industries, Inc."

    @pytest.mark.asyncio
    async def test_save_with_errors_prompts_not_blocks(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            # seed an error: umbrella attach moves, creating a gap
            editor.session.mutate(lambda p: setattr(editor._layer("umbrella"), "attach", 3_000_000))
            before = sample_copy.read_bytes()
            await pilot.press("ctrl+s")
            await pilot.pause()
            from towerkit.tui.widgets.modals import ConfirmModal

            assert isinstance(app.screen, ConfirmModal)  # asked, not silent
            await pilot.press("escape")  # cancel
            await pilot.pause()
            assert sample_copy.read_bytes() == before  # cancelled → untouched

    @pytest.mark.asyncio
    async def test_create_valid_program_from_scratch(self, tmp_path, monkeypatch) -> None:
        """The §9 gate for the TUI, in miniature: start blank, build a valid
        program using only editor operations, save, and validate the file."""
        monkeypatch.chdir(tmp_path)
        app = TowerkitApp(new=True)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            # name the insured via the program form
            editor.selected = ("program", None)
            await editor._rebuild_detail()
            await pilot.pause()
            insured = editor.query_one("#f-insured")
            insured.value = "Scratch Built Co"
            editor._commit_input(insured)
            # add a primary layer on the default gl line (attach suggested: 0)
            editor.selected = ("layers-group", None)
            await editor.action_add_node()
            await pilot.pause()
            kind, layer_id = editor.selected
            assert kind == "layer"
            layer = editor._layer(layer_id)
            assert layer.attach == 0
            # set its limit and fill it with one carrier at 100%
            limit = editor.query_one("#f-layer-limit")
            limit.value = "5m"
            editor._commit_input(limit)
            editor.query_one("#add-participant").press()
            await pilot.pause()
            carrier = editor.query_one("#p-carrier-0")
            carrier.value = "Zurich"
            editor._commit_input(carrier)
            # add a retention on gl
            editor.selected = ("retentions-group", None)
            await editor.action_add_node()
            await pilot.pause()
            # save via the name prompt
            editor.action_save()
            await pilot.pause()
            from towerkit.tui.widgets.modals import PromptModal

            assert isinstance(app.screen, PromptModal)
            prompt = app.screen.query_one("#prompt")
            prompt.value = "scratch"
            await pilot.press("enter")
            await pilot.pause()
        saved = tmp_path / "programs" / "scratch.json"
        assert saved.exists()
        from towerkit.validate import validate_file

        program, diags = validate_file(saved)
        assert program is not None
        assert diags.ok, [str(d) for d in diags.errors]
        assert program.insured == "Scratch Built Co"
        assert program.layers[0].signed_bps == BPS_SCALE


class TestAppliesToLayout:
    @pytest.mark.asyncio
    async def test_all_six_line_checkboxes_visible(self, sample_copy, monkeypatch) -> None:
        # Regression: a single horizontal row silently clipped everything
        # after the third checkbox in the 46-cell detail pane.
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            detail = editor.query_one("#detail")
            for line_id in ("gl", "al", "el", "pl", "cy", "pr"):
                box = editor.query_one(f"#applies-{line_id}")
                assert box.region.width > 0, f"{line_id} checkbox not laid out"
                assert box.region.x + box.region.width <= (
                    detail.region.x + detail.region.width
                ), f"{line_id} checkbox clipped off the detail pane"


class TestRenderOptionsMenu:
    @pytest.mark.asyncio
    async def test_menu_sets_theme_and_toggles(self, sample_copy, monkeypatch) -> None:
        # copy themes next to programs so the menu finds them
        import shutil as sh

        themes = sample_copy.parent.parent / "themes"
        themes.mkdir(exist_ok=True)
        sh.copy(REPO / "themes" / "marsh.json", themes / "marsh.json")
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            assert editor.tower_theme.name == "default"
            assert app.show_totals and app.show_premiums
            await pilot.press("t")
            await pilot.pause()
            from towerkit.tui.widgets.modals import RenderOptionsModal

            assert isinstance(app.screen, RenderOptionsModal)
            modal = app.screen
            modal.query_one("#themes").highlighted = 1  # marsh
            modal.query_one("#opt-premiums").value = False
            modal.query_one("#apply").press()
            await pilot.pause()
            assert editor.tower_theme.name == "marsh"
            assert editor.theme_path == Path("themes/marsh.json")
            assert app.show_premiums is False
            assert app.show_totals is True


class TestPrivatePrograms:
    @pytest.mark.asyncio
    async def test_private_subdir_listed_in_browser(self, tmp_path, monkeypatch) -> None:
        programs = tmp_path / "programs"
        (programs / "private").mkdir(parents=True)
        shutil.copy(SAMPLE, programs / "atomic-2026.json")
        shutil.copy(SAMPLE, programs / "private" / "client.json")
        monkeypatch.chdir(tmp_path)
        app = TowerkitApp()
        async with app.run_test(size=(120, 40)):
            table = app.screen.query_one("#programs")
            names = [table.get_row_at(i)[1] for i in range(table.row_count)]
            assert "atomic-2026.json" in names
            assert "client.json" in names


class TestLayerNotesField:
    @pytest.mark.asyncio
    async def test_note_entered_in_form_lands_on_the_layer(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            notes = editor.query_one("#f-layer-notes")
            notes.value = "quota share under negotiation"
            editor._commit_input(notes)
            await pilot.pause()
            assert editor._layer("umbrella").notes == "quota share under negotiation"


class TestPersistedRenderSettings:
    @pytest.mark.asyncio
    async def test_menu_choice_saves_into_the_program(self, sample_copy, monkeypatch) -> None:
        import shutil as sh

        themes = sample_copy.parent.parent / "themes"
        themes.mkdir(exist_ok=True)
        sh.copy(REPO / "themes" / "marsh.json", themes / "marsh.json")
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            await pilot.press("t")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#themes").highlighted = 1  # marsh
            modal.query_one("#opt-cell-premiums").value = True
            modal.query_one("#apply").press()
            await pilot.pause()
            stored = editor.session.program.render
            assert stored is not None
            assert stored.theme == "themes/marsh.json"
            assert stored.cell_premiums is True
            editor.session.save()
        # a fresh session opens with the same settings
        app2 = TowerkitApp(path=sample_copy)
        async with app2.run_test(size=(140, 45)):
            editor2 = app2.screen
            assert editor2.tower_theme.name == "marsh"
            assert app2.cell_premiums is True


class TestLineIdLocked:
    @pytest.mark.asyncio
    async def test_id_field_is_locked_and_label_not_polluted(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        app = TowerkitApp(new=True)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("lines-group", None)
            await editor.action_add_node()
            await pilot.pause()
            name = editor.query_one("#f-line-name")
            name.value = "Directors and Officers"
            editor._commit_input(name)
            await pilot.pause()
            kind, line_id = editor.selected
            assert line_id == "directors-and-officers"  # id auto-generated
            line = editor._line(line_id)
            assert line.abbr is None  # the label was NOT auto-created…
            assert line.label == "DAO"  # …it derives from the name's initials
            id_field = editor.query_one("#f-line-id")
            assert id_field.disabled  # locked, not editable


class TestLineReorder:
    @pytest.mark.asyncio
    async def test_shift_arrows_reorder_columns(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            assert editor.session.program.line_ids()[:3] == ["gl", "al", "el"]
            editor.selected = ("line", "al")
            await pilot.press("left_square_bracket")  # the real key path
            await pilot.pause()
            assert editor.session.program.line_ids()[:3] == ["al", "gl", "el"]
            # geometry follows the new order
            from towerkit.layout import build_layout

            tower = build_layout(editor.session.program)
            assert tower.columns[0].line_id == "al"
            # and it undoes like any other edit
            assert editor.session.undo()
            assert editor.session.program.line_ids()[:3] == ["gl", "al", "el"]

    @pytest.mark.asyncio
    async def test_bounds_are_safe(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("line", "gl")
            await pilot.press("left_square_bracket")  # already first: no-op
            await pilot.pause()
            assert editor.session.program.line_ids()[0] == "gl"


class TestHelp:
    @pytest.mark.asyncio
    async def test_question_mark_opens_help(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            from towerkit.tui.widgets.modals import HelpModal

            assert isinstance(app.screen, HelpModal)
            assert "[ / ]" in app.screen.help_text  # the reorder keys are documented
            await pilot.press("escape")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_line_form_carries_reorder_hint(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("line", "gl")
            await editor._rebuild_detail()
            await pilot.pause()
            from textual.widgets import Label

            texts = [str(w.render()) for w in editor.query(Label)]
            assert any("moves this column left" in t for t in texts)


class TestBlurCommitRace:
    @pytest.mark.asyncio
    async def test_edit_survives_clicking_away(self, sample_copy, monkeypatch) -> None:
        """Type into a field, then move the tree selection WITHOUT pressing
        enter: the blur-commit must land on the node the form was built for,
        not the newly selected one — 'GL OpCo' must not lose its edit."""
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("line", "gl")
            await editor._rebuild_detail()
            await pilot.pause()
            abbr = editor.query_one("#f-line-abbr")
            abbr.focus()
            await pilot.pause()
            abbr.value = "GL OpCo"
            # selection moves before the blur-commit is processed
            editor.selected = ("layer", "umbrella")
            editor._commit_input(abbr)
            await pilot.pause()
            line = editor._line("gl")
            assert line.abbr == "GL OpCo"  # spaces intact, edit not dropped


class TestColumnLabelPrefill:
    @pytest.mark.asyncio
    async def test_label_prefills_with_name(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            # gl has an explicit abbr in the sample: shows the abbr
            editor.selected = ("line", "gl")
            await editor._rebuild_detail()
            await pilot.pause()
            assert editor.query_one("#f-line-abbr").value == "GL"
            # a line without abbr prefills with its name
            editor.session.mutate(lambda p: setattr(p.lines[0], "abbr", None))
            await editor._rebuild_detail()
            await pilot.pause()
            assert editor.query_one("#f-line-abbr").value == "General Liability"
