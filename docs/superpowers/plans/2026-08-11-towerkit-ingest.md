# towerkit.ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build programs from schedules — pasted tower text or canonical tabular rows — plus `towerctl import` and `towerctl template` CLI verbs.

**Architecture:** A new `ingest` module produces `DraftProgram` (Program-shaped, may be incomplete, carries `Diagnostics`); `to_program()` refuses while errors remain. Callers map their own headers to `CANONICAL_FIELDS` before calling; carrier names stay verbatim strings — alias resolution is the caller's job (bookkit's, later). Template export and row import share one column registry so they round-trip by construction.

**Tech Stack:** Python 3.11+, pydantic v2 (existing `model.py`), openpyxl (existing dep), pytest via `uv run pytest`.

## Global Constraints

- Repo: `/Users/grantgreeson/Developer/towerkit` — all paths below are relative to it. Commits go to this repo.
- Money is integer **whole dollars** everywhere in towerkit (existing rule; `parse_money` enforces it).
- Shares are integer basis points in memory (`Participant.share_bps`); percent semantics for text entry ("60" and "60%" both mean 60%).
- `ingest.py` is pure core → **mypy strict applies in full** (only `towerkit.tui.*` / `towerkit.render.*` are relaxed). Run `uv run mypy src` before each commit.
- Ruff: line length 100, rules E,F,I,W,UP,B. Run `uv run ruff check src tests`.
- No new dependencies. Canonical serialisation (`dump_program` key order) is untouched.
- Every test run: `uv run pytest <file>::<test> -v` from the repo root.

---

### Task 1: `parse_share` in money.py

Percent-text → basis points. Lives in towerkit because tower grammar needs it; bookkit's `parse_share_bps` will later delegate here (bookkit plan, not this one).

**Files:**
- Modify: `src/towerkit/money.py` (append after `format_share`)
- Test: `tests/test_units.py` (append)

**Interfaces:**
- Produces: `parse_share(text: str) -> int` — `'60%' | '60' | '12.5'` → bps (6000, 6000, 1250). Raises `MoneyParseError` on non-numeric, sub-basis-point precision, or outside (0, 100].

- [ ] **Step 1: Write the failing tests** (append to `tests/test_units.py`)

```python
import pytest

from towerkit.money import MoneyParseError, parse_share


@pytest.mark.parametrize(
    ("text", "bps"),
    [("60%", 6000), ("60", 6000), ("12.5%", 1250), ("100", 10_000), ("0.5", 50)],
)
def test_parse_share(text: str, bps: int) -> None:
    assert parse_share(text) == bps


@pytest.mark.parametrize("text", ["", "sixty", "0", "101", "12.345", "-5"])
def test_parse_share_rejects(text: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_share(text)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_units.py -k parse_share -v`
Expected: FAIL — `ImportError: cannot import name 'parse_share'`

- [ ] **Step 3: Implement** (append to `src/towerkit/money.py`; `Decimal`/`InvalidOperation` are already imported at top of the module)

```python
def parse_share(text: str) -> int:
    """'60%', '60', '12.5' → basis points. Everything is read as a PERCENT
    (the % sign optional) — brokers speak percent, and one consistent rule
    beats guessing whether 0.25 meant a quarter share."""
    cleaned = text.strip().rstrip("%").strip()
    try:
        pct = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MoneyParseError(f"cannot read a share from {text!r}") from exc
    scaled = pct * 100  # percent → bps
    if scaled != scaled.to_integral_value():
        raise MoneyParseError(f"{text!r} has sub-basis-point precision")
    bps = int(scaled)
    if not 0 < bps <= 10_000:
        raise MoneyParseError(f"{text!r} is not a share between 0% and 100%")
    return bps
```

- [ ] **Step 4: Verify pass + lint**

Run: `uv run pytest tests/test_units.py -k parse_share -v` → PASS
Run: `uv run mypy src && uv run ruff check src tests` → clean

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/money.py tests/test_units.py
git commit -m "money: parse_share — percent text to basis points"
```

---

### Task 2: `DraftProgram` and `to_program()`

**Files:**
- Create: `src/towerkit/ingest.py`
- Test: `tests/test_ingest.py` (new)

**Interfaces:**
- Consumes: `model.Program/Layer/Line/Participant/Period/Placement/Retention/RetentionType`, `validate.Diagnostics/ProgramInvalidError/validate_program` (all existing; note `populate_by_name=True`, so snake_case kwargs like `applies_to=` work).
- Produces:
  - `CANONICAL_FIELDS: tuple[str, ...] = ("layer", "line", "limit", "attachment", "carrier", "share", "premium", "inception", "expiry", "policy_number")`
  - `DraftProgram` dataclass: fields `insured: str = ""`, `program: str = ""`, `placement: Placement = Placement.PROPOSED`, `period: Period | None = None`, `currency: str = "USD"`, `lines: list[Line]`, `layers: list[Layer]`, `retentions: list[Retention]`, `diagnostics: Diagnostics` (all list/Diagnostics fields default-factory).
  - `DraftProgram.to_program() -> Program` — raises `ProgramInvalidError` when insured/program/period missing or `validate_program` reports errors.

- [ ] **Step 1: Write the failing tests** (create `tests/test_ingest.py`)

```python
"""ingest: drafts, tower-text parsing, tabular rows, template round-trips."""

