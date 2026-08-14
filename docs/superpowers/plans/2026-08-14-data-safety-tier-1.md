# Data Safety Tier 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every towerkit write survive a failed disk, make every unprompted exit ask first, and stop the import path from computing validation warnings and then throwing them away.

**Architecture:** One new module (`towerkit.atomicio`) owns "how a file reaches disk safely" and becomes the single primitive under all three write paths — `model.dump_program` (CLI + TUI), `EditSession.save` (TUI), and `mcpserver._atomic_write`. The TUI's save and reload handlers stop letting `OSError` escape into Textual. `TowerkitApp.action_quit` routes `ctrl+q` through the same unsaved-changes prompt `escape` already uses, and `action_back` drains the focused input *before* consulting the dirty flag. `DraftProgram.to_program()` merges validation diagnostics onto the draft on both the success and failure paths, and the two import callers surface them and refuse to write on errors.

**Tech Stack:** Python 3.12+, pydantic v2, Textual (`run_test` pilot for TUI tests), pytest. No new dependencies.

**Spec:** The expert-panel audit of 2026-08-14 — https://claude.ai/code/artifact/3e8d5215-5218-4465-8aa2-7662a9a43f04 — findings SB-01, SB-02, SB-05, SB-06, SB-07, SB-08, SB-09, SB-10, and the M1/M2 notes on `fsync` and untracked write artifacts. The findings this plan closes are restated verbatim below so no executor needs the artifact.

## Global Constraints

- **Findings closed by this plan** (each task names the ones it closes):
  - **SB-01** — `model.py:361` `Path(path).write_text()` truncates before writing; a failure mid-write leaves the file at 0 bytes with no backup. Reproduced on a real ENOSPC filesystem: `size before: 3007237 -> after: 0`, file no longer parses.
  - **SB-02** — `editor.py:1657` `_save_guarded` catches only `StaleFileError`, so a `PermissionError` or `OSError` on save kills the TUI, destroying the in-memory session too.
  - **SB-06** — `ingest.py:80-83` `to_program()` runs `validate_program()` and keeps the diagnostics *only when it raises*. On success they are discarded, so `towerctl import` printed nothing while `towerctl validate` on the result printed `⚠ S1: 1% placed — $9,900,000 unplaced`.
  - **SB-07** — import error diagnostics do not block the write and the exit code stays 0, so a script gating on `$?` reads a stripped-dates import as success.
  - **SB-08** — `editor.py:1890` checks `session.dirty` *before* `_drain_focused_input()` at 1893. Uncommitted typing never reached the model, so `esc` discarded it silently with no prompt.
  - **SB-09** — `app.py` defines no `BINDINGS` and no quit guard, so Textual's stock priority `ctrl+q` runs past `ExitChoiceModal`. `ctrl+c` raises a toast reading "Press ctrl+q to quit the app".
  - **SB-10** — `session.py:149` `reload()` calls `load_program` unguarded; answering **Reload** when the file has been deleted raises `FileNotFoundError` and kills the app, while the scarier-sounding **Overwrite** recovers cleanly.
