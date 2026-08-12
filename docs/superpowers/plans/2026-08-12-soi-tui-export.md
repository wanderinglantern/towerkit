# SOI Export from the TUI Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pressing `x` in the TUI editor exports the Schedule of Insurance workbook to `dist/`, exactly as accessible as `r` render.

**Architecture:** One new action on `EditorScreen` that mirrors `action_render` step for step (drain input → validation gate → write → notify → OPEN_CMD), calling the existing pure core `build_soi`/`sheet_title`/`default_filename` and writer `write_soi`. No changes to `soi.py` or `render/soi_xlsx.py`.

**Tech Stack:** Python, Textual (pilot tests via `run_test`), openpyxl (test read-back only).

**Spec:** `docs/superpowers/specs/2026-08-12-soi-tui-export-design.md`

## Global Constraints

- ⚠️ **Dirty working tree:** `src/towerkit/tui/screens/editor.py` and `tests/test_tui.py` already contain UNRELATED uncommitted changes (a placeholder-id bugfix and in-flight dirty-exit-modal work). Commit ONLY this feature's hunks: use `git add -p` and select the hunks added by this plan. Never `git add` those two files whole.
- Known pre-existing failure: `tests/test_tui.py::TestDirtyExitOffersSave::test_escape_save_with_errors_stays_in_editor` fails from the unrelated in-flight work. It is NOT caused by this plan; do not fix it, do not let it block.
- Run tests with `uv run pytest` (repo uses uv; PyPI is proxied — never pip install).
- Output filename comes from `towerkit.soi.default_filename(program)`; never hardcode a filename in implementation code.
- Program JSON is never written by export.

---

### Task 1: `x` binding + `action_export_soi` with validation gate

**Files:**
- Modify: `src/towerkit/tui/screens/editor.py` (BINDINGS list ~line 119; help text "Output" block ~line 92; new method after `action_render_options` ~line 1180)
- Test: `tests/test_tui.py` (new class at end of file)

**Interfaces:**
- Consumes: `towerkit.soi.build_soi(program) -> list[SoiSection]`, `sheet_title(program) -> str`, `default_filename(program) -> str`; `towerkit.render.soi_xlsx.write_soi(sections, *, title: str, theme: Theme, out_path: Path, show_premiums: bool = True) -> Path`; `EditorScreen._drain_focused_input()`, `self.session.diagnostics().errors`, `self.tower_theme`, `_opts(screen).show_premiums`.
- Produces: `EditorScreen.action_export_soi() -> None`, bound to key `x` (Task 2's test presses `x`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py` (imports `Path`, `pytest`, `TowerkitApp`, `EditorScreen`, and the `sample_copy` fixture already exist at module top):

```python
class TestSoiExport:
    @pytest.mark.asyncio
    async def test_x_exports_workbook_to_dist(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        before = sample_copy.read_bytes()
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            assert isinstance(editor, EditorScreen)
            await pilot.press("x")
            await pilot.pause()
            from towerkit.soi import default_filename

            out = Path("dist") / default_filename(editor.session.program)
        assert out.exists()
        # program file untouched by export
        assert sample_copy.read_bytes() == before

    @pytest.mark.asyncio
    async def test_x_blocked_by_validation_errors(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            # overlap: pull an excess layer down to attach 0 (same trick as
            # the dirty-exit tests) -> validation errors
            editor.session.mutate(lambda p: setattr(p.layers[1], "attach", 0))
            assert editor.session.diagnostics().errors
            await pilot.press("x")
            await pilot.pause()
            from towerkit.soi import default_filename

            out = Path("dist") / default_filename(editor.session.program)
        assert not out.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tui.py::TestSoiExport -v`
Expected: both FAIL — `x` is unbound, so no file is written; the first test's `assert out.exists()` fails. (If the second passes trivially, that's fine — it must still pass after Step 3.)

- [ ] **Step 3: Implement binding, help text, and action**

In `src/towerkit/tui/screens/editor.py`:

