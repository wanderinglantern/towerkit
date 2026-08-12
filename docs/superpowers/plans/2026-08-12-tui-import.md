# TUI Import Entry Points Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The program browser gains `i` (import a schedule file), `p` (paste a schedule), and `w` (write a blank template workbook) — all riding the existing `towerctl import`/`template` machinery.

**Architecture:** `cli._cmd_import`'s source-dispatch pipeline moves into `towerkit.ingest.import_schedule(...) -> DraftProgram` (returning the draft keeps CLI diagnostic printing byte-identical and gives the TUI the same diagnostics to notify); the CLI shrinks to plumbing around it. The browser adds three actions: two `PromptModal` flows and one new `PasteImportModal`, converging on a shared `_finish_import` that names the file from the built Program, refuses overwrites, writes canonically, refreshes the table, and opens the editor.

**Tech Stack:** Python, existing `towerkit.ingest`/`ingest_template`, Textual (browser actions, one new modal, pilot tests).

**Spec:** `docs/superpowers/specs/2026-08-12-tui-import-design.md`

## Global Constraints

- Zero behavior change to the CLI: every existing test in `tests/test_cli.py` passes unmodified through the Task 1 refactor.
- The TUI import writes exactly one new `.json` (never overwrites; refusal notifies naming the file) or one `.xlsx` (template — overwrites silently, same as `towerctl template`); no existing program file is ever modified.
- Output naming in the TUI: `programs_dir / f"{slugify(program.insured)}-{slugify(program.program)}.json"` using `towerkit.tui.session.slugify` and the **built Program's** fields (not raw user input).
- openpyxl imports stay deferred (inside functions), never at module top of `ingest.py` or `browser.py`.
- Test command: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest ...` — never plain pytest, never pip.
- When gating commits on tests in shell chains, NEVER pipe pytest into tail/grep before `&&` — redirect to a file, gate on the command, tail the file (repo rule, CLAUDE.md).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `import_schedule` in ingest.py + CLI refactor

**Files:**
- Modify: `src/towerkit/ingest.py` (new function at end)
- Modify: `src/towerkit/cli.py:245-305` (`_cmd_import` body shrinks; flag handling, naming, overwrite refusal, `--edit` stay)
- Test: `tests/test_ingest.py` (append), `tests/test_cli.py` (must pass unchanged)

**Interfaces:**
- Consumes: `parse_tower`, `program_from_rows`, `CANONICAL_FIELDS` (already in ingest.py); `towerkit.ingest_template.read_rows`; `towerkit.dates.parse_flexible_date`; `towerkit.model.Period`.
- Produces (Tasks 2–3 rely on this exact signature):
  ```python
  def import_schedule(
      source: str | Path | None,
      *,
      text: str | None = None,     # pasted schedule text; mutually exclusive with source
      insured: str = "",
      program: str = "",
      inception: str = "",
      expiry: str = "",
  ) -> DraftProgram
  ```
  Raises `ValueError` with the exact message `f"unknown columns {unknown!r}; expected {list(CANONICAL_FIELDS)!r}"` on strict-CSV failure. Callers then use `draft.diagnostics` and `draft.to_program()` (which raises `ProgramInvalidError`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest.py`:

```python
# --- import_schedule ----------------------------------------------------------

from pathlib import Path  # noqa: E402

from towerkit.ingest import import_schedule  # noqa: E402


class TestImportSchedule:
    def test_text_routes_through_parse_tower(self) -> None:
        draft = import_schedule(None, text=PASTE, insured="Atomic", program="Property")
        assert [layer.attach for layer in draft.layers] == [0, 10_000_000]

    def test_txt_file_routes_through_parse_tower(self, tmp_path) -> None:
        src = tmp_path / "sched.txt"
        src.write_text(PASTE, encoding="utf-8")
        draft = import_schedule(src, insured="Atomic", program="Property")
        assert draft.layers[0].limit == 10_000_000

    def test_csv_unknown_column_raises_value_error(self, tmp_path) -> None:
        src = tmp_path / "sched.csv"
        src.write_text("line,mystery\nGL,1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown columns"):
            import_schedule(src)

    def test_period_fallback_parses_human_dates(self) -> None:
        draft = import_schedule(
            None, text=PASTE, insured="Atomic", program="Property",
            inception="Jan 1 2026", expiry="1/1/2027",
        )
        assert draft.period is not None
        assert draft.period.start.isoformat() == "2026-01-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_ingest.py::TestImportSchedule -q`
