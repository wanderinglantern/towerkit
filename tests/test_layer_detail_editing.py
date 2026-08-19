"""Setting the three layer detail fields from the editor.

`states`, `namedLimits` and `premiumDetail` shipped with no way to set them
that was not a text editor. These are the tests for the way in: a
comma-separated `states` field, a `premiumDetail` field shaped exactly like
the two detail fields beside it, and a repeating-row named-limits grid on the
participants-sheet pattern.

Three claims are load-bearing here:

* set a field and clear it again and the FILE is byte-identical — the
  canonical form has to survive a round trip through the UI, not just through
  `dumps_program`;
* every validator refusal reaches the user as words. The rules live in
  `validate.py` and produce diagnostics, which is a quiet home for a message
  the user needs while their hands are on the field;
* the keystroke path is short enough to find. A key nobody can reach is the
  same as no key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from towerkit.model import (
    Layer,
    Line,
    NamedLimit,
    Participant,
    Period,
    Placement,
    Program,
    dumps_program,
    load_program,
)
from towerkit.tui.app import TowerkitApp
from towerkit.tui.screens.editor import EditorScreen
from towerkit.tui.widgets.sheet import SheetCellEditor, SheetTable


def _program() -> Program:
    """Two layers on purpose: a statutory one (where states belong) and a
    dollar-limited one (where they are refused).

    The dollar-limited layer is SECOND. A fixture whose first layer answers
    every question lets a write that lands on the wrong layer pass unnoticed —
    this feature's own branch shipped exactly that defect a day ago.
    """
    return Program(
        insured="Acme",
        program="Casualty",
        placement=Placement.BOUND,
        period=Period(start="2026-01-01", end="2027-01-01"),
        lines=[
            Line(id="wc", name="Workers Compensation", abbr="WC"),
            Line(id="el", name="Employers Liability", abbr="EL"),
        ],
        layers=[
            Layer(
                id="wc-stat",
                name="Workers Compensation",
                applies_to=["wc"],
                attach=0,
                limit=0,
                statutory=True,
                premium=0,
                participants=[Participant(carrier="Travelers", share_bps=10_000)],
            ),
            Layer(
                id="el-primary",
                name="Primary EL",
                applies_to=["el"],
                attach=0,
                limit=1_000_000,
                premium=400_000,
                participants=[Participant(carrier="Travelers", share_bps=10_000)],
            ),
        ],
    )


@pytest.fixture()
def program_file(tmp_path, monkeypatch) -> Path:
    programs = tmp_path / "programs"
    programs.mkdir()
    path = programs / "acme-2026.json"
    path.write_text(dumps_program(_program()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return path


async def _open_layer(pilot, layer_id: str) -> EditorScreen:
    editor = pilot.app.screen
    assert isinstance(editor, EditorScreen)
    editor.selected = ("layer", layer_id)
    await editor._rebuild_detail()
    await pilot.pause()
    return editor


async def _type_into(pilot, widget_id: str, text: str) -> None:
    """Type a form field the way a user does: focus it, type, press enter."""
    field = pilot.app.screen.query_one(widget_id)
    field.focus()
    await pilot.pause()
    field.value = ""
    if text:
        await pilot.press(*text)
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


def _messages(app) -> list[str]:
    return [n.message for n in app._notifications]


# --- byte-identical round trips ---------------------------------------------


class TestZeroDiffThroughTheEditor:
    """Set a field and clear it: the file is what it was.

    Every one of these asserts the SET landed before clearing it — a clear
    that reverts nothing is byte-identical for the wrong reason, and that is
    the shape of test defect this feature has already shipped once.
    """

    @pytest.mark.asyncio
    async def test_states_set_then_cleared(self, program_file) -> None:
        before = program_file.read_bytes()
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY, NJ")
            assert editor._layer("wc-stat").states == ["NY", "NJ"]
            assert '"states"' in dumps_program(editor.session.program)

            await _type_into(pilot, "#f-layer-states", "")
            assert editor._layer("wc-stat").states == []
            editor.session.save()
        assert program_file.read_bytes() == before

    @pytest.mark.asyncio
    async def test_premium_detail_set_then_cleared(self, program_file) -> None:
        before = program_file.read_bytes()
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-premium-detail", "Included with Part A")
            assert editor._layer("wc-stat").premium_detail == "Included with Part A"
            assert '"premiumDetail"' in dumps_program(editor.session.program)

            await _type_into(pilot, "#f-layer-premium-detail", "")
            assert editor._layer("wc-stat").premium_detail is None
            editor.session.save()
        assert program_file.read_bytes() == before

    @pytest.mark.asyncio
    async def test_named_limits_added_then_removed(self, program_file) -> None:
        before = program_file.read_bytes()
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            await pilot.pause()

            await pilot.press("a")  # add a row
            await pilot.pause()
            await pilot.press("i")  # edit its name
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "Each Accident"
            await pilot.press("tab")  # commit + hop to the amount
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "1m"
            await pilot.press("enter")
            await pilot.pause()

            layer = editor._layer("el-primary")
            assert [(nl.name, nl.amount) for nl in layer.named_limits] == [
                ("Each Accident", 1_000_000)
            ]
            assert '"namedLimits"' in dumps_program(editor.session.program)

            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            sheet.move_cursor(row=0, column=0)
            await pilot.pause()
            await pilot.press("delete")
            await pilot.pause()
            assert editor._layer("el-primary").named_limits == []
            editor.session.save()
        assert program_file.read_bytes() == before

    @pytest.mark.asyncio
    async def test_a_trailing_comma_is_a_typing_artefact_not_a_state(
        self, program_file
    ) -> None:
        """And the field echoes back what it stored, so the user can see that
        the phrase they typed became a list."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY, NJ,")
            assert editor._layer("wc-stat").states == ["NY", "NJ"]
            assert editor.query_one("#f-layer-states").value == "NY, NJ"

    @pytest.mark.asyncio
    async def test_a_named_limit_cell_opens_prefilled_with_what_is_there(
        self, program_file
    ) -> None:
        """i on an existing row edits it — an editor that opens empty is a
        delete with extra steps."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            editor.session.mutate(
                lambda p: p.layers[1].named_limits.append(
                    NamedLimit(name="Each Accident", amount=1_000_000)
                )
            )
            await editor._rebuild_detail()
            await pilot.pause()
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            sheet.move_cursor(row=0, column=0)
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            assert editor.query_one(SheetCellEditor).value == "Each Accident"
            await pilot.press("escape")
            await pilot.pause()
            sheet.move_cursor(row=0, column=1)
            await pilot.press("i")
            await pilot.pause()
            assert editor.query_one(SheetCellEditor).value == "$1,000,000"

    @pytest.mark.asyncio
    async def test_the_row_under_the_cursor_is_the_row_that_moves(
        self, program_file
    ) -> None:
        """Three rows, so an edit or a delete that always lands on the first
        one cannot hide. With one row every index is index 0."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")

            def furnish(p):
                for name in ("A", "B", "C"):
                    p.layers[1].named_limits.append(
                        NamedLimit(name=name, amount=1_000_000)
                    )

            editor.session.mutate(furnish)
            await editor._rebuild_detail()
            await pilot.pause()

            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            sheet.move_cursor(row=1, column=1)  # B's amount
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            editor.query_one(SheetCellEditor).value = "2m"
            await pilot.press("enter")
            await pilot.pause()
            assert [
                (nl.name, nl.amount) for nl in editor._layer("el-primary").named_limits
            ] == [("A", 1_000_000), ("B", 2_000_000), ("C", 1_000_000)]

            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            sheet.move_cursor(row=1, column=0)
            await pilot.pause()
            await pilot.press("delete")
            await pilot.pause()
            assert [
                nl.name for nl in editor._layer("el-primary").named_limits
            ] == ["A", "C"]

    @pytest.mark.asyncio
    async def test_the_form_shows_what_is_already_on_file(self, program_file) -> None:
        """A form that renders a stored value blank is worse than no form: the
        next commit of that field writes the blank back, which is exactly the
        clear the round-trip tests above perform on purpose."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen

            def furnish(p):
                layer = p.layers[0]
                layer.states = ["NY", "NJ"]
                layer.premium_detail = "Included with Part A"
                layer.named_limits.append(
                    NamedLimit(name="Each Accident", amount=1_000_000)
                )

            editor.session.mutate(furnish)
            editor.selected = ("layer", "wc-stat")
            await editor._rebuild_detail()
            await pilot.pause()

            assert editor.query_one("#f-layer-states").value == "NY, NJ"
            assert (
                editor.query_one("#f-layer-premium-detail").value
                == "Included with Part A"
            )
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            assert sheet.row_count == 1
            assert sheet.get_row_at(0)[0] == "Each Accident"

    @pytest.mark.asyncio
    async def test_the_other_layer_is_never_written(self, program_file) -> None:
        """The write lands on the layer whose form is open, and only there."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY")
            await _type_into(pilot, "#f-layer-premium-detail", "Included with Part A")
            other = editor._layer("el-primary")
            assert other.states == []
            assert other.premium_detail is None
            assert other.named_limits == []