- **Never regress the canonical round trip.** `programs/*.json` must stay byte-identical through save; `tests/test_canonical.py` is the gate and must keep passing.
- **Never modify `programs/*.json` while developing.** Every test writes to `tmp_path`. `programs/private/` holds real client data.
- **`scale.py` and `layout.py` must not import plotting libraries** (existing test enforces this); `atomicio.py` must import nothing beyond the stdlib.
- **Backup/rollback story for this plan:** these changes only ever *add* durability — no schema migration, no rewrite of on-disk files, no format change. The one new on-disk artifact is a `.<name>.json.bak` sidecar written beside each program on save. Task 1 adds it to `.gitignore` in the same commit that creates it.
- **When gating a commit on tests in a shell chain, never pipe pytest into `tail`/`grep` before the `&&`** — the pipe eats the exit code. Redirect to a file, gate on the command, tail the file.
- Run the suite with `uv run --group dev pytest -q`. Baseline before starting: **501 passed, 1 failed** — `tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration` fails environmentally (no `bookctl` on this machine) and is not yours to fix.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/towerkit/atomicio.py` **(new)** | The only place that knows how a file reaches disk without risking the old contents. Stdlib only, no towerkit imports, so it is trivially unit-testable and importable from anywhere. |
| `src/towerkit/model.py` | `dump_program` delegates its write to `atomicio`. No other change. |
| `src/towerkit/mcpserver.py` | `_atomic_write` / `_atomic_write_bytes` become thin delegates, deleting the second copy of this logic. |
| `src/towerkit/tui/session.py` | `EditSession.save` writes through `atomicio`. |
| `src/towerkit/tui/screens/editor.py` | `_save_guarded`, `_do_save` and `action_back` stop letting `OSError` escape and stop consulting `dirty` before draining. |
| `src/towerkit/tui/app.py` | `action_quit` override routes `ctrl+q` through the editor's unsaved-changes prompt. |
| `src/towerkit/ingest.py` | `DraftProgram` merges validation diagnostics onto itself; `to_program()` stops discarding them. |
| `src/towerkit/cli.py` | `_cmd_import` surfaces the merged diagnostics and returns 1 on any error. |
| `src/towerkit/tui/screens/browser.py` | `_finish_import` does the same, via notifications. |
| `tests/test_atomicio.py` **(new)** | Unit tests for the write primitive, including a simulated ENOSPC. |
| `tests/test_edit.py` | One added test: `dump_program` never truncates on a failed write. |
| `tests/test_tui.py` | Added tests for the save-failure, reload, `ctrl+q` and `esc` paths. |
| `tests/test_ingest.py` | Added tests for diagnostics survival. |
| `tests/test_cli.py` | Added test for the import exit code. |

---

### Task 1: The atomic write primitive

Closes the mechanism behind **SB-01**. Produces the primitive every later task wires in.

**Files:**
- Create: `src/towerkit/atomicio.py`
- Create: `tests/test_atomicio.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8", backup: bool = True) -> None`
  - `atomic_write_bytes(path: Path | str, data: bytes, *, backup: bool = True) -> None`
  - `backup_path(path: Path) -> Path`
  - All three raise `OSError` on failure and guarantee the pre-existing file is byte-unchanged when they do.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atomicio.py`:

```python
"""The write primitive: a failed write must never cost the old contents.

These tests simulate the failure rather than filling a real disk — `fsync`
is the last call before the atomic replace, so raising there exercises the
exact window where `write_text` used to leave a truncated file.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from towerkit.atomicio import atomic_write_bytes, atomic_write_text, backup_path


def _enospc(_fd: int) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


class TestAtomicWrite:
    def test_a_failed_write_leaves_the_original_byte_identical(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("original contents", encoding="utf-8")
        monkeypatch.setattr(os, "fsync", _enospc)

        with pytest.raises(OSError):
            atomic_write_text(target, "replacement")

        assert target.read_text(encoding="utf-8") == "original contents"

    def test_a_failed_write_leaves_no_temp_file_behind(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("original", encoding="utf-8")
        monkeypatch.setattr(os, "fsync", _enospc)

        with pytest.raises(OSError):
            atomic_write_text(target, "replacement")

        assert list(tmp_path.glob(".*.tmp")) == []

    def test_the_previous_contents_are_kept_aside(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        atomic_write_text(target, "v2")

        assert target.read_text(encoding="utf-8") == "v2"
        assert backup_path(target).read_text(encoding="utf-8") == "v1"

    def test_the_backup_sidecar_is_hidden_and_not_a_program_file(
        self, tmp_path: Path
    ) -> None:
        # the program browser globs programs/*.json — the sidecar must not
        # show up there as a phantom program
        target = tmp_path / "atomic-2026.json"
        target.write_text("v1", encoding="utf-8")
        atomic_write_text(target, "v2")

        assert backup_path(target).name.startswith(".")
        assert list(tmp_path.glob("*.json")) == [target]

    def test_a_first_write_makes_no_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "new.json"

        atomic_write_text(target, "v1")

        assert target.read_text(encoding="utf-8") == "v1"
        assert not backup_path(target).exists()

    def test_backup_can_be_turned_off(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        atomic_write_text(target, "v2", backup=False)

        assert not backup_path(target).exists()

    def test_a_read_only_target_is_still_refused(self, tmp_path: Path) -> None:
        # replace() only needs directory permission, so without an explicit
        # check a read-only file would silently become writable again
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        target.chmod(0o444)
        try:
            with pytest.raises(PermissionError):
                atomic_write_text(target, "v2")
            assert target.read_text(encoding="utf-8") == "v1"
        finally:
            target.chmod(0o644)

    def test_bytes_and_text_agree(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.json", tmp_path / "b.json"

        atomic_write_text(a, "héllo")
        atomic_write_bytes(b, "héllo".encode())

        assert a.read_bytes() == b.read_bytes()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_atomicio.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'towerkit.atomicio'`

- [ ] **Step 3: Write the module**

Create `src/towerkit/atomicio.py`:

```python
"""Durable file writes.

`open(mode="w")` truncates before the first byte lands, so any failure
between truncate and flush destroys the old file. Everything towerkit
writes is a user's irreplaceable work — program JSON above all — so every
write goes through here: a same-directory temp file, fsynced, then an
atomic `os.replace`. Every failure mode then leaves either the old
contents or the new ones, never nothing.

This is the one place that knows how a file reaches disk. The TUI, the
CLI and the MCP server all route through it, so "what happens when the
write fails" has one answer instead of three.

Stdlib only, deliberately: no towerkit import may ever appear here, so
this module can be used from anywhere without an import cycle.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "backup_path"]


def backup_path(path: Path | str) -> Path:
    """Sidecar holding the contents this file had before the last write.

    Hidden, and not named `*.json`, so it never shows up as a phantom
    program in the browser's `programs/*.json` glob."""
    path = Path(path)
    return path.with_name(f".{path.name}.bak")


