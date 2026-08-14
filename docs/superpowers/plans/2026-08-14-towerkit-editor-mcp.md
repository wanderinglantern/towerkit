# TowerKit Editor MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an assistant a tool surface for designing towers — lines, retentions, sublimits, stack structure — hosted in towerkit, sharing one edit API with the TUI editor.

**Architecture:** Extract every structural mutation out of `editor.py`'s inline lambdas into `towerkit/edit.py`, which both the editor and a new stdio MCP server call. The server refuses writes against a file that moved under it; the editor refuses to save over a file that moved under it. Semantic validation errors ride back in tool results instead of blocking writes, because a tower under construction is invalid by construction.

**Tech Stack:** Python 3.11+, pydantic v2, `mcp>=2.0` (SDK class `MCPServer`), argparse, Textual (editor modal), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-08-14-towerkit-editor-mcp-design.md`

## Global Constraints

- Branch `feat/editor-mcp`, worktree `towerkit/.claude/worktrees/towerkit-editor-mcp`. Task 10 is in the **bookkit** repo and needs its own worktree.
- Gates before every commit: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe pytest into `tail`/`grep` before an `&&` — the pipe eats the exit code. Redirect to a file in the scratchpad, gate on the command, tail the file after.
- `src/towerkit/edit.py` and `src/towerkit/mcpserver.py` are **strict-mypy** modules (the `tui.*` / `render.*` relaxations do not cover them). Full annotations, no bare `Any` returns.
- ruff: line-length 100, rules `E, F, I, W, UP, B`.
- Money on disk and in `edit.py` is **integer whole dollars**. Shares in memory are **bps**. Only the MCP edge parses human strings, via `money.parse_money` and `money.parse_share`.
- Canonical round-trip is sacred: an untouched program re-saves byte-identically (`tests/test_canonical.py` already pins this).
- MCP tool wrappers registered with `@server.tool()` are declared `async def` even when their bodies are synchronous — the SDK runs sync callables on a worker thread via `anyio.to_thread.run_sync`, and the server's last-seen-sha state should stay on the event-loop thread.
- Never `print()` in `mcpserver.py`: stdout is the protocol.
- Real program fixtures only (`programs/atomic-2026.json` via `shutil.copy` into `tmp_path`), never hand-built dicts.

---

## File Structure

**Create:**
- `src/towerkit/edit.py` — every structural mutation of a `Program`. Plain functions, no session or UI state. The single definition of what "add a line" means.
- `src/towerkit/mcpserver.py` — `build_server(roots)`, name resolution and sandbox, read tools, the guarded write cycle, snapshots, revert, post-write hook.
- `tests/test_edit.py` — the edit API in isolation.
- `tests/test_mcpserver.py` — tools called directly plus protocol round-trips.

**Modify:**
- `src/towerkit/tui/session.py` — re-export/delegate to `edit`; add `_disk_sha`, `StaleFileError`, `reload()`.
- `src/towerkit/tui/screens/editor.py` — call `edit.*` instead of inline lambdas; guard the two `session.save()` sites behind a stale-file modal.
- `src/towerkit/tui/widgets/modals.py` — add `StaleFileModal`.
- `src/towerkit/cli.py` — `towerctl mcp`.
- `pyproject.toml` — `mcp>=2.0`.
- `tests/test_tui.py` — pilot test for the stale-save modal; existing tests must keep passing unchanged.
- bookkit `src/bookkit/cli.py` — `bookctl sync --path FILE` (Task 10).

---

### Task 1: `towerkit.edit` — line operations

The cascade in `editor.py:1553`'s `drop_line` has a real bug: when removing a line would empty a layer's `appliesTo`, it falls back to the **original** list, leaving the layer pointing at a line that no longer exists (`layer-unknown-line` forever). This task fixes that by removing such layers.

**Files:**
- Create: `src/towerkit/edit.py`
- Test: `tests/test_edit.py`

**Interfaces:**
- Consumes: `towerkit.model` (`Program`, `Line`), nothing from earlier tasks.
- Produces:
  - `slugify(name: str) -> str`
  - `unique_id(program: Program, prefix: str, exclude: str | None = None) -> str`
  - `add_line(program: Program, name: str, abbr: str | None = None, group: str | None = None) -> Line`
  - `rename_line(program: Program, line_id: str, name: str) -> Line` (id follows the name; cascades the id through every `appliesTo`)
  - `set_line_group(program: Program, line_id: str, group: str | None) -> Line`
  - `move_line(program: Program, line_id: str, delta: int) -> int` (returns the new index)
  - `remove_line(program: Program, line_id: str) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_edit.py
"""The structural edit API — one definition of every mutation, shared by
the TUI editor and the MCP server."""

from __future__ import annotations

import pytest

from towerkit import edit
from towerkit.model import load_program
from tests.conftest import SAMPLE  # see Step 3 if conftest does not exist yet


def _sample():
    return load_program(SAMPLE)


class TestLines:
    def test_add_line_slugs_its_id_from_the_name(self) -> None:
        program = _sample()
        line = edit.add_line(program, "Cyber Liability", abbr="CYB")
        assert line.id == "cyber-liability"
        assert line.abbr == "CYB"
        assert program.lines[-1] is line

    def test_add_line_does_not_collide_with_an_existing_id(self) -> None:
        program = _sample()
        first = edit.add_line(program, "Excess Casualty")
        second = edit.add_line(program, "Excess Casualty")
        assert first.id == "excess-casualty"
        assert second.id == "excess-casualty-2"

    def test_rename_line_cascades_the_new_id_everywhere(self) -> None:
        program = _sample()
        old = program.lines[0].id
        carriers = [ly.id for ly in program.layers if old in ly.applies_to]
        assert carriers, "fixture must have layers on the first line"
        line = edit.rename_line(program, old, "Marine Cargo")
        assert line.id == "marine-cargo"
        assert not any(old in ly.applies_to for ly in program.layers)
        for layer_id in carriers:
            layer = next(ly for ly in program.layers if ly.id == layer_id)
            assert "marine-cargo" in layer.applies_to

    def test_rename_line_to_its_own_slug_does_not_drift(self) -> None:
        """The self-collision guard: re-committing the same name must not
        walk the id to '-2', '-3' on every edit."""
        program = _sample()
        line = edit.add_line(program, "Cyber")
        again = edit.rename_line(program, line.id, "Cyber")
        assert again.id == "cyber"

    def test_move_line_reorders_columns(self) -> None:
        program = _sample()
        second = program.lines[1].id
        assert edit.move_line(program, second, -1) == 0
        assert program.lines[0].id == second

    def test_move_line_at_the_edge_is_a_no_op(self) -> None:
        program = _sample()
        first = program.lines[0].id
        assert edit.move_line(program, first, -1) == 0
        assert program.lines[0].id == first

    def test_remove_line_drops_layers_it_would_leave_empty(self) -> None:
        """The bug in editor.py's drop_line: it kept the original appliesTo
        when filtering emptied it, stranding a reference to a dead line."""
        program = _sample()
        target = program.lines[0].id
        solo = [ly.id for ly in program.layers if ly.applies_to == [target]]
        assert solo, "fixture must have a single-line layer to strand"
        edit.remove_line(program, target)
        assert target not in program.line_ids()
        assert not any(ly.id in solo for ly in program.layers)
        assert not any(target in ly.applies_to for ly in program.layers)

    def test_remove_line_keeps_shared_layers_and_trims_them(self) -> None:
        program = _sample()
        target = program.lines[0].id
        shared = [
            ly.id for ly in program.layers
            if target in ly.applies_to and len(ly.applies_to) > 1
        ]
        assert shared, "fixture must have a multi-line layer"
        edit.remove_line(program, target)
        for layer_id in shared:
            layer = next(ly for ly in program.layers if ly.id == layer_id)
            assert target not in layer.applies_to

    def test_remove_line_drops_retentions_and_sublimits_left_empty(self) -> None:
        program = _sample()
        target = program.lines[0].id
        edit.remove_line(program, target)
        for retention in program.retentions:
            assert target not in retention.applies_to
            assert retention.applies_to
        for sublimit in program.sublimits:
            assert target not in sublimit.applies_to
            assert sublimit.applies_to

    def test_remove_unknown_line_raises(self) -> None:
        program = _sample()
        with pytest.raises(KeyError):
            edit.remove_line(program, "no-such-line")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'towerkit.edit'`

- [ ] **Step 3: Add the shared fixture constant**

`tests/conftest.py` does not exist yet. Create it:

```python
# tests/conftest.py
"""Shared fixtures. SAMPLE is the committed multi-line program used across
the suite — real scaffolded data, never hand-built dicts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SAMPLE = REPO / "programs" / "atomic-2026.json"


@pytest.fixture()
def sample_copy(tmp_path: Path) -> Path:
    """A writable copy of SAMPLE inside a programs/ directory."""
    target = tmp_path / "programs"
    target.mkdir()
    shutil.copy(SAMPLE, target / "atomic-2026.json")
    return target / "atomic-2026.json"
```

`tests/test_tui.py` defines its own `SAMPLE` and `sample_copy`; leave them alone — a module-level fixture shadows the conftest one harmlessly, and touching that 56K file here would widen the diff for no gain.

- [ ] **Step 4: Write `src/towerkit/edit.py`**

```python
"""Structural edits to a Program — the single definition of what each
mutation means.

Both surfaces call these: the TUI editor wraps them in `EditSession.mutate`
for undo, and the MCP server calls them inside its load → mutate → dump
cycle. Nothing here knows about sessions, screens, or transports.

Money is integer whole dollars, as on disk. Callers parse human strings.
"""

from __future__ import annotations

import re

from .model import Line, Program


def slugify(name: str) -> str:
    """'Primary D&O' → 'primary-do': ids nobody has to invent."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "item"


def unique_id(program: Program, prefix: str, exclude: str | None = None) -> str:
    """`exclude` is the id of the thing being renamed. Without it a cosmetic
    edit that re-slugs to the id the entity ALREADY has would collide with
    itself and drift to 'cyber-2', then 'cyber-3', on every later edit."""
    taken = {layer.id for layer in program.layers} | {line.id for line in program.lines}
    taken.discard(exclude)
    if prefix not in taken:
        return prefix
    n = 2
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


def _line(program: Program, line_id: str) -> Line:
    line = next((ln for ln in program.lines if ln.id == line_id), None)
    if line is None:
        raise KeyError(f"no line {line_id!r}")
    return line


def add_line(
    program: Program, name: str, abbr: str | None = None, group: str | None = None
) -> Line:
    line = Line(id=unique_id(program, slugify(name)), name=name, abbr=abbr, group=group)
    program.lines.append(line)
    return line


def rename_line(program: Program, line_id: str, name: str) -> Line:
    """The id follows the name (towerkit 67ac42f), cascading through every
    appliesTo so nothing is stranded on the old slug."""
    line = _line(program, line_id)
    new_id = unique_id(program, slugify(name), exclude=line_id)
    line.name = name
    line.id = new_id
    if new_id != line_id:
        _recast(program, line_id, new_id)
    return line


def _recast(program: Program, old: str, new: str) -> None:
    for layer in program.layers:
        layer.applies_to = [new if x == old else x for x in layer.applies_to]
    for retention in program.retentions:
        retention.applies_to = [new if x == old else x for x in retention.applies_to]
    for sublimit in program.sublimits:
        sublimit.applies_to = [new if x == old else x for x in sublimit.applies_to]