Expected: ImportError — `import_schedule` doesn't exist.

- [ ] **Step 3: Implement `import_schedule`**

Append to `src/towerkit/ingest.py` (module already imports `Path`-adjacent basics — verify and reuse its import style; `csv` and the template reader import lazily inside):

```python
def import_schedule(
    source: str | Path | None,
    *,
    text: str | None = None,
    insured: str = "",
    program: str = "",
    inception: str = "",
    expiry: str = "",
) -> DraftProgram:
    """One entry point for every schedule source: pasted text, xlsx
    template, strict-header csv, or free text file. Returns the draft so
    callers surface draft.diagnostics their own way (print vs notify)
    before draft.to_program()."""
    if text is not None:
        draft = parse_tower(text, insured=insured, program=program)
    else:
        path = Path(source)  # type: ignore[arg-type]
        suffix = path.suffix.lower()
        if suffix == ".xlsx":
            from .ingest_template import read_rows

            draft = program_from_rows(read_rows(path), insured=insured, program=program)
        elif suffix == ".csv":
            import csv

            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                headers = [h.strip().lower() for h in reader.fieldnames or []]
                unknown = [h for h in headers if h and h not in CANONICAL_FIELDS]
                if unknown:  # same strictness as the xlsx reader — never drop silently
                    raise ValueError(
                        f"unknown columns {unknown!r}; expected {list(CANONICAL_FIELDS)!r}"
                    )
                rows: list[dict[str, object]] = [
                    {k.strip().lower(): v for k, v in row.items() if v not in (None, "")}
                    for row in reader
                ]
            draft = program_from_rows(rows, insured=insured, program=program)
        else:
            draft = parse_tower(
                path.read_text(encoding="utf-8"), insured=insured, program=program
            )
    if draft.period is None and inception and expiry:
        from .dates import parse_flexible_date

        start = parse_flexible_date(inception)
        end = parse_flexible_date(expiry)
        if start and end:
            from .model import Period

            draft.period = Period(start=start, end=end)
    return draft
```

(If `Period` / `parse_flexible_date` are already imported at ingest.py's top, use them directly instead of the local imports — match the module's existing style.)

- [ ] **Step 4: Refactor `_cmd_import` to call it**

In `src/towerkit/cli.py`, `_cmd_import` keeps: flag unpacking, stdin read, diagnostics printing, `to_program` try/except, naming, overwrite refusal, `dump_program`, `--edit`. The dispatch block (from `if source == "-":` through the `else: draft = parse_tower(...)` branch, plus the period-fallback block) is replaced by:

```python
    from .ingest import import_schedule

    try:
        draft = import_schedule(
            None if source == "-" else source,
            text=sys.stdin.read() if source == "-" else None,
            insured=insured,
            program=program_name,
            inception=args.inception,
            expiry=args.expiry,
        )
    except ValueError as exc:
        print(exc)
        return 1
```

Remove the now-unused local imports (`csv`, `parse_flexible_date`, `Period`, `CANONICAL_FIELDS`, `parse_tower`, `program_from_rows`, `read_rows`) from `_cmd_import` — keep only what the remaining body uses.

- [ ] **Step 5: Run the new tests, then the CLI suite unchanged**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_ingest.py tests/test_cli.py -q`
Expected: all pass; `tests/test_cli.py` is byte-identical to before this task.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/ingest.py src/towerkit/cli.py tests/test_ingest.py
git commit -m "ingest: import_schedule — one entry point for file/paste/stdin schedules"
```

