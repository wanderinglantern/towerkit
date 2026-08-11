# Schedule of Insurance xlsx Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `towerctl soi program.json` exports a themed Schedule of Insurance workbook (one row per layer, coverage-group sections) whose styling mirrors the reviewed sample workbook.

**Architecture:** Pure mapping in `towerkit/soi.py` (program → sections/rows, all text composition — strict mypy, no openpyxl import), openpyxl glue in `towerkit/render/soi_xlsx.py` (rows + theme → workbook, zip-normalized for byte-identical output), a `soi` style block on `Theme`, a CLI subcommand, and two new optional `Layer` fields (`limitsDetail`, `retentionDetail`) captured in the TUI.

**Tech Stack:** Python 3.11+, pydantic v2, openpyxl ≥3.1 (new runtime dep), Babel money formatting, Textual TUI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-soi-export-design.md` — authoritative for column mapping, style values, and deviations.

## Global Constraints

- Money is integer whole dollars; format with `towerkit.money.format_money` / `format_share` — never hand-rolled f-strings for currency.
- Canonical serialisation: zero-diff round trip is tested; any new Layer key must join `_LAYER_KEYS` or `_ordered()` raises at save time.
- Byte-identical output across runs is a repo rule (applies to the xlsx too).
- `towerkit/soi.py` is pure core: strict mypy, never imports openpyxl or plotting libraries. `towerkit/render/*` has relaxed mypy.
- SOI default style values (from the sample workbook): header fill `#003865`, header text `#FFFFFF`, body text `#3D3C37`, band fill `#F7F3EE`, border `#B9B6B1`, font `Noto Sans` 11.
- Column widths: 23.33, 37.83, 39.83, 15, 11.83, 13, 100, 34.83, 12.16.
- Excel sheet titles are limited to 31 chars and forbid `\ / * ? : [ ]`.
- Run tests with `uv run --group dev pytest -q`, lint with `uv run --group dev ruff check src tests`, types with `uv run --group dev mypy src/towerkit`.
- Commit messages: descriptive imperative summary (repo style, no `feat:` prefixes), footer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- `programs/private/` is gitignored client data — never commit anything under it, and never commit the sample workbook.

---

### Task 1: Layer gains `limitsDetail` / `retentionDetail` (model + schema)

**Files:**
- Modify: `src/towerkit/model.py:89-99` (Layer fields), `:227-230` (`_LAYER_KEYS`), `:278-311` (`program_to_jsonable` layers block)
- Modify: `schema/program.schema.json:142-190` (layer `$defs` properties)
- Test: `tests/test_canonical.py`

**Interfaces:**
- Produces: `Layer.limits_detail: str | None` (alias `limitsDetail`), `Layer.retention_detail: str | None` (alias `retentionDetail`). Canonical key order: `... "premium", "limitsDetail", "retentionDetail", "participants", "notes"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_canonical.py`)

```python
def test_soi_detail_fields_round_trip() -> None:
    program = load_program(SAMPLE)
    layer = program.layers[0]
    layer.limits_detail = "Each Occurrence $1,000,000; Med Pay $5,000"
    layer.retention_detail = "SIR $250,000"
    text = dumps_program(program)
    reloaded = loads_program(text)
    assert reloaded.layers[0].limits_detail == "Each Occurrence $1,000,000; Med Pay $5,000"
    assert reloaded.layers[0].retention_detail == "SIR $250,000"


def test_soi_detail_keys_sit_between_premium_and_participants() -> None:
    program = load_program(SAMPLE)
    program.layers[0].premium = 12_345  # ensure "premium" appears in this layer
    program.layers[0].limits_detail = "L"
    program.layers[0].retention_detail = "R"
    text = dumps_program(program)
    block = text[text.index('"layers"'):]
    assert block.index('"limitsDetail"') < block.index('"retentionDetail"')
    assert block.index('"premium"') < block.index('"limitsDetail"') < block.index('"participants"')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_canonical.py -q`
Expected: 2 FAIL — pydantic rejects the unknown `limits_detail` attribute (`extra="forbid"` + `validate_assignment`).

- [ ] **Step 3: Implement**

In `Layer` (after `premium: Money | None = None`):

```python
    limits_detail: str | None = Field(alias="limitsDetail", default=None)
    retention_detail: str | None = Field(alias="retentionDetail", default=None)
```

`_LAYER_KEYS` becomes:

```python
_LAYER_KEYS = (
    "id", "name", "policyNumber", "period", "followsUnderlying", "appliesTo",
    "attach", "limit", "premium", "limitsDetail", "retentionDetail",
    "participants", "notes",
)
```

In the layers dict inside `program_to_jsonable`, after `"premium": layer.premium,`:

```python
                    "limitsDetail": layer.limits_detail,
                    "retentionDetail": layer.retention_detail,
```

In `schema/program.schema.json`, inside `$defs.layer.properties` after `premium`:

```json
        "limitsDetail": {
          "description": "SOI Limits column prose, exported verbatim; composed from limit/attach/sublimits when absent.",
          "type": "string",
          "minLength": 1
        },
        "retentionDetail": {
          "description": "SOI Deductible/SIR/Retention column prose, exported verbatim; composed from retentions when absent.",
          "type": "string",
          "minLength": 1
        },
```

- [ ] **Step 4: Run the full suite** — the pre-existing zero-diff tests prove files *without* the fields still round-trip untouched.

Run: `uv run --group dev pytest -q && uv run --group dev mypy src/towerkit`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/model.py schema/program.schema.json tests/test_canonical.py
git commit -m "Layer captures SOI limits/retention detail prose"
```

---

### Task 2: Theme `soi` style block

**Files:**
- Modify: `src/towerkit/theme.py` (new `SoiStyle` dataclass; `Theme.soi` field; parsing in `_theme_from_jsonable`)
- Test: `tests/test_soi.py` (create)

**Interfaces:**
- Consumes: `contrast_text(face, light, dark)` at `theme.py:80`.
- Produces: `SoiStyle` frozen dataclass with fields `header_fill, header_text, body_text, band_fill, border, font, size` (defaults = sample values) and property `effective_header_text -> str`; `Theme.soi: SoiStyle`; JSON keys `soi.headerFill/headerText/bodyText/bandFill/border/font/size`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_soi.py`)

```python
"""SOI mapping and theming: pure logic, no Excel here."""

from towerkit.theme import SoiStyle, load_theme, _theme_from_jsonable


class TestSoiStyle:
    def test_default_theme_mirrors_the_sample_workbook(self) -> None:
        soi = load_theme(None).soi
        assert soi.header_fill == "#003865"
        assert soi.header_text == "#FFFFFF"
        assert soi.body_text == "#3D3C37"
        assert soi.band_fill == "#F7F3EE"
        assert soi.border == "#B9B6B1"
        assert soi.font == "Noto Sans"
        assert soi.size == 11

    def test_theme_json_overrides(self) -> None:
        theme = _theme_from_jsonable({"name": "x", "soi": {"headerFill": "#000F47"}})
        assert theme.soi.header_fill == "#000F47"
        assert theme.soi.band_fill == "#F7F3EE"  # untouched defaults survive

    def test_light_header_fill_never_gets_white_text(self) -> None:
        soi = SoiStyle(header_fill="#CEECFF")  # Marsh Sky
        assert soi.effective_header_text == soi.body_text

    def test_dark_header_fill_keeps_declared_text(self) -> None:
        assert SoiStyle().effective_header_text == "#FFFFFF"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_soi.py -q`
Expected: ImportError — `SoiStyle` does not exist.

- [ ] **Step 3: Implement**

In `theme.py`, after `Chrome`:

```python
@dataclass(frozen=True)
class SoiStyle:
    """Schedule of Insurance workbook styling. Defaults mirror the reviewed
    sample workbook, not the chart chrome — SOIs circulate as Excel files
    with their own established look."""

    header_fill: str = "#003865"
    header_text: str = "#FFFFFF"
    body_text: str = "#3D3C37"
    band_fill: str = "#F7F3EE"
    border: str = "#B9B6B1"
    font: str = "Noto Sans"
    size: int = 11

    @property
    def effective_header_text(self) -> str:
        return contrast_text(self.header_fill, self.header_text, self.body_text)
```

Add to `Theme`: `soi: SoiStyle = field(default_factory=SoiStyle)`.

In `_theme_from_jsonable`, before the `return Theme(...)`:

```python
    soi_raw = data.get("soi", {})
    soi = SoiStyle(
        header_fill=soi_raw.get("headerFill", SoiStyle.header_fill),
        header_text=soi_raw.get("headerText", SoiStyle.header_text),
        body_text=soi_raw.get("bodyText", SoiStyle.body_text),
        band_fill=soi_raw.get("bandFill", SoiStyle.band_fill),
        border=soi_raw.get("border", SoiStyle.border),
        font=soi_raw.get("font", SoiStyle.font),
        size=soi_raw.get("size", SoiStyle.size),
    )
```

and pass `soi=soi` to the `Theme(...)` constructor.

- [ ] **Step 4: Run tests**

Run: `uv run --group dev pytest tests/test_soi.py -q && uv run --group dev mypy src/towerkit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/theme.py tests/test_soi.py
git commit -m "Theme gains an SOI style block; defaults mirror the sample workbook"
```

---

### Task 3: `soi.py` text composition helpers

**Files:**
- Create: `src/towerkit/soi.py`
- Test: `tests/test_soi.py` (extend; add the shared fixture)

**Interfaces:**
- Consumes: `format_money`, `format_share`, `BPS_SCALE` from `towerkit.money`; `Layer`, `Program` from `towerkit.model`.
- Produces (all used by Task 4 and its tests):
  - `carrier_text(layer: Layer) -> str`
  - `limits_text(layer: Layer, program: Program) -> str`
  - `retention_text(layer: Layer, program: Program) -> str`
  - `coverage_text(layer: Layer, program: Program) -> str`
  - test helper `make_program() -> Program` at module level of `tests/test_soi.py`

- [ ] **Step 1: Add the shared fixture to `tests/test_soi.py`** (module level, above the classes)

```python
from towerkit.model import Program


def make_program() -> Program:
    """Exercises every SOI mapping rule: a grouped tower with quota share,
    a pending layer, an ungrouped line with a sublimit, a same-group
    umbrella, and a cross-group follows-underlying umbrella."""
    return Program.model_validate(
        {
            "insured": "Atomic Industries, LLC",
            "program": "Casualty",
            "placement": "bound",
            "period": {"start": "2026-01-01", "end": "2027-01-01"},
            "lines": [
                {"id": "gl", "name": "General Liability", "group": "Casualty"},
                {"id": "al", "name": "Auto Liability", "group": "Casualty"},
                {"id": "prop", "name": "Property"},
            ],
            "layers": [
                {
                    "id": "gl-primary", "name": "Primary", "appliesTo": ["gl"],
                    "attach": 0, "limit": 1_000_000, "premium": 50_000,
                    "policyNumber": "GL-123",
                    "period": {"start": "2026-02-01", "end": "2027-02-01"},
                    "participants": [{"carrier": "Zenith", "share": 1}],
                },
                {
                    "id": "gl-x1", "name": "1st Excess", "appliesTo": ["gl"],
                    "attach": 1_000_000, "limit": 4_000_000, "premium": 30_000,
                    "participants": [
                        {"carrier": "Alpha Re", "share": 0.6},
                        {"carrier": "Beta Syndicate", "share": 0.4},
                    ],
                },
                {
                    "id": "al-primary", "name": "Primary", "appliesTo": ["al"],
                    "attach": 0, "limit": 1_000_000,
                },
                {
                    "id": "prop-primary", "name": "Primary", "appliesTo": ["prop"],
                    "attach": 0, "limit": 10_000_000, "premium": 80_000,
                    "participants": [{"carrier": "Gamma", "share": 1}],
                },
                {
                    "id": "casualty-umbrella", "name": "Umbrella",
                    "appliesTo": ["gl", "al"], "attach": 5_000_000,
                    "limit": 5_000_000, "premium": 20_000,
                    "participants": [{"carrier": "Zenith", "share": 1}],
                },
                {
                    "id": "program-umbrella", "name": "Program Umbrella",
                    "appliesTo": ["gl", "prop"], "followsUnderlying": True,
                    "attach": 0, "limit": 25_000_000, "premium": 40_000,
                    "participants": [{"carrier": "Delta", "share": 1}],
                },
            ],
            "retentions": [
                {"appliesTo": ["gl", "al"], "type": "sir", "amount": 250_000,
                 "aggregate": 1_000_000},
                {"appliesTo": ["prop"], "type": "deductible", "amount": 100_000},
            ],
            "sublimits": [
                {"name": "Flood", "amount": 5_000_000, "appliesTo": ["prop"]},
            ],
        }
    )
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_soi.py`)

```python
from towerkit.soi import carrier_text, coverage_text, limits_text, retention_text


class TestCompositionHelpers:
    def test_sole_full_share_carrier_is_plain(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[0]) == "Zenith"

    def test_quota_share_lists_carriers_with_shares(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[1]) == "Alpha Re (60%), Beta Syndicate (40%)"

    def test_no_participants_reads_to_be_placed(self) -> None:
        p = make_program()
        assert carrier_text(p.layers[2]) == "To be placed"

    def test_primary_limits_quoted_by_limit_alone(self) -> None:
        p = make_program()
        assert limits_text(p.layers[2], p) == "$1,000,000"

    def test_excess_limits_use_xs(self) -> None:
        p = make_program()
        assert limits_text(p.layers[1], p) == "$4,000,000 xs $1,000,000"

    def test_follows_underlying_reads_xs_underlying(self) -> None:
        p = make_program()
        assert limits_text(p.layers[5], p).startswith("$25,000,000 xs underlying")

    def test_sublimits_appended_to_composed_limits(self) -> None:
        p = make_program()
        assert limits_text(p.layers[3], p) == "$10,000,000; Sublimit: Flood $5,000,000"

    def test_limits_detail_wins_verbatim(self) -> None:
        p = make_program()
        p.layers[0].limits_detail = "Each Occurrence $1,000,000"
        assert limits_text(p.layers[0], p) == "Each Occurrence $1,000,000"

    def test_primary_retention_composed_with_aggregate(self) -> None:
        p = make_program()
        assert retention_text(p.layers[0], p) == "SIR $250,000; Aggregate $1,000,000"

    def test_excess_retention_blank(self) -> None:
        p = make_program()
        assert retention_text(p.layers[1], p) == ""

    def test_follows_layer_counts_as_excess_for_retention(self) -> None:
        p = make_program()
        assert retention_text(p.layers[5], p) == ""

    def test_retention_detail_wins_verbatim(self) -> None:
        p = make_program()
        p.layers[1].retention_detail = "See policy."
        assert retention_text(p.layers[1], p) == "See policy."

    def test_sole_layer_on_line_shows_line_name(self) -> None:
        # Drop the cross-group umbrella so Property genuinely has one layer.
        p = make_program()
        p.layers = [layer for layer in p.layers if layer.id != "program-umbrella"]
        assert coverage_text(p.layers[3], p) == "Property"

    def test_towered_by_umbrella_line_appends_layer_name(self) -> None:
        # The cross-group umbrella makes Property a two-layer line.
        p = make_program()
        assert coverage_text(p.layers[3], p) == "Property — Primary"

    def test_towered_line_appends_layer_name(self) -> None:
        p = make_program()
        assert coverage_text(p.layers[0], p) == "General Liability — Primary"
        assert coverage_text(p.layers[1], p) == "General Liability — 1st Excess"

    def test_multi_line_layer_uses_layer_name_plus_line_labels(self) -> None:
        p = make_program()
        assert coverage_text(p.layers[4], p) == "Umbrella (GL, AL)"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_soi.py -q`
Expected: ImportError — `towerkit.soi` does not exist.

- [ ] **Step 4: Implement** (create `src/towerkit/soi.py`)

```python
"""Schedule of Insurance mapping: program -> ordered sections of rows.

Pure core: all SOI text composition and ordering lives here, fully typed,
with no Excel/openpyxl imports — the workbook writer in render/soi_xlsx.py
consumes what this module produces (working rule: pure modules never import
rendering libraries)."""

from __future__ import annotations

from .model import Layer, Line, Program
from .money import BPS_SCALE, format_money, format_share

_RETENTION_LABELS = {"deductible": "Deductible", "sir": "SIR", "captive": "Captive"}


def carrier_text(layer: Layer) -> str:
    if not layer.participants:
        return "To be placed"
    if len(layer.participants) == 1 and layer.participants[0].share_bps == BPS_SCALE:
        return layer.participants[0].carrier
    return ", ".join(
        f"{p.carrier} ({format_share(p.share_bps)})" for p in layer.participants
    )


def _is_primary(layer: Layer) -> bool:
    return layer.attach == 0 and not layer.follows_underlying


def _covered_lines(layer: Layer, program: Program) -> list[Line]:
    return [line for line in program.lines if line.id in layer.applies_to]


def limits_text(layer: Layer, program: Program) -> str:
    if layer.limits_detail:
        return layer.limits_detail
    if layer.follows_underlying:
        base = f"{format_money(layer.limit)} xs underlying"
    elif layer.attach == 0:
        base = format_money(layer.limit)  # primaries by limit alone, never "xs $0"
    else:
        base = f"{format_money(layer.limit)} xs {format_money(layer.attach)}"
    covered = set(layer.applies_to)
    subs = [s for s in program.sublimits if covered & set(s.applies_to)]
    if subs:
        tail = "; ".join(f"Sublimit: {s.name} {format_money(s.amount)}" for s in subs)
        return f"{base}; {tail}"
    return base


def retention_text(layer: Layer, program: Program) -> str:
    if layer.retention_detail:
        return layer.retention_detail
    if not _is_primary(layer):
        return ""
    covered = set(layer.applies_to)
    parts: list[str] = []
    for r in program.retentions:
        if not covered & set(r.applies_to):
            continue
        text = f"{_RETENTION_LABELS[r.type.value]} {format_money(r.amount)}"
        if r.aggregate is not None:
            text += f"; Aggregate {format_money(r.aggregate)}"
        if r.vehicle:
            text += f" (via {r.vehicle})"
        parts.append(text)
    return "; ".join(parts)


def coverage_text(layer: Layer, program: Program) -> str:
    lines = _covered_lines(layer, program)
    if len(lines) == 1:
        line = lines[0]
        if len(program.layers_for_line(line.id)) == 1:
            return line.name
        return f"{line.name} — {layer.name}"
    labels = ", ".join(line.label for line in lines)
    return f"{layer.name} ({labels})"
```

- [ ] **Step 5: Run tests**

Run: `uv run --group dev pytest tests/test_soi.py -q && uv run --group dev mypy src/towerkit && uv run --group dev ruff check src tests`
Expected: PASS, no type or lint errors (soi.py is strict-mypy automatically — only `tui.*`/`render.*` are relaxed).

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/soi.py tests/test_soi.py
git commit -m "SOI text composition: carrier, limits, retention, coverage labels"
```

---

### Task 4: `soi.py` sections, ordering, naming

**Files:**
- Modify: `src/towerkit/soi.py`
- Modify: `DECISIONS.md` (two entries, see Step 5)
- Test: `tests/test_soi.py` (extend)

**Interfaces:**
- Consumes: Task 3's helpers; `Program.layers_for_line`; `Period`.
- Produces (consumed by the writer in Task 5 and CLI in Task 6):

```python
@dataclass(frozen=True)
class SoiRow:
    insured: str
    coverage: str
    carrier: str
    policy_number: str      # "" when absent
    effective: date
    expiration: date
    limits: str
    retention: str
    premium: int | None

@dataclass(frozen=True)
class SoiSection:
    label: str | None       # None = unlabeled ungrouped section (no band row)
    rows: tuple[SoiRow, ...]
    # property premium_total -> int

PROGRAM_WIDE = "Program-wide"

def build_soi(program: Program) -> list[SoiSection]
def sheet_title(program: Program) -> str      # <=31 chars, illegal chars stripped
def default_filename(program: Program) -> str # "<Insured> - Schedule of Insurance.xlsx"
```

- [ ] **Step 1: Write the failing tests** (append to `tests/test_soi.py`)

```python
import datetime

from towerkit.model import Period
from towerkit.soi import PROGRAM_WIDE, build_soi, default_filename, sheet_title


class TestBuildSoi:
    def test_sections_named_groups_then_ungrouped_then_program_wide(self) -> None:
        sections = build_soi(make_program())
        assert [s.label for s in sections] == ["Casualty", None, PROGRAM_WIDE]

    def test_casualty_rows_ordered_line_then_attach(self) -> None:
        casualty = build_soi(make_program())[0]
        assert [r.coverage for r in casualty.rows] == [
            "General Liability — Primary",
            "General Liability — 1st Excess",
            "Umbrella (GL, AL)",          # same-group umbrella stays in its group
            "Auto Liability — Primary",
        ]

    def test_cross_group_layer_lands_program_wide(self) -> None:
        wide = build_soi(make_program())[2]
        assert [r.coverage for r in wide.rows] == ["Program Umbrella (GL, Property)"]

    def test_section_premium_totals(self) -> None:
        sections = build_soi(make_program())
        assert sections[0].premium_total == 100_000  # 50k + 30k + 20k, pending=0
        assert sections[1].premium_total == 80_000
        assert sections[2].premium_total == 40_000

    def test_row_period_falls_back_to_program(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[0].effective == datetime.date(2026, 2, 1)   # layer override
        assert rows[1].effective == datetime.date(2026, 1, 1)   # program period

    def test_insured_and_policy_number(self) -> None:
        rows = build_soi(make_program())[0].rows
        assert rows[0].insured == "Atomic Industries, LLC"
        assert rows[0].policy_number == "GL-123"
        assert rows[1].policy_number == ""


class TestNaming:
    def test_sheet_title_years_from_period(self) -> None:
        assert sheet_title(make_program()) == "Casualty SOI - 26-27"

    def test_sheet_title_truncated_to_31_chars(self) -> None:
        p = make_program()
        p.program = "An Extremely Long Program Name That Overflows"
        title = sheet_title(p)
        assert len(title) <= 31
        assert title.endswith(" SOI - 26-27")

    def test_sheet_title_strips_illegal_chars(self) -> None:
        p = make_program()
        p.program = "Cas[ual]ty: Pro/gram?"
        assert "[" not in sheet_title(p) and ":" not in sheet_title(p)

    def test_default_filename(self) -> None:
        assert (
            default_filename(make_program())
            == "Atomic Industries, LLC - Schedule of Insurance.xlsx"
        )

    def test_default_filename_replaces_path_hostile_chars(self) -> None:
        p = make_program()
        p.insured = "A/B: C"
        assert "/" not in default_filename(p) and ":" not in default_filename(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_soi.py -q`
Expected: ImportError — `build_soi` not defined.

- [ ] **Step 3: Implement** (append to `src/towerkit/soi.py`; add imports `re`, `dataclass/field`, `date`)

```python
import re
from dataclasses import dataclass
from datetime import date

PROGRAM_WIDE = "Program-wide"

_SHEET_ILLEGAL = re.compile(r"[\\/*?:\[\]]")
_PATH_HOSTILE = re.compile(r"[\\/:]")


@dataclass(frozen=True)
class SoiRow:
    insured: str
    coverage: str
    carrier: str
    policy_number: str
    effective: date
    expiration: date
    limits: str
    retention: str
    premium: int | None


@dataclass(frozen=True)
class SoiSection:
    label: str | None
    rows: tuple[SoiRow, ...]

    @property
    def premium_total(self) -> int:
        return sum(row.premium or 0 for row in self.rows)


def _section_key(layer: Layer, program: Program) -> str | None:
    """The section a layer belongs to: its lines' shared group, None when the
    shared group is absent, PROGRAM_WIDE when its lines span groups."""
    groups = {line.group for line in _covered_lines(layer, program)}
    if len(groups) == 1:
        return groups.pop()
    return PROGRAM_WIDE


def _row(layer: Layer, program: Program) -> SoiRow:
    period = layer.period or program.period
    return SoiRow(
        insured=program.insured,
        coverage=coverage_text(layer, program),
        carrier=carrier_text(layer),
        policy_number=layer.policy_number or "",
        effective=period.start,
        expiration=period.end,
        limits=limits_text(layer, program),
        retention=retention_text(layer, program),
        premium=layer.premium,
    )


def build_soi(program: Program) -> list[SoiSection]:
    line_index = {line.id: i for i, line in enumerate(program.lines)}
    layer_index = {layer.id: i for i, layer in enumerate(program.layers)}

    def sort_key(layer: Layer) -> tuple[int, int, int]:
        anchor = min(line_index[lid] for lid in layer.applies_to if lid in line_index)
        return (anchor, layer.attach, layer_index[layer.id])

    buckets: dict[str | None, list[Layer]] = {}
    for layer in program.layers:
        buckets.setdefault(_section_key(layer, program), []).append(layer)

    order: list[str | None] = []
    for line in program.lines:  # named groups by first appearance
        if line.group is not None and line.group in buckets and line.group not in order:
            order.append(line.group)
    if None in buckets:
        order.append(None)
    if PROGRAM_WIDE in buckets:
        order.append(PROGRAM_WIDE)

    return [
        SoiSection(
            label=key,
            rows=tuple(_row(layer, program) for layer in sorted(buckets[key], key=sort_key)),
        )
        for key in order
    ]


def sheet_title(program: Program) -> str:
    suffix = (
        f" SOI - {program.period.start.year % 100:02d}-{program.period.end.year % 100:02d}"
    )
    name = _SHEET_ILLEGAL.sub("", program.program).strip()
    return name[: 31 - len(suffix)].rstrip() + suffix


def default_filename(program: Program) -> str:
    return f"{_PATH_HOSTILE.sub('-', program.insured)} - Schedule of Insurance.xlsx"
```

Note: `_section_key` returning `str | None` where `None` means "ungrouped" and the sentinel string `PROGRAM_WIDE` means cross-group is deliberate — both become `SoiSection.label` directly.

- [ ] **Step 4: Run tests**

Run: `uv run --group dev pytest tests/test_soi.py -q && uv run --group dev mypy src/towerkit`
Expected: PASS.

- [ ] **Step 5: Record decisions** — append to `DECISIONS.md` under a "SOI export (2026-08-11)" heading:

```markdown
## SOI export (2026-08-11)

- **No grand-total row** at the bottom of the SOI — the sample has none;
  section roll-ups carry the sums. Revisit if a full-program total is wanted.
- **Multi-line layers section into their lines' shared group**, else the
  final "Program-wide" section — so section premium roll-ups never double
  count a shared layer.
- **Follows-underlying layers compose limits as "xs underlying"** — their
  attachment is per-column derived state, so no single dollar figure is
  honest; `limitsDetail` overrides where prose is wanted.
```

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/soi.py tests/test_soi.py DECISIONS.md
git commit -m "SOI sections: group buckets, ordering, roll-ups, sheet naming"
```

---

### Task 5: openpyxl dependency + xlsx writer with deterministic output

**Files:**
- Modify: `pyproject.toml` + `uv.lock` (via `uv add`)
- Create: `src/towerkit/render/soi_xlsx.py`
- Modify: `changelog.md` (wheelhouse note), `DECISIONS.md` (openpyxl entry)
- Test: `tests/test_soi_xlsx.py` (create)

**Interfaces:**
- Consumes: `build_soi`/`SoiSection` (Task 4), `Theme.soi: SoiStyle` (Task 2), `provenance()` from `towerkit/render/common.py:46`.
- Produces: `write_soi(sections: list[SoiSection], *, title: str, theme: Theme, out_path: Path, show_premiums: bool = True) -> Path` in `towerkit.render.soi_xlsx`.

- [ ] **Step 1: Add the dependency**

```bash
uv add "openpyxl>=3.1"
```

Verify `pyproject.toml` dependencies now include `openpyxl>=3.1` and `uv.lock` updated.

- [ ] **Step 2: Write the failing tests** (create `tests/test_soi_xlsx.py`)

```python
"""SOI workbook writer: styling mirrors the sample; output is byte-identical."""

from pathlib import Path

import pytest
from openpyxl import load_workbook

from towerkit.render.soi_xlsx import write_soi
from towerkit.soi import build_soi, sheet_title
from towerkit.theme import load_theme

from test_soi import make_program


@pytest.fixture()
def program():
    return make_program()


@pytest.fixture()
def theme():
    return load_theme(None)


def _write(program, theme, path: Path, **kw) -> Path:
    return write_soi(
        build_soi(program), title=sheet_title(program), theme=theme, out_path=path, **kw
    )


class TestDeterminism:
    def test_two_writes_are_byte_identical(self, program, theme, tmp_path) -> None:
        a = _write(program, theme, tmp_path / "a.xlsx")
        b = _write(program, theme, tmp_path / "b.xlsx")
        assert a.read_bytes() == b.read_bytes()


class TestContent:
    def test_headers_and_title(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        assert ws.title == "Casualty SOI - 26-27"
        assert [c.value for c in ws[1]] == [
            "Insured", "Line of Coverage", "Carrier", "Policy Number",
            "Effective Date", "Expiration Date", "Limits",
            "Deductible / SIR / Retention", "Premium",
        ]

    def test_header_style_mirrors_sample(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        cell = ws["A1"]
        assert cell.fill.fgColor.rgb == "FF003865"
        assert cell.font.bold and cell.font.name == "Noto Sans"
        assert cell.alignment.horizontal == "center" and cell.alignment.wrap_text
        assert ws.freeze_panes == "A2"

    def test_section_band_rows_merged_with_rollup(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        # row 2 = "Casualty" band: merged A:H, roll-up premium in I
        assert ws["A2"].value == "Casualty"
        assert "A2:H2" in {str(r) for r in ws.merged_cells.ranges}
        assert ws["I2"].value == 100_000
        assert ws["I2"].number_format == '"$"#,##0.00'

    def test_zebra_restarts_per_section(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        # Casualty: band r2, body r3-r6; unlabeled section body starts r7.
        assert ws["A3"].fill.patternType is None          # first body row: white
        assert ws["A4"].fill.fgColor.rgb == "FFF7F3EE"    # second: banded
        assert ws["A7"].fill.patternType is None          # new section restarts white

    def test_unlabeled_section_has_no_band_row(self, program, theme, tmp_path) -> None:
        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        assert ws["B7"].value == "Property — Primary"  # body row, not a section label

    def test_dates_are_real_dates(self, program, theme, tmp_path) -> None:
        import datetime

        ws = load_workbook(_write(program, theme, tmp_path / "s.xlsx")).active
        cell = ws["E3"]
        assert cell.value == datetime.datetime(2026, 2, 1)
        assert cell.number_format == "mm/dd/yyyy"

    def test_no_premiums_drops_the_column(self, program, theme, tmp_path) -> None:
        ws = load_workbook(
            _write(program, theme, tmp_path / "s.xlsx", show_premiums=False)
        ).active
        assert [c.value for c in ws[1]][-1] == "Deductible / SIR / Retention"
        assert ws.max_column == 8
        assert "A2:H2" in {str(r) for r in ws.merged_cells.ranges}  # full-width band
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_soi_xlsx.py -q`
Expected: ImportError — `towerkit.render.soi_xlsx` does not exist.
(If `from test_soi import make_program` fails to resolve, add an empty `tests/__init__.py`-free conftest import is NOT needed — pytest's rootdir insertion handles same-directory imports; verify before working around.)

- [ ] **Step 4: Implement** (create `src/towerkit/render/soi_xlsx.py`)

```python
"""Schedule of Insurance workbook writer.

Takes the pure sections from towerkit.soi plus a theme and writes a styled
.xlsx. Determinism: workbook properties are pinned (no wall clock) and the
finished archive is rewritten with epoch timestamps, so two identical runs
produce byte-identical files (repo rule)."""

from __future__ import annotations

import math
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..soi import SoiSection
from ..theme import Theme
from .common import provenance

_HEADERS = (
    "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
    "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium",
)
_WIDTHS = (23.33, 37.83, 39.83, 15.0, 11.83, 13.0, 100.0, 34.83, 12.16)
_CURRENCY = '"$"#,##0.00'
_DATE_FMT = "mm/dd/yyyy"
_PINNED = datetime(1980, 1, 1)


def _argb(hex_colour: str) -> str:
    return "FF" + hex_colour.lstrip("#").upper()


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
    soi = theme.soi
    ncols = 9 if show_premiums else 8
    thin = Side(style="thin", color=_argb(soi.border))
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(name=soi.font, size=soi.size, bold=True,
                       color=_argb(soi.effective_header_text))
    body_font = Font(name=soi.font, size=soi.size, color=_argb(soi.body_text))
    header_fill = PatternFill("solid", fgColor=_argb(soi.header_fill))
    band_fill = PatternFill("solid", fgColor=_argb(soi.band_fill))

    wb = Workbook()
    ws = wb.active
    ws.title = title
    for i, width in enumerate(_WIDTHS[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    for col, text in enumerate(_HEADERS[:ncols], start=1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.font, cell.fill, cell.border = header_font, header_fill, border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 36.0
    ws.freeze_panes = "A2"

    row_ix = 2
    for section in sections:
        if section.label is not None:
            ws.merge_cells(start_row=row_ix, start_column=1,
                           end_row=row_ix, end_column=8 if show_premiums else ncols)
            label = ws.cell(row=row_ix, column=1, value=section.label)
            label.font, label.fill = header_font, header_fill
            label.alignment = Alignment(vertical="center")
            if show_premiums:
                total = ws.cell(row=row_ix, column=9, value=section.premium_total)
                total.font, total.fill = header_font, header_fill
                total.number_format = _CURRENCY
                total.alignment = Alignment(horizontal="right", vertical="center")
            for col in range(1, ncols + 1):
                ws.cell(row=row_ix, column=col).border = border
            ws.row_dimensions[row_ix].height = 22.0
            row_ix += 1
        for band, row in enumerate(section.rows):
            values: list[object] = [
                row.insured, row.coverage, row.carrier, row.policy_number,
                datetime.combine(row.effective, datetime.min.time()),
                datetime.combine(row.expiration, datetime.min.time()),
                row.limits, row.retention,
            ]
            if show_premiums:
                values.append(row.premium)
            for col, value in enumerate(values, start=1):
                cell = ws.cell(row=row_ix, column=col, value=value)
                cell.font, cell.border = body_font, border
                if band % 2 == 1:
                    cell.fill = band_fill
                if col in (5, 6):
                    cell.number_format = _DATE_FMT
                    cell.alignment = Alignment(horizontal="left", vertical="top")
                elif col == 9:
                    cell.number_format = _CURRENCY
                    cell.alignment = Alignment(horizontal="right", vertical="top",
                                               wrap_text=True)
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="top",
                                               wrap_text=True)
            ws.row_dimensions[row_ix].height = _row_height(row.limits, row.retention)
            row_ix += 1

    props = wb.properties
    props.creator = provenance()
    props.created = _PINNED
    props.modified = _PINNED
    props.lastModifiedBy = None

    buffer = BytesIO()
    wb.save(buffer)
    _normalize_zip(buffer.getvalue(), out_path)
    return out_path


def _row_height(limits: str, retention: str) -> float:
    """Wrapped-line estimate for the two prose columns (widths 100 and 34.83);
    the sample uses fixed 36/54 heights, so the floor is two lines."""
    lines = 1
    for text, width in ((limits, 100), (retention, 34)):
        if text:
            lines = max(lines, math.ceil(len(text) / width))
    return 18.0 * max(lines, 2)


def _normalize_zip(data: bytes, out_path: Path) -> None:
    """Rewrite the archive with epoch timestamps and fixed compression so
    identical content is identical bytes (openpyxl stamps wall-clock zip
    entries)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as src, zipfile.ZipFile(
        out_path, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for name in src.namelist():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, src.read(name))
```

- [ ] **Step 5: Run tests**

Run: `uv run --group dev pytest tests/test_soi_xlsx.py -q`
Expected: PASS. If `test_two_writes_are_byte_identical` fails, inspect the differing part by unzipping both files and diffing `docProps/core.xml` first — a missed volatile property, not the zip pass, is the likely cause.

- [ ] **Step 6: Record the dependency consequences**

Append to `changelog.md` under today's heading:

```markdown
- SOI export: new runtime dependency `openpyxl>=3.1` — **run `make wheelhouse`
  and attach the rebuilt `towerkit-wheelhouse-macos.zip` to the next release**
  before updating any corporate machine.
```

Append to the `## SOI export (2026-08-11)` section of `DECISIONS.md`:

```markdown
- **openpyxl chosen over a hand-rolled stdlib xlsx writer** (user call;
  hand-rolled was recommended to avoid the new dep). Costs accepted and
  handled: wheelhouse rebuild on release, and a zip-normalization pass in
  soi_xlsx.py to keep byte-identical output.
- **SOI dates are real Excel dates** formatted mm/dd/yyyy (sample stores
  text) — sortable/filterable, visually identical.
```

- [ ] **Step 7: Full suite + commit**

Run: `uv run --group dev pytest -q && uv run --group dev ruff check src tests`

```bash
git add pyproject.toml uv.lock src/towerkit/render/soi_xlsx.py tests/test_soi_xlsx.py changelog.md DECISIONS.md
git commit -m "SOI xlsx writer: sample-mirroring styles, deterministic output (openpyxl)"
```

---

### Task 6: `towerctl soi` subcommand

**Files:**
- Modify: `src/towerkit/cli.py` (parser wiring after the `compare` block at `:81`; handler after `_cmd_compare`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_soi`, `sheet_title`, `default_filename` (Task 4); `write_soi` (Task 5); existing `_load_for_render`, `_maybe_open`, `load_theme`.
- Produces: `towerctl soi program.json [-o out.xlsx] [--theme path] [--no-premiums]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`, matching its existing `main([...])` call style)

```python
def test_soi_exports_workbook(tmp_path) -> None:
    from towerkit.cli import main

    sample = Path(__file__).parent.parent / "programs" / "atomic-2026.json"
    out = tmp_path / "soi.xlsx"
    assert main(["soi", str(sample), "-o", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 0


def test_soi_default_filename_from_insured(tmp_path, monkeypatch) -> None:
    from towerkit.cli import main
    from towerkit.model import load_program
    from towerkit.soi import default_filename

    sample = Path(__file__).parent.parent / "programs" / "atomic-2026.json"
    monkeypatch.chdir(tmp_path)
    assert main(["soi", str(sample)]) == 0
    assert (tmp_path / default_filename(load_program(sample))).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_cli.py -q`
Expected: FAIL — argparse exits with "invalid choice: 'soi'" (SystemExit).

- [ ] **Step 3: Implement**

Parser block in `_build_parser`, after the `compare` block:

```python
    p_soi = sub.add_parser("soi", help="export a Schedule of Insurance workbook (.xlsx)")
    p_soi.add_argument("path", type=Path, metavar="program.json")
    p_soi.add_argument(
        "-o", "--out", type=Path, default=None,
        help="output file (default: '<Insured> - Schedule of Insurance.xlsx')",
    )
    p_soi.add_argument("--theme", type=Path, default=None)
    p_soi.add_argument(
        "--no-premiums", action="store_true",
        help="omit the Premium column and section roll-ups",
    )
    p_soi.set_defaults(handler=_cmd_soi)
```

Handler after `_cmd_compare` (mirrors `_cmd_render`'s stored-theme fallback):

```python
def _cmd_soi(args: argparse.Namespace) -> int:
    from .render.soi_xlsx import write_soi
    from .soi import build_soi, default_filename, sheet_title
    from .theme import load_theme
    from .validate import ProgramInvalidError

    try:
        program = _load_for_render(args.path)
    except ProgramInvalidError as exc:
        print(exc, file=sys.stderr)
        return 1
    stored = program.render
    theme_path = args.theme or (Path(stored.theme) if stored and stored.theme else None)
    theme = load_theme(theme_path)
    out_path = args.out or Path(default_filename(program))
    written = write_soi(
        build_soi(program),
        title=sheet_title(program),
        theme=theme,
        out_path=out_path,
        show_premiums=not args.no_premiums and (stored.show_premiums if stored else True),
    )
    print(written)
    _maybe_open([written])
    return 0
```

- [ ] **Step 4: Run tests**

Run: `uv run --group dev pytest tests/test_cli.py -q && uv run --group dev pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/cli.py tests/test_cli.py
git commit -m "towerctl soi exports the Schedule of Insurance workbook"
```

---

### Task 7: TUI captures the SOI detail fields

**Files:**
- Modify: `src/towerkit/tui/screens/editor.py:430-437` (`_form_layer` widgets, after the Notes input), `:664-667` (`_commit_layer_field` branches, after the `f-layer-notes` branch), `:1199-1220` (`_FIELD_HANDLERS` table)
- Modify: `DECISIONS.md` (one entry)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `Layer.limits_detail` / `Layer.retention_detail` (Task 1); the existing stamped-node commit flow (`_commit_ref`, `_mutate_and_refresh`) — field commits must resolve against the stamped node, never live selection (blur-race rule; the existing `_commit_layer_field` already does this via `self._commit_ref`).
- Produces: form inputs `#f-layer-limits-detail`, `#f-layer-retention-detail`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_tui.py`, same shape as `TestLayerNotesField` at `tests/test_tui.py:345`)

```python
class TestSoiDetailFields:
    @pytest.mark.asyncio
    async def test_detail_prose_lands_on_the_layer(self, sample_copy, monkeypatch) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            limits = editor.query_one("#f-layer-limits-detail")
            limits.value = "Each Occurrence $5,000,000; Aggregate $5,000,000"
            editor._commit_input(limits)
            retention = editor.query_one("#f-layer-retention-detail")
            retention.value = "Retention $10,000"
            editor._commit_input(retention)
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.limits_detail == "Each Occurrence $5,000,000; Aggregate $5,000,000"
            assert layer.retention_detail == "Retention $10,000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest tests/test_tui.py::TestSoiDetailFields -q`
Expected: FAIL — `query_one("#f-layer-limits-detail")` finds nothing.

- [ ] **Step 3: Implement**

In `_form_layer`, directly after the Notes `Input` (`editor.py:437`):

```python
            Label("SOI limits detail (verbatim in the schedule)", classes="field-label"),
            Input(
                value=layer.limits_detail or "",
                placeholder="e.g. Each Occurrence $1,000,000; Aggregate $2,000,000",
                id="f-layer-limits-detail",
            ),
            Label("SOI deductible / SIR / retention detail", classes="field-label"),
            Input(
                value=layer.retention_detail or "",
                placeholder="e.g. SIR $250,000 each occurrence",
                id="f-layer-retention-detail",
            ),
```

In `_commit_layer_field`, after the `f-layer-notes` branch (`editor.py:667`):

```python
        if wid == "f-layer-limits-detail":
            value = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(layer, "limits_detail", value))
            return
        if wid == "f-layer-retention-detail":
            value = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(layer, "retention_detail", value))
            return
```

In `_FIELD_HANDLERS`, after the `"f-layer-notes"` entry:

```python
    "f-layer-limits-detail": EditorScreen._commit_layer_field,
    "f-layer-retention-detail": EditorScreen._commit_layer_field,
```

- [ ] **Step 4: Record the deviation** — append to `## SOI export (2026-08-11)` in `DECISIONS.md`:

```markdown
- **SOI detail fields are single-line `Input`s in the TUI**, not the spec's
  "multiline" — matching the Notes field and reusing the stamped-ref commit
  flow; a `TextArea` would need its own commit path. Long prose still fits
  (the field scrolls); revisit if editing long schedules in place hurts.
```

- [ ] **Step 5: Run the full check**

Run: `uv run --group dev pytest -q && uv run --group dev ruff check src tests && uv run --group dev mypy src/towerkit`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/tui/screens/editor.py tests/test_tui.py DECISIONS.md
git commit -m "Layer form captures SOI limits/retention detail prose"
```
