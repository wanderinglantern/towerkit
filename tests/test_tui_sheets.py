"""Data-sheet tests: spreadsheet-style inline cell editing in the editor.

The participants grid and the whole-program layers sheet edit through the
same SheetTable widget: i opens a one-line editor over the cell, enter
commits (one session.mutate — one undo step), tab hops across the row's
editable cells, esc abandons, and the over-sign guard rejects at the input
with the editor kept open.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from towerkit.money import BPS_SCALE
from towerkit.tui.app import TowerkitApp
from towerkit.tui.screens.editor import EditorScreen
from towerkit.tui.widgets.inputs import CarrierSuggester, known_layer_names
from towerkit.tui.widgets.sheet import SheetCellEditor, SheetTable

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


@pytest.fixture()
def sample_copy(tmp_path, monkeypatch):
    target = tmp_path / "programs"
    target.mkdir()
    shutil.copy(SAMPLE, target / "atomic-2026.json")
    monkeypatch.chdir(tmp_path)
    return target / "atomic-2026.json"


async def _open_participants(editor: EditorScreen, pilot, layer_id: str) -> SheetTable:
    editor.selected = ("layer", layer_id)
    await editor._rebuild_detail()
    await pilot.pause()
    sheet = editor.query_one("#participants-sheet", SheetTable)
    sheet.focus()
    await pilot.pause()
    return sheet


class TestParticipantsSheet:
    async def test_i_edit_enter_commits_through_session(self, sample_copy) -> None:
        """i opens the cell editor prefilled; enter commits as ONE session
        mutation (u reverts it) and the editor closes."""
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=1)  # AIG's share cell
            await pilot.press("i")
            await pilot.pause()
            cell = editor.query_one(SheetCellEditor)
            assert cell.value == "60"
            cell.value = "50"
            await pilot.press("enter")
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.participants[0].share_bps == 5_000
            assert editor.session.dirty
            assert not editor.query(SheetCellEditor), "editor closes on commit"
            # one cell commit = one undo step
            await pilot.press("u")
            await pilot.pause()
            assert editor._layer("umbrella").participants[0].share_bps == 6_000

    async def test_escape_abandons_without_writing(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=1)
            await pilot.press("i")
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "1"
            await pilot.press("escape")
            await pilot.pause()
            assert editor._layer("umbrella").participants[0].share_bps == 6_000
            assert not editor.session.dirty
            assert not editor.query(SheetCellEditor)

    async def test_ctrl_q_does_not_drop_text_in_an_open_cell_editor(
        self, sample_copy
    ) -> None:
        """The cell editor's commit is a posted message, so no synchronous
        drain can collect it: ctrl+q used to see a clean session and exit,
        taking the typed cell text with it. It now waits for enter or esc."""
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=1)
            await pilot.press("i")
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "50"
            assert not editor.session.dirty  # the model has not seen it yet

            await pilot.press("ctrl+q")
            await pilot.pause()

            assert app.is_running, "ctrl+q quit with a cell edit still typed"
            cell = editor.query_one(SheetCellEditor)
            assert cell.value == "50", "the typed text is still there"
            # and it still commits normally
            await pilot.press("enter")
            await pilot.pause()
            assert editor._layer("umbrella").participants[0].share_bps == 5_000

    async def test_over_sign_rejected_editor_kept_open(self, sample_copy) -> None:
        """AIG 60→90 with Berkley at 40 would be 130%: blocked at the input,
        nothing committed, and the editor stays open for a correction."""
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=1)
            await pilot.press("i")
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "90"
            await pilot.press("enter")
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.signed_bps == BPS_SCALE  # unchanged: block, don't flag
            assert editor.query(SheetCellEditor), "editor stays open on rejection"
            assert any(
                "over-signed" in n.message for n in app._notifications
            )
            assert not editor.session.dirty

    async def test_tab_hops_and_defers_the_rebuild(self, sample_copy) -> None:
        """tab commits the carrier and hops to the share cell; the table must
        NOT rebuild under the open editor (deferred until it closes)."""
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=0)
            await pilot.press("i")
            await pilot.pause()
            cell = editor.query_one(SheetCellEditor)
            assert cell.value == "AIG"
            cell.value = "AIG plc"
            await pilot.press("tab")  # commit + hop to the share cell
            await pilot.pause()
            assert editor._layer("umbrella").participants[0].carrier == "AIG plc"
            # same table instance — the mutate did not rebuild mid-edit
            assert editor.query_one("#participants-sheet", SheetTable) is sheet
            cell = editor.query_one(SheetCellEditor)
            assert cell.value == "60"  # now editing the share
            cell.value = "55"
            await pilot.press("enter")
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.participants[0].share_bps == 5_500

    async def test_a_adds_participant_with_open_remainder(self, sample_copy) -> None:
        """xs-3 is 80% signed: a adds a row defaulting to the 20% remainder."""
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            await _open_participants(editor, pilot, "xs-3")
            await pilot.press("a")
            await pilot.pause()
            layer = editor._layer("xs-3")
            assert len(layer.participants) == 3
            added = layer.participants[-1]
            assert added.carrier == "New Carrier"
            assert added.share_bps == 2_000  # the open remainder
            await pilot.press("u")
            await pilot.pause()
            assert len(editor._layer("xs-3").participants) == 2

    async def test_del_removes_cursor_row(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=1, column=0)  # Berkley
            await pilot.press("delete")
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert [p.carrier for p in layer.participants] == ["AIG"]
            await pilot.press("u")
            await pilot.pause()
            assert [p.carrier for p in editor._layer("umbrella").participants] == [
                "AIG",
                "Berkley",
            ]

    async def test_carrier_cell_carries_suggester_and_dropdown(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            sheet = await _open_participants(editor, pilot, "umbrella")
            sheet.move_cursor(row=0, column=0)
            await pilot.press("i")
            await pilot.pause()
            cell = editor.query_one(SheetCellEditor)
            assert isinstance(cell.suggester, CarrierSuggester)
            assert "Zurich" in cell.suggester.known
            assert sheet._dropdown is not None, "autocomplete dropdown mounted"
            await pilot.press("escape")
            await pilot.pause()
            assert sheet._dropdown is None, "dropdown removed with the editor"


class TestLayersSheet:
    async def test_toggle_edit_premium_dirty_and_preview(self, sample_copy) -> None:
        """v opens the whole-program sheet; a premium edit lands through the
        session (dirty, undoable) and the tower preview refreshes."""
        from towerkit.tui.widgets.preview import TowerPreview

        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(180, 50)) as pilot:
            editor = app.screen
            await pilot.press("v")
            await pilot.pause()
            sheet = editor.query_one("#layers-sheet", SheetTable)
            assert sheet.row_count == len(editor.session.program.layers)
            preview = editor.query_one("#preview", TowerPreview)
            calls = []
            original = preview.show_program
            preview.show_program = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
            row = sheet.get_row_index("umbrella")
            sheet.focus()
            sheet.move_cursor(row=row)
            await pilot.pause()
            from textual.coordinate import Coordinate

            sheet._open_editor(Coordinate(row, 5))  # the premium column
            await pilot.pause()
            cell = editor.query_one(SheetCellEditor)
            assert cell.value == "$3,900,000"
            cell.value = "4.2m"
            await pilot.press("enter")
            await pilot.pause()
            assert editor._layer("umbrella").premium == 4_200_000
            assert editor.session.dirty
            assert calls, "tower preview refreshed by the commit"
            await pilot.press("u")
            await pilot.pause()
            assert editor._layer("umbrella").premium == 3_900_000

    async def test_enter_jumps_outline_to_the_layer(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(180, 50)) as pilot:
            editor = app.screen
            await pilot.press("v")
            await pilot.pause()
            sheet = editor.query_one("#layers-sheet", SheetTable)
            sheet.focus()
            sheet.move_cursor(row=sheet.get_row_index("xs-1"))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not editor._layers_sheet_open
            assert editor.selected == ("layer", "xs-1")
            assert not editor.query("#layers-sheet")
            assert editor.query_one("#f-layer-name").value == "1st Excess"
            from textual.widgets import Tree

            tree = editor.query_one("#structure", Tree)
            assert tree.cursor_node is not None
            assert tree.cursor_node.data == ("layer", "xs-1")

    async def test_escape_returns_to_form_not_exit(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(180, 50)) as pilot:
            editor = app.screen
            await pilot.press("v")
            await pilot.pause()
            assert editor.query("#layers-sheet")
            await pilot.press("escape")
            await pilot.pause()
            assert not editor.query("#layers-sheet")
            assert isinstance(app.screen, EditorScreen)  # still editing

    async def test_a_adds_layer_and_del_removes(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(180, 50)) as pilot:
            editor = app.screen
            n = len(editor.session.program.layers)
            await pilot.press("v")
            await pilot.pause()
            sheet = editor.query_one("#layers-sheet", SheetTable)
            sheet.focus()
            await pilot.press("a")
            await pilot.pause()
            assert len(editor.session.program.layers) == n + 1
            # the rebuilt sheet has the cursor on the new row; del removes it
            sheet = editor.query_one("#layers-sheet", SheetTable)
            assert sheet.cursor_row == n
            await pilot.press("delete")
            await pilot.pause()
            assert len(editor.session.program.layers) == n
            assert editor.session.undo()  # both edits were session-backed

    async def test_name_cells_carry_layer_name_suggester(self, sample_copy) -> None:
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(180, 50)) as pilot:
            editor = app.screen
            await pilot.press("v")
            await pilot.pause()
            sheet = editor.query_one("#layers-sheet", SheetTable)
            suggester = sheet.fields[0].suggester
            assert isinstance(suggester, CarrierSuggester)
            assert "Umbrella" in suggester.known
            # the layer form's name input completes from the same corpus
            await pilot.press("escape")
            await pilot.pause()
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            name = editor.query_one("#f-layer-name")
            assert isinstance(name.suggester, CarrierSuggester)


class TestKnownLayerNames:
    def test_harvests_from_programs_and_extra(self, tmp_path) -> None:
        programs = tmp_path / "programs"
        programs.mkdir()
        shutil.copy(SAMPLE, programs / "atomic-2026.json")
        names = known_layer_names(programs, ["Local Layer"])
        assert "Umbrella" in names
        assert "1st Excess" in names
        assert "Local Layer" in names

    def test_missing_dir_is_extra_only(self, tmp_path) -> None:
        assert known_layer_names(tmp_path / "nope", ["X"]) == ["X"]