---

### Task 2: Browser `w` (template) and `i` (import file)

**Files:**
- Modify: `src/towerkit/tui/screens/browser.py` (BINDINGS ~line 27; two actions + shared `_finish_import` helper after `action_clone`)
- Test: `tests/test_tui.py` (new class at end)

**Interfaces:**
- Consumes: `import_schedule` (Task 1, exact signature above); `write_template` from `towerkit.ingest_template`; `PromptModal(label, default="") -> str | None`; `EditSession.open`, `EditorScreen`, `slugify` (browser already imports the first two — extend the existing import lines); `dump_program` from `towerkit.model`; the browser's existing table-repopulation method (the one `on_mount` and `action_clone` use after writing a file — read `browser.py:63-80` and call the same method, not a hand-rolled refresh).
- Produces: `ProgramBrowser._finish_import(draft) -> None` (Task 3 reuses it verbatim); bindings `w`/`i`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py` (drive `PromptModal` the same way existing tests do — set its `Input`'s value, press enter; check `TestCreateFromScratch`/save-flow tests for the exact idiom and reuse it):

```python
class TestBrowserImport:
    @pytest.mark.asyncio
    async def test_w_writes_template_workbook(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "programs").mkdir()
        app = TowerkitApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("w")
            await pilot.pause()
            prompt = app.screen
            prompt.query_one(Input).value = "blank.xlsx"
            await pilot.press("enter")
            await pilot.pause()
        assert (tmp_path / "blank.xlsx").exists()

    @pytest.mark.asyncio
    async def test_i_imports_filled_template_and_opens_editor(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "programs").mkdir()
        # Build a filled template exactly the way the CLI round-trip test
        # does: lift the workbook-filling fixture from
        # tests/test_cli.py::TestTemplate::test_template_then_import and
        # reuse it here (copy the helper, do not import across test files).
        src = _filled_template(tmp_path)  # returns Path to a filled .xlsx
        app = TowerkitApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("i")
            await pilot.pause()
            prompt = app.screen
            prompt.query_one(Input).value = str(src)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EditorScreen)  # opened for editing
            new_files = list((tmp_path / "programs").glob("*.json"))
            assert len(new_files) == 1

    @pytest.mark.asyncio
    async def test_i_refuses_existing_output(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "programs").mkdir()
        src = _filled_template(tmp_path)
        app = TowerkitApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("i")
            await pilot.pause()
            app.screen.query_one(Input).value = str(src)
            await pilot.press("enter")
            await pilot.pause()
        target = next((tmp_path / "programs").glob("*.json"))
        before = target.read_bytes()
        app2 = TowerkitApp()
        async with app2.run_test(size=(140, 45)) as pilot:
            await pilot.press("i")
            await pilot.pause()
            app2.screen.query_one(Input).value = str(src)
            await pilot.press("enter")
            await pilot.pause()
            assert any(
                "not overwriting" in n.message for n in app2._notifications
            )
        assert target.read_bytes() == before

    @pytest.mark.asyncio
    async def test_i_bad_source_notifies(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "programs").mkdir()
        app = TowerkitApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("i")
            await pilot.pause()
            app.screen.query_one(Input).value = str(tmp_path / "nope.txt")
            await pilot.press("enter")
            await pilot.pause()
            assert any(
                "import failed" in n.message for n in app._notifications
            )
        assert not list((tmp_path / "programs").glob("*.json"))
```

Add `_filled_template(tmp_path) -> Path` as a module-level helper in `tests/test_tui.py`, copied from the CLI round-trip test's fixture (write_template → openpyxl fill of the required cells → save). `Input` is already imported at the top of test_tui.py (verify; add to the existing import line if not).

- [ ] **Step 2: Run tests to verify they fail**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py::TestBrowserImport -q`
Expected: 4 FAIL — `w`/`i` unbound, so no prompt opens and `query_one(Input)` raises or files never appear.