def set_line_group(program: Program, line_id: str, group: str | None) -> Line:
    line = _line(program, line_id)
    line.group = group or None
    return line


def move_line(program: Program, line_id: str, delta: int) -> int:
    """Array order is column order in the diagram. Returns the resulting
    index; a move off either end is a no-op, not an error."""
    index = next((i for i, ln in enumerate(program.lines) if ln.id == line_id), None)
    if index is None:
        raise KeyError(f"no line {line_id!r}")
    target = index + delta
    if not 0 <= target < len(program.lines):
        return index
    lines = program.lines
    lines[index], lines[target] = lines[target], lines[index]
    return target


def remove_line(program: Program, line_id: str) -> None:
    """Cascade: the id leaves every appliesTo, and anything left with an
    EMPTY appliesTo goes with it. appliesTo is min_length=1, so a partial
    cascade would raise on assignment instead of producing a diagnostic —
    and the editor's old drop_line kept the stale id rather than face it."""
    _line(program, line_id)  # raises KeyError when unknown
    program.lines = [ln for ln in program.lines if ln.id != line_id]
    program.layers = [
        layer for layer in program.layers if [x for x in layer.applies_to if x != line_id]
    ]
    for layer in program.layers:
        layer.applies_to = [x for x in layer.applies_to if x != line_id]
    program.retentions = [
        r for r in program.retentions if [x for x in r.applies_to if x != line_id]
    ]
    for retention in program.retentions:
        retention.applies_to = [x for x in retention.applies_to if x != line_id]
    program.sublimits = [
        s for s in program.sublimits if [x for x in s.applies_to if x != line_id]
    ]
    for sublimit in program.sublimits:
        sublimit.applies_to = [x for x in sublimit.applies_to if x != line_id]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_edit.py -q`
Expected: PASS (10 tests)

If `test_remove_line_drops_layers_it_would_leave_empty` fails on the `assert solo` precondition, the fixture's first line carries no single-line layer — pick a line that does by inspecting `programs/atomic-2026.json` and pin that id in the test rather than weakening the assertion.

- [ ] **Step 6: Run the full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/edit.py tests/test_edit.py tests/conftest.py
git commit -m "edit: line operations extracted, with the cascade drop_line got wrong"
```

---

### Task 2: `towerkit.edit` — retentions, sublimits, layers; session delegates

**Files:**
- Modify: `src/towerkit/edit.py`
- Modify: `src/towerkit/tui/session.py`
- Test: `tests/test_edit.py`

**Interfaces:**
- Consumes: Task 1's `unique_id`, `slugify`, `_line`.
- Produces:
  - `add_retention(program, applies_to: list[str], type: RetentionType | str, amount: int, aggregate: int | None = None, vehicle: str | None = None, notes: str | None = None) -> Retention`
  - `edit_retention(program, index: int, **fields) -> Retention` — accepts `applies_to`, `type`, `amount`, `aggregate`, `vehicle`, `notes`; `None` means "leave alone"
  - `remove_retention(program, index: int) -> None`
  - `add_sublimit(program, name: str, amount: int, applies_to: list[str], notes: str | None = None) -> Sublimit`
  - `edit_sublimit(program, index: int, **fields) -> Sublimit`
  - `remove_sublimit(program, index: int) -> None`
  - `rename_layer(program, layer_id: str, name: str) -> Layer`
  - `remove_layer(program, layer_id: str) -> None`
  - `set_applies_to(program, layer_id: str, line_ids: list[str]) -> Layer`
  - `set_follows_underlying(program, layer_id: str, follows: bool) -> Layer`
  - `add_layer(program, line_ids: list[str] | None = None) -> Layer` (moved from `EditSession`)
  - `restack(program) -> None` (moved)
  - `suggested_attach(program, line_ids: list[str]) -> int` (moved)
  - `ordinal(n: int) -> str` (moved)
  - `heal_follows(program) -> None` (extracted from `EditSession.mutate`)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_edit.py
from towerkit.model import RetentionType


class TestRetentionsAndSublimits:
    def test_add_retention_appends_and_returns_it(self) -> None:
        program = _sample()
        before = len(program.retentions)
        retention = edit.add_retention(
            program, [program.lines[0].id], "sir", 500_000, aggregate=2_000_000
        )
        assert len(program.retentions) == before + 1
        assert retention.type is RetentionType.SIR
        assert retention.amount == 500_000
        assert program.retentions[-1] is retention

    def test_edit_retention_leaves_unnamed_fields_alone(self) -> None:
        program = _sample()
        edit.add_retention(program, [program.lines[0].id], "deductible", 100_000)
        index = len(program.retentions) - 1
        edit.edit_retention(program, index, amount=250_000)
        assert program.retentions[index].amount == 250_000
        assert program.retentions[index].type is RetentionType.DEDUCTIBLE

    def test_edit_retention_out_of_range_raises(self) -> None:
        program = _sample()
        with pytest.raises(IndexError):
            edit.edit_retention(program, 99, amount=1)

    def test_remove_retention_by_index(self) -> None:
        program = _sample()
        edit.add_retention(program, [program.lines[0].id], "sir", 1)
        before = len(program.retentions)
        edit.remove_retention(program, before - 1)
        assert len(program.retentions) == before - 1

    def test_add_and_edit_sublimit(self) -> None:
        program = _sample()
        sublimit = edit.add_sublimit(program, "Flood", 5_000_000, [program.lines[0].id])
        index = len(program.sublimits) - 1
        assert sublimit.name == "Flood"
        edit.edit_sublimit(program, index, name="Flood & Quake", amount=10_000_000)
        assert program.sublimits[index].name == "Flood & Quake"
        assert program.sublimits[index].amount == 10_000_000


