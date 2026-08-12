# Line Transfer Between Programs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `>` in the TUI editor sends the selected line — with its exclusive dependents — into another program file, as a copy (default) or a move.

**Architecture:** A new pure module `src/towerkit/transfer.py` owns all semantics (`transfer_line`, returning fresh copies plus a human-readable summary); the editor grows a `>` action that drives a new target-picker modal, confirms with the summary, writes the target canonically, and applies the source change through the undoable session. Rule: **exclusive travels, shared stays, move narrows.**

**Tech Stack:** Python, pydantic models in `towerkit.model`, Textual (modal + pilot tests).

**Spec:** `docs/superpowers/specs/2026-08-12-line-transfer-design.md`

## Global Constraints

- `transfer.py` never imports rendering libraries or anything from `towerkit.tui` (repo rule: pure modules stay pure; the id-suffix convention is reimplemented, not imported from `tui/session.py`).
- `transfer_line` never mutates its inputs; deep-copy via `loads_program(dumps_program(p))` (canonical round-trip is the repo's fidelity guarantee).
- Target file writes are additive-only and canonical (`dumps_program`); nothing existing in the target is removed or reordered.
- Source changes go through `session.mutate` (one undo step); the source file is never written by this feature.
- Test command: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest ...` — never plain pytest, never pip (uv + corporate wheelhouse).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Program files list as `programs/*.json` + `programs/private/*.json` (the browser's globbing convention, `browser.py:75-77`).

---

### Task 1: Pure core — `transfer_line` copy/move semantics

**Files:**
- Create: `src/towerkit/transfer.py`
- Test: `tests/test_transfer.py` (new file)

**Interfaces:**
- Consumes: `towerkit.model.Program/Line/Layer/Retention/Sublimit`, `dumps_program`, `loads_program`; `towerkit.money.format_money`.
- Produces (Tasks 2–3 rely on these exact names):
  - `TransferSummary` dataclass: `travels: list[str]`, `stays: list[str]`, `renames: list[tuple[str, str]]`
  - `TransferResult` dataclass: `src_after: Program`, `dst_after: Program`, `summary: TransferSummary`
  - `transfer_line(src: Program, dst: Program, line_id: str, *, move: bool) -> TransferResult`; raises `KeyError` on unknown `line_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transfer.py`:

```python
"""transfer_line: exclusive travels, shared stays, move narrows."""

from __future__ import annotations

import pytest

from towerkit.model import Program, dumps_program
from towerkit.transfer import transfer_line


def make_src() -> Program:
    return Program.model_validate({
        "insured": "Src Co", "program": "Casualty", "placement": "bound",
        "period": {"start": "2026-01-01", "end": "2027-01-01"},
        "lines": [
            {"id": "gl", "name": "General Liability"},
            {"id": "al", "name": "Auto Liability"},
        ],
        "layers": [
            {"id": "gl-primary", "name": "Primary GL", "appliesTo": ["gl"],
             "attach": 0, "limit": 1_000_000},
            {"id": "umbrella", "name": "Umbrella", "appliesTo": ["gl", "al"],
             "attach": 1_000_000, "limit": 5_000_000},
        ],
        "retentions": [
            {"appliesTo": ["gl"], "type": "sir", "amount": 250_000},
            {"appliesTo": ["gl", "al"], "type": "deductible", "amount": 10_000},
        ],
        "sublimits": [
            {"name": "Flood", "amount": 100_000, "appliesTo": ["gl"]},
        ],
    })


def make_dst() -> Program:
    return Program.model_validate({
        "insured": "Dst Co", "program": "Scenario", "placement": "proposed",
        "period": {"start": "2026-01-01", "end": "2027-01-01"},
        "lines": [{"id": "el", "name": "Employers Liability"}],
        "layers": [{"id": "el-primary", "name": "Primary EL",
                    "appliesTo": ["el"], "attach": 0, "limit": 1_000_000}],
        "retentions": [], "sublimits": [],
    })


class TestCopy:
    def test_exclusive_travels_shared_stays(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=False)
        dst_line_ids = [ln.id for ln in r.dst_after.lines]
        dst_layer_ids = [ly.id for ly in r.dst_after.layers]
        assert dst_line_ids == ["el", "gl"]              # appended at end
        assert dst_layer_ids == ["el-primary", "gl-primary"]
        assert "umbrella" not in dst_layer_ids           # shared stays behind
        # exclusive retention + sublimit travel; shared retention does not
        assert [ret.amount for ret in r.dst_after.retentions] == [250_000]
        assert [s.name for s in r.dst_after.sublimits] == ["Flood"]

    def test_copy_leaves_source_identical(self) -> None:
        src = make_src()
        r = transfer_line(src, make_dst(), "gl", move=False)
        assert dumps_program(r.src_after) == dumps_program(src)

    def test_inputs_never_mutated(self) -> None:
        src, dst = make_src(), make_dst()
        before_src, before_dst = dumps_program(src), dumps_program(dst)
        transfer_line(src, dst, "gl", move=True)
        assert dumps_program(src) == before_src
        assert dumps_program(dst) == before_dst

    def test_unknown_line_raises(self) -> None:
        with pytest.raises(KeyError):
            transfer_line(make_src(), make_dst(), "nope", move=False)


class TestMove:
    def test_move_removes_line_and_exclusives_and_narrows_shared(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=True)
        assert [ln.id for ln in r.src_after.lines] == ["al"]
        src_layer_ids = [ly.id for ly in r.src_after.layers]
        assert src_layer_ids == ["umbrella"]             # exclusive gone
        umbrella = r.src_after.layers[0]
        assert umbrella.applies_to == ["al"]             # narrowed, not empty
        # shared retention narrowed; exclusive retention/sublimit removed
        assert len(r.src_after.retentions) == 1
        assert r.src_after.retentions[0].applies_to == ["al"]
        assert r.src_after.sublimits == []

    def test_shared_never_copied_narrowed_into_target(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=True)
        assert "umbrella" not in [ly.id for ly in r.dst_after.layers]


class TestSummary:
    def test_travels_and_stays_are_named(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=False)
        joined = "\n".join(r.summary.travels)
        assert "Line: General Liability" in joined
        assert "Layer: Primary GL" in joined
        assert "SIR" in joined and "$250,000" in joined
        assert "Sublimit: Flood" in joined
        stays = "\n".join(r.summary.stays)
        assert "Umbrella" in stays and "Auto Liability" in stays
        assert r.summary.renames == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_transfer.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'towerkit.transfer'`.

- [ ] **Step 3: Implement `src/towerkit/transfer.py`**

```python
"""Move or copy a line — with its exclusive dependents — between programs.

Pure core: no rendering imports, no TUI imports (repo rule). The one rule:
exclusive travels, shared stays, move narrows. Shared layers are never
copied narrowed into the target — that would fabricate a placement that
does not exist. Inputs are never mutated; both returned programs are fresh
canonical copies."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Program, dumps_program, loads_program
from .money import format_money

_RETENTION_LABELS = {"deductible": "Deductible", "sir": "SIR", "captive": "Captive"}


@dataclass
class TransferSummary:
    travels: list[str] = field(default_factory=list)
    stays: list[str] = field(default_factory=list)
    renames: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TransferResult:
    src_after: Program
    dst_after: Program
    summary: TransferSummary


def _copy(program: Program) -> Program:
    return loads_program(dumps_program(program))


def _unique_id(wanted: str, taken: set[str]) -> str:
    # same suffix convention as EditSession.unique_id (not imported: pure
    # modules never import from towerkit.tui)
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}" in taken:
        n += 1
    return f"{wanted}-{n}"


def transfer_line(
    src: Program, dst: Program, line_id: str, *, move: bool
) -> TransferResult:
    if line_id not in {ln.id for ln in src.lines}:
        raise KeyError(line_id)
    src_after, dst_after = _copy(src), _copy(dst)
    summary = TransferSummary()
    line_names = {ln.id: ln.name for ln in src.lines}

    def exclusive(applies_to: list[str]) -> bool:
        return set(applies_to) == {line_id}

    def others(applies_to: list[str]) -> str:
        return ", ".join(line_names[i] for i in applies_to if i != line_id)

    # -- what travels (read from the src copy) --------------------------------
    line = next(ln for ln in src_after.lines if ln.id == line_id)
    layers = [ly for ly in src_after.layers if exclusive(ly.applies_to)]
    retentions = [r for r in src_after.retentions if exclusive(r.applies_to)]
    sublimits = [s for s in src_after.sublimits if exclusive(s.applies_to)]

    summary.travels.append(f"Line: {line.name}")
    summary.travels += [f"Layer: {ly.name}" for ly in layers]
    summary.travels += [
        f"Retention: {_RETENTION_LABELS[r.type.value]} {format_money(r.amount)}"
        for r in retentions
    ]
    summary.travels += [
        f"Sublimit: {s.name} {format_money(s.amount)}" for s in sublimits
    ]
    for ly in src.layers:
        if line_id in ly.applies_to and not exclusive(ly.applies_to):
            summary.stays.append(
                f"Layer: {ly.name} — shared with {others(ly.applies_to)}"
            )
    for r in src.retentions:
        if line_id in r.applies_to and not exclusive(r.applies_to):
            summary.stays.append(
                f"Retention: {_RETENTION_LABELS[r.type.value]} "
                f"{format_money(r.amount)} — shared with {others(r.applies_to)}"
            )
    for s in src.sublimits:
        if line_id in s.applies_to and not exclusive(s.applies_to):
            summary.stays.append(
                f"Sublimit: {s.name} — shared with {others(s.applies_to)}"
            )

    # -- graft into the target (collision handling lands in Task 2) ----------
    dst_after.lines.append(line)
    dst_after.layers.extend(layers)
    dst_after.retentions.extend(retentions)
    dst_after.sublimits.extend(sublimits)

    # -- source side ----------------------------------------------------------
    if move:
        src_after.lines = [ln for ln in src_after.lines if ln.id != line_id]
        src_after.layers = [
            ly for ly in src_after.layers if not exclusive(ly.applies_to)
        ]
        for ly in src_after.layers:
            if line_id in ly.applies_to:
                ly.applies_to = [i for i in ly.applies_to if i != line_id]
        src_after.retentions = [
            r
            for r in src_after.retentions
            if not exclusive(r.applies_to)
        ]
        for r in src_after.retentions:
            if line_id in r.applies_to:
                r.applies_to = [i for i in r.applies_to if i != line_id]
        src_after.sublimits = [
            s for s in src_after.sublimits if not exclusive(s.applies_to)
        ]
        for s in src_after.sublimits:
            if line_id in s.applies_to:
                s.applies_to = [i for i in s.applies_to if i != line_id]

    return TransferResult(src_after=src_after, dst_after=dst_after, summary=summary)
```

Note: `layers`/`retentions`/`sublimits` are objects from the `src_after` copy. In move mode the source-side list rebuilds drop them by the `exclusive` predicate, so appending the same objects to `dst_after` is safe — they are never shared between the two returned programs. Also note `applies_to` narrowing can never produce an empty list (shared means ≥2 refs), and `min_length=1` on the model enforces it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_transfer.py -q`
Expected: 8 passed.

- [ ] **Step 5: Confirm purity and run neighbors**

Run: `grep -n "matplotlib\|openpyxl\|from .tui\|from towerkit.tui" src/towerkit/transfer.py`
Expected: no output.
Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_transfer.py tests/test_canonical.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/transfer.py tests/test_transfer.py
git commit -m "transfer: pure core — exclusive travels, shared stays, move narrows"
```

---

### Task 2: Pure core — target id collisions re-slug with cascade

**Files:**
- Modify: `src/towerkit/transfer.py` (the graft section from Task 1)
- Test: `tests/test_transfer.py` (append a class)

**Interfaces:**
- Consumes: Task 1's `transfer_line`, `_unique_id`, `TransferSummary.renames`.
- Produces: collision behavior Tasks 3 relies on — transferred line/layer ids re-slugged against the target's `lines ∪ layers` id namespace, `applies_to` inside the transferred bundle cascaded, `(old, new)` pairs in `summary.renames`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer.py`:

```python
class TestCollisions:
    def test_line_id_collision_reslugs_and_cascades(self) -> None:
        dst = make_dst()
        dst.lines.append(type(dst.lines[0])(id="gl", name="Existing GL"))
        r = transfer_line(make_src(), dst, "gl", move=False)
        ids = [ln.id for ln in r.dst_after.lines]
        assert ids == ["el", "gl", "gl-2"]               # existing untouched
        moved_primary = next(
            ly for ly in r.dst_after.layers if ly.name == "Primary GL"
        )
        assert moved_primary.applies_to == ["gl-2"]      # cascade
        assert r.dst_after.retentions[0].applies_to == ["gl-2"]
        assert r.dst_after.sublimits[0].applies_to == ["gl-2"]
        assert ("gl", "gl-2") in r.summary.renames

    def test_layer_id_collision_reslugs(self) -> None:
        dst = make_dst()
        dst.layers[0].id = "gl-primary"                  # collide with traveller
        dst.layers[0].applies_to = ["el"]
        r = transfer_line(make_src(), dst, "gl", move=False)
        layer_ids = [ly.id for ly in r.dst_after.layers]
        assert layer_ids == ["gl-primary", "gl-primary-2"]
        assert ("gl-primary", "gl-primary-2") in r.summary.renames

    def test_no_collision_no_renames(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=False)
        assert r.summary.renames == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_transfer.py::TestCollisions -q`
Expected: first two FAIL (duplicate ids land unrenamed); third passes.

- [ ] **Step 3: Implement collision handling**

In `transfer_line`, replace the four graft lines (`dst_after.lines.append(line)` through `dst_after.sublimits.extend(sublimits)`) with:

```python
    taken = {ln.id for ln in dst_after.lines} | {ly.id for ly in dst_after.layers}
    new_line_id = _unique_id(line.id, taken)
    if new_line_id != line.id:
        summary.renames.append((line.id, new_line_id))
        line.id = new_line_id
    taken.add(new_line_id)
    for ly in layers:
        new_id = _unique_id(ly.id, taken)
        if new_id != ly.id:
            summary.renames.append((ly.id, new_id))
            ly.id = new_id
        taken.add(new_id)
        ly.applies_to = [new_line_id]
    for r in retentions:
        r.applies_to = [new_line_id]
    for s in sublimits:
        s.applies_to = [new_line_id]
    dst_after.lines.append(line)
    dst_after.layers.extend(layers)
    dst_after.retentions.extend(retentions)
    dst_after.sublimits.extend(sublimits)
```

(Travelling items are exclusive by definition, so `applies_to = [new_line_id]` is a rewrite of a one-element list, correct whether or not the line was renamed.)

- [ ] **Step 4: Run the whole transfer suite**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_transfer.py -q`
Expected: 11 passed (Task 1's tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/transfer.py tests/test_transfer.py
git commit -m "transfer: target id collisions re-slug with cascade, renames reported"
```

---

### Task 3: TUI — `>` sends the selected line

**Files:**
- Modify: `src/towerkit/tui/widgets/modals.py` (new `SendLineModal` at end of file)
- Modify: `src/towerkit/tui/screens/editor.py` (binding, help text, `action_send_line` after `action_export_soi`)
- Test: `tests/test_tui.py` (new class at end)

**Interfaces:**
- Consumes: `transfer_line`/`TransferResult` (Tasks 1–2, exact signature above); `load_program`, `dumps_program` from `towerkit.model`; `validate_program` from `towerkit.validate`; existing `ConfirmModal(question, yes_label=...)` → `bool`; `self.selected: tuple[str, Any]`, `self.session` (`.path`, `.program`, `.mutate`), `self.notify`, `self.refresh_all()`, `self._select_tree_node(...)` in the editor.
- Produces: `SendLineModal(ModalScreen[tuple[Path, bool] | None])` — dismisses with `(target_path, move)` or `None`; `EditorScreen.action_send_line`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py`:

```python
class TestSendLine:
    def _two_programs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "programs"
        target.mkdir()
        shutil.copy(SAMPLE, target / "src.json")
        shutil.copy(SAMPLE, target / "dst.json")
        return target / "src.json", target / "dst.json"

    @pytest.mark.asyncio
    async def test_copy_line_writes_target_additively(
        self, tmp_path, monkeypatch
    ) -> None:
        src, dst = self._two_programs(tmp_path, monkeypatch)
        from towerkit.model import load_program

        before = load_program(dst)
        app = TowerkitApp(path=src)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("line", "gl")
            await pilot.press("greater_than_sign")
            await pilot.pause()
            from towerkit.tui.widgets.modals import SendLineModal

            modal = app.screen
            assert isinstance(modal, SendLineModal)
            # pick dst.json in the option list, leave "move" unchecked
            options = modal.query_one("#send-targets")
            idx = next(
                i for i, p in enumerate(modal.targets) if p.name == "dst.json"
            )
            options.highlighted = idx
            modal.query_one("#send-confirm").press()
            await pilot.pause()
            # summary confirm modal
            await pilot.press("y")
            await pilot.pause()
            after = load_program(dst)
            # additive: every original line/layer still present, gl grafted
            assert {ln.id for ln in before.lines} <= {ln.id for ln in after.lines}
            assert len(after.lines) == len(before.lines) + 1
            # copy mode: source session untouched
            assert not editor.session.dirty

    @pytest.mark.asyncio
    async def test_move_is_undoable_and_source_file_untouched(
        self, tmp_path, monkeypatch
    ) -> None:
        src, dst = self._two_programs(tmp_path, monkeypatch)
        src_bytes = src.read_bytes()
        app = TowerkitApp(path=src)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            n_lines = len(editor.session.program.lines)
            editor.selected = ("line", "gl")
            await pilot.press("greater_than_sign")
            await pilot.pause()
            from towerkit.tui.widgets.modals import SendLineModal

            modal = app.screen
            idx = next(
                i for i, p in enumerate(modal.targets) if p.name == "dst.json"
            )
            modal.query_one("#send-targets").highlighted = idx
            modal.query_one("#send-move").value = True
            modal.query_one("#send-confirm").press()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert len(editor.session.program.lines) == n_lines - 1
            assert editor.session.undo()
            assert len(editor.session.program.lines) == n_lines
        assert src.read_bytes() == src_bytes  # source file never written

    @pytest.mark.asyncio
    async def test_malformed_target_refused_bytes_unchanged(
        self, tmp_path, monkeypatch
    ) -> None:
        src, dst = self._two_programs(tmp_path, monkeypatch)
        dst.write_text("{not json", encoding="utf-8")
        bad = dst.read_bytes()
        app = TowerkitApp(path=src)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("line", "gl")
            await pilot.press("greater_than_sign")
            await pilot.pause()
            from towerkit.tui.widgets.modals import SendLineModal

            modal = app.screen
            idx = next(
                i for i, p in enumerate(modal.targets) if p.name == "dst.json"
            )
            modal.query_one("#send-targets").highlighted = idx
            modal.query_one("#send-confirm").press()
            await pilot.pause()
            assert any(
                "can't read" in n.message for n in app._notifications
            )
        assert dst.read_bytes() == bad

    @pytest.mark.asyncio
    async def test_requires_line_selection(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("program", None)
            await pilot.press("greater_than_sign")
            await pilot.pause()
            assert isinstance(app.screen, EditorScreen)  # no modal
            assert any("select a line" in n.message for n in app._notifications)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py::TestSendLine -q`
Expected: 4 FAIL (ImportError on `SendLineModal` / no modal opens — `>` unbound).

- [ ] **Step 3: Implement `SendLineModal`**

Append to `src/towerkit/tui/widgets/modals.py` (add `Checkbox`, `OptionList` to the existing `textual.widgets` import, and `Path` from `pathlib` if absent):

```python
class SendLineModal(ModalScreen[tuple[Path, bool] | None]):
    """Pick a target program file and copy/move mode for a line transfer."""

    CSS = """
    SendLineModal { align: center middle; }
    #send-box { width: 70; height: auto; max-height: 24; padding: 1 2;
                background: $surface; border: thick $primary; }
    #send-targets { height: auto; max-height: 12; }
    """

    def __init__(self, line_name: str, targets: list[Path]) -> None:
        super().__init__()
        self.line_name = line_name
        self.targets = targets

    def compose(self) -> ComposeResult:
        with Vertical(id="send-box"):
            yield Label(f"Send {self.line_name} to…")
            yield OptionList(
                *[str(p) for p in self.targets], id="send-targets"
            )
            yield Checkbox(
                "Move (remove from this program)", value=False, id="send-move"
            )
            with Horizontal():
                yield Button("Send", variant="primary", id="send-confirm")
                yield Button("Cancel", id="send-cancel")

    @on(Button.Pressed, "#send-confirm")
    def _confirm(self) -> None:
        options = self.query_one("#send-targets", OptionList)
        idx = options.highlighted
        if idx is None:
            return
        move = self.query_one("#send-move", Checkbox).value
        self.dismiss((self.targets[idx], move))

    @on(Button.Pressed, "#send-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)
```

Match the file's existing import style (it already imports `Button`, `Label`, `ModalScreen`, `Vertical`, `Horizontal`, `on` for the other modals — extend, don't duplicate).

- [ ] **Step 4: Implement the editor action**

In `src/towerkit/tui/screens/editor.py`:

(a) `BINDINGS`, after the `("x", "export_soi", "SOI")` entry:

```python
        ("greater_than_sign", "send_line", "> send"),
```

(b) Help text, in the `Editing`-adjacent structure area (after the `=` restack lines), add:

```
  >          send the selected line (and its exclusive
             layers/retentions/sublimits) to another program
```

(c) After `action_export_soi`:

```python
    def action_send_line(self) -> None:
        kind, key = self.selected
        if kind != "line":
            self.notify("select a line to send", severity="warning")
            return
        line = self._line(key)
        if line is None:
            return
        targets = sorted(Path("programs").glob("*.json")) + sorted(
            (Path("programs") / "private").glob("*.json")
        )
        if self.session.path is not None:
            targets = [
                p for p in targets if p.resolve() != self.session.path.resolve()
            ]
        if not targets:
            self.notify("no other program files under programs/", severity="warning")
            return

        def on_target(choice: tuple[Path, bool] | None) -> None:
            if choice is None:
                return
            target_path, move = choice
            from ...model import dumps_program, load_program
            from ...transfer import transfer_line
            from ...validate import validate_program

            try:
                dst = load_program(target_path)
            except Exception as exc:
                self.notify(f"can't read {target_path}: {exc}", severity="error")
                return
            if validate_program(dst).errors:
                self.notify(
                    f"{target_path.name} has validation errors — fix it first",
                    severity="error",
                )
                return
            result = transfer_line(
                self.session.program, dst, line.id, move=move
            )
            lines = [f"{'Move' if move else 'Copy'} to {target_path.name}:"]
            lines += [f"  + {t}" for t in result.summary.travels]
            lines += [f"  stays: {s}" for s in result.summary.stays]
            lines += [
                f"  renamed in target: {old} → {new}"
                for old, new in result.summary.renames
            ]

            def on_confirm(go: bool | None) -> None:
                if not go:
                    return
                target_path.write_text(
                    dumps_program(result.dst_after), encoding="utf-8"
                )
                if move:
                    def apply_src(p: Program) -> None:
                        p.lines = result.src_after.lines
                        p.layers = result.src_after.layers
                        p.retentions = result.src_after.retentions
                        p.sublimits = result.src_after.sublimits

                    self.session.mutate(apply_src)
                    self.selected = ("program", None)
                    self.refresh_all()
                verb = "moved" if move else "copied"
                self.notify(f"{verb} {line.name} to {target_path.name}")

            self.app.push_screen(
                ConfirmModal("\n".join(lines), yes_label="Send"), on_confirm
            )

        self.app.push_screen(SendLineModal(line.name, targets), on_target)
```

Add `SendLineModal` to the existing `from ..widgets.modals import ...` line, and confirm `Program` is already imported in `editor.py` (it is, via the model imports at the top — verify before relying on it).

- [ ] **Step 5: Run the new tests, then the full TUI file**

Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py::TestSendLine -q`
Expected: 4 passed. (If the ConfirmModal `y` keypress doesn't confirm, check how `TestDirtyExitOffersSave` drives its modals and use the same mechanism — button press over keypress.)
Run: `MPL_IGNORE_SYSTEM_FONTS=1 uv run pytest tests/test_tui.py tests/test_transfer.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/tui/widgets/modals.py src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "tui: > sends the selected line to another program (copy/move)"
```