from datetime import date

import pytest

from towerkit.ingest import DraftProgram
from towerkit.model import Layer, Line, Participant, Period, Placement
from towerkit.validate import ProgramInvalidError


def _complete_draft() -> DraftProgram:
    draft = DraftProgram(insured="Atomic Industries", program="Property")
    draft.period = Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    draft.lines = [Line(id="cover", name="Property")]
    draft.layers = [
        Layer(
            id="primary", name="Primary", applies_to=["cover"], attach=0,
            limit=10_000_000,
            participants=[Participant(carrier="Chubb", share_bps=10_000)],
        )
    ]
    return draft


def test_complete_draft_becomes_program() -> None:
    program = _complete_draft().to_program()
    assert program.insured == "Atomic Industries"
    assert program.placement is Placement.PROPOSED
    assert program.layers[0].limit == 10_000_000


def test_draft_missing_period_refuses() -> None:
    draft = _complete_draft()
    draft.period = None
    with pytest.raises(ProgramInvalidError) as exc:
        draft.to_program()
    assert any("period" in d.message for d in exc.value.diagnostics.errors)


def test_draft_missing_insured_refuses() -> None:
    draft = _complete_draft()
    draft.insured = "  "
    with pytest.raises(ProgramInvalidError):
        draft.to_program()