(a) In `EditorScreen.BINDINGS`, directly after `("t", "render_options", "Options"),` add:

```python
        ("x", "export_soi", "SOI"),
```

(b) In the help text's `Output` block (after the `t          render options: …` lines) add:

```
  x          export SOI workbook (.xlsx to dist/)
```

(c) After `action_render_options`, add (note: `os`, `subprocess`, `shlex`, `Path` are already imported at module top — verify, don't re-import):

```python
    def action_export_soi(self) -> None:
        self._drain_focused_input()
        diags = self.session.diagnostics()
        if diags.errors:
            self.notify(
                f"{len(diags.errors)} validation errors — fix before exporting",
                severity="error",
            )
            return
        from ...render.soi_xlsx import write_soi
        from ...soi import build_soi, default_filename, sheet_title

        program = self.session.program
        written = write_soi(
            build_soi(program),
            title=sheet_title(program),
            theme=self.tower_theme,
            out_path=Path("dist") / default_filename(program),
            show_premiums=_opts(self).show_premiums,
        )
        self.notify(f"exported: {written}")
        open_cmd = os.environ.get("OPEN_CMD")
        if open_cmd:
            subprocess.run([*shlex.split(open_cmd), str(written)], check=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tui.py::TestSoiExport -v`
Expected: 2 PASS

- [ ] **Step 5: Run the full TUI suite**

Run: `uv run pytest tests/test_tui.py -q`
Expected: everything passes EXCEPT the known pre-existing `TestDirtyExitOffersSave::test_escape_save_with_errors_stays_in_editor` failure (unrelated in-flight work — see Global Constraints).

- [ ] **Step 6: Commit (partial add — see Global Constraints)**

```bash
git add -p src/towerkit/tui/screens/editor.py   # select ONLY the x-binding, help line, and action_export_soi hunks
git add -p tests/test_tui.py                    # select ONLY the TestSoiExport class hunk
git commit -m "tui: x exports the SOI workbook to dist/"
```

---

### Task 2: Premiums toggle carries into the export

**Files:**
- Modify: `tests/test_tui.py` (extend `TestSoiExport`)
- Modify (only if Step 2 fails): `src/towerkit/tui/screens/editor.py:action_export_soi`

**Interfaces:**
- Consumes: `EditorScreen.action_export_soi` bound to `x` (Task 1); `app.show_premiums` (the `_opts` attribute on `TowerkitApp`); workbook headers live in worksheet row 1 (see `tests/test_soi_xlsx.py::TestContent::test_headers_and_title`).
- Produces: nothing new — this task locks the `show_premiums` pass-through with a test.

- [ ] **Step 1: Write the test**

Append inside `class TestSoiExport` in `tests/test_tui.py`:

```python
    @pytest.mark.asyncio
    async def test_premiums_toggle_drops_premium_column(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            app.show_premiums = False  # the t-menu toggle's backing attr
            await pilot.press("x")
            await pilot.pause()
            from towerkit.soi import default_filename

            out = Path("dist") / default_filename(editor.session.program)
        from openpyxl import load_workbook

        headers = [c.value for c in load_workbook(out).active[1]]
        assert "Premium" not in headers
        assert headers[0] == "Insured"
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_tui.py::TestSoiExport::test_premiums_toggle_drops_premium_column -v`
Expected: PASS (Task 1's implementation already passes `show_premiums=_opts(self).show_premiums`). If it FAILS, the pass-through is broken — fix `action_export_soi`'s `show_premiums` argument to read `_opts(self).show_premiums`, and re-run until PASS.

- [ ] **Step 3: Run the full TUI suite**

Run: `uv run pytest tests/test_tui.py -q`
Expected: same result as Task 1 Step 5 (only the known unrelated failure).

- [ ] **Step 4: Commit (partial add)**

```bash
git add -p tests/test_tui.py   # select ONLY the new premiums-toggle test hunk
git commit -m "tui: lock SOI export premiums toggle with a read-back test"
```