# --- refusals reach the user ------------------------------------------------


class TestRefusalsSaySomething:
    """Each rule in validate.py, provoked through the widget that sets it.

    A refusal that only lands in the diagnostics list is a refusal the user's
    eyes are not on: they are on the field they just typed.
    """

    @pytest.mark.asyncio
    async def test_states_on_a_dollar_limited_layer(self, program_file) -> None:
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            await _open_layer(pilot, "el-primary")
            await _type_into(pilot, "#f-layer-states", "NY")
            assert any(
                "dollar-limited layer cannot carry them" in m for m in _messages(app)
            ), _messages(app)

    @pytest.mark.asyncio
    async def test_states_on_a_dollar_limited_layer_do_not_land_at_all(
        self, program_file
    ) -> None:
        """The refusal used to be soft: the value landed and the validator
        flagged it. `edit._guard_states` blocks the write, so the layer keeps
        what it had and the FIELD has to show that — a form still displaying
        the typed text claims a write that never happened."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            await _type_into(pilot, "#f-layer-states", "NY")
            assert editor._layer("el-primary").states == []
            assert editor.query_one("#f-layer-states").value == ""

    @pytest.mark.asyncio
    async def test_monopolistic_state(self, program_file) -> None:
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY, OH")
            assert any("monopolistic" in m for m in _messages(app)), _messages(app)

    @pytest.mark.asyncio
    async def test_duplicate_state(self, program_file) -> None:
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY, ny")
            assert any("listed twice" in m for m in _messages(app)), _messages(app)

    @pytest.mark.asyncio
    async def test_unrecognised_state_warns(self, program_file) -> None:
        """A warning, not an error — and it still has to be said out loud, or
        the one check the field exists for is quietly not applied."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "Ontario")
            said = [n for n in app._notifications if "could not be applied" in n.message]
            assert said, _messages(app)
            assert said[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_premium_detail_on_a_priced_layer(self, program_file) -> None:
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            await _open_layer(pilot, "el-primary")  # premium $400,000
            await _type_into(pilot, "#f-layer-premium-detail", "Included with Part A")
            assert any(
                "premiumDetail says what a ZERO premium" in m for m in _messages(app)
            ), _messages(app)

    @pytest.mark.asyncio
    async def test_premium_edit_that_invalidates_a_premium_detail(
        self, program_file
    ) -> None:
        """The conflict can arrive from the OTHER side: a stated detail is
        fine until the premium stops being zero."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-premium-detail", "Included with Part A")
            assert not any(
                "premiumDetail says" in m for m in _messages(app)
            ), "a zero premium takes the detail without complaint"
            await _type_into(pilot, "#f-layer-premium", "50k")
            assert editor._layer("wc-stat").premium == 50_000
            assert any(
                "premiumDetail says what a ZERO premium" in m for m in _messages(app)
            ), _messages(app)

    @pytest.mark.asyncio
    async def test_duplicate_named_limit(self, program_file) -> None:
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            await pilot.pause()
            for _ in range(2):
                await pilot.press("a")
                await pilot.pause()
            rows = editor._layer("el-primary").named_limits
            assert len(rows) == 2, rows  # both rows carry the placeholder name
            assert any(
                "two named limits are both called" in m for m in _messages(app)
            ), _messages(app)

    @pytest.mark.asyncio
    async def test_named_limits_under_a_limits_detail(self, program_file) -> None:
        """Prose wins, and carrying both is refused — the user hears that at
        the moment they add the row, not when a schedule prints without it."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            await _type_into(
                pilot, "#f-layer-limits-detail", "Each Accident $1,000,000"
            )
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            sheet.focus()
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert any("would hide the named limits" in m for m in _messages(app)), (
                _messages(app)
            )

    @pytest.mark.asyncio
    async def test_limits_detail_typed_over_named_limits(self, program_file) -> None:
        """And from the other side: the prose arrives second."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "el-primary")
            editor.session.mutate(
                lambda p: editor._layer("el-primary").named_limits.append(
                    NamedLimit(name="Each Accident", amount=1_000_000)
                )
            )
            await editor._rebuild_detail()
            await pilot.pause()
            await _type_into(
                pilot, "#f-layer-limits-detail", "Each Accident $1,000,000"
            )
            assert any("would hide the named limits" in m for m in _messages(app)), (
                _messages(app)
            )


# --- reachability ------------------------------------------------------------


class TestKeystrokePath:
    """Driven as real keypresses from the state the editor opens in."""

    @pytest.mark.asyncio
    async def test_n_reaches_the_named_limits_grid_from_a_cold_start(
        self, program_file
    ) -> None:
        """n · down · n — the participants jump's own shape, on the grid it
        was named as the pattern for. Five keys later there is a row on file.
        """
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            assert editor.selected == ("program", None)  # where the editor opens

            keys = ["n", "down", "n"]
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            sheet = editor.query_one("#named-limits-sheet", SheetTable)
            assert sheet.has_focus, "n lands on the named-limits grid"
            assert editor.selected == ("layer", "el-primary")

            await pilot.press("a")
            await pilot.pause()
            keys.append("a")
            assert len(editor._layer("el-primary").named_limits) == 1
            assert len(keys) == 4, keys

    @pytest.mark.asyncio
    async def test_n_with_no_layer_selected_says_what_to_do(self, program_file) -> None:
        """The first n opens the picker and names the next step, exactly as p
        does — a key that refuses in silence reads as a broken app."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = app.screen
            await pilot.press("n")
            await pilot.pause()
            assert editor._layers_sheet_open
            assert any("then n" in m for m in _messages(app)), _messages(app)

    @pytest.mark.asyncio
    async def test_states_and_premium_detail_are_two_tabs_from_that_grid(
        self, program_file
    ) -> None:
        """The three fields are one cluster: `n` lands in the middle of it, so
        shift+tab reaches states and tab reaches the premium detail."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            editor.query_one("#named-limits-sheet", SheetTable).focus()
            await pilot.pause()

            await pilot.press("shift+tab", "shift+tab")
            await pilot.pause()
            assert editor.query_one("#f-layer-states").has_focus

            editor.query_one("#named-limits-sheet", SheetTable).focus()
            await pilot.pause()
            await pilot.press("tab", "tab")
            await pilot.pause()
            assert editor.query_one("#f-layer-premium-detail").has_focus

    @pytest.mark.asyncio
    async def test_typed_states_survive_a_reload_from_disk(self, program_file) -> None:
        """End to end: keypresses in, canonical file out, model back."""
        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(160, 48)) as pilot:
            editor = await _open_layer(pilot, "wc-stat")
            await _type_into(pilot, "#f-layer-states", "NY, NJ")
            editor.session.save()
        reloaded = load_program(program_file)
        assert reloaded.layers[0].states == ["NY", "NJ"]


class TestHintLinesFit:
    """The hint line is where `n` is advertised, and it is one row of a
    height-1 Static: content past the width is not scrolled, it is gone —
    starting with the `? all keys` escape hatch at the end of it. Adding `n`
    to the layers-sheet hint pushed it to 146 columns against a 138-column
    content box, which is why that line lost some words.

    This is the live-widget proof of the 138 that tests/test_dead_keys.py then
    applies to every hint line without paying for an app run each time.
    """

    @pytest.mark.asyncio
    async def test_at_140_columns_nothing_is_cropped(self, program_file) -> None:
        from rich.cells import cell_len
        from textual.widgets import Static

        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            hint = editor.query_one("#key-hint", Static)
            assert hint.content_size.width == 138, "the constant tests assume 138"

            def printed() -> int:
                # the widget's OWN rendered text, not the constant behind it:
                # _refresh_hint appends `· ? all keys` and the markup is gone
                return cell_len(hint.render().plain)

            too_wide = []
            for kind, key in (
                ("program", None),
                ("layers-group", None),
                ("layer", "el-primary"),
                ("line", "wc"),
                ("retention", 0),
            ):
                editor.selected = (kind, key)
                await editor._rebuild_detail()
                await pilot.pause()
                if printed() > 138:
                    too_wide.append((kind, printed()))
            await pilot.press("v")  # the layers sheet, the longest line of all
            await pilot.pause()
            if printed() > 138:
                too_wide.append(("layers-sheet", printed()))
            assert not too_wide, too_wide

    @pytest.mark.asyncio
    async def test_the_measurement_can_fail(self, program_file) -> None:
        """The assertion above is a negative one over text nobody typed in the
        test — it would pass just as happily against a hint line that no
        longer exists. This is its proof of life."""
        from rich.cells import cell_len
        from textual.widgets import Static

        app = TowerkitApp(path=program_file)
        async with app.run_test(size=(140, 45)) as pilot:
            hint = app.screen.query_one("#key-hint", Static)
            hint.update("[b]x[/b] " + "wide " * 40)
            await pilot.pause()
            assert cell_len(hint.render().plain) > hint.content_size.width