def atomic_write_text(
    path: Path | str, text: str, *, encoding: str = "utf-8", backup: bool = True
) -> None:
    """Write `text` durably. See `atomic_write_bytes` for the guarantees."""
    atomic_write_bytes(path, text.encode(encoding), backup=backup)


def atomic_write_bytes(path: Path | str, data: bytes, *, backup: bool = True) -> None:
    """Write `data` durably: temp in the same directory, fsync, replace.

    Raises `OSError` (including `PermissionError`) on failure, and the
    pre-existing file is byte-unchanged when it does.

    `backup` keeps the previous contents in a `backup_path` sidecar. It is
    best-effort and stays silent when it fails: the atomic replace already
    guarantees crash-safety, so the sidecar exists only to undo a save the
    user *meant* to make — an accidental Overwrite, a bad edit committed —
    and is not worth failing an otherwise good save over.
    """
    path = Path(path)
    if path.exists() and not os.access(path, os.W_OK):
        # replace() needs only directory permission, so without this a
        # read-only file would silently become writable again — and a
        # read-only program file usually means "do not edit this".
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.exists():
            _keep_aside(path)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _keep_aside(path: Path) -> None:
    """Point the sidecar at the current contents.

    A hard link first: it copies no data, so it cannot itself fail with
    ENOSPC on the full disk this whole module exists to survive. Copying
    is the fallback for filesystems without hard links (SMB, exFAT)."""
    bak = backup_path(path)
    try:
        bak.unlink(missing_ok=True)
        os.link(path, bak)
    except OSError:
        try:
            shutil.copyfile(path, bak)
        except OSError:
            pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_atomicio.py -q`
Expected: `8 passed`

- [ ] **Step 5: Ignore the new write artifacts**

Add these four lines to the end of `.gitignore`:

```gitignore
.*.bak
.*.tmp
programs/.mcp-snapshots/
```

(The `*.xlsx` exposure from audit finding SB-15 is deliberately **not** handled here — it belongs with the SOI export work in tier 2. These three cover only the artifacts this plan's write path creates, plus the MCP snapshot directory that was already unignored.)

- [ ] **Step 6: Confirm nothing else broke, then commit**

Run: `uv run --group dev pytest -q > /tmp/tk-t1.txt 2>&1 && tail -5 /tmp/tk-t1.txt`
Expected: the passed count rises by the 8 new tests and the failed count stays at exactly 1 — the same environmental `test_connector` failure from the baseline. Any second failure is yours.

```bash
git add src/towerkit/atomicio.py tests/test_atomicio.py .gitignore
git commit -m "feat: atomic writes with a backup sidecar

Path.write_text truncates before writing, so a full disk or a dropped
network mount left program files at zero bytes with nothing to recover
from. One primitive, stdlib only, used by every write path next."
```

---

### Task 2: Route every write through it

Closes **SB-01** end to end, and collapses the second copy of this logic in `mcpserver.py` (audit convergence 06: three write paths diverging on the same primitives).

**Files:**
- Modify: `src/towerkit/model.py:360-362`
- Modify: `src/towerkit/mcpserver.py:194-210`
- Modify: `src/towerkit/tui/session.py:138-139`
- Test: `tests/test_edit.py`

**Interfaces:**
- Consumes: `atomic_write_text`, `atomic_write_bytes` from Task 1.
- Produces: no signature changes. `dump_program(program, path)` and `EditSession.save(path=None, force=False)` keep their exact signatures and now raise `OSError` instead of truncating.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_edit.py` (the file already has `SAMPLE` and imports `load_program`; add `import errno`, `import os`, `import shutil` to its import block):

```python
class TestDurableWrites:
    def test_dump_program_never_truncates_on_a_failed_write(
        self, tmp_path, monkeypatch
    ) -> None:
        target = tmp_path / "atomic-2026.json"
        shutil.copy(SAMPLE, target)
        before = target.read_bytes()

        def _enospc(_fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", _enospc)

        from towerkit.model import dump_program

        with pytest.raises(OSError):
            dump_program(_sample(), target)

        assert target.read_bytes() == before
        assert load_program(target).insured  # still a loadable program

    def test_a_save_keeps_the_previous_contents_aside(self, tmp_path) -> None:
        from towerkit.atomicio import backup_path
        from towerkit.model import dump_program

        target = tmp_path / "atomic-2026.json"
        shutil.copy(SAMPLE, target)
        original = target.read_bytes()

        program = _sample()
        program.insured = "Changed Co"
        dump_program(program, target)

        assert load_program(target).insured == "Changed Co"
        assert backup_path(target).read_bytes() == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_edit.py::TestDurableWrites -q`
Expected: FAIL — the first test fails on `assert target.read_bytes() == before` (the file is now empty); the second fails on `ModuleNotFoundError` or a missing sidecar.

- [ ] **Step 3: Point `dump_program` at the primitive**