- [ ] **Step 3: Implement bindings and actions**

In `src/towerkit/tui/screens/browser.py`:

(a) BINDINGS, after `("t", "render_options", "Options")`:

```python
        ("i", "import_file", "Import"),
        ("w", "template", "Template"),
```

(b) Actions after `action_clone` (imports: extend the module's existing top-level imports for `dump_program` and `slugify` if absent; keep `import_schedule`/`write_template` imports inside the methods so openpyxl stays off the TUI startup path):

```python
    def action_template(self) -> None:
        def on_name(name: str | None) -> None:
            if not name:
                return
            from ...ingest_template import write_template

            target = Path(name if name.endswith(".xlsx") else f"{name}.xlsx")
            self.notify(f"template written: {write_template(target)}")

        self.app.push_screen(
            PromptModal("Template file name:", default="template.xlsx"), on_name
        )

    def action_import_file(self) -> None:
        def on_source(value: str | None) -> None:
            if not value:
                return
            from ...ingest import import_schedule

            try:
                draft = import_schedule(Path(value))
            except Exception as exc:
                self.notify(f"import failed: {exc}", severity="error")
                return
            self._finish_import(draft)

        self.app.push_screen(
            PromptModal("Schedule file (xlsx/csv/text):"), on_source
        )

    def _finish_import(self, draft) -> None:
        from ...model import dump_program
        from ...validate import ProgramInvalidError
        from ..session import EditSession, slugify

        for diag in draft.diagnostics.items:
            self.notify(str(diag), severity="warning")
        try:
            program = draft.to_program()
        except ProgramInvalidError as exc:
            first = (
                exc.diagnostics.errors[0].message
                if exc.diagnostics.errors
                else "invalid schedule"
            )
            self.notify(f"import failed: {first}", severity="error")
            return
        out = self.programs_dir / (
            f"{slugify(program.insured)}-{slugify(program.program)}.json"
        )
        if out.exists():  # program files are the source of truth — never clobber
            self.notify(f"{out.name} exists — not overwriting", severity="error")
            return
        dump_program(program, out)
        # repopulate the table with the SAME method on_mount/action_clone use
        # (read browser.py:63-80 for its name), then open the editor:
        self._reload_rows_method_used_elsewhere()
        self.notify(f"imported {out.name}")
        self.app.push_screen(
            EditorScreen(EditSession.open(out), theme_path=self.theme_path)
        )
```

`_reload_rows_method_used_elsewhere()` is a stand-in for the browser's real repopulation method — find its actual name in `browser.py:63-80` (the method containing `table.clear()`) and call that. Everything else is verbatim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py::TestBrowserImport -q`
Expected: 4 passed.

- [ ] **Step 5: Full TUI + CLI suites**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/tui/screens/browser.py tests/test_tui.py
git commit -m "tui: browser imports schedules (i) and writes templates (w)"
```

---

### Task 3: `p` — paste a schedule

**Files:**
- Modify: `src/towerkit/tui/widgets/modals.py` (new `PasteImportModal` at end)
- Modify: `src/towerkit/tui/screens/browser.py` (binding + action)
- Test: `tests/test_tui.py` (extend `TestBrowserImport`)

**Interfaces:**
- Consumes: `import_schedule(None, text=..., insured=..., program=..., inception=..., expiry=...)` (Task 1); `ProgramBrowser._finish_import(draft)` (Task 2, reused verbatim).
- Produces: `PasteImportModal(ModalScreen[dict | None])` — dismisses with `{"text": str, "insured": str, "program": str, "inception": str, "expiry": str}` or `None`.

- [ ] **Step 1: Write the failing test**

Append inside `class TestBrowserImport` in `tests/test_tui.py` (PASTE format from `tests/test_ingest.py` — copy the constant, don't import across test files):

```python
    PASTE = (
        "Primary 10M — Chubb 100% — 250,000\n"
        "15M xs 10M — AXA XL 60%, Sompo 40% — 180k\n"
        "SIR 500k\n"
    )

    @pytest.mark.asyncio
    async def test_p_pastes_schedule_with_fields(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "programs").mkdir()
        app = TowerkitApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("p")
            await pilot.pause()
            from towerkit.tui.widgets.modals import PasteImportModal

            modal = app.screen
            assert isinstance(modal, PasteImportModal)
            modal.query_one("#paste-text").text = self.PASTE
            modal.query_one("#paste-insured").value = "Atomic Industries"
            modal.query_one("#paste-program").value = "Property"
            modal.query_one("#paste-inception").value = "Jan 1 2026"
            modal.query_one("#paste-expiry").value = "1/1/2027"
            modal.query_one("#paste-confirm").press()
            await pilot.pause()
            assert isinstance(app.screen, EditorScreen)
            files = list((tmp_path / "programs").glob("*.json"))
            assert len(files) == 1
            from towerkit.model import load_program

            program = load_program(files[0])
            assert program.insured == "Atomic Industries"
            assert len(program.layers) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest "tests/test_tui.py::TestBrowserImport::test_p_pastes_schedule_with_fields" -q`
Expected: FAIL — ImportError on `PasteImportModal` / `p` unbound.

- [ ] **Step 3: Implement `PasteImportModal`**

Append to `src/towerkit/tui/widgets/modals.py`, matching the file's conventions (`DEFAULT_CSS`, single `on_button_pressed`, `BINDINGS` with escape — mirror `SendLineModal`, and extend the existing `textual.widgets` import with `TextArea` if absent):

```python
class PasteImportModal(ModalScreen[dict | None]):
    """Paste a schedule as text plus the meta the text can't carry."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PasteImportModal { align: center middle; }
    #paste-box { width: 90; height: auto; max-height: 32; padding: 1 2;
                 background: $surface; border: thick $primary; }
    #paste-text { height: 10; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="paste-box"):
            yield Label("Paste the schedule (one layer per line):")
            yield TextArea(id="paste-text")
            yield Label("Insured")
            yield Input(id="paste-insured")
            yield Label("Program")
            yield Input(id="paste-program")
            yield Label("Inception / Expiry (any date form)")
            yield Input(id="paste-inception", placeholder="Jan 1 2026")
            yield Input(id="paste-expiry", placeholder="Jan 1 2027")
            with Horizontal():
                yield Button("Import", variant="primary", id="paste-confirm")
                yield Button("Cancel", id="paste-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "paste-confirm":
            self.dismiss({
                "text": self.query_one("#paste-text", TextArea).text,
                "insured": self.query_one("#paste-insured", Input).value.strip(),
                "program": self.query_one("#paste-program", Input).value.strip(),
                "inception": self.query_one("#paste-inception", Input).value.strip(),
                "expiry": self.query_one("#paste-expiry", Input).value.strip(),
            })
        else:
            self.dismiss(None)
```

- [ ] **Step 4: Wire the browser action**

In `browser.py`: binding `("p", "paste_import", "Paste"),` after the `i` binding; action after `action_import_file` (add `PasteImportModal` to the modals import):

```python
    def action_paste_import(self) -> None:
        def on_fields(fields: dict | None) -> None:
            if not fields or not fields["text"].strip():
                return
            from ...ingest import import_schedule

            try:
                draft = import_schedule(
                    None,
                    text=fields["text"],
                    insured=fields["insured"],
                    program=fields["program"],
                    inception=fields["inception"],
                    expiry=fields["expiry"],
                )
            except Exception as exc:
                self.notify(f"import failed: {exc}", severity="error")
                return
            self._finish_import(draft)

        self.app.push_screen(PasteImportModal(), on_fields)
```

- [ ] **Step 5: Run the test, then both suites**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py::TestBrowserImport tests/test_cli.py tests/test_ingest.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/tui/widgets/modals.py src/towerkit/tui/screens/browser.py tests/test_tui.py
git commit -m "tui: p pastes a schedule into a new program"
```