def test_draft_runs_full_validation() -> None:
    draft = _complete_draft()
    draft.layers[0] = draft.layers[0].model_copy(update={"applies_to": ["nope"]})
    with pytest.raises(ProgramInvalidError):
        draft.to_program()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'towerkit.ingest'`

- [ ] **Step 3: Implement** (create `src/towerkit/ingest.py`)

```python
"""Build Programs from schedules: pasted tower text or canonical tabular rows.

The seam rule (bookkit spec 2026-08-11): callers map their own messy headers
to CANONICAL_FIELDS before calling; this module decides what the tower MEANS.
Carrier names are kept verbatim — alias resolution is the caller's job.

Drafts may be incomplete: parse what you can, surface what you couldn't via
Diagnostics. `to_program()` refuses while errors remain, keeping the strict
Program model free of half-parsed states.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Layer, Line, Period, Placement, Program, Retention
from .validate import Diagnostics, ProgramInvalidError, validate_program

CANONICAL_FIELDS: tuple[str, ...] = (
    "layer", "line", "limit", "attachment", "carrier", "share",
    "premium", "inception", "expiry", "policy_number",
)


@dataclass
class DraftProgram:
    """A Program in the making — same shape, laxer rules, plus diagnostics."""

    insured: str = ""
    program: str = ""
    placement: Placement = Placement.PROPOSED
    period: Period | None = None
    currency: str = "USD"
    lines: list[Line] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    retentions: list[Retention] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    def to_program(self) -> Program:
        gate = Diagnostics()
        if not self.insured.strip():
            gate.error("draft.insured", "insured name is required")
        if not self.program.strip():
            gate.error("draft.program", "program name is required")
        if self.period is None:
            gate.error("draft.period", "policy period (inception and expiry) is required")
        if not gate.ok:
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
        if not diags.ok:
            raise ProgramInvalidError(diags, source="draft")
        return program
```

- [ ] **Step 4: Verify pass + lint**

Run: `uv run pytest tests/test_ingest.py -v` → PASS
Run: `uv run mypy src && uv run ruff check src tests` → clean
(If `Diagnostics` lacks a no-arg constructor, check `src/towerkit/validate.py:48` and use whatever its dataclass default is — the field default-factory must produce an empty Diagnostics.)

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/ingest.py tests/test_ingest.py
git commit -m "ingest: DraftProgram — permissive build target with a strict exit"
```

---

### Task 3: `parse_tower` — pasted schedule text

**Files:**
- Modify: `src/towerkit/ingest.py` (append)
- Test: `tests/test_ingest.py` (append)

**Interfaces:**
- Consumes: Task 2's `DraftProgram`; `money.parse_money/parse_share/format_money_compact` (Task 1); `model.Participant/Retention/RetentionType`.
- Produces: `parse_tower(text: str, *, insured: str = "", program: str = "") -> DraftProgram`.
  Grammar v1, line-oriented; segments split on em-dash, spaced hyphen, or pipe:
  - Layer: `primary <money>` or `<money> xs <money>`, then participants (`Carrier 60%`, comma-separated; a single carrier without a share means 100%), then optional premium.
  - Retention: `sir <money>` | `deductible <money>` | `ded <money>` | `retention <money>` (last reads as deductible with a warning).
  - Unparseable lines become error diagnostics with their line number; parsing continues.
  - One synthesized coverage `Line(id="cover", name=<program or "Coverage">)` with a warning — pasted schedules never name lines.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ingest.py`)

```python
from towerkit.ingest import parse_tower
from towerkit.model import RetentionType

PASTE = """
Primary 10M — Chubb 100% — 250,000
15M xs 10M — AXA XL 60%, Sompo 40% — 180k
SIR 500k
"""


def test_parse_tower_layers() -> None:
    draft = parse_tower(PASTE, insured="Atomic Industries", program="Property")
    assert [layer.attach for layer in draft.layers] == [0, 10_000_000]
    assert draft.layers[0].limit == 10_000_000
    assert draft.layers[0].premium == 250_000
    assert draft.layers[1].participants[1].carrier == "Sompo"
    assert draft.layers[1].participants[1].share_bps == 4000
    assert draft.retentions[0].type is RetentionType.SIR
    assert draft.retentions[0].amount == 500_000


def test_parse_tower_single_carrier_means_full_share() -> None:
    draft = parse_tower("Primary 5M — Chubb")
    assert draft.layers[0].participants[0].share_bps == 10_000


def test_parse_tower_synthesizes_line_with_warning() -> None:
    draft = parse_tower("Primary 5M — Chubb", program="Casualty")
    assert draft.lines[0].name == "Casualty"
    assert any(d.code == "paste.line" for d in draft.diagnostics.warnings)


def test_parse_tower_bad_line_is_diagnostic_not_crash() -> None:
    draft = parse_tower("Primary 5M — Chubb\ntotal nonsense here")
    assert len(draft.layers) == 1
    assert any("line 2" in d.message for d in draft.diagnostics.errors)


def test_parse_tower_pipe_and_hyphen_separators() -> None:
    draft = parse_tower("5M xs 5M | Zurich 50%, Allianz 50% | 90k")
    assert draft.layers[0].signed_bps == 10_000
    assert draft.layers[0].premium == 90_000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -k parse_tower -v`
Expected: FAIL — `ImportError: cannot import name 'parse_tower'`

- [ ] **Step 3: Implement** (append to `src/towerkit/ingest.py`; add `import re` and extend the existing imports with `Participant`, `RetentionType`, `MoneyParseError`, `format_money_compact`, `parse_money`, `parse_share`)

```python
_SEGMENT_SPLIT = re.compile(r"\s+—\s*|\s+-\s+|\s*\|\s*")
_BAND_XS = re.compile(r"^(?P<limit>\S+)\s+xs\.?\s+(?P<attach>\S+)$", re.IGNORECASE)
_BAND_PRIMARY = re.compile(r"^primary\s+(?P<limit>\S+)$", re.IGNORECASE)
_RETENTION_LINE = re.compile(
    r"^(?P<kind>sir|deductible|ded|retention)\s+(?P<amount>\S+)$", re.IGNORECASE
)
_PARTICIPANT = re.compile(r"^(?P<carrier>.+?)\s+(?P<share>[\d.]+)\s*%$")


def parse_tower(text: str, *, insured: str = "", program: str = "") -> DraftProgram:
    draft = DraftProgram(insured=insured, program=program)
    cover = Line(id="cover", name=program.strip() or "Coverage")
    draft.lines = [cover]
    draft.diagnostics.warn(
        "paste.line", "no coverage lines in pasted text; synthesized one"
    )
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _try_retention(draft, stripped, lineno, cover.id):
            continue
        _try_layer(draft, stripped, lineno, cover.id)
    return draft


def _try_retention(draft: DraftProgram, text: str, lineno: int, line_id: str) -> bool:
    match = _RETENTION_LINE.match(text)
    if match is None:
        return False
    try:
        amount = parse_money(match.group("amount"))
    except MoneyParseError as exc:
        draft.diagnostics.error("paste.retention", f"line {lineno}: {exc}")
        return True
    kind = match.group("kind").lower()
    if kind == "retention":
        draft.diagnostics.warn(
            "paste.retention", f"line {lineno}: 'retention' read as a deductible"
        )
    rtype = RetentionType.SIR if kind == "sir" else RetentionType.DEDUCTIBLE
    draft.retentions.append(Retention(applies_to=[line_id], type=rtype, amount=amount))
    return True


def _try_layer(draft: DraftProgram, text: str, lineno: int, line_id: str) -> None:
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(text) if s.strip()]
    band = _parse_band(segments[0])
    if band is None:
        draft.diagnostics.error(
            "paste.layer", f"line {lineno}: cannot read a layer from {text!r}"
        )
        return
    attach, limit = band
    participants = (
        _parse_participants(draft, segments[1], lineno) if len(segments) > 1 else []
    )
    premium: int | None = None
    if len(segments) > 2:
        try:
            premium = parse_money(segments[2])
        except MoneyParseError as exc:
            draft.diagnostics.error("paste.premium", f"line {lineno}: {exc}")
    name = (
        "Primary"
        if attach == 0
        else f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
    )
    draft.layers.append(
        Layer(
            id=f"layer-{len(draft.layers) + 1}", name=name, applies_to=[line_id],
            attach=attach, limit=limit, premium=premium, participants=participants,
        )
    )


def _parse_band(segment: str) -> tuple[int, int] | None:
    match = _BAND_XS.match(segment)
    if match:
        try:
            return parse_money(match.group("attach")), parse_money(match.group("limit"))
        except MoneyParseError:
            return None
    match = _BAND_PRIMARY.match(segment)
    if match:
        try:
            return 0, parse_money(match.group("limit"))
        except MoneyParseError:
            return None
    return None


def _parse_participants(
    draft: DraftProgram, segment: str, lineno: int
) -> list[Participant]:
    out: list[Participant] = []
    entries = [entry.strip() for entry in segment.split(",") if entry.strip()]
    for entry in entries:
        match = _PARTICIPANT.match(entry)
        if match:
            try:
                share = parse_share(match.group("share"))
            except MoneyParseError as exc:
                draft.diagnostics.error("paste.share", f"line {lineno}: {exc}")
                continue
            out.append(Participant(carrier=match.group("carrier").strip(), share_bps=share))
        else:
            out.append(Participant(carrier=entry, share_bps=0))
    if len(out) == 1 and out[0].share_bps == 0:
        out[0] = Participant(carrier=out[0].carrier, share_bps=10_000)
    for participant in out:
        if participant.share_bps == 0:
            draft.diagnostics.warn(
                "paste.share", f"line {lineno}: no share for {participant.carrier!r}"
            )
    return out
```

- [ ] **Step 4: Verify pass + lint**

Run: `uv run pytest tests/test_ingest.py -v` → PASS (all, including Task 2's)
Run: `uv run mypy src && uv run ruff check src tests` → clean

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/ingest.py tests/test_ingest.py
git commit -m "ingest: parse_tower — pasted schedule text to a draft program"
```

---

### Task 4: `program_from_rows` / `rows_from_program`

**Files:**
- Modify: `src/towerkit/ingest.py` (append)
- Test: `tests/test_ingest.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3; `dates.parse_flexible_date`; `money.format_share`, `model.Period`.
- Produces:
  - `program_from_rows(rows: list[dict[str, object]], *, insured: str = "", program: str = "") -> DraftProgram`. Keys are `CANONICAL_FIELDS`; values may be parsed strings or native ints/dates. Rows sharing a `layer` name merge participants onto one layer; conflicting limit/attach on a shared layer is an error diagnostic. The first row carrying inception+expiry sets the draft period; later rows with *different* dates set that layer's own `period` (staggered effective dates are per-layer, matching the model). `line` cells may hold several names split on `;`; rows without `line` share one synthesized line (warning).
  - `rows_from_program(program: Program) -> list[dict[str, object]]` — one row per (layer, participant); money as ints, shares via `format_share`, dates as ISO strings, `line` as `;`-joined line *names*. Inverse of `program_from_rows` for tower structure.
  - All shares are PERCENT (0.6 means 0.6%, not 60%) — one rule, no cell-format guessing.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ingest.py`)

```python
from towerkit.ingest import program_from_rows, rows_from_program

ROWS: list[dict[str, object]] = [
    {"layer": "Primary", "line": "Property", "limit": "10M", "attachment": 0,
     "carrier": "Chubb", "share": "100%", "premium": 250_000,
     "inception": "2026-10-01", "expiry": "2027-10-01"},
    {"layer": "15M xs 10M", "line": "Property", "limit": "15M", "attachment": "10M",
     "carrier": "AXA XL", "share": "60", "premium": "180k",
     "inception": "2026-10-01", "expiry": "2027-10-01"},
    {"layer": "15M xs 10M", "line": "Property", "limit": "15M", "attachment": "10M",
     "carrier": "Sompo", "share": "40",
     "inception": "2026-10-01", "expiry": "2027-10-01"},
]


def test_rows_build_and_merge_layers() -> None:
    draft = program_from_rows(ROWS, insured="Atomic Industries", program="Property")
    assert draft.period == Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    assert len(draft.layers) == 2
    assert draft.layers[1].signed_bps == 10_000
    assert draft.diagnostics.ok
    assert draft.to_program().total_premium() == 430_000


def test_rows_staggered_dates_become_layer_period() -> None:
    rows = [dict(ROWS[0]), dict(ROWS[1]), dict(ROWS[2])]
    rows[1]["inception"], rows[1]["expiry"] = "2027-01-01", "2028-01-01"
    rows[2]["inception"], rows[2]["expiry"] = "2027-01-01", "2028-01-01"
    draft = program_from_rows(rows, insured="A", program="P")
    assert draft.period == Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    assert draft.layers[1].period == Period(start=date(2027, 1, 1), end=date(2028, 1, 1))


def test_rows_conflicting_band_is_error() -> None:
    rows = [dict(ROWS[1]), {**ROWS[2], "attachment": "12M"}]
    draft = program_from_rows(rows, insured="A", program="P")
    assert any(d.code == "rows.band" for d in draft.diagnostics.errors)


def test_rows_bad_money_is_diagnostic_with_row_number() -> None:
    draft = program_from_rows([{**ROWS[0], "limit": "banana"}], insured="A", program="P")
    assert any("row 1" in d.message for d in draft.diagnostics.errors)


def test_round_trip_program_rows_program() -> None:
    original = program_from_rows(ROWS, insured="Atomic", program="Property").to_program()
    again = program_from_rows(
        rows_from_program(original), insured="Atomic", program="Property"
    ).to_program()
    assert again.period == original.period
    assert [layer.name for layer in again.layers] == [l.name for l in original.layers]
    assert [layer.limit for layer in again.layers] == [l.limit for l in original.layers]
    assert again.layers[1].participants == original.layers[1].participants
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -k rows -v`
Expected: FAIL — `ImportError: cannot import name 'program_from_rows'`

- [ ] **Step 3: Implement** (append to `src/towerkit/ingest.py`; add `from datetime import date` and `from .dates import parse_flexible_date`, `from .money import format_share`)

```python
def program_from_rows(
    rows: list[dict[str, object]], *, insured: str = "", program: str = ""
) -> DraftProgram:
    draft = DraftProgram(insured=insured, program=program)
    lines_by_name: dict[str, Line] = {}
    layers_by_name: dict[str, Layer] = {}
    synthesized = False
    for rownum, row in enumerate(rows, start=1):
        try:
            limit = _as_money(row.get("limit"))
            attach = _as_money(row.get("attachment"))
            premium = _as_money(row.get("premium"))
        except MoneyParseError as exc:
            draft.diagnostics.error("rows.money", f"row {rownum}: {exc}")
            continue
        if limit is None:
            draft.diagnostics.error("rows.money", f"row {rownum}: limit is required")
            continue
        line_ids, synthesized = _line_ids_for(
            draft, lines_by_name, row.get("line"), synthesized
        )
        period = _period_from(draft, row, rownum)
        if draft.period is None and period is not None:
            draft.period = period
        layer_name = str(row.get("layer") or "").strip() or (
            "Primary" if not attach
            else f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
        )
        layer = layers_by_name.get(layer_name)
        if layer is None:
            layer = Layer(
                id=_slug(layer_name, taken={la.id for la in draft.layers}),
                name=layer_name, applies_to=line_ids, attach=attach or 0, limit=limit,
                premium=premium,
                policy_number=str(row.get("policy_number") or "").strip() or None,
                period=period if period is not None and period != draft.period else None,
            )
            layers_by_name[layer_name] = layer
            draft.layers.append(layer)
        elif layer.limit != limit or layer.attach != (attach or 0):
            draft.diagnostics.error(
                "rows.band",
                f"row {rownum}: {layer_name!r} already has a different limit/attachment",
            )
            continue
        carrier = str(row.get("carrier") or "").strip()
        if carrier:
            share_raw = row.get("share")
            try:
                share = (
                    parse_share(str(share_raw)) if share_raw not in (None, "") else 10_000
                )
            except MoneyParseError as exc:
                draft.diagnostics.error("rows.share", f"row {rownum}: {exc}")
                continue
            layer.participants = [
                *layer.participants, Participant(carrier=carrier, share_bps=share)
            ]
    return draft


def rows_from_program(program: Program) -> list[dict[str, object]]:
    names = {line.id: line.name for line in program.lines}
    rows: list[dict[str, object]] = []
    for layer in program.layers:
        period = layer.period or program.period
        base: dict[str, object] = {
            "layer": layer.name,
            "line": ";".join(names[lid] for lid in layer.applies_to),
            "limit": layer.limit, "attachment": layer.attach,
            "inception": period.start.isoformat(), "expiry": period.end.isoformat(),
        }
        if layer.premium is not None:
            base["premium"] = layer.premium
        if layer.policy_number is not None:
            base["policy_number"] = layer.policy_number
        for participant in layer.participants or [None]:
            row = dict(base)
            if participant is not None:
                row["carrier"] = participant.carrier
                row["share"] = format_share(participant.share_bps)
            rows.append(row)
    return rows


def _as_money(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MoneyParseError(f"cannot parse money value: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return parse_money(str(value))


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return parse_flexible_date(str(value))


def _period_from(
    draft: DraftProgram, row: dict[str, object], rownum: int
) -> Period | None:
    start, end = _as_date(row.get("inception")), _as_date(row.get("expiry"))
    for key, given, parsed in (("inception", row.get("inception"), start),
                               ("expiry", row.get("expiry"), end)):
        if given not in (None, "") and parsed is None:
            draft.diagnostics.error(
                "rows.date", f"row {rownum}: cannot read a date from {given!r} ({key})"
            )
    if start is None or end is None:
        return None
    return Period(start=start, end=end)


def _line_ids_for(
    draft: DraftProgram,
    lines_by_name: dict[str, Line],
    cell: object,
    synthesized: bool,
) -> tuple[list[str], bool]:
    names = [n.strip() for n in str(cell or "").split(";") if n.strip()]
    if not names:
        if not synthesized:
            cover = Line(id="cover", name=draft.program.strip() or "Coverage")
            lines_by_name[cover.name] = cover
            draft.lines.append(cover)
            draft.diagnostics.warn("rows.line", "rows without a line share one synthesized line")
        return [lines_by_name[draft.program.strip() or "Coverage"].id], True
    ids: list[str] = []
    for name in names:
        line = lines_by_name.get(name)
        if line is None:
            line = Line(id=_slug(name, taken={li.id for li in draft.lines}), name=name)
            lines_by_name[name] = line
            draft.lines.append(line)
        ids.append(line.id)
    return ids, synthesized


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug
```

- [ ] **Step 4: Verify pass + lint**

Run: `uv run pytest tests/test_ingest.py -v` → PASS
Run: `uv run mypy src && uv run ruff check src tests` → clean
(If `format_share` output doesn't parse back through `parse_share` — e.g. it emits "60%" vs "60.0%" — fix `rows_from_program` to emit `str(Decimal(bps) / 100) + "%"`; the round-trip test is the referee.)

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/ingest.py tests/test_ingest.py
git commit -m "ingest: canonical rows to draft program, and the inverse"
```

---

### Task 5: import template workbook

**Files:**
- Create: `src/towerkit/ingest_template.py`
- Test: `tests/test_ingest.py` (append)

**Interfaces:**
- Consumes: Task 4's `program_from_rows`, `CANONICAL_FIELDS`.
- Produces:
  - `COLUMNS: tuple[ColumnSpec, ...]` where `ColumnSpec` is a frozen dataclass `(key: str, required: bool, example: str)` — keys exactly `CANONICAL_FIELDS`, required: layer, limit, attachment, carrier, inception, expiry.
  - `write_template(path: Path) -> Path` — one sheet named "Program", row 1 = canonical headers (bold, required cells filled light yellow `FFF9E6A0`), rows 2–4 = the worked example (the three ROWS from Task 4's test, as strings), frozen header row.
  - `read_rows(path: Path) -> list[dict[str, object]]` — reads any .xlsx first sheet whose headers are canonical names (case-insensitive, stripped); unknown headers raise `ValueError` naming them; blank rows skipped. This is towerkit's strict reader — fuzzy mapping is bookkit's job.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ingest.py`)

```python
from pathlib import Path

from towerkit.ingest_template import COLUMNS, read_rows, write_template


def test_template_round_trips_clean(tmp_path: Path) -> None:
    out = write_template(tmp_path / "template.xlsx")
    rows = read_rows(out)
    draft = program_from_rows(rows, insured="Example Co", program="Property")
    assert draft.diagnostics.ok
    assert draft.to_program().total_limit() == 25_000_000


def test_template_columns_match_canonical() -> None:
    from towerkit.ingest import CANONICAL_FIELDS

    assert tuple(c.key for c in COLUMNS) == CANONICAL_FIELDS


def test_read_rows_rejects_unknown_header(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["layer", "limit", "wibble"])
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="wibble"):
        read_rows(path)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -k template -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'towerkit.ingest_template'`

- [ ] **Step 3: Implement** (create `src/towerkit/ingest_template.py`)

```python
"""The populate-and-reimport workbook: one registry drives export and read.