In `src/towerkit/model.py`, add to the import block near the top:

```python
from .atomicio import atomic_write_text
```

Replace `dump_program` (currently at line 360):

```python
def dump_program(program: Program, path: Path | str) -> None:
    """Canonical JSON, written durably — a failed write costs the new
    contents, never the old ones. See `towerkit.atomicio`."""
    atomic_write_text(path, dumps_program(program))
```

- [ ] **Step 4: Collapse the MCP server's private copy**

In `src/towerkit/mcpserver.py`, add to its import block:

```python
from .atomicio import atomic_write_bytes, atomic_write_text
```

Replace both helpers (currently at lines 194-210) with delegates, keeping the names so the ~6 call sites in that file are untouched:

```python
def _atomic_write(path: Path, text: str) -> None:
    """Delegates to `towerkit.atomicio` — one definition of a safe write,
    shared with the TUI and the CLI."""
    atomic_write_text(path, text)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Bytes-oriented sibling, used by `restore()`. The pre-image is
    already-canonical UTF-8 JSON read straight off disk; a text round-trip
    risks re-encoding it, so this writes the exact bytes."""
    atomic_write_bytes(path, data)
```

If `os` is now unused in `mcpserver.py`, leave the import alone — it is used elsewhere in that file. Run `uv run --group dev ruff check src` at step 6 to confirm.

- [ ] **Step 5: Point the edit session at it**

In `src/towerkit/tui/session.py`, add to the import block:

```python
from ..atomicio import atomic_write_text
```

In `EditSession.save`, replace line 139:

```python
        target.write_text(text, encoding="utf-8")
```

with:

```python
        atomic_write_text(target, text)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_edit.py::TestDurableWrites tests/test_canonical.py tests/test_mcpserver.py -q`
Expected: PASS. `test_canonical.py` passing is the gate that the zero-diff round trip survived the change.

Run: `uv run --group dev ruff check src`
Expected: no findings.

- [ ] **Step 7: Full suite, then commit**

Run: `uv run --group dev pytest -q > /tmp/tk-t2.txt 2>&1 && tail -5 /tmp/tk-t2.txt`
Expected: the failed count is still exactly 1 (the environmental `test_connector` one). This is the riskiest task in the plan — it changes how every program file in the project is written — so a clean `test_canonical.py` and `test_mcpserver.py` here is the gate for continuing.

```bash
git add src/towerkit/model.py src/towerkit/mcpserver.py src/towerkit/tui/session.py tests/test_edit.py
git commit -m "fix: every program write is atomic, not just the MCP server's

The safest write path in the codebase was the one no human used. Now
dump_program, EditSession.save and the MCP server share one primitive."
```

---

### Task 3: A failed save must not kill the editor

Closes **SB-02** and **SB-10**.