class TestLayers:
    def test_add_layer_stacks_on_top_and_names_by_ordinal(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        top = edit.suggested_attach(program, [line_id])
        layer = edit.add_layer(program, [line_id])
        assert layer.attach == top
        assert layer.limit == 5_000_000
        assert layer.participants == []

    def test_rename_layer_id_follows_the_name(self) -> None:
        program = _sample()
        layer = edit.add_layer(program, [program.lines[0].id])
        renamed = edit.rename_layer(program, layer.id, "Lead Umbrella")
        assert renamed.id == "lead-umbrella"
        assert renamed.name == "Lead Umbrella"

    def test_remove_layer(self) -> None:
        program = _sample()
        layer = edit.add_layer(program, [program.lines[0].id])
        edit.remove_layer(program, layer.id)
        assert not any(ly.id == layer.id for ly in program.layers)

    def test_remove_unknown_layer_raises(self) -> None:
        program = _sample()
        with pytest.raises(KeyError):
            edit.remove_layer(program, "nope")

    def test_set_applies_to_rejects_an_unknown_line(self) -> None:
        program = _sample()
        layer = program.layers[0]
        with pytest.raises(KeyError):
            edit.set_applies_to(program, layer.id, ["ghost"])

    def test_set_applies_to_rejects_an_empty_list(self) -> None:
        program = _sample()
        layer = program.layers[0]
        with pytest.raises(ValueError):
            edit.set_applies_to(program, layer.id, [])

    def test_follows_underlying_heals_its_attachment(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        layer = edit.add_layer(program, [line_id])
        edit.set_applies_to(program, layer.id, [line_id])
        edit.set_follows_underlying(program, layer.id, True)
        edit.heal_follows(program)
        expected = max(program.underlying_tops(layer).values(), default=0)
        assert layer.attach == expected

    def test_restack_closes_gaps(self) -> None:
        program = _sample()
        line_id = program.lines[0].id
        stack = [ly for ly in program.layers_for_line(line_id) if ly.limit > 0]
        stack[-1].attach += 25_000_000  # blow a gap
        edit.restack(program)
        healed = [ly for ly in program.layers_for_line(line_id) if ly.limit > 0]
        healed.sort(key=lambda ly: ly.attach)
        assert healed[0].attach == 0
        for below, above in zip(healed, healed[1:]):
            assert above.attach == below.top
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit.py -q`
Expected: FAIL — `AttributeError: module 'towerkit.edit' has no attribute 'add_retention'`

- [ ] **Step 3: Extend `src/towerkit/edit.py`**

Add to the imports: `from .model import Layer, Line, Program, Retention, RetentionType, Sublimit`.

```python
def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def suggested_attach(program: Program, line_ids: list[str]) -> int:
    """Default attachment for a new layer: the top of the existing stack for
    those lines. Contiguity by construction beats contiguity by validation."""
    tops = [
        layer.top
        for layer in program.layers
        if layer.limit > 0 and any(lid in layer.applies_to for lid in line_ids)
    ]
    return max(tops, default=0)


def heal_follows(program: Program) -> None:
    """Follows-underlying attachment is derived state: recompute it so that
    editing a lower layer's limit can never strand the layer above."""
    for layer in program.layers:
        if layer.follows_underlying:
            tops = program.underlying_tops(layer)
            layer.attach = max(tops.values(), default=0)


def _layer(program: Program, layer_id: str) -> Layer:
    layer = next((ly for ly in program.layers if ly.id == layer_id), None)
    if layer is None:
        raise KeyError(f"no layer {layer_id!r}")
    return layer


def add_layer(program: Program, line_ids: list[str] | None = None) -> Layer:
    lines = line_ids or ([program.lines[0].id] if program.lines else [])
    attach = suggested_attach(program, lines)
    if attach == 0:
        name = "New Layer"
    else:
        n = sum(
            1
            for ly in program.layers
            if ly.attach > 0 and ly.limit > 0 and any(lid in ly.applies_to for lid in lines)
        )
        name = f"{ordinal(n + 1)} Excess"
    layer = Layer(
        id=unique_id(program, "layer"),
        name=name,
        applies_to=lines or ["gl"],
        attach=attach,
        limit=5_000_000,
        participants=[],
    )
    program.layers.append(layer)
    return layer


def rename_layer(program: Program, layer_id: str, name: str) -> Layer:
    layer = _layer(program, layer_id)
    layer.name = name
    layer.id = unique_id(program, slugify(name), exclude=layer_id)
    return layer


def remove_layer(program: Program, layer_id: str) -> None:
    _layer(program, layer_id)
    program.layers = [ly for ly in program.layers if ly.id != layer_id]


def set_applies_to(program: Program, layer_id: str, line_ids: list[str]) -> Layer:
    layer = _layer(program, layer_id)
    if not line_ids:
        raise ValueError("a layer must apply to at least one line")
    known = set(program.line_ids())
    unknown = [lid for lid in line_ids if lid not in known]
    if unknown:
        raise KeyError(f"unknown line(s): {', '.join(sorted(unknown))}")
    layer.applies_to = list(dict.fromkeys(line_ids))
    return layer


def set_follows_underlying(program: Program, layer_id: str, follows: bool) -> Layer:
    layer = _layer(program, layer_id)
    layer.follows_underlying = follows
    return layer


def restack(program: Program) -> None:
    """Recalculate every attachment from the stacking order: each layer lands
    on top of what its lines already carry. One call heals a tower after limit
    edits — gaps and overlaps disappear."""
    tops: dict[str, int] = {line.id: 0 for line in program.lines}
    ordered = sorted((ly for ly in program.layers if ly.limit > 0), key=lambda ly: ly.attach)
    for layer in ordered:
        base = max((tops.get(lid, 0) for lid in layer.applies_to), default=0)
        layer.attach = base
        for lid in layer.applies_to:
            tops[lid] = base + layer.limit


def _check_lines(program: Program, line_ids: list[str]) -> list[str]:
    if not line_ids:
        raise ValueError("appliesTo must name at least one line")
    known = set(program.line_ids())
    unknown = [lid for lid in line_ids if lid not in known]
    if unknown:
        raise KeyError(f"unknown line(s): {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(line_ids))


def add_retention(
    program: Program,
    applies_to: list[str],
    type: RetentionType | str,
    amount: int,
    aggregate: int | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> Retention:
    retention = Retention(
        applies_to=_check_lines(program, applies_to),
        type=RetentionType(type),
        amount=amount,
        aggregate=aggregate,
        vehicle=vehicle,
        notes=notes,
    )
    program.retentions.append(retention)
    return retention


def _at(items: list[object], index: int, what: str) -> int:
    if not 0 <= index < len(items):
        raise IndexError(f"no {what} at index {index} (there are {len(items)})")
    return index


def edit_retention(
    program: Program,
    index: int,
    applies_to: list[str] | None = None,
    type: RetentionType | str | None = None,
    amount: int | None = None,
    aggregate: int | None = None,
    vehicle: str | None = None,
    notes: str | None = None,
) -> Retention:
    """`None` means leave alone. Retentions have no ids — the caller addresses
    them by the index a read reported, guarded by an expecting_* field at the
    tool layer."""
    _at(list(program.retentions), index, "retention")
    retention = program.retentions[index]
    if applies_to is not None:
        retention.applies_to = _check_lines(program, applies_to)
    if type is not None:
        retention.type = RetentionType(type)
    if amount is not None:
        retention.amount = amount
    if aggregate is not None:
        retention.aggregate = aggregate
    if vehicle is not None:
        retention.vehicle = vehicle
    if notes is not None:
        retention.notes = notes
    return retention


def remove_retention(program: Program, index: int) -> None:
    _at(list(program.retentions), index, "retention")
    program.retentions.pop(index)


def add_sublimit(
    program: Program,
    name: str,
    amount: int,
    applies_to: list[str],
    notes: str | None = None,
) -> Sublimit:
    sublimit = Sublimit(
        name=name,
        amount=amount,
        applies_to=_check_lines(program, applies_to),
        notes=notes,
    )
    program.sublimits.append(sublimit)
    return sublimit


def edit_sublimit(
    program: Program,
    index: int,
    name: str | None = None,
    amount: int | None = None,
    applies_to: list[str] | None = None,
    notes: str | None = None,
) -> Sublimit:
    _at(list(program.sublimits), index, "sublimit")
    sublimit = program.sublimits[index]
    if name is not None:
        sublimit.name = name
    if amount is not None:
        sublimit.amount = amount
    if applies_to is not None:
        sublimit.applies_to = _check_lines(program, applies_to)
    if notes is not None:
        sublimit.notes = notes
    return sublimit


def remove_sublimit(program: Program, index: int) -> None:
    _at(list(program.sublimits), index, "sublimit")
    program.sublimits.pop(index)
```

- [ ] **Step 4: Make `session.py` delegate**

In `src/towerkit/tui/session.py`: delete the bodies of `ordinal`, `slugify`, `suggested_attach`, `EditSession.unique_id`, `EditSession.add_layer`, `EditSession.restack`, and the follows-healing loop inside `mutate`. Replace with delegation, keeping the module-level names importable because `editor.py` and `tests/test_tui.py` import them from here:

```python
from .. import edit
from ..edit import ordinal, slugify, suggested_attach  # re-exported: editor.py imports these

    def mutate(self, fn: Callable[[Program], object]) -> None:
        """Apply one user-visible edit, snapshotting for undo first."""
        before = dumps_program(self.program)
        fn(self.program)
        edit.heal_follows(self.program)
        after = dumps_program(self.program)
        if after != before:
            self._undo.append(before)
            self._redo.clear()

    def unique_id(self, prefix: str, exclude: str | None = None) -> str:
        return edit.unique_id(self.program, prefix, exclude)

    def add_layer(self, line_ids: list[str] | None = None) -> Layer:
        layer: Layer | None = None

        def do(p: Program) -> None:
            nonlocal layer
            layer = edit.add_layer(p, line_ids)

        self.mutate(do)
        assert layer is not None
        return layer

    def restack(self) -> None:
        self.mutate(edit.restack)
```

Ruff will flag the re-exported names as unused (F401) — add them to `__all__` in `session.py` rather than adding a `noqa`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_edit.py tests/test_tui.py -q`
Expected: PASS. `test_tui.py` exercises `add_layer`, `restack`, `suggested_attach`, and undo/redo — it is the regression net for the delegation, and it must pass **unchanged**. If it fails, the delegation changed behavior; fix the delegation, not the test.

- [ ] **Step 6: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/edit.py src/towerkit/tui/session.py tests/test_edit.py
git commit -m "edit: retentions, sublimits, layers; EditSession delegates to the shared API"
```

---

### Task 3: The editor calls `edit.*`

**Files:**
- Modify: `src/towerkit/tui/screens/editor.py` (`action_add_node` ~1490-1540, `action_remove_node` ~1553-1585, `action_move_line` ~1602-1622, `_commit_line_field` ~1171-1200)
- Test: `tests/test_conventions.py` (create)

**Interfaces:**
- Consumes: everything `edit` produces from Tasks 1-2.
- Produces: nothing new. This task removes duplicate rules; behavior is unchanged except that removing a line now drops stranded layers.

- [ ] **Step 1: Write the failing convention test**

```python
# tests/test_conventions.py
"""One definition per rule.

Structural mutation lives in towerkit.edit, so the TUI and the MCP server
cannot drift apart. This is the same shape as bookkit's no-raw-SQL-in-tui
test: cheap, mechanical, and it fails the moment someone reaches past the
API instead of extending it."""

from __future__ import annotations

from pathlib import Path

TUI = Path(__file__).parent.parent / "src" / "towerkit" / "tui"

BANNED = (
    ".lines.append(", ".layers.append(", ".retentions.append(", ".sublimits.append(",
    ".lines.pop(", ".layers.pop(", ".retentions.pop(", ".sublimits.pop(",
    ".lines.remove(", ".layers.remove(", ".retentions.remove(", ".sublimits.remove(",
    'setattr(p, "lines"', 'setattr(p, "layers"',
    'setattr(p, "retentions"', 'setattr(p, "sublimits"',
    "p.lines =", "p.layers =", "p.retentions =", "p.sublimits =",
)


def test_tui_never_mutates_program_collections_directly() -> None:
    offenders = []
    for path in sorted(TUI.rglob("*.py")):
        for number, text in enumerate(path.read_text("utf-8").splitlines(), start=1):
            for pattern in BANNED:
                if pattern in text:
                    offenders.append(f"{path.relative_to(TUI.parent)}:{number}: {pattern}")
    assert not offenders, (
        "structural mutation belongs in towerkit.edit, not the TUI:\n"
        + "\n".join(offenders)
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_conventions.py -q`
Expected: FAIL, listing `screens/editor.py` line hits for `p.lines =`, `.retentions.append(`, `.retentions.pop(`, `.sublimits.append(`, `.sublimits.pop(`.

- [ ] **Step 3: Rewrite the editor's mutation sites**

**The rule for every site in this task:** the `edit.*` call must happen *inside* the callable handed to `session.mutate`, never before it. `mutate` snapshots the program for undo before invoking the callable, so a mutation performed outside it is invisible to undo. When you need the created object's id, capture it from inside the callable (`created: list[str] = []` … `created.append(...)`, or `nonlocal`), rather than mutating first and passing a no-op.

In `action_add_node`, replace the four inline branches:

```python
        if kind in ("lines-group", "line"):
            created: list[str] = []
            self._mutate_and_refresh(
                lambda p: created.append(edit.add_line(p, "New Line").id)
            )
            self.selected = ("line", created[0])
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
        elif kind in ("layers-group", "layer"):
            base_lines = None
            if kind == "layer":
                current = self._layer(key)
                base_lines = list(current.applies_to) if current else None
            layer = self.session.add_layer(base_lines)   # already delegates
            self.refresh_all()
            self.selected = ("layer", layer.id)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
            self.notify(
                f"attach suggested at {format_money(layer.attach)} "
                f"(top of stack for {'/'.join(layer.applies_to)})"
            )
        elif kind in ("retentions-group", "retention"):
            covered = {lid for r in program.retentions for lid in r.applies_to}
            uncovered = [ln.id for ln in program.lines if ln.id not in covered]
            target = uncovered[:1] or ([program.lines[0].id] if program.lines else ["gl"])
            self._mutate_and_refresh(
                lambda p: edit.add_retention(p, target, RetentionType.DEDUCTIBLE, 250_000)
            )
            self.selected = ("retention", len(program.retentions) - 1)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
        elif kind in ("sublimits-group", "sublimit"):
            first = [program.lines[0].id] if program.lines else ["gl"]
            self._mutate_and_refresh(
                lambda p: edit.add_sublimit(p, "New Sublimit", 1_000_000, first)
            )
            self.selected = ("sublimit", len(program.sublimits) - 1)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
```

Note the parenthesisation fix in the retention branch: the original
`uncovered[:1] or [program.lines[0].id] if program.lines else ["gl"]` parses as
`(uncovered[:1] or [program.lines[0].id]) if program.lines else ["gl"]`, which
raises `IndexError` when `uncovered` is empty and `program.lines` is empty —
unreachable today but a trap. The explicit parentheses above fix it.

In `action_remove_node`:

```python
        if kind == "line":
            if self._line(key) is None:
                return
            self.session.mutate(lambda p: edit.remove_line(p, key))
            await after()
        elif kind == "layer":
            self.session.mutate(lambda p: edit.remove_layer(p, key))
            await after()
        elif kind == "retention" and key < len(program.retentions):
            self.session.mutate(lambda p: edit.remove_retention(p, key))
            await after()
        elif kind == "sublimit" and key < len(program.sublimits):
            self.session.mutate(lambda p: edit.remove_sublimit(p, key))
            await after()
```

In `action_move_line`, replace the `swap` closure:

```python
        target = None

        def do(p: Program) -> None:
            nonlocal target
            target = edit.move_line(p, key, delta)

        self.session.mutate(do)
        if target is None or target == index:
            return
        self.refresh_all()
        self._select_tree_node(("line", key))
        self.notify(f"{lines[target].name} → column {target + 1} of {len(lines)}")
```

In `_commit_line_field`, the rename branch keeps its `PLACEHOLDER_ID` gate (renaming only re-slugs an auto-generated id; a hand-chosen id stays put) but delegates the cascade:

```python
        if widget.id == "f-line-name" and value and value != line.name:
            if PLACEHOLDER_ID.match(line.id):
                old = line.id
                self._mutate_and_refresh(lambda p: edit.rename_line(p, old, value))
                self.selected = ("line", edit.slugify(value))
                self._select_tree_node(self.selected)
            else:
                self._mutate_and_refresh(lambda p: setattr(line, "name", value))
```

`self.selected` uses `edit.slugify(value)` rather than the returned id because the lambda's return is discarded; if a collision made the real id `cyber-2`, the tree selection falls back to the program node, which is cosmetic. If you want it exact, use the `nonlocal` capture idiom shown above.

Add `from ... import edit` and `from ...model import RetentionType` to the imports at the top of `editor.py`. Remove any now-unused imports (`Retention`, `Sublimit`, `Line`) — ruff F401 will name them.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_conventions.py tests/test_tui.py tests/test_tui_sheets.py -q`
Expected: PASS. The TUI suite is the behavioral net here.

- [ ] **Step 5: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/tui/screens/editor.py tests/test_conventions.py
git commit -m "editor: structural edits go through towerkit.edit; convention test pins it"
```

---

### Task 4: Stale-file guard on the editor's save

**Files:**
- Modify: `src/towerkit/tui/session.py`
- Modify: `src/towerkit/tui/widgets/modals.py`
- Modify: `src/towerkit/tui/screens/editor.py` (`_do_save` ~1650, `action_back`'s `on_choice` ~1888)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `session.StaleFileError` (subclass of `RuntimeError`)
  - `EditSession.save(path: Path | None = None, force: bool = False) -> Path`
  - `EditSession.reload() -> None`
  - `modals.StaleFileModal` — `ModalScreen[str]`, dismisses `"reload" | "overwrite" | "keep"`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tui.py, in TestSession
    def test_save_refuses_when_the_file_moved_underneath(self, sample_copy) -> None:
        from towerkit.tui.session import StaleFileError

        session = EditSession.open(sample_copy)
        session.mutate(lambda p: setattr(p, "insured", "Mine"))
        sample_copy.write_text(
            sample_copy.read_text("utf-8").replace('"insured"', '"insured"', 1) + " ",
            encoding="utf-8",
        )
        with pytest.raises(StaleFileError):
            session.save()

    def test_forced_save_overwrites_and_re_arms_the_guard(self, sample_copy) -> None:
        session = EditSession.open(sample_copy)
        session.mutate(lambda p: setattr(p, "insured", "Mine"))
        sample_copy.write_text(sample_copy.read_text("utf-8") + " ", encoding="utf-8")
        session.save(force=True)
        assert load_program(sample_copy).insured == "Mine"
        session.mutate(lambda p: setattr(p, "insured", "Mine Again"))
        session.save()  # the guard was re-armed by the forced save; no raise
        assert load_program(sample_copy).insured == "Mine Again"

    def test_reload_takes_the_file_and_drops_local_edits(self, sample_copy) -> None:
        session = EditSession.open(sample_copy)
        session.mutate(lambda p: setattr(p, "insured", "Mine"))
        program = load_program(sample_copy)
        program.insured = "Theirs"
        sample_copy.write_text(dumps_program(program), encoding="utf-8")
        session.reload()
        assert session.program.insured == "Theirs"
        assert not session.dirty
        assert not session.undo()  # history belongs to the discarded edits
        session.save()  # no raise: the guard re-armed on reload
```

```python
# append to tests/test_tui.py, in TestEditor
    @pytest.mark.asyncio
    async def test_save_over_a_changed_file_offers_a_choice(
        self, sample_copy, monkeypatch
    ) -> None:
        from towerkit.tui.widgets.modals import StaleFileModal

        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            assert isinstance(editor, EditorScreen)
            editor.session.mutate(lambda p: setattr(p, "insured", "Mine"))
            sample_copy.write_text(sample_copy.read_text("utf-8") + " ", encoding="utf-8")
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, StaleFileModal)
            await pilot.press("escape")  # keep editing
            await pilot.pause()
            assert editor.session.program.insured == "Mine"  # nothing lost
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_tui.py -k "stale or moved or reload or forced" -q`
Expected: FAIL — `ImportError: cannot import name 'StaleFileError'`

- [ ] **Step 3: Add the guard to `session.py`**

```python
import hashlib


class StaleFileError(RuntimeError):
    """The file changed on disk since this session opened or last saved it.

    `EditSession` holds a whole program in memory and writes it whole, so a
    save over someone else's write is a silent total loss — bookkit's MCP
    writes to these same files, and so does towerkit's own MCP server."""


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
```

In `EditSession.__init__`, after `self._saved_text = …`:

```python
        self._disk_sha: str | None = _file_sha(path) if path else None
```

Replace `save`:

```python
    def save(self, path: Path | None = None, force: bool = False) -> Path:
        """Write canonical JSON. Never silent about errors — the caller must
        have confirmed if diagnostics().ok is False.

        Refuses to overwrite a file that changed since we opened or last
        saved it, unless `force`. Saving AS a different path is not guarded:
        that is a new destination, not a clobber of what we loaded."""
        target = path or self.path
        if target is None:
            raise ValueError("no path for save")
        same_file = self.path is not None and target == self.path
        if same_file and not force and _file_sha(target) != self._disk_sha:
            raise StaleFileError(
                f"{target} changed on disk since it was opened — reload to take "
                f"the file (losing this session's edits), or overwrite it"
            )
        text = dumps_program(self.program)
        target.write_text(text, encoding="utf-8")
        self.path = target
        self._saved_text = text
        self._disk_sha = _file_sha(target)
        return target

    def reload(self) -> None:
        """Take what is on disk, discarding this session's edits and history."""
        if self.path is None:
            raise ValueError("nothing to reload from")
        self.program = load_program(self.path)
        self._undo.clear()
        self._redo.clear()
        self._saved_text = dumps_program(self.program)
        self._disk_sha = _file_sha(self.path)
```

- [ ] **Step 4: Add `StaleFileModal` to `modals.py`**

```python
class StaleFileModal(ModalScreen[str]):
    """The file moved under an open editor. Dismisses with
    'reload' | 'overwrite' | 'keep'."""

    BINDINGS = [
        ("r", "dismiss('reload')", "Reload"),
        ("o", "dismiss('overwrite')", "Overwrite"),
        ("escape", "dismiss('keep')", "Keep editing"),
    ]

    DEFAULT_CSS = """
    StaleFileModal { align: center middle; }
    StaleFileModal > VerticalScroll {
        width: 68; height: auto; max-height: 80%; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    StaleFileModal .modal-hint { color: $text-muted; margin-top: 1; }
    StaleFileModal Horizontal { height: auto; align-horizontal: right; }
    StaleFileModal Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("This file changed on disk since you opened it.")
            yield Label(
                "Reload takes the file and DISCARDS your edits. "
                "Overwrite keeps your edits and discards theirs.",
                classes="modal-hint",
            )
            yield Label(
                "[b]r[/b] reload · [b]o[/b] overwrite · [b]esc[/b] keep editing",
                classes="modal-hint",
            )
            with Horizontal():
                yield Button("Keep editing", id="keep")
                yield Button("Overwrite", id="overwrite", variant="warning")
                yield Button("Reload", id="reload", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#reload", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "keep")
```

- [ ] **Step 5: Route both save sites through one guarded helper in `editor.py`**

```python
    def _save_guarded(self, then: Callable[[], None] | None = None) -> None:
        """Every save that overwrites the loaded file goes through here.
        StaleFileError is a question for the user, not an error to swallow."""
        try:
            self.session.save()
        except StaleFileError:
            def on_choice(choice: str | None) -> None:
                if choice == "overwrite":
                    self.session.save(force=True)
                elif choice == "reload":
                    self.session.reload()
                    self.refresh_all()
                    self.notify("reloaded from disk — your edits were discarded")
                    return
                else:
                    return
                self.notify(f"saved {self.session.path}")
                self._refresh_title()
                if then is not None:
                    then()

            self.app.push_screen(StaleFileModal(), on_choice)
            return
        self.notify(f"saved {self.session.path}")
        self._refresh_title()
        if then is not None:
            then()
```

In `_do_save`, replace the tail (the `self.session.save()` / notify / `_refresh_title()` trio, keeping the save-as branch above it untouched) with `self._save_guarded()`.

In `action_back`'s `on_choice`, replace the final `self.session.save()` / notify / `dismiss_editor()` trio with `self._save_guarded(then=self.dismiss_editor)`.

Import `StaleFileError` from `..session` and `StaleFileModal` from `..widgets.modals`; `Callable` from `collections.abc`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_tui.py -q`
Expected: PASS, including the four new tests.

- [ ] **Step 7: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/tui/session.py src/towerkit/tui/widgets/modals.py src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "editor: refuse to save over a file that moved underneath, and offer the choice"
```

---

### Task 5: MCP server skeleton and read tools

**Files:**
- Create: `src/towerkit/mcpserver.py`
- Create: `tests/test_mcpserver.py`
- Modify: `pyproject.toml`, `src/towerkit/cli.py`

**Interfaces:**
- Consumes: `towerkit.model`, `towerkit.validate`, `towerkit.render.ascii`, `towerkit.theme`.
- Produces:
  - `build_server(roots: list[Path] | None = None) -> MCPServer`
  - `serve(roots: list[Path] | None = None) -> None`
  - `Programs` — the resolver/state object: `.resolve(name) -> Path`, `.roots`, `.seen: dict[str, str]`
  - Tools: `program_list`, `program_read`, `program_view`, `program_check`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"mcp>=2.0",` to `[project.dependencies]`, then:

```bash
uv sync --group dev
uv run python -c "from mcp.server.mcpserver import MCPServer; print('ok')"
```

Wheelhouse drill (required — install.sh has a wheelhouse fallback path):

```bash
python3 -m pip download mcp --no-deps -d wheelhouse
cd wheelhouse && zip -r ../towerkit-wheelhouse-macos.zip . && cd ..
gh release upload v0.2.0 towerkit-wheelhouse-macos.zip --clobber
```

Then extend install.sh's stale-wheelhouse probe to check for an `mcp-*.whl`, matching how it probes for the other runtime deps.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_mcpserver.py
"""towerctl mcp — the design surface.

Tools are exercised two ways: directly (fast, precise) and over a real
in-memory protocol round-trip (proves the wiring the SDK does for us).
The SDK is mcp==2.0: the server class is `MCPServer` at
`mcp.server.mcpserver.MCPServer`, and the in-process client is
`mcp.client.Client`, which takes the server instance. pyproject sets
asyncio_mode = "auto", so bare `async def test_` functions are collected
without a marker."""

from __future__ import annotations

import shutil

import pytest

from towerkit.mcpserver import build_server
from tests.conftest import SAMPLE


@pytest.fixture()
def roots(tmp_path):
    programs = tmp_path / "programs"
    (programs / "private").mkdir(parents=True)
    shutil.copy(SAMPLE, programs / "atomic-2026.json")
    shutil.copy(SAMPLE, programs / "private" / "secret-2026.json")
    return [programs]


def _tool(server, name):
    """Call a registered tool's underlying function directly."""
    return server._tool_manager.get_tool(name).fn


class TestResolution:
    def test_list_finds_programs_in_roots_and_private(self, roots) -> None:
        server = build_server(roots)
        names = {p["name"] for p in _tool(server, "program_list")()["programs"]}
        assert names == {"atomic-2026", "private/secret-2026"}

    def test_read_reports_structure_and_a_sha(self, roots) -> None:
        server = build_server(roots)
        out = _tool(server, "program_read")("atomic-2026")
        assert out["insured"]
        assert [ln["id"] for ln in out["lines"]]
        assert out["layers"][0]["id"]
        assert len(out["sha"]) == 64
        assert out["retentions"][0]["index"] == 0

    def test_escaping_the_roots_is_refused(self, roots) -> None:
        server = build_server(roots)
        for bad in ("../outside", "/etc/passwd", "private/../../escape"):
            with pytest.raises(ValueError, match="outside"):
                _tool(server, "program_read")(bad)

    def test_unknown_name_names_what_is_available(self, roots) -> None:
        server = build_server(roots)
        with pytest.raises(ValueError, match="atomic-2026"):
            _tool(server, "program_read")("nope")


class TestSeeing:
    def test_view_returns_an_ascii_tower_without_escape_codes(self, roots) -> None:
        server = build_server(roots)
        art = _tool(server, "program_view")("atomic-2026")["view"]
        assert "\x1b[" not in art
        assert art.count("\n") > 5

    def test_check_reports_diagnostics_with_refs(self, roots) -> None:
        server = build_server(roots)
        out = _tool(server, "program_check")("atomic-2026")
        assert out["errors"] == []
        assert any("placed" in w["message"] for w in out["warnings"])
        assert out["warnings"][0]["ref"]


async def test_tools_are_registered_and_callable_over_the_protocol(roots) -> None:
    from mcp.client import Client

    server = build_server(roots)
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"program_list", "program_read", "program_view", "program_check"} <= names
        result = await client.call_tool("program_read", {"name": "atomic-2026"})
        assert not result.is_error
        assert result.structured_content["lines"]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'towerkit.mcpserver'`

- [ ] **Step 4: Write the server**

```python
"""towerctl mcp — the design surface over towerkit's program files.

Tower DESIGN lives here: lines, retentions, sublimits, and the shape of the
stack. Book facts — premiums firming, markets binding, dates moving — belong
to bookkit's server, which edits these same files through towerkit.

stdout is protocol. Never print.

Tool wrappers are `async def` with synchronous bodies: the SDK runs a *sync*
tool callable on a worker thread via `anyio.to_thread.run_sync`, and this
server's last-seen-sha state should stay on the event-loop thread that owns
it. The bodies do no I/O worth yielding for.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .model import Program, load_program
from .validate import validate_program

DEFAULT_ROOT = Path("programs")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Programs:
    """Name resolution, the sandbox, and what this session has last seen.

    A name is a file stem relative to a root — 'atomic-2027' or
    'private/endeavour-2026'. Anything resolving outside the roots is
    refused: this server is not a general file writer."""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = [Path(r).resolve() for r in (roots or [DEFAULT_ROOT])]
        self.seen: dict[str, str] = {}

    def resolve(self, name: str, must_exist: bool = True) -> Path:
        if name.endswith(".json"):
            name = name[: -len(".json")]
        for root in self.roots:
            candidate = (root / f"{name}.json").resolve()
            if not self._inside(candidate):
                continue
            if candidate.exists() or not must_exist:
                return candidate
        if not must_exist:
            raise ValueError(
                f"{name!r} resolves outside the program roots "
                f"({', '.join(str(r) for r in self.roots)})"
            )
        for root in self.roots:
            if not self._inside((root / f"{name}.json").resolve()):
                raise ValueError(
                    f"{name!r} resolves outside the program roots "
                    f"({', '.join(str(r) for r in self.roots)})"
                )
        raise ValueError(f"no program {name!r} — available: {', '.join(self.names())}")

    def _inside(self, candidate: Path) -> bool:
        return any(candidate.is_relative_to(root) for root in self.roots)

    def names(self) -> list[str]:
        out: list[str] = []
        for root in self.roots:
            for path in sorted(root.rglob("*.json")):
                if ".mcp-snapshots" in path.parts:
                    continue
                out.append(path.relative_to(root).with_suffix("").as_posix())
        return out

    def note(self, path: Path) -> str:
        """Record the sha this session has now seen for a file, and return it."""
        sha = file_sha256(path)
        self.seen[str(path)] = sha
        return sha


def _period(program: Program) -> dict[str, str]:
    return {"from": program.period.start.isoformat(), "to": program.period.end.isoformat()}


def _diag(items: list[Any]) -> list[dict[str, Any]]:
    return [
        {"code": d.code, "message": d.message, "ref": f"{d.ref[0]}:{d.ref[1]}"}
        for d in items
    ]


def build_server(roots: list[Path] | None = None) -> MCPServer:
    programs = Programs(roots)
    server = MCPServer(
        name="towerkit",
        instructions=(
            "Design insurance towers in towerkit program files: coverage lines, "
            "retentions, sublimits, and the shape of the layer stack. Read a "
            "program (program_read) before writing to it — writes refuse against "
            "a file this session has not read, and against one that changed since. "
            "program_view draws the tower; use it to see gaps and overlaps. "
            "Validation errors do NOT block writes: a tower under construction is "
            "invalid by construction, so errors come back in the result and you "
            "keep building. Book facts — premiums, market shares, policy dates — "
            "belong to bookkit's tools, not these."
        ),
    )
    _register_read_tools(server, programs)
    return server


def _register_read_tools(server: MCPServer, programs: Programs) -> None:
    @server.tool()
    async def program_list() -> dict[str, Any]:
        """Every program file under the configured roots."""
        out = []
        for name in programs.names():
            path = programs.resolve(name)
            try:
                program = load_program(path)
            except Exception as exc:  # a broken file must not hide the rest
                out.append({"name": name, "error": str(exc)})
                continue
            out.append({
                "name": name,
                "insured": program.insured,
                "program": program.program,
                "placement": program.placement.value,
                "period": _period(program),
                "layers": len(program.layers),
            })
        return {"programs": out, "roots": [str(r) for r in programs.roots]}

    @server.tool()
    async def program_read(name: str) -> dict[str, Any]:
        """The full structure of one program. Read before you write.

        Retentions and sublimits have no ids — they carry the `index` that
        the edit tools address them by."""
        path = programs.resolve(name)
        program = load_program(path)
        sha = programs.note(path)
        return {
            "name": name,
            "insured": program.insured,
            "program": program.program,
            "placement": program.placement.value,
            "period": _period(program),
            "currency": program.currency,
            "sha": sha,
            "lines": [
                {"id": ln.id, "name": ln.name, "abbr": ln.abbr, "group": ln.group}
                for ln in program.lines
            ],
            "layers": [
                {
                    "id": ly.id,
                    "name": ly.name,
                    "appliesTo": list(ly.applies_to),
                    "attach": ly.attach,
                    "limit": ly.limit,
                    "premium": ly.premium,
                    "followsUnderlying": ly.follows_underlying,
                    "participants": [
                        {"carrier": p.carrier, "share_bps": p.share_bps}
                        for p in ly.participants
                    ],
                }
                for ly in program.layers
            ],
            "retentions": [
                {
                    "index": i,
                    "appliesTo": list(r.applies_to),
                    "type": r.type.value,
                    "amount": r.amount,
                    "aggregate": r.aggregate,
                    "vehicle": r.vehicle,
                }
                for i, r in enumerate(program.retentions)
            ],
            "sublimits": [
                {"index": i, "name": s.name, "amount": s.amount,
                 "appliesTo": list(s.applies_to)}
                for i, s in enumerate(program.sublimits)
            ],
        }

    @server.tool()
    async def program_view(name: str) -> dict[str, Any]:
        """Draw the tower as text. Gaps, overlaps, and column shape are
        visible here in a way they are not in a layer list."""
        from .render.ascii import render_ascii
        from .theme import load_theme

        path = programs.resolve(name)
        program = load_program(path)
        programs.note(path)
        diags = validate_program(program)
        return {
            "name": name,
            "view": render_ascii(
                program,
                load_theme(),  # no argument = the built-in default, as tests/test_ascii.py does
                colour=False,
                error_layers=frozenset(
                    str(d.ref[1]) for d in diags.errors if d.ref[0] == "layer"
                ),
                error_lines=frozenset(
                    str(d.ref[1]) for d in diags.errors if d.ref[0] == "line"
                ),
            ),
        }

    @server.tool()
    async def program_check(name: str) -> dict[str, Any]:
        """towerkit's validator on one program: what is wrong and where."""
        path = programs.resolve(name)
        program = load_program(path)
        programs.note(path)
        diags = validate_program(program)
        return {
            "name": name,
            "errors": _diag(diags.errors),
            "warnings": _diag(diags.warnings),
        }


def serve(roots: list[Path] | None = None) -> None:
    build_server(roots).run()  # stdio transport is the SDK's default
```

- [ ] **Step 5: Wire the CLI**

In `_build_parser`, after the `import` subparser:

```python
    p_mcp = sub.add_parser("mcp", help="stdio MCP server for design-level assist")
    p_mcp.add_argument(
        "--programs", type=Path, nargs="+", default=None,
        help="program roots to serve (default: ./programs)",
    )
    p_mcp.set_defaults(handler=_cmd_mcp)
```

And the handler:

```python
def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcpserver import serve

    serve(args.programs)
    return 0
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: PASS (7 tests, including the protocol round-trip)

If `_tool_manager.get_tool(name).fn` does not exist on the installed SDK, find the accessor by inspecting it — `uv run python -c "from mcp.server.mcpserver import MCPServer; s=MCPServer(name='x'); print(dir(s._tool_manager))"` — and adjust the `_tool` helper. bookkit's `test_mcpserver.py` calls its `_verb(conn, …)` helpers directly instead; if the accessor proves awkward, split each tool body into a module-level `_program_read(programs, name)` helper and have the tests call those, keeping the protocol round-trip as the wiring proof.

- [ ] **Step 7: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add pyproject.toml uv.lock install.sh src/towerkit/mcpserver.py src/towerkit/cli.py tests/test_mcpserver.py
git commit -m "mcp: towerctl mcp with the read tools — list, read, view, check"
```

---

### Task 6: The guarded write cycle, snapshots, and revert

The vehicle is `restack` — one tool, no arguments beyond the name, so the plumbing is what is under test.

**Files:**
- Modify: `src/towerkit/mcpserver.py`
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `Programs` (Task 5), `towerkit.edit` (Tasks 1-2).
- Produces:
  - `_write(programs: Programs, name: str, summary: str, mutate: Callable[[Program], None]) -> dict[str, Any]` — the one cycle every write tool goes through
  - `write_ref()` — `TKW-<YYYYMMDDTHHMMSS>-<4 hex>`
  - `snapshot(path, ref, pre_image)` / `restore(path, ref)`
  - Tools: `restack`, `program_revert_write`
  - Every write tool's return shape: `{"wrote": name, "summary": str, "write_ref": str, "errors": [...], "warnings": [...]}`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_mcpserver.py
from towerkit.model import load_program


class TestWriteCycle:
    def test_a_write_needs_a_read_first(self, roots) -> None:
        server = build_server(roots)
        with pytest.raises(ValueError, match="read"):
            _tool(server, "restack")("atomic-2026")

    def test_a_write_refuses_when_the_file_moved(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed on disk"):
            _tool(server, "restack")("atomic-2026")
        assert path.read_text("utf-8") == before.decode() + " "  # untouched by us

    def test_a_write_lands_canonically_and_re_arms_the_guard(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        out = _tool(server, "restack")("atomic-2026")
        assert out["write_ref"].startswith("TKW-")
        path = roots[0] / "atomic-2026.json"
        from towerkit.model import dumps_program
        assert path.read_text("utf-8") == dumps_program(load_program(path))
        _tool(server, "restack")("atomic-2026")  # guard re-armed; no raise

    def test_a_model_invalid_write_is_refused_and_the_file_is_untouched(
        self, roots
    ) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        from towerkit import mcpserver

        def poison(program):
            program.layers[0].applies_to = []   # min_length=1 → ValidationError

        with pytest.raises(ValueError, match="refused"):
            mcpserver._write(
                server._programs, "atomic-2026", "poison", poison
            )
        assert path.read_bytes() == before


class TestRevert:
    def test_revert_restores_byte_identically(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        ref = _tool(server, "restack")("atomic-2026")["write_ref"]
        _tool(server, "program_revert_write")(ref)
        assert path.read_bytes() == before

    def test_revert_refuses_after_a_later_edit(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        ref = _tool(server, "restack")("atomic-2026")["write_ref"]
        path = roots[0] / "atomic-2026.json"
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed since"):
            _tool(server, "program_revert_write")(ref)

    def test_snapshots_share_the_directory_with_bookkit_and_prune_only_their_own(
        self, roots
    ) -> None:
        server = build_server(roots)
        snapdir = roots[0] / ".mcp-snapshots"
        snapdir.mkdir(exist_ok=True)
        (snapdir / "MCP-abc.json").write_text("{}")   # bookkit's, not ours
        _tool(server, "program_read")("atomic-2026")
        for _ in range(3):
            _tool(server, "restack")("atomic-2026")
        assert (snapdir / "MCP-abc.json").exists()
        assert len(list(snapdir.glob("TKW-*.meta.json"))) == 3
```

The soft-validation behavior — errors reported, write still landing — needs a tool that can produce an error, so it is tested in Task 7 (`test_line_add_lands_and_reports_that_it_is_empty`). This task proves the plumbing: the drift refusal, the hard model gate, the canonical write, and the snapshots.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_mcpserver.py -k "Write or Revert" -q`
Expected: FAIL — no `restack` tool registered.

- [ ] **Step 3: Implement the cycle**

Add to `mcpserver.py`:

```python
import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from . import edit
from .model import dumps_program, loads_program

SNAPSHOT_KEEP = 20
_SNAPDIR = ".mcp-snapshots"
_PREFIX = "TKW-"


def write_ref() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{_PREFIX}{stamp}-{secrets.token_hex(2)}"


def _snapdir(path: Path) -> Path:
    return path.parent / _SNAPDIR


def snapshot(path: Path, ref: str, pre_image: bytes) -> None:
    """Record the pre-image and the post-write sha. Called AFTER a successful
    write, so a refused write leaves no debris. The directory is shared with
    bookkit's snapshots; each side globs only its own prefix."""
    snapdir = _snapdir(path)
    snapdir.mkdir(exist_ok=True)
    (snapdir / f"{ref}.json").write_bytes(pre_image)
    (snapdir / f"{ref}.meta.json").write_text(
        json.dumps({"path": str(path), "post_sha256": file_sha256(path)})
    )
    images = sorted(
        (p for p in snapdir.glob(f"{_PREFIX}*.json") if not p.name.endswith(".meta.json")),
        key=lambda p: p.stat().st_mtime,
    )
    for stale in images[:-SNAPSHOT_KEEP]:
        stale.unlink(missing_ok=True)
        (snapdir / f"{stale.stem}.meta.json").unlink(missing_ok=True)


def restore(path: Path, ref: str) -> None:
    """Put the pre-image back, only while the file still holds exactly what
    that write produced. Anything newer — the TUI, bookkit, a later write —
    makes the pre-image stale and this refuses."""
    snapdir = _snapdir(path)
    image, meta_file = snapdir / f"{ref}.json", snapdir / f"{ref}.meta.json"
    if not image.exists() or not meta_file.exists():
        raise ValueError(
            f"no snapshot for {ref} — it may have been pruned "
            f"(the last {SNAPSHOT_KEEP} writes are kept)"
        )
    meta = json.loads(meta_file.read_text())
    if str(path) != meta["path"]:
        raise ValueError(f"{ref} was a write to {meta['path']}, not this file")
    if file_sha256(path) != meta["post_sha256"]:
        raise ValueError(
            f"the file has changed since {ref} wrote it — a newer edit (the "
            f"towerkit editor, bookkit, or a later write) would be lost; revert "
            f"newer writes first"
        )
    path.write_bytes(image.read_bytes())


def _atomic_write(path: Path, text: str) -> None:
    """Same-directory temp + replace: a crash mid-write leaves the old file,
    never a truncated one."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write(
    programs: Programs,
    name: str,
    summary: str,
    mutate: Callable[[Program], None],
) -> dict[str, Any]:
    """One design write: drift check → load → mutate → hard-validate →
    canonical dump → atomic replace → snapshot → re-arm the guard.

    Two tiers of validation, on purpose. HARD: the mutation must produce a
    program that still models and still round-trips through the canonical
    dump — a file towerkit cannot load is never written. SOFT: semantic
    diagnostics do not block. A tower under construction is invalid by
    construction (a new line is line-empty until it carries a layer, a stack
    being built passes through line-gap), so errors come back in the result
    and the caller keeps building."""
    path = programs.resolve(name)
    known = programs.seen.get(str(path))
    if known is None:
        raise ValueError(
            f"{name} has not been read in this session — call program_read first "
            f"so there is a baseline to compare against"
        )
    if file_sha256(path) != known:
        raise ValueError(
            f"{name} changed on disk since you read it (the towerkit editor, "
            f"bookkit, or another tool) — re-read it and retry"
        )

    pre_image = path.read_bytes()
    program = load_program(path)
    try:
        mutate(program)
        edit.heal_follows(program)
        text = dumps_program(program)
        loads_program(text)  # proves the file we are about to write is loadable
    except (ValidationError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        raise ValueError(f"refused — nothing written: {exc}") from exc

    _atomic_write(path, text)
    ref = write_ref()
    snapshot(path, ref, pre_image)
    programs.note(path)
    diags = validate_program(program)
    return {
        "wrote": name,
        "summary": summary,
        "write_ref": ref,
        "errors": _diag(diags.errors),
        "warnings": _diag(diags.warnings),
    }
```

`_write` catches `ValueError` and re-raises it wrapped, so a caller cannot tell a resolution failure from a mutation failure by type — that is fine, both are tool errors, and the message distinguishes them.

In `build_server`, expose the resolver for tests and register the write tools:

```python
    server._programs = programs  # type: ignore[attr-defined]  # tests reach for it
    _register_read_tools(server, programs)
    _register_write_tools(server, programs)
```

```python
def _register_write_tools(server: MCPServer, programs: Programs) -> None:
    @server.tool()
    async def restack(name: str) -> dict[str, Any]:
        """Reseat every layer on top of what its lines already carry —
        one call heals gaps and overlaps after limit edits."""
        return _write(programs, name, "restacked the tower", edit.restack)

    @server.tool()
    async def program_revert_write(write_ref: str) -> dict[str, Any]:
        """Undo one write by restoring its pre-image — only while the file
        still holds exactly what that write produced."""
        for root in programs.roots:
            snapdir = root / _SNAPDIR
            meta_file = snapdir / f"{write_ref}.meta.json"
            if not meta_file.exists():
                continue
            target = Path(json.loads(meta_file.read_text())["path"])
            restore(target, write_ref)
            programs.note(target)
            return {"reverted": write_ref, "file": str(target)}
        raise ValueError(f"no snapshot for {write_ref} under the program roots")
```

The `server._programs` attribute is a test seam; if mypy strict objects to assigning an attribute on `MCPServer`, return the pair from a private `_build(roots)` and have `build_server` return only the server, with tests calling `_build`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: PASS, with `test_semantic_errors_ride_back_and_the_write_still_lands` reported as SKIPPED.

- [ ] **Step 5: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: the guarded write cycle — drift refusal, hard model gate, soft diagnostics, snapshots"
```

---

### Task 7: The design write tools

**Files:**
- Modify: `src/towerkit/mcpserver.py` (`_register_write_tools`)
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `_write` (Task 6), all of `towerkit.edit`.
- Produces: tools `line_add`, `line_edit`, `line_move`, `line_remove`, `retention_add`, `retention_edit`, `retention_remove`, `sublimit_add`, `sublimit_edit`, `sublimit_remove`, `layer_remove`, `layer_lines`, `layer_follows`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_mcpserver.py
class TestDesignTools:
    def test_line_add_lands_and_reports_that_it_is_empty(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        out = _tool(server, "line_add")("atomic-2026", "Cyber", abbr="CYB")
        assert out["added"] == "cyber"
        assert any(d["code"] == "line-empty" for d in out["errors"])
        read = _tool(server, "program_read")("atomic-2026")
        assert "cyber" in [ln["id"] for ln in read["lines"]]

    def test_line_remove_takes_stranded_layers_with_it(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        target = read["lines"][0]["id"]
        solo = [ly["id"] for ly in read["layers"] if ly["appliesTo"] == [target]]
        _tool(server, "line_remove")("atomic-2026", target)
        after = _tool(server, "program_read")("atomic-2026")
        assert target not in [ln["id"] for ln in after["lines"]]
        assert not [ly for ly in after["layers"] if ly["id"] in solo]

    def test_retention_add_parses_human_money(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _tool(server, "retention_add")(
            "atomic-2026", [line_id], "sir", "500k", aggregate="2m"
        )
        assert out["index"] == len(read["retentions"])
        after = _tool(server, "program_read")("atomic-2026")
        assert after["retentions"][out["index"]]["amount"] == 500_000
        assert after["retentions"][out["index"]]["aggregate"] == 2_000_000

    def test_retention_edit_guards_against_a_shifted_index(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        assert read["retentions"], "fixture must carry a retention"
        with pytest.raises(ValueError, match="expecting"):
            _tool(server, "retention_edit")(
                "atomic-2026", 0, amount="1m", expecting_lines=["ghost-line"]
            )

    def test_retention_edit_applies_when_the_guard_matches(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        current = read["retentions"][0]
        _tool(server, "retention_edit")(
            "atomic-2026", 0, amount="750k", expecting_lines=current["appliesTo"]
        )
        after = _tool(server, "program_read")("atomic-2026")
        assert after["retentions"][0]["amount"] == 750_000

    def test_layer_lines_rejects_an_unknown_line_and_writes_nothing(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="refused"):
            _tool(server, "layer_lines")("atomic-2026", read["layers"][0]["id"], ["ghost"])
        assert path.read_bytes() == before

    def test_layer_follows_heals_the_attachment(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        top = max(read["layers"], key=lambda ly: ly["attach"])
        _tool(server, "layer_follows")("atomic-2026", top["id"], True)
        after = _tool(server, "program_read")("atomic-2026")
        healed = next(ly for ly in after["layers"] if ly["id"] == top["id"])
        assert healed["followsUnderlying"] is True

    def test_sublimit_round_trip(self, roots) -> None:
        server = build_server(roots)
        read = _tool(server, "program_read")("atomic-2026")
        line_id = read["lines"][0]["id"]
        out = _tool(server, "sublimit_add")("atomic-2026", "Flood", "5m", [line_id])
        index = out["index"]
        _tool(server, "sublimit_edit")(
            "atomic-2026", index, amount="10m", expecting_name="Flood"
        )
        after = _tool(server, "program_read")("atomic-2026")
        assert after["sublimits"][index]["amount"] == 10_000_000
        _tool(server, "sublimit_remove")("atomic-2026", index, expecting_name="Flood")
        assert len(_tool(server, "program_read")("atomic-2026")["sublimits"]) == index
```

`test_line_add_lands_and_reports_that_it_is_empty` is the soft-validation test the spec turns on: a `line-empty` error comes back in the result **and** the line is in the file afterwards. If that test ever gets "fixed" by making the write refuse, the design decision has been reversed — re-read §4 of the spec before touching it.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_mcpserver.py -k Design -q`
Expected: FAIL — no `line_add` tool.

- [ ] **Step 3: Register the tools**

Add to `_register_write_tools`. `money.parse_money` and `money.parse_share` do the edge parsing; the `expecting_*` guards are checked against the loaded program *inside* the mutation, so a mismatch aborts before anything is written.

```python
    from .money import parse_money

    @server.tool()
    async def line_add(
        name: str, line_name: str, abbr: str | None = None, group: str | None = None
    ) -> dict[str, Any]:
        """Add a coverage line — a new column in the tower. It will report
        line-empty until a layer covers it."""
        added: list[str] = []

        def do(program: Program) -> None:
            added.append(edit.add_line(program, line_name, abbr, group).id)

        out = _write(programs, name, f"added line {line_name!r}", do)
        return {**out, "added": added[0]}

    @server.tool()
    async def line_edit(
        name: str,
        line_id: str,
        line_name: str | None = None,
        abbr: str | None = None,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Rename a line (the id follows the name, cascading everywhere it is
        referenced), set its abbreviation, or set its coverage group."""
        result: list[str] = []

        def do(program: Program) -> None:
            current = line_id
            if line_name is not None:
                current = edit.rename_line(program, current, line_name).id
            if group is not None:
                edit.set_line_group(program, current, group or None)
            if abbr is not None:
                next(ln for ln in program.lines if ln.id == current).abbr = abbr or None
            result.append(current)

        out = _write(programs, name, f"edited line {line_id}", do)
        return {**out, "line_id": result[0]}

    @server.tool()
    async def line_move(name: str, line_id: str, delta: int) -> dict[str, Any]:
        """Move a line left (-1) or right (+1). Array order is column order."""
        return _write(
            programs, name, f"moved line {line_id} by {delta}",
            lambda program: edit.move_line(program, line_id, delta),
        )

    @server.tool()
    async def line_remove(name: str, line_id: str) -> dict[str, Any]:
        """Remove a line. Anything left covering nothing — layers, retentions,
        sublimits — goes with it."""
        return _write(
            programs, name, f"removed line {line_id}",
            lambda program: edit.remove_line(program, line_id),
        )

    @server.tool()
    async def retention_add(
        name: str,
        applies_to: list[str],
        type: str,
        amount: str,
        aggregate: str | None = None,
        vehicle: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """What the insured pays below the tower. `type` is deductible, sir,
        or captive; a captive needs a named vehicle. Money in dollars
        ('500k', '$1,000,000')."""
        index: list[int] = []

        def do(program: Program) -> None:
            edit.add_retention(
                program, applies_to, type, parse_money(amount),
                aggregate=parse_money(aggregate) if aggregate else None,
                vehicle=vehicle, notes=notes,
            )
            index.append(len(program.retentions) - 1)

        out = _write(programs, name, f"added a {type} retention", do)
        return {**out, "index": index[0]}

    @server.tool()
    async def retention_edit(
        name: str,
        index: int,
        expecting_lines: list[str],
        applies_to: list[str] | None = None,
        type: str | None = None,
        amount: str | None = None,
        aggregate: str | None = None,
        vehicle: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Edit the retention at `index` from a program_read. Retentions have
        no ids, so `expecting_lines` guards against the list having shifted:
        it must match what the read reported."""
        def do(program: Program) -> None:
            _guard_index(program.retentions, index, "retention")
            actual = list(program.retentions[index].applies_to)
            if actual != list(expecting_lines):
                raise ValueError(
                    f"retention {index} now applies to {actual}, not "
                    f"{list(expecting_lines)} — re-read and retry"
                )
            edit.edit_retention(
                program, index, applies_to=applies_to, type=type,
                amount=parse_money(amount) if amount else None,
                aggregate=parse_money(aggregate) if aggregate else None,
                vehicle=vehicle, notes=notes,
            )

        return _write(programs, name, f"edited retention {index}", do)

    @server.tool()
    async def retention_remove(
        name: str, index: int, expecting_lines: list[str]
    ) -> dict[str, Any]:
        """Remove the retention at `index`, guarded by the lines it covers."""
        def do(program: Program) -> None:
            _guard_index(program.retentions, index, "retention")
            actual = list(program.retentions[index].applies_to)
            if actual != list(expecting_lines):
                raise ValueError(
                    f"retention {index} now applies to {actual}, not "
                    f"{list(expecting_lines)} — re-read and retry"
                )
            edit.remove_retention(program, index)

        return _write(programs, name, f"removed retention {index}", do)

    @server.tool()
    async def sublimit_add(
        name: str, sublimit_name: str, amount: str, applies_to: list[str],
        notes: str | None = None,
    ) -> dict[str, Any]:
        """A named cap inside the tower — flood, quake, a per-location limit."""
        index: list[int] = []

        def do(program: Program) -> None:
            edit.add_sublimit(
                program, sublimit_name, parse_money(amount), applies_to, notes
            )
            index.append(len(program.sublimits) - 1)

        out = _write(programs, name, f"added sublimit {sublimit_name!r}", do)
        return {**out, "index": index[0]}

    @server.tool()
    async def sublimit_edit(
        name: str, index: int, expecting_name: str,
        sublimit_name: str | None = None, amount: str | None = None,
        applies_to: list[str] | None = None, notes: str | None = None,
    ) -> dict[str, Any]:
        """Edit the sublimit at `index`, guarded by the name the read reported."""
        def do(program: Program) -> None:
            _guard_index(program.sublimits, index, "sublimit")
            actual = program.sublimits[index].name
            if actual != expecting_name:
                raise ValueError(
                    f"sublimit {index} is now {actual!r}, not {expecting_name!r} "
                    f"— re-read and retry"
                )
            edit.edit_sublimit(
                program, index, name=sublimit_name,
                amount=parse_money(amount) if amount else None,
                applies_to=applies_to, notes=notes,
            )

        return _write(programs, name, f"edited sublimit {index}", do)

    @server.tool()
    async def sublimit_remove(
        name: str, index: int, expecting_name: str
    ) -> dict[str, Any]:
        """Remove the sublimit at `index`, guarded by its name."""
        def do(program: Program) -> None:
            _guard_index(program.sublimits, index, "sublimit")
            actual = program.sublimits[index].name
            if actual != expecting_name:
                raise ValueError(
                    f"sublimit {index} is now {actual!r}, not {expecting_name!r} "
                    f"— re-read and retry"
                )
            edit.remove_sublimit(program, index)

        return _write(programs, name, f"removed sublimit {index}", do)

    @server.tool()
    async def layer_remove(name: str, layer_id: str) -> dict[str, Any]:
        """Remove a layer from the stack. Expect line-gap until you restack."""
        return _write(
            programs, name, f"removed layer {layer_id}",
            lambda program: edit.remove_layer(program, layer_id),
        )

    @server.tool()
    async def layer_lines(
        name: str, layer_id: str, line_ids: list[str]
    ) -> dict[str, Any]:
        """Set which coverage lines a layer spans — how wide its block is."""
        return _write(
            programs, name, f"set {layer_id} to cover {', '.join(line_ids)}",
            lambda program: edit.set_applies_to(program, layer_id, line_ids),
        )

    @server.tool()
    async def layer_follows(
        name: str, layer_id: str, follows: bool
    ) -> dict[str, Any]:
        """Mark a layer as following the underlying: its attachment becomes
        derived state, reseating itself on the highest underlying top. This is
        how a shared umbrella sits over columns with differing limits."""
        return _write(
            programs, name, f"set followsUnderlying={follows} on {layer_id}",
            lambda program: edit.set_follows_underlying(program, layer_id, follows),
        )
```

And the shared index guard, at module level:

```python
def _guard_index(items: list[Any], index: int, what: str) -> None:
    if not 0 <= index < len(items):
        raise ValueError(
            f"no {what} at index {index} — there are {len(items)}; re-read and retry"
        )
```

Note the parameter naming: the first argument of every tool is `name` (the program), so a line's own name is `line_name` and a sublimit's is `sublimit_name`. Keep this consistent — the model reads these signatures.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: PASS, with no skips remaining.

- [ ] **Step 5: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: design tools — lines, retentions, sublimits, layer structure"
```

---

### Task 8: Creation tools

**Files:**
- Modify: `src/towerkit/mcpserver.py`
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `Programs.resolve(name, must_exist=False)`, `towerkit.dates.parse_flexible_date`, `Program.clone_as_renewal`.
- Produces: tools `program_create`, `program_clone_renewal`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_mcpserver.py
class TestCreation:
    def test_create_writes_a_canonical_file_and_reports_it_is_empty(self, roots) -> None:
        server = build_server(roots)
        out = _tool(server, "program_create")(
            "prospect-2027", insured="Prospect Co", program="Casualty",
            placement="proposed", period_from="1/1/27", period_to="1/1/28",
            lines=["General Liability", "Auto Liability"],
        )
        path = roots[0] / "prospect-2027.json"
        assert path.exists()
        from towerkit.model import dumps_program
        assert path.read_text("utf-8") == dumps_program(load_program(path))
        assert {d["code"] for d in out["errors"]} == {"line-empty"}
        assert load_program(path).period.start.isoformat() == "2027-01-01"

    def test_create_refuses_an_existing_file(self, roots) -> None:
        server = build_server(roots)
        with pytest.raises(ValueError, match="already exists"):
            _tool(server, "program_create")(
                "atomic-2026", insured="X", program="Y", placement="bound",
                period_from="1/1/26", period_to="1/1/27", lines=["GL"],
            )

    def test_create_refuses_outside_the_roots(self, roots) -> None:
        server = build_server(roots)
        with pytest.raises(ValueError, match="outside"):
            _tool(server, "program_create")(
                "../escape", insured="X", program="Y", placement="bound",
                period_from="1/1/26", period_to="1/1/27", lines=["GL"],
            )

    def test_create_rejects_an_unreadable_date(self, roots) -> None:
        server = build_server(roots)
        with pytest.raises(ValueError, match="date"):
            _tool(server, "program_create")(
                "bad-dates", insured="X", program="Y", placement="bound",
                period_from="whenever", period_to="1/1/27", lines=["GL"],
            )
        assert not (roots[0] / "bad-dates.json").exists()

    def test_clone_as_renewal_bumps_the_period_and_proposes(self, roots) -> None:
        server = build_server(roots)
        _tool(server, "program_clone_renewal")("atomic-2026", "atomic-2027")
        source = load_program(roots[0] / "atomic-2026.json")
        clone = load_program(roots[0] / "atomic-2027.json")
        assert clone.period.start.year == source.period.start.year + 1
        assert clone.placement.value == "proposed"
        assert len(clone.layers) == len(source.layers)

    def test_a_created_program_is_immediately_writable(self, roots) -> None:
        """Creation notes the sha, so the caller does not have to read back
        a file it just wrote before editing it."""
        server = build_server(roots)
        _tool(server, "program_create")(
            "fresh-2027", insured="Fresh", program="Property", placement="proposed",
            period_from="1/1/27", period_to="1/1/28", lines=["Property"],
        )
        _tool(server, "line_add")("fresh-2027", "Cyber")  # no raise
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_mcpserver.py -k Creation -q`
Expected: FAIL — no `program_create` tool.

- [ ] **Step 3: Implement**

```python
    from .dates import parse_flexible_date
    from .model import Line, Period, Placement

    def _date(value: str, field: str) -> "date":
        parsed = parse_flexible_date(value)
        if parsed is None:
            raise ValueError(f"can't read {value!r} as a date for {field}")
        return parsed

    @server.tool()
    async def program_create(
        name: str,
        insured: str,
        program: str,
        placement: str,
        period_from: str,
        period_to: str,
        lines: list[str],
    ) -> dict[str, Any]:
        """Start a program from nothing: an insured, a period, and its
        coverage lines. It will report line-empty for every line until layers
        cover them — that is expected, keep building. `placement` is bound or
        proposed. Numeric dates are month/day/year."""
        path = programs.resolve(name, must_exist=False)
        if path.exists():
            raise ValueError(f"{name} already exists — edit it, or pick another name")
        fresh = Program(
            insured=insured,
            program=program,
            placement=Placement(placement),
            period=Period(
                start=_date(period_from, "period_from"),
                end=_date(period_to, "period_to"),
            ),
            lines=[],
        )
        for line_name in lines:
            edit.add_line(fresh, line_name)
        text = dumps_program(fresh)
        loads_program(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, text)
        programs.note(path)
        diags = validate_program(fresh)
        return {
            "created": name,
            "file": str(path),
            "lines": [ln.id for ln in fresh.lines],
            "errors": _diag(diags.errors),
            "warnings": _diag(diags.warnings),
        }

    @server.tool()
    async def program_clone_renewal(source: str, dest: str) -> dict[str, Any]:
        """Copy a program forward a year as a proposed renewal — the most
        common starting point for next year's design."""
        source_path = programs.resolve(source)
        dest_path = programs.resolve(dest, must_exist=False)
        if dest_path.exists():
            raise ValueError(f"{dest} already exists — pick another name")
        clone = load_program(source_path).clone_as_renewal()
        text = dumps_program(clone)
        loads_program(text)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest_path, text)
        programs.note(dest_path)
        diags = validate_program(clone)
        return {
            "created": dest,
            "from": source,
            "file": str(dest_path),
            "period": _period(clone),
            "errors": _diag(diags.errors),
            "warnings": _diag(diags.warnings),
        }
```

`Programs.resolve(name, must_exist=False)` must return the path under the **first** root when the file does not exist yet, and raise on anything outside the roots — re-read the Task 5 implementation and confirm that branch behaves that way; adjust it if the loop falls through without raising.

Import `date` from `datetime` for the `_date` annotation.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: PASS

- [ ] **Step 5: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: program_create and program_clone_renewal"
```

---

### Task 9: The post-write re-sync hook

**Files:**
- Modify: `src/towerkit/mcpserver.py`
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `_write`, `program_create`, `program_clone_renewal`.
- Produces: `run_hook(path: Path) -> str` — returns `"not configured"`, `"ok"`, or `"failed: …"`. Every write result grows a `resync` key.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_mcpserver.py
class TestResyncHook:
    def test_no_hook_configured_is_reported_not_hidden(self, roots, monkeypatch) -> None:
        monkeypatch.delenv("TOWERKIT_POST_WRITE_CMD", raising=False)
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        assert _tool(server, "restack")("atomic-2026")["resync"] == "not configured"

    def test_the_hook_runs_with_the_path_substituted(
        self, roots, tmp_path, monkeypatch
    ) -> None:
        marker = tmp_path / "hooked.txt"
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD",
            f"{sys.executable} -c "
            f"\"import sys,pathlib; pathlib.Path(r'{marker}').write_text(sys.argv[1])\" {{path}}",
        )
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        out = _tool(server, "restack")("atomic-2026")
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("atomic-2026.json")

    def test_a_failing_hook_never_undoes_the_write(
        self, roots, monkeypatch
    ) -> None:
        monkeypatch.setenv("TOWERKIT_POST_WRITE_CMD", f"{sys.executable} -c \"raise SystemExit(3)\"")
        server = build_server(roots)
        _tool(server, "program_read")("atomic-2026")
        path = roots[0] / "atomic-2026.json"
        before = path.read_bytes()
        out = _tool(server, "restack")("atomic-2026")
        assert out["resync"].startswith("failed")
        assert path.read_bytes() != before or out["write_ref"]  # the write stands
        assert load_program(path)  # and the file is still loadable

    def test_creation_runs_the_hook_too(self, roots, tmp_path, monkeypatch) -> None:
        marker = tmp_path / "created.txt"
        monkeypatch.setenv(
            "TOWERKIT_POST_WRITE_CMD",
            f"{sys.executable} -c "
            f"\"import sys,pathlib; pathlib.Path(r'{marker}').write_text(sys.argv[1])\" {{path}}",
        )
        server = build_server(roots)
        out = _tool(server, "program_create")(
            "hooked-2027", insured="H", program="P", placement="proposed",
            period_from="1/1/27", period_to="1/1/28", lines=["GL"],
        )
        assert out["resync"] == "ok"
        assert marker.read_text().endswith("hooked-2027.json")
```

Add `import sys` to the test module's imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_mcpserver.py -k Resync -q`
Expected: FAIL — `KeyError: 'resync'`

- [ ] **Step 3: Implement**

```python
import shlex
import subprocess

HOOK_ENV = "TOWERKIT_POST_WRITE_CMD"
HOOK_TIMEOUT = 30


def run_hook(path: Path) -> str:
    """Best-effort notification that a program file changed.

    towerkit does not know what is downstream — bookkit sets this to
    `bookctl sync --path {path}` so its projection cache follows. It NEVER
    fails the write: by the time this runs the file on disk is already
    correct, and rolling a good write back over a hook failure would be a
    worse lie than a stale cache."""
    template = os.environ.get(HOOK_ENV)
    if not template:
        return "not configured"
    command = [part.replace("{path}", str(path)) for part in shlex.split(template)]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=HOOK_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return f"failed: exit {done.returncode}" + (f" — {detail[-1]}" if detail else "")
    return "ok"
```

In `_write`, after `programs.note(path)`, add `"resync": run_hook(path),` to the returned dict. Do the same in `program_create` and `program_clone_renewal`, after their `programs.note(...)` calls.

Note for `shlex.split`: the template is split *before* substitution, so a path containing spaces stays one argument. Splitting after substitution would break on `/Users/.../My Programs/x.json`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mcpserver.py -q`
Expected: PASS

- [ ] **Step 5: Document it in the README**

Add to towerkit's README, under the CLI section:

```markdown
### `towerctl mcp`

A stdio MCP server for design-level assist: coverage lines, retentions,
sublimits, and the shape of the layer stack.

    towerctl mcp --programs ~/programs

Validation errors do not block writes — a tower under construction is
invalid by construction — so they come back in each tool's result instead.
Writes refuse against a file the session has not read, or one that changed
since it read it.

Set `TOWERKIT_POST_WRITE_CMD` to have something re-read a file after every
write; `{path}` is substituted:

    export TOWERKIT_POST_WRITE_CMD='bookctl sync --path {path}'
```

- [ ] **Step 6: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate.txt
git add src/towerkit/mcpserver.py tests/test_mcpserver.py README.md
git commit -m "mcp: best-effort post-write hook, so a projection cache can follow"
```

---

### Task 10: bookkit — `bookctl sync --path FILE`

**This task is in the bookkit repo.** Make a worktree for it: `git worktree add -b feat/sync-one-path .claude/worktrees/sync-one-path main` from `/Users/grantgreeson/Developer/bookkit`. Gates are bookkit's own (`uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`).

**Files:**
- Modify: `src/bookkit/cli.py` (`sync` subparser ~43-46, and its handler)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `bookkit.sync.project(conn, path)` — already exists and takes a single path.
- Produces: `bookctl sync --path FILE` projecting exactly one file; exit 0 on success, 1 when the file fails to project.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py` points `main()` at a database through the `BOOKKIT_DB` environment variable (the `cli_db` fixture) and passes no `--db` flag — follow that. Seeded, already-linked program files come from `seed.seed(conn, today=…, programs_dir=…)`, exactly as `tests/test_sync.py`'s `synced` fixture does; it writes three linked programs including `atomic-casualty.json`.

```python
# append to bookkit tests/test_cli.py
from datetime import date


def _seeded_programs(cli_db: Path, tmp_path: Path) -> Path:
    """Three linked program files plus the book that knows about them."""
    from bookkit import seed

    main(["init"])
    programs = tmp_path / "programs"
    conn = db.connect(cli_db)
    seed.seed(conn, today=date(2026, 8, 11), programs_dir=programs)
    conn.close()
    return programs


def test_sync_one_path_projects_only_that_file(cli_db: Path, tmp_path: Path, capsys) -> None:
    """The towerkit MCP's post-write hook calls this per file — a full roots
    scan after every design edit would be absurd."""
    programs = _seeded_programs(cli_db, tmp_path)
    target = programs / "atomic-casualty.json"

    assert main(["sync", "--path", str(target)]) == 0
    out = capsys.readouterr().out
    assert "atomic-casualty.json" in out
    assert "✓" in out


def test_sync_one_path_reports_an_unlinked_file_without_crashing(
    cli_db: Path, tmp_path: Path, capsys
) -> None:
    import shutil

    programs = _seeded_programs(cli_db, tmp_path)
    orphan = tmp_path / "orphan.json"
    shutil.copy(programs / "atomic-casualty.json", orphan)

    assert main(["sync", "--path", str(orphan)]) == 1
    assert "link" in capsys.readouterr().out.lower()
```

If `seed.seed`'s signature differs, read `src/bookkit/seed.py` and match it — the `synced` fixture in `tests/test_sync.py` is the working call site.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -k sync_one_path -q`
Expected: FAIL — `unrecognized arguments: --path`

- [ ] **Step 3: Implement**

In the `sync` subparser:

```python
    sync_p.add_argument("--path", type=Path, default=None,
                        help="project exactly one file instead of scanning the roots")
```

bookkit dispatches `sync` inline, not through a handler function — the block is `if args.command == "sync":` at `src/bookkit/cli.py:142`. Insert the single-path branch at the top of that block, before `roots` is resolved, so `--path` never needs configured roots:

```python
    if args.command == "sync":
        from . import sync

        if args.path is not None:
            try:
                diags = sync.project(conn, Path(args.path))
            except sync.AmbiguousPlacement as exc:
                print(f"✗ {args.path}: {exc} — confirm it in the review queue")
                return 1
            for diag in diags.items:
                print(f"  {diag}")
            print(("✓ " if diags.ok else "✗ ") + str(args.path))
            return 0 if diags.ok else 1

        roots = (
            ...  # unchanged
        )
```

An unlinked file does not raise — `sync.project` returns a diagnostic with code `unlinked` and the message "no confirmed account link — confirm in review queue", which the loop above prints and which the second test asserts on. Confirm `AmbiguousPlacement` is exported from `bookkit.sync` under that name before writing the `except`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Full gate and commit**

```bash
uv run pytest -q > /tmp/gate-bookkit.txt 2>&1 && uv run mypy src && uv run ruff check src tests && tail -3 /tmp/gate-bookkit.txt
git add src/bookkit/cli.py tests/test_cli.py
git commit -m "cli: bookctl sync --path — project one file, for towerkit's post-write hook"
```

---

## Final verification

- [ ] Both suites green: towerkit `uv run pytest -q`, bookkit `uv run pytest -q`
- [ ] `uv run mypy src` clean in both
- [ ] `uv run ruff check src tests` clean in both
- [ ] End-to-end by hand, from the towerkit worktree:

```bash
export TOWERKIT_POST_WRITE_CMD='bookctl sync --path {path}'
uv run towerctl mcp --programs ./programs
```

Then from an MCP client: `program_list` → `program_read atomic-2026` → `program_view atomic-2026` → `line_add atomic-2026 "Cyber"` (expect a `line-empty` error in the result and the write landing) → `program_revert_write <ref>` (expect the file byte-identical again).

- [ ] Open the same program in `towerctl edit`, write to it over MCP, then press ctrl+s in the editor — the stale-file modal must appear.