Headers stay exactly the canonical field names so a filled template imports
with zero mapping. Required-ness is conveyed by styling, never by mangling
the header text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ingest import CANONICAL_FIELDS


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    required: bool
    example: str


_EXAMPLES: dict[str, tuple[bool, str]] = {
    "layer": (True, "Primary"),
    "line": (False, "Property"),
    "limit": (True, "10M"),
    "attachment": (True, "0"),
    "carrier": (True, "Chubb"),
    "share": (False, "100%"),
    "premium": (False, "250,000"),
    "inception": (True, "2026-10-01"),
    "expiry": (True, "2027-10-01"),
    "policy_number": (False, ""),
}

COLUMNS: tuple[ColumnSpec, ...] = tuple(
    ColumnSpec(key, *_EXAMPLES[key]) for key in CANONICAL_FIELDS
)

_EXAMPLE_ROWS: tuple[tuple[str, ...], ...] = (
    ("Primary", "Property", "10M", "0", "Chubb", "100%", "250,000",
     "2026-10-01", "2027-10-01", ""),
    ("15M xs 10M", "Property", "15M", "10M", "AXA XL", "60", "180k",
     "2026-10-01", "2027-10-01", ""),
    ("15M xs 10M", "Property", "15M", "10M", "Sompo", "40", "",
     "2026-10-01", "2027-10-01", ""),
)