**Files:**
- Modify: `src/towerkit/tui/screens/editor.py:1632-1682` (`_do_save`, `_save_guarded`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `EditSession.save` / `.reload` from Task 2 (unchanged signatures; `save` now raises `OSError` rather than truncating).
- Produces: `EditorScreen._notify_save_failure(exc: OSError) -> None` — one message shape for every save failure, used by all three call sites in this task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py` (it already imports `Path`, `shutil`, `pytest`, `TowerkitApp`, `EditorScreen` and defines the `sample_copy` fixture):

```python
class TestSaveFailures:
    @pytest.mark.asyncio
    async def test_an_unwritable_file_notifies_instead_of_killing_the_app(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        sample_copy.chmod(0o444)
        try:
            app = TowerkitApp(path=sample_copy)
            async with app.run_test(size=(140, 45)) as pilot:
                screen = app.screen
                assert isinstance(screen, EditorScreen)
                screen.session.mutate(lambda p: setattr(p, "insured", "Mine"))
                await pilot.press("ctrl+s")
                await pilot.pause()

                assert app.is_running
                assert screen.session.dirty  # the edit is still here
        finally:
            sample_copy.chmod(0o644)

    @pytest.mark.asyncio
    async def test_a_deleted_file_is_written_back_without_a_question(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = app.screen
            assert isinstance(screen, EditorScreen)
            screen.session.mutate(lambda p: setattr(p, "insured", "Mine"))
            sample_copy.unlink()  # git checkout, a rename, a sync client

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert app.is_running
            assert sample_copy.exists()
            assert load_program(sample_copy).insured == "Mine"

    @pytest.mark.asyncio
    async def test_reload_of_a_vanished_file_does_not_kill_the_app(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = app.screen
            assert isinstance(screen, EditorScreen)
            screen.session.mutate(lambda p: setattr(p, "insured", "Mine"))

            # the StaleFileModal's own reload branch, reached directly: the
            # file is gone by the time the user answers the question
            sample_copy.unlink()
            screen._reload_guarded()
            await pilot.pause()

            assert app.is_running
            assert screen.session.program.insured == "Mine"
```

`load_program` is already imported at the top of `tests/test_tui.py`; no new import is needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_tui.py::TestSaveFailures -q`
Expected: FAIL — the first two raise `PermissionError` / `StaleFileError` out of the app; the third fails with `AttributeError: 'EditorScreen' object has no attribute '_reload_guarded'`.

- [ ] **Step 3: Add the shared failure message and the reload guard**

In `src/towerkit/tui/screens/editor.py`, add these two methods immediately above `_save_guarded` (which currently starts at line 1656):

```python
    def _notify_save_failure(self, exc: OSError) -> None:
        """A save that cannot reach disk is a message, never a crash — the
        session still holds every edit, and killing the app would throw
        them away on top of the failure."""
        reason = exc.strerror or str(exc)
        self.notify(
            f"could not save {self.session.path}: {reason} — your edits are "
            f"still here; try ctrl+s again, or use a different location",
            severity="error",
            timeout=10,
        )

    def _reload_guarded(self) -> None:
        """Take what is on disk. The file may have been deleted rather than
        merely changed, and a bad or missing file must not cost the
        session that is still holding the user's work."""
        try:
            self.session.reload()
        except (OSError, ValueError) as exc:
            self.notify(
                f"could not reload {self.session.path}: {exc} — your edits "
                f"are still here",
                severity="error",
                timeout=10,
            )
            return
        self.refresh_all()
        self.notify("reloaded from disk — your edits were discarded")
```

- [ ] **Step 4: Rewrite `_save_guarded`**

Replace the whole of `_save_guarded` (lines 1656-1682) with:

```python
    def _save_guarded(self, then: Callable[[], None] | None = None) -> None:
        """Every save that overwrites the loaded file goes through here.
        StaleFileError is a question for the user, not an error to swallow;
        an OSError is a message, not a crash."""

        def done() -> None:
            self.notify(f"saved {self.session.path}")
            self._refresh_title()
            if then is not None:
                then()

        try:
            self.session.save()
        except StaleFileError:
            if self.session.path is not None and not self.session.path.exists():
                # deleted or renamed underneath us: there is nothing to
                # clobber, so write it back rather than asking a question
                # whose "reload" answer has nothing left to read
                try:
                    self.session.save(force=True)
                except OSError as exc:
                    self._notify_save_failure(exc)
                    return
                self.notify(f"{self.session.path} had been removed — wrote it back")
                self._refresh_title()
                if then is not None:
                    then()
                return

            def on_choice(choice: str | None) -> None:
                if choice == "overwrite":
                    try:
                        self.session.save(force=True)
                    except OSError as exc:
                        self._notify_save_failure(exc)
                        return
                    done()
                elif choice == "reload":
                    self._reload_guarded()
                # anything else: keep editing

            self.app.push_screen(StaleFileModal(), on_choice)
            return
        except OSError as exc:
            self._notify_save_failure(exc)
            return
        done()
```

- [ ] **Step 5: Guard the save-as path too**

In `_do_save` (line 1632), replace the body of `on_name` from `target.parent.mkdir(...)` onward with:

```python
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.session.save(target)
                except OSError as exc:
                    self._notify_save_failure(exc)
                    return
                self.notify(f"saved {target}")
                self._refresh_title()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_tui.py -q`
Expected: PASS, including the pre-existing stale-file tests around line 91.

- [ ] **Step 7: Commit**

```bash
git add src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "fix: a save that cannot reach disk is a message, not a crash

_save_guarded caught only StaleFileError, so a read-only mount or a full
disk killed the app and took the unsaved session with it. A deleted file
is now written back instead of asking a question whose Reload branch had
nothing to read."
```

---

### Task 4: No exit discards work without asking

Closes **SB-08** and **SB-09**.

**Files:**
- Modify: `src/towerkit/tui/app.py`
- Modify: `src/towerkit/tui/screens/editor.py:1885-1893` (`action_back`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `EditorScreen.action_back()` (async) and `EditorScreen.session` from the existing code.
- Produces: `TowerkitApp.action_quit()` (async, overrides `App.action_quit`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py`:

```python
class TestExitGuards:
    @pytest.mark.asyncio
    async def test_esc_does_not_discard_text_still_sitting_in_a_field(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = app.screen
            assert isinstance(screen, EditorScreen)
            field = screen.query_one("#f-insured", Input)
            field.focus()
            await pilot.pause()
            field.value = "Acme Holdings"

            # dirty is still False here: the text has not reached the model
            assert not screen.session.dirty

            await pilot.press("escape")
            await pilot.pause()

            assert app.is_running, "esc left the editor with typed text unsaved"
            assert screen.session.program.insured == "Acme Holdings"

    @pytest.mark.asyncio
    async def test_ctrl_q_asks_before_discarding_unsaved_edits(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = app.screen
            assert isinstance(screen, EditorScreen)
            screen.session.mutate(lambda p: setattr(p, "insured", "Mine"))

            await pilot.press("ctrl+q")
            await pilot.pause()

            assert app.is_running, "ctrl+q quit past the unsaved-changes prompt"
            assert "ExitChoiceModal" in [type(s).__name__ for s in app.screen_stack]

    @pytest.mark.asyncio
    async def test_ctrl_q_still_quits_when_there_is_nothing_to_lose(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            assert isinstance(app.screen, EditorScreen)

            await pilot.press("ctrl+q")
            await pilot.pause()

            assert not app.is_running
```

The insured field is mounted as `id="f-insured"` by `_form_program` (`editor.py:534`) — note it is `f-insured`, not `f-program-insured`; `f-program` is the *program name* field on the same form.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_tui.py::TestExitGuards -q`
Expected: FAIL — the esc test finds `insured` unchanged and the app stopped; the ctrl+q test finds `app.is_running` False.

- [ ] **Step 3: Drain before consulting the dirty flag**

In `src/towerkit/tui/screens/editor.py`, `action_back` currently reads:

```python
        if not self.session.dirty:
            self.dismiss_editor()
            return
        self._drain_focused_input()
```

Replace those four lines with:

```python
        # Drain FIRST. Text sitting in a focused Input has not reached the
        # model yet, so `dirty` cannot see it — checking dirty before
        # draining is exactly how typed-but-uncommitted edits used to
        # vanish on esc, with no prompt and no undo.
        self._drain_focused_input()
        if not self.session.dirty:
            self.dismiss_editor()
            return
```

- [ ] **Step 4: Route ctrl+q through the same prompt**

In `src/towerkit/tui/app.py`, add the `action_quit` override to `TowerkitApp` (after `on_mount`):

```python
    async def action_quit(self) -> None:
        """ctrl+q must not be a shortcut past the unsaved-changes prompt.

        Textual binds it straight to `App.exit`, so a whole session of tower
        edits died on one keypress — and the built-in ctrl+c toast actively
        advertises it ("Press ctrl+q to quit the app"). Routing it into the
        editor's own esc handler makes both keys mean the same thing, and
        makes that toast honest.
        """
        screen = self.screen
        if isinstance(screen, EditorScreen) and screen.session.dirty:
            await screen.action_back()
            return
        self.exit()
```

`EditorScreen` is already imported at the top of `app.py`; no new import is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_tui.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/tui/app.py src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "fix: no exit path discards unsaved work without asking

esc checked `dirty` before draining the focused input, so typed text that
had not reached the model vanished silently. ctrl+q bypassed the prompt
entirely, while the ctrl+c toast told users to press it."
```

---

### Task 5: Import stops discarding the diagnostics it computes

Closes **SB-06**. This is the safety net under the five silent-drop bugs in SB-07 and under the percent-format misread in SB-05 — every one of those surfaces as an unplaced-share or missing-data warning that the import currently computes and throws away.

**Files:**
- Modify: `src/towerkit/ingest.py:58-83` (`DraftProgram.to_program`)
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `validate_program`, `Diagnostics`, `ProgramInvalidError` (already imported in `ingest.py`).
- Produces:
  - `DraftProgram.to_program() -> Program` — unchanged signature, but now merges every diagnostic it computes onto `self.diagnostics` on **both** the success and the raise path. Idempotent: calling it twice does not duplicate items.
  - Callers must read `draft.diagnostics` **after** calling `to_program()`, not before.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest.py`:

```python
class TestDiagnosticsSurvive:
    @staticmethod
    def _draft_with_an_unplaced_layer():
        from datetime import date

        from towerkit.ingest import DraftProgram
        from towerkit.model import Layer, Line, Participant, Period

        draft = DraftProgram(insured="Acme Ltd", program="Casualty")
        draft.period = Period(start=date(2026, 1, 1), end=date(2027, 1, 1))
        draft.lines = [Line(id="gl", name="General Liability", abbr="GL")]
        draft.layers = [
            Layer(
                id="primary",
                name="Primary",
                applies_to=["gl"],
                attach=0,
                limit=10_000_000,
                participants=[Participant(carrier="Chubb", share_bps=8_000)],
            )
        ]
        return draft

    def test_a_successful_build_keeps_its_validation_warnings(self) -> None:
        draft = self._draft_with_an_unplaced_layer()

        program = draft.to_program()

        assert program.insured == "Acme Ltd"
        assert any(
            "unplaced" in str(d) for d in draft.diagnostics.warnings
        ), f"warnings were discarded: {[str(d) for d in draft.diagnostics.items]}"

    def test_a_failed_build_keeps_its_errors_on_the_draft(self) -> None:
        from towerkit.validate import ProgramInvalidError

        draft = self._draft_with_an_unplaced_layer()
        draft.insured = ""  # trips the gate

        with pytest.raises(ProgramInvalidError):
            draft.to_program()

        assert any("insured" in str(d) for d in draft.diagnostics.errors)

    def test_building_twice_does_not_duplicate_diagnostics(self) -> None:
        draft = self._draft_with_an_unplaced_layer()

        draft.to_program()
        first = len(draft.diagnostics.items)
        draft.to_program()

        assert len(draft.diagnostics.items) == first
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_ingest.py::TestDiagnosticsSurvive -q`
Expected: FAIL — `test_a_successful_build_keeps_its_validation_warnings` reports `warnings were discarded: []`.

- [ ] **Step 3: Merge instead of discard**

In `src/towerkit/ingest.py`, replace `DraftProgram.to_program` (currently lines 58-83) with:

```python
    def to_program(self) -> Program:
        """Build the Program, keeping every diagnostic on the draft.

        Validation warnings used to be computed here and dropped on the
        floor whenever the build succeeded, so an import that produced a
        1%-placed tower printed nothing at all while `towerctl validate`
        on the same file printed the warning. Callers now read
        `draft.diagnostics` AFTER this call, not before."""
        gate = Diagnostics()
        if not self.insured.strip():
            gate.error("draft.insured", "insured name is required")
        if not self.program.strip():
            gate.error("draft.program", "program name is required")
        if self.period is None:
            gate.error("draft.period", "policy period (inception and expiry) is required")
        if not gate.ok:
            self._carry(gate)
            raise ProgramInvalidError(gate, source="draft")
        assert self.period is not None  # narrowed by the gate above
        program = Program(
            insured=self.insured.strip(),
            program=self.program.strip(),
            placement=self.placement,
            period=self.period,
            currency=self.currency,
            lines=list(self.lines),
            layers=list(self.layers),
            retentions=list(self.retentions),
        )
        diags = validate_program(program)
        self._carry(diags)
        if not diags.ok:
            raise ProgramInvalidError(diags, source="draft")
        return program

    def _carry(self, diags: Diagnostics) -> None:
        """Fold diagnostics onto the draft, skipping ones already there so
        a second `to_program()` cannot double them up."""
        for diag in diags.items:
            if diag not in self.diagnostics.items:
                self.diagnostics.items.append(diag)
```

`Diagnostic` is a frozen dataclass, so the `not in` membership test is a structural comparison and needs no extra machinery.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_ingest.py -q`
Expected: PASS. If an existing test in this file asserted an exact `len(draft.diagnostics.items)`, update its expected count — the new behaviour is correct and the old count was the bug.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/ingest.py tests/test_ingest.py
git commit -m "fix: import keeps the validation diagnostics it computes

to_program() ran validate_program and kept the result only when it
raised, so a clean-looking import could hide a 1%-placed tower that
towerctl validate reported immediately afterwards."
```

---

### Task 6: Both import callers surface them, and errors stop the write

Closes **SB-07**. Depends on Task 5.

**Files:**
- Modify: `src/towerkit/cli.py:283-296` (`_cmd_import`)
- Modify: `src/towerkit/tui/screens/browser.py:267-282` (`_finish_import`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `DraftProgram.to_program()` and the post-call `draft.diagnostics` contract from Task 5.
- Produces: `towerctl import` exits **1** and writes nothing when `draft.diagnostics.errors` is non-empty; exits 0 and writes the file when there are only warnings, printing them either way.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`, immediately after the existing `class TestImport:`. That file already imports `main` at module level and calls it bare, and its `test_template_then_import` is the invocation to copy — note the flag is `--program`, **not** `--program-name` (the argparse dest is `program_name`, the flag is not):

```python
class TestImportDiagnostics:
    @staticmethod
    def _template_with(tmp_path, column: str, value: str):
        """The shipped template, with one cell of row 2 overwritten."""
        from openpyxl import load_workbook

        from towerkit.ingest_template import write_template

        source = write_template(tmp_path / "sched.xlsx")
        wb = load_workbook(source)
        ws = wb.worksheets[0]
        headers = [str(c.value or "").strip().lower() for c in ws[1]]
        ws.cell(row=2, column=headers.index(column) + 1, value=value)
        wb.save(source)
        return source

    def test_warnings_are_printed_and_the_file_is_still_written(
        self, tmp_path, capsys
    ) -> None:
        """A partly-placed tower is a legitimate import — it must be
        written, and it must not go out silently."""
        source = self._template_with(tmp_path, "share", "60%")
        out = tmp_path / "imported.json"

        code = main(
            [
                "import", str(source), "-o", str(out),
                "--insured", "Example Co", "--program", "Property",
            ]
        )

        captured = capsys.readouterr()
        assert code == 0
        assert out.exists()
        assert "placed" in captured.out, captured.out

    def test_errors_stop_the_import_and_exit_non_zero(self, tmp_path) -> None:
        source = self._template_with(tmp_path, "limit", "banana")
        out = tmp_path / "imported.json"

        code = main(
            [
                "import", str(source), "-o", str(out),
                "--insured", "Example Co", "--program", "Property",
            ]
        )

        assert code == 1
        assert not out.exists(), "a schedule with errors must not be written"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest tests/test_cli.py::TestImportDiagnostics -q`
Expected: FAIL — the second test finds `code == 0` and the file written.

- [ ] **Step 3: Rewrite the CLI's diagnostics handling**

In `src/towerkit/cli.py`, replace lines 283-296 (from `for diag in draft.diagnostics.items:` down to and including the `out.exists()` refusal) with:

```python
    # to_program() folds its validation diagnostics onto the draft, so the
    # printing happens after the build, not before — that ordering is what
    # stops a clean-looking import from hiding a 1%-placed tower.
    program = None
    try:
        program = draft.to_program()
    except ProgramInvalidError:
        pass
    for diag in draft.diagnostics.items:
        print(f"  {diag}")
    if program is None or draft.diagnostics.errors:
        n = len(draft.diagnostics.errors)
        print(
            f"{n} error{'s' if n != 1 else ''} in the schedule — nothing "
            f"written; fix the source and re-run"
        )
        return 1
    out = args.out or Path(f"{_file_slug(insured)}-{_file_slug(program_name)}.json")
    if out.exists():  # program files are the source of truth — never clobber one
        print(f"{out} already exists — refusing to overwrite (pass a different -o)")
        return 1
```

The `dump_program(program, out)` and following lines stay as they are.

- [ ] **Step 4: Make the TUI import agree**

In `src/towerkit/tui/screens/browser.py`, replace the head of `_finish_import` (lines 267-282, from the `for diag in draft.diagnostics.items:` loop through the `except ProgramInvalidError` block) with:

```python
    def _finish_import(self, draft: DraftProgram) -> None:
        # diagnostics are surfaced after the build, not before: to_program()
        # is where validation runs, and its warnings used to be discarded
        program = None
        try:
            program = draft.to_program()
        except ProgramInvalidError:
            pass
        for diag in draft.diagnostics.items:
            self.notify(
                str(diag), severity="error" if diag.severity == "error" else "warning"
            )
        if program is None or draft.diagnostics.errors:
            n = len(draft.diagnostics.errors)
            self.notify(
                f"import failed: {n} error{'s' if n != 1 else ''} in the "
                f"schedule — nothing written",
                severity="error",
                timeout=10,
            )
            return
```

The rest of the method — the `out` path, the clobber refusal, `dump_program`, `reload()` and the `push_screen` — is unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest tests/test_cli.py tests/test_tui.py tests/test_ingest.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `uv run --group dev pytest -q > /tmp/tk-t6.txt 2>&1 && tail -5 /tmp/tk-t6.txt`
Expected: all green except the environmental `test_connector` failure.

Run: `uv run --group dev ruff check src tests && uv run --group dev mypy src/towerkit`
Expected: no findings.

```bash
git add src/towerkit/cli.py src/towerkit/tui/screens/browser.py tests/test_cli.py
git commit -m "fix: import surfaces its diagnostics and refuses on errors

Errors used to print and then let the import write the file anyway with
exit 0, so a script gating on \$? read a stripped-dates import as a
success."
```

---

## Verification before calling this done

- [ ] `uv run --group dev pytest -q` — everything green but the known environmental `test_connector` failure. Redirect to a file; do not pipe into `tail` before a `&&`.
- [ ] `uv run --group dev ruff check src tests`
- [ ] `uv run --group dev mypy src/towerkit`
- [ ] `uv run towerctl validate programs/*.json` — still ✓, proving no on-disk program was disturbed.
- [ ] `make render` — the sample charts still render, proving `dump_program`'s new write path did not change file contents.
- [ ] `git status --porcelain` inside `programs/` is empty, and `git check-ignore -v .atomic-2026.json.bak` reports a match from `.gitignore`.
- [ ] Manual smoke, since three of these six findings are only reachable by hand:
  1. `./towerctl edit programs/atomic-2026.json`, type into Insured without pressing enter, press `esc` → the unsaved-changes prompt appears and the typed text is in the program.
  2. Same, but press `ctrl+q` → the same prompt appears rather than the app exiting.
  3. With the editor open, `rm` the file from another terminal, then `ctrl+s` → "had been removed — wrote it back", app alive.
  4. `chmod 444` a copy, open it, edit, `ctrl+s` → an error toast, app alive, edit still present.

## What this plan deliberately does not do

Named so a reviewer does not read them as omissions. All are tier-2 items from the same audit:

- **SB-03** (`total_premium()` printing a partial sum as fact) and **SB-04** (unplaced capacity invisible on the SOI) — both need a product decision about how partial data should read on a client document, not just a code fix.
- **SB-05** (percent-formatted cells importing at 1/100) — this plan makes it *visible* via Task 5's warnings, which is the stated safety net, but does not fix the parse. That belongs with the `number_format` work in `ingest_template.read_rows`.
- **SB-11** (autocomplete writing data the user never typed) — a design reversal, per audit DEC-2.
- **SB-12** (`send_line` move half-commits) — its blast radius shrinks here because Task 4 removes the unprompted exit that made the duplication permanent, but the two-file commit itself is untouched.
- **SB-13**/**SB-14** (MCP re-slug and OCC re-arm) and **SB-15**'s `*.xlsx` route — separate plans.