def write_template(path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Program"
    required_fill = PatternFill("solid", fgColor="F9E6A0")
    for col, spec in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=spec.key)
        cell.font = Font(bold=True)
        if spec.required:
            cell.fill = required_fill
        ws.column_dimensions[cell.column_letter].width = max(12, len(spec.key) + 2)
    for row in _EXAMPLE_ROWS:
        ws.append(list(row))
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def read_rows(path: Path) -> list[dict[str, object]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if header is None:
        return []
    keys = [str(h or "").strip().lower() for h in header]
    unknown = [k for k in keys if k and k not in CANONICAL_FIELDS]
    if unknown:
        raise ValueError(f"unknown columns {unknown!r}; expected {list(CANONICAL_FIELDS)!r}")
    out: list[dict[str, object]] = []
    for raw in rows_iter:
        row = {k: v for k, v in zip(keys, raw) if k and v is not None and v != ""}
        if row:
            out.append(row)
    wb.close()
    return out
```

- [ ] **Step 4: Verify pass + lint**

Run: `uv run pytest tests/test_ingest.py -v` → PASS
Run: `uv run mypy src && uv run ruff check src tests` → clean

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/ingest_template.py tests/test_ingest.py
git commit -m "ingest: template workbook — one registry drives export and read"
```

---

### Task 6: CLI — `towerctl import` and `towerctl template`

**Files:**
- Modify: `src/towerkit/cli.py` (parsers after `p_new` in `_build_parser`; handlers after `_cmd_edit`)
- Test: `tests/test_cli.py` (append; follow the file's existing style of invoking `main([...])`)

**Interfaces:**
- Consumes: Tasks 3–5 (`parse_tower`, `program_from_rows`, `read_rows`, `write_template`); `model.dump_program`.
- Produces:
  - `towerctl template <out.xlsx>` — writes the template, prints the path.
  - `towerctl import <source> [-o out.json] [--insured X] [--program Y] [--edit]` — source is `.xlsx` (strict canonical headers), `.csv` (same headers via `csv.DictReader`), any other file read as pasted tower text, or `-` for stdin text. Prints every diagnostic; errors → exit 1, nothing written. Default output: `<slug(insured)>-<slug(program)>.json` in the cwd. `--edit` opens the written file in the TUI afterwards.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
import json
from pathlib import Path

from towerkit.cli import main


def test_cli_template_then_import(tmp_path: Path, capsys) -> None:
    template = tmp_path / "t.xlsx"
    assert main(["template", str(template)]) == 0
    out = tmp_path / "prog.json"
    code = main([
        "import", str(template), "-o", str(out),
        "--insured", "Example Co", "--program", "Property",
    ])
    assert code == 0
    data = json.loads(out.read_text())
    assert data["insured"] == "Example Co"
    assert len(data["layers"]) == 2


def test_cli_import_paste_text(tmp_path: Path) -> None:
    paste = tmp_path / "tower.txt"
    paste.write_text("Primary 10M — Chubb 100% — 250,000\n5M xs 10M — Sompo\n")
    out = tmp_path / "prog.json"
    code = main([
        "import", str(paste), "-o", str(out),
        "--insured", "Example Co", "--program", "Casualty",
    ])
    assert code == 1  # no period in pasted text → error, nothing written
    assert not out.exists()


def test_cli_import_bad_rows_exits_nonzero(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("layer,limit,attachment,carrier,inception,expiry\n"
                   "Primary,banana,0,Chubb,2026-10-01,2027-10-01\n")
    code = main(["import", str(bad), "-o", str(tmp_path / "x.json"),
                 "--insured", "A", "--program", "P"])
    assert code == 1
    assert "banana" in capsys.readouterr().out
```

Note the paste test: `parse_tower` yields no period, so `to_program()` refuses — the CLI surfaces that as exit 1. Pasted-text imports need `--inception/--expiry`? No: YAGNI — the TUI editor sets the period; but *without* `-o` written there is nothing to edit. So the CLI accepts two more optional flags `--inception` / `--expiry` (parsed with `parse_flexible_date`) to complete pasted imports. Add to the same test file:

```python
def test_cli_import_paste_with_dates(tmp_path: Path) -> None:
    paste = tmp_path / "tower.txt"
    paste.write_text("Primary 10M — Chubb 100%\n")
    out = tmp_path / "prog.json"
    code = main([
        "import", str(paste), "-o", str(out), "--insured", "Example Co",
        "--program", "Casualty", "--inception", "10/1/2026", "--expiry", "10/1/2027",
    ])
    assert code == 0
    assert json.loads(out.read_text())["period"]["start"] == "2026-10-01"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k "template or import" -v`
Expected: FAIL — argparse error: invalid choice 'template'

- [ ] **Step 3: Implement** — in `_build_parser` after the `p_new` block:

```python
    p_tpl = sub.add_parser("template", help="write a blank import template workbook")
    p_tpl.add_argument("out", type=Path, metavar="template.xlsx")
    p_tpl.set_defaults(handler=_cmd_template)

    p_imp = sub.add_parser(
        "import", help="build a program file from a schedule (xlsx/csv/text/stdin)"
    )
    p_imp.add_argument("source", help="schedule file, or - for pasted text on stdin")
    p_imp.add_argument("-o", "--out", type=Path, default=None)
    p_imp.add_argument("--insured", default="")
    p_imp.add_argument("--program", default="", dest="program_name")
    p_imp.add_argument("--inception", default="", help="period start for pasted text")
    p_imp.add_argument("--expiry", default="", help="period end for pasted text")
    p_imp.add_argument("--edit", action="store_true", help="open the result in the TUI")
    p_imp.set_defaults(handler=_cmd_import)
```

Handlers after `_cmd_edit`:

```python
def _cmd_template(args: argparse.Namespace) -> int:
    from .ingest_template import write_template

    print(write_template(args.out))
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    import csv

    from .dates import parse_flexible_date
    from .ingest import parse_tower, program_from_rows
    from .model import Period, dump_program
    from .validate import ProgramInvalidError

    insured, program_name = args.insured, args.program_name
    source = args.source
    if source == "-":
        draft = parse_tower(sys.stdin.read(), insured=insured, program=program_name)
    else:
        path = Path(source)
        if path.suffix.lower() == ".xlsx":
            from .ingest_template import read_rows

            draft = program_from_rows(read_rows(path), insured=insured, program=program_name)
        elif path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8") as fh:
                rows: list[dict[str, object]] = [
                    {k.strip().lower(): v for k, v in row.items() if v not in (None, "")}
                    for row in csv.DictReader(fh)
                ]
            draft = program_from_rows(rows, insured=insured, program=program_name)
        else:
            draft = parse_tower(
                path.read_text(encoding="utf-8"), insured=insured, program=program_name
            )
    if draft.period is None and args.inception and args.expiry:
        start = parse_flexible_date(args.inception)
        end = parse_flexible_date(args.expiry)
        if start and end:
            draft.period = Period(start=start, end=end)
    for diag in draft.diagnostics.items:
        print(f"  {diag}")
    try:
        program = draft.to_program()
    except ProgramInvalidError as exc:
        for diag in exc.diagnostics.errors:
            print(f"  {diag}")
        return 1
    out = args.out or Path(f"{_file_slug(insured)}-{_file_slug(program_name)}.json")
    dump_program(program, out)
    print(out)
    if args.edit:
        from .tui.app import TowerkitApp

        TowerkitApp(path=out, new=False, theme_path=None).run()
    return 0


def _file_slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "program"
```

(Check `ProgramInvalidError`'s attribute name at `src/towerkit/validate.py:73` — if the stored field is not `.diagnostics`, use what it actually is.)

- [ ] **Step 4: Verify pass + lint, full suite**

Run: `uv run pytest -q` → all pass (existing suite + new)
Run: `uv run mypy src && uv run ruff check src tests` → clean

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/cli.py tests/test_cli.py
git commit -m "cli: towerctl import + template — schedule in, program file out"
```

---

## Deviation notes (spec → plan)

- The spec's contract test "SOI export → `program_from_rows` → equal program" is implemented as the **template/rows round-trip** (Task 4/5): the SOI export's prose columns (`limits_text`, `carrier_text`) are display strings, not data, and reparsing them would test string formatting, not import correctness. The template *is* the SOI-shaped tabular layout; `rows_from_program` is the exact inverse. Tower structure (layers, bands, participants, periods) round-trips byte-equal.
- `--inception/--expiry` flags on `towerctl import` were added because pasted text never carries a period and a period-less import could write nothing.

## Not in this plan

Everything bookkit-side (readers, tablemap, fieldspec, mappers, matcher, staging, commit, backups, ImportScreen, `bookctl` verbs, `parse_share_bps` delegation) — second plan, written after this one executes and the API above is real.
