# Statutory (unlimited) Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a layer declare that it has no dollar limit — Workers Comp Part A statutory cover — and render it as a full-height bar with a chevron top edge on all three visual surfaces.

**Architecture:** A single `Layer.statutory: bool`, with the invariant `statutory ⇒ limit == 0`. Statutory layers are withheld from `build_y_map`, so they contribute no breakpoints and the global dollar scale is untouched; they are drawn from `y=0.0` to `y=1.0` with a chevron band modelled as real geometry in `TowerLayout.chevrons` spanning `[1.0, 1.04]`.

**Tech Stack:** Python 3.12, pydantic v2, openpyxl, matplotlib, Textual, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-statutory-limits-design.md`

## Global Constraints

- `scale.py` and `layout.py` never import plotting libraries (enforced by `tests/test_conventions.py`).
- `programs/*.json` are the source of truth; saves must stay canonical (zero-diff round trip is tested).
- Rendered SVG/PDF must stay byte-identical across runs.
- New model fields are emitted to JSON **only when truthy** (`followsUnderlying` / `soiSchematic` precedent) so untouched programs re-save byte-identically.
- Gates for every commit: `uv run --group dev pytest -q`, `uv run --group dev ruff check src tests`, `uv run --group dev mypy src/towerkit`.
- Never pipe pytest into `tail`/`grep` before an `&&` — the pipe eats the exit code. Redirect to a file, gate on the command, tail the file.
- `tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration` fails on this machine for environmental reasons (bookctl has no roots configured). It fails identically on pristine `main`. Treat 1 failure / 500 passed as green; any *other* failure is real.

---

### Task 1: Model — `Layer.statutory`, canonical order, schema

**Files:**
- Modify: `src/towerkit/model.py:81-110` (the `Layer` class), `:230-234` (`_LAYER_KEYS`), `:288-324` (`program_to_jsonable`)
- Modify: `schema/program.schema.json` (the `layer` def)
- Test: `tests/test_canonical.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Layer.statutory: bool` (default `False`); the JSON key `"statutory"`, emitted only when `True`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_canonical.py`:

```python
from towerkit.model import dumps_program, loads_program


def _program_json(layer_extra: str) -> str:
    return (
        '{\n'
        '  "$schema": "https://towerkit.dev/schema/program.schema.json",\n'
        '  "insured": "Acme",\n'
        '  "program": "Casualty",\n'
        '  "placement": "bound",\n'
        '  "period": {\n'
        '    "start": "2026-01-01",\n'
        '    "end": "2027-01-01"\n'
        '  },\n'
        '  "currency": "USD",\n'
        '  "lines": [\n'
        '    {\n'
        '      "id": "wc",\n'
        '      "name": "Workers Compensation"\n'
        '    }\n'
        '  ],\n'
        '  "layers": [\n'
        '    {\n'
        '      "id": "wc-stat",\n'
        '      "name": "Workers Compensation",\n'
        '      "appliesTo": [\n'
        '        "wc"\n'
        '      ],\n'
        '      "attach": 0,\n'
        '      "limit": 0,\n'
        f'{layer_extra}'
        '      "participants": [\n'
        '        {\n'
        '          "carrier": "Travelers",\n'
        '          "share": 1\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "retentions": [],\n'
        '  "sublimits": []\n'
        '}\n'
    )


def test_statutory_round_trips_zero_diff() -> None:
    text = _program_json('      "statutory": true,\n')
    assert dumps_program(loads_program(text)) == text


def test_statutory_omitted_when_false() -> None:
    """The followsUnderlying precedent: a program that does not use the
    feature must not gain the key, so untouched files re-save byte-identically
    and older wheels keep loading them."""
    text = _program_json("")
    assert dumps_program(loads_program(text)) == text
    assert "statutory" not in text


def test_statutory_key_sits_after_limit() -> None:
    text = _program_json('      "statutory": true,\n')
    out = dumps_program(loads_program(text))
    assert out.index('"limit"') < out.index('"statutory"') < out.index('"participants"')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_canonical.py -k statutory -v`
Expected: FAIL — pydantic raises `ValidationError: Extra inputs are not permitted [statutory]` (the model sets `extra="forbid"`).

- [ ] **Step 3: Add the field**

In `src/towerkit/model.py`, in `class Layer`, immediately after the `limit` field:

```python
    limit: int
    statutory: bool = False  # no dollar limit (WC Part A); limit MUST be 0
```

- [ ] **Step 4: Add it to the canonical key order**

In `src/towerkit/model.py`, `_LAYER_KEYS`:

```python
_LAYER_KEYS = (
    "id", "name", "policyNumber", "period", "followsUnderlying", "appliesTo",
    "attach", "limit", "statutory", "premium", "limitsDetail",
    "retentionDetail", "participants", "notes",
)
```

- [ ] **Step 5: Emit it only when true**

In `program_to_jsonable`, in the layer dict, immediately after `"limit": layer.limit,`:

```python
                    "limit": layer.limit,
                    # emitted only when true (the followsUnderlying pattern):
                    # untouched programs re-save byte-identically, and older
                    # towerkit wheels only reject files that USE the feature
                    "statutory": layer.statutory or None,
```

- [ ] **Step 6: Add it to the JSON schema**

In `schema/program.schema.json`, in `$defs.layer.properties`, after `"limit"`:

```json
    "statutory": {
      "description": "Coverage with no dollar limit (WC statutory). limit must be 0; excluded from limit totals.",
      "type": "boolean"
    },
```

Leave `limit` in `required` and leave its type as `integer` — positivity is already documented there as a semantic rule enforced by the validator, which is exactly where the exemption belongs.

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest tests/test_canonical.py -v`
Expected: PASS, including the pre-existing round-trip tests.

- [ ] **Step 8: Commit**

```bash
git add src/towerkit/model.py schema/program.schema.json tests/test_canonical.py
git commit -m "model: Layer.statutory — cover with no dollar limit

Emitted to JSON only when true, the followsUnderlying pattern: untouched
programs re-save byte-identically and older wheels only reject files that
actually use the feature. Canonical key sits after limit, which it qualifies.

The invariant statutory => limit == 0 is enforced by the validator, not the
schema — limit is already a plain integer there because positivity is a
semantic rule that keeps drafts loadable."
```

---

### Task 2: Labels — "Statutory" in the terms slot

**Files:**
- Modify: `src/towerkit/render/labels.py:13-27`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: `Layer.statutory` from Task 1.
- Produces: `layer_terms(attach: int, limit: int, statutory: bool = False) -> str`. `layer_heading` is unchanged in signature but reads `layer.statutory` off the `LayerBlock` — **which does not exist until Task 4**. To keep this task independently testable, Task 2 changes `layer_terms` only and Task 4 wires `layer_heading` to it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_labels.py`:

```python
def test_layer_terms_statutory_has_no_dollar_figure() -> None:
    """Statutory cover has no limit to quote — a dollar figure here would be
    a lie, and '$0' reads as a data error."""
    assert layer_terms(0, 0, statutory=True) == "Statutory"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest tests/test_labels.py::test_layer_terms_statutory_has_no_dollar_figure -v`
Expected: FAIL with `TypeError: layer_terms() got an unexpected keyword argument 'statutory'`.

- [ ] **Step 3: Implement**

In `src/towerkit/render/labels.py`:

```python
def layer_terms(attach: int, limit: int, statutory: bool = False) -> str:
    """Market convention: a primary is quoted by its limit alone — 'xs $0'
    is meaningless and reads as an error on a chart. Statutory cover has no
    limit to quote at all."""
    if statutory:
        return "Statutory"
    if attach > 0:
        return f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
    return format_money_compact(limit)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest tests/test_labels.py -v`
Expected: PASS, including the existing `test_layer_terms_market_convention`.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/render/labels.py tests/test_labels.py
git commit -m "labels: statutory cover quotes 'Statutory', never a dollar figure"
```

---

### Task 3: SOI — `limits_text`

**Files:**
- Modify: `src/towerkit/soi.py:38-50`
- Test: `tests/test_soi.py`

**Interfaces:**
- Consumes: `Layer.statutory` from Task 1.
- Produces: no new symbols; `limits_text(layer, program)` returns `"Statutory"` for a statutory layer whose `limits_detail` is unset.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_soi.py`. **`make_program()` in this file takes NO arguments** — it returns a fixed sample program. `limits_text` only reads `program.sublimits` off it, so that program is a fine backdrop; the layer under test is passed separately and need not belong to it.

```python
from towerkit.model import Layer
from towerkit.soi import limits_text


def _statutory_layer(**kw) -> Layer:
    base = dict(
        id="wc-stat", name="Workers Compensation", applies_to=["wc"],
        attach=0, limit=0, statutory=True,
    )
    return Layer(**{**base, **kw})


def test_limits_text_statutory() -> None:
    assert limits_text(_statutory_layer(), make_program()) == "Statutory"


def test_limits_detail_still_wins_over_statutory() -> None:
    """towerkit does not invent a sentence about state law. If the broker
    wants the long form on the SOI they type it, and it wins."""
    layer = _statutory_layer(limits_detail="Benefits as required by NY state law")
    assert limits_text(layer, make_program()) == "Benefits as required by NY state law"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_soi.py -k statutory -v`
Expected: FAIL — `test_limits_text_statutory` gets `"$0"` instead of `"Statutory"`.

- [ ] **Step 3: Implement**

In `src/towerkit/soi.py`, inside `limits_text`, the statutory branch goes **after** the `limits_detail` override and **before** the follows/primary/excess branches:

```python
def limits_text(layer: Layer, program: Program) -> str:
    if layer.limits_detail:
        return layer.limits_detail
    if layer.statutory:
        base = "Statutory"
    elif layer.follows_underlying:
        base = f"{format_money(layer.limit)} xs underlying"
    elif layer.attach == 0:
        base = format_money(layer.limit)  # primaries by limit alone, never "xs $0"
    else:
        base = f"{format_money(layer.limit)} xs {format_money(layer.attach)}"
    ...
```

Keep the existing sublimit-appending tail exactly as it is — a statutory layer can still carry sublimits.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest tests/test_soi.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/towerkit/soi.py tests/test_soi.py
git commit -m "soi: statutory layers show 'Statutory' in the Limits column

Placed after the limitsDetail override so a typed long form still wins —
towerkit does not put words in the broker's mouth about state law."
```

---

### Task 4: Layout — no-breakpoint geometry, full-column bar

**Files:**
- Modify: `src/towerkit/layout.py:81-92` (`LayerBlock`), `:128-193` (`build_layout`)
- Modify: `src/towerkit/render/labels.py:21-27` (`layer_heading`)
- Test: `tests/test_layout.py`, `tests/test_labels.py`

**Interfaces:**
- Consumes: `Layer.statutory` (Task 1), `layer_terms(..., statutory=)` (Task 2).
- Produces: `LayerBlock.statutory: bool` (new field, default `False`, declared **after** `outlines` because it has a default). `build_layout` now admits statutory layers.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_layout.py`:

```python
def statutory_layer(id: str, applies: list[str], shares) -> Layer:
    return Layer(
        id=id, name=id, applies_to=applies, attach=0, limit=0, statutory=True,
        participants=[Participant(carrier=c, share_bps=b) for c, b in shares],
    )


class TestStatutory:
    def test_does_not_move_any_other_layer(self) -> None:
        """THE load-bearing invariant. scale.py builds ONE global map over the
        program's breakpoints and the whole design hangs on $52M sitting at the
        same height in every column. A statutory layer must contribute no
        breakpoints, so adding one cannot shift anything else by a single
        float."""
        layers = [
            layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
            layer("gl-excess", ["gl"], 5_000_000, 20_000_000, [("B", 10_000)]),
        ]
        before = build_layout(make_program(["gl"], layers))
        after = build_layout(
            make_program(
                ["gl", "wc"],
                [*layers, statutory_layer("wc-stat", ["wc"], [("C", 10_000)])],
            )
        )
        baseline = {b.layer_id: (b.y0, b.y1) for b in before.layers}
        for block in after.layers:
            if block.layer_id in baseline:
                assert (block.y0, block.y1) == baseline[block.layer_id], block.layer_id

    def test_occupies_the_full_column(self) -> None:
        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("wc-stat", ["wc"], [("C", 10_000)]),
                ],
            )
        )
        stat = next(b for b in tower.layers if b.layer_id == "wc-stat")
        assert (stat.y0, stat.y1) == (0.0, 1.0)
        assert stat.statutory is True

    def test_contributes_no_breakpoints(self) -> None:
        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("wc-stat", ["wc"], [("C", 10_000)]),
                ],
            )
        )
        assert tower.ymap.breakpoints == (0, 5_000_000)

    def test_statutory_only_program_is_drawable(self) -> None:
        """build_y_map([]) returns the degenerate YMap. The bar still draws
        floor to top; there are simply no axis labels."""
        tower = build_layout(
            make_program(["wc"], [statutory_layer("wc-stat", ["wc"], [("C", 10_000)])])
        )
        stat = next(b for b in tower.layers if b.layer_id == "wc-stat")
        assert (stat.y0, stat.y1) == (0.0, 1.0)
        assert len(tower.participants) == 1

    def test_participants_allocate_across_the_bar(self) -> None:
        tower = build_layout(
            make_program(
                ["wc"],
                [statutory_layer("wc-stat", ["wc"], [("A", 6_000), ("B", 4_000)])],
            )
        )
        blocks = [b for b in tower.participants if b.layer_id == "wc-stat"]
        assert [b.carrier for b in blocks] == ["A", "B"]
        assert all(r.y0 == 0.0 and r.y1 == 1.0 for b in blocks for r in b.rects)
```

Add to `tests/test_labels.py` — extend the local `_layer` helper with a `statutory` parameter:

```python
def _layer(name: str, attach: int, limit: int, statutory: bool = False) -> LayerBlock:
    return LayerBlock(
        layer_id="x", name=name, attach=attach, limit=limit, premium=None,
        signed_bps=10_000, y0=0.0, y1=1.0, outlines=(), statutory=statutory,
    )


def test_layer_heading_statutory() -> None:
    block = _layer("Workers Compensation", 0, 0, statutory=True)
    assert layer_heading(block, follows=False) == "Workers Compensation — Statutory"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_layout.py::TestStatutory tests/test_labels.py -v`
Expected: FAIL — `TypeError: LayerBlock.__init__() got an unexpected keyword argument 'statutory'`, and the layout tests find no `wc-stat` block because `build_layout` filters on `limit > 0`.

- [ ] **Step 3: Add the field to `LayerBlock`**

In `src/towerkit/layout.py`:

```python
@dataclass(frozen=True)
class LayerBlock:
    layer_id: str
    name: str
    attach: int
    limit: int
    premium: int | None
    signed_bps: int
    y0: float
    y1: float
    outlines: tuple[Rect, ...]  # one per run
    statutory: bool = False  # no dollar limit: drawn floor to top, off-scale
```

- [ ] **Step 4: Admit statutory layers and withhold them from the scale**

In `build_layout`, replace the `drawable` / `stepped` / `ymap` block:

```python
    drawable = [
        layer
        for layer in program.layers
        if (layer.limit > 0 or layer.statutory)
        and any(lid in order for lid in layer.applies_to)
    ]
    # Statutory cover has no dollar top, so it contributes NO breakpoints:
    # scale.py's global map is built from the dollar-scaled layers alone and
    # is bit-identical whether or not a statutory layer is present.
    scaled = [layer for layer in drawable if not layer.statutory]
    # a follows-underlying layer has a stepped bottom: one y per column,
    # sitting on that column's stack — all of them become breakpoints
    stepped: dict[str, dict[str, int]] = {
        layer.id: program.underlying_tops(layer)
        for layer in scaled
        if layer.follows_underlying
    }
    extra_points = [top for tops in stepped.values() for top in tops.values()]
    ymap = build_y_map(scaled, gamma=gamma, extra_points=extra_points)
```

- [ ] **Step 5: Draw the bar floor to top**

In the `for layer in drawable:` loop, replace the y assignment:

```python
    for layer in drawable:
        runs = _runs(columns, sorted({order[lid] for lid in layer.applies_to if lid in order}))
        if layer.statutory:
            y0, y1 = 0.0, 1.0  # the whole column; the chevron band marks "continues"
        else:
            y0, y1 = ymap.y(layer.attach), ymap.y(layer.top)
```

and pass the flag when building the block:

```python
        layer_blocks.append(
            LayerBlock(
                layer_id=layer.id,
                name=layer.name,
                attach=layer.attach,
                limit=layer.limit,
                premium=layer.premium,
                signed_bps=layer.signed_bps,
                y0=y0,
                y1=y1,
                outlines=outlines,
                statutory=layer.statutory,
            )
        )
```

- [ ] **Step 6: Wire `layer_heading` to the flag**

In `src/towerkit/render/labels.py`:

```python
def layer_heading(layer: LayerBlock, follows: bool, marker: str = "") -> str:
    if layer.statutory:
        terms = layer_terms(layer.attach, layer.limit, statutory=True)
    elif follows:
        terms = f"{format_money_compact(layer.limit)} xs underlying"
    else:
        terms = layer_terms(layer.attach, layer.limit)
    return f"{layer.name}{marker} — {terms}"
```

Statutory is checked first because the two are mutually exclusive — nothing underlies a statutory bar.

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest tests/test_layout.py tests/test_labels.py tests/test_scale.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/towerkit/layout.py src/towerkit/render/labels.py tests/test_layout.py tests/test_labels.py
git commit -m "layout: statutory layers draw floor to top, off the dollar scale

Withheld from build_y_map, so they contribute no breakpoints and the global
map is bit-identical whether or not one is present — the property the whole
design hangs on. test_does_not_move_any_other_layer pins exactly that, and
is the test that would catch a naive 'just give it a big limit'."
```

---

### Task 5: Layout — the chevron band

**Files:**
- Modify: `src/towerkit/layout.py` (module constants, `TowerLayout`, `build_layout`)
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: Task 4's `drawable` / `runs`.
- Produces: `layout.CHEVRON_BAND: float = 0.04` and `TowerLayout.chevrons: tuple[Rect, ...]` — one rect per statutory run, spanning `[1.0, 1.0 + CHEVRON_BAND]`. All three renderers consume this.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_layout.py`, inside `class TestStatutory`:

```python
    def test_chevron_band_sits_above_the_tower(self) -> None:
        from towerkit.layout import CHEVRON_BAND

        tower = build_layout(
            make_program(
                ["gl", "wc"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("wc-stat", ["wc"], [("C", 10_000)]),
                ],
            )
        )
        assert len(tower.chevrons) == 1
        band = tower.chevrons[0]
        assert (band.y0, band.y1) == (1.0, 1.0 + CHEVRON_BAND)
        stat = next(b for b in tower.layers if b.layer_id == "wc-stat")
        assert (band.x0, band.x1) == (stat.outlines[0].x0, stat.outlines[0].x1)

    def test_no_chevrons_without_statutory_cover(self) -> None:
        """The band is added ONLY when a statutory layer exists, so every
        existing program's geometry — and its golden hash — is untouched."""
        tower = build_layout(
            make_program(["gl"], [layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)])])
        )
        assert tower.chevrons == ()

    def test_one_chevron_per_run(self) -> None:
        """A statutory layer spanning non-contiguous columns is several runs,
        and each needs its own band."""
        tower = build_layout(
            make_program(
                ["wc", "gl", "wc2"],
                [
                    layer("gl-primary", ["gl"], 0, 5_000_000, [("A", 10_000)]),
                    statutory_layer("stat", ["wc", "wc2"], [("C", 10_000)]),
                ],
            )
        )
        assert len(tower.chevrons) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_layout.py::TestStatutory -v`
Expected: FAIL with `ImportError: cannot import name 'CHEVRON_BAND'` and `AttributeError: 'TowerLayout' object has no attribute 'chevrons'`.

- [ ] **Step 3: Add the constant**

In `src/towerkit/layout.py`, with the other module constants:

```python
RETENTION_BAND = 0.18  # height of the retention band, in tower-height units
# Height of the chevron band drawn ABOVE a statutory bar, in the same units.
# It is geometry, not decoration: ascii.py's rule is that a drawing decision
# needing geometry not already in the layout belongs here, so all three
# renderers consume the same rects instead of each inventing a band.
CHEVRON_BAND = 0.04
```

- [ ] **Step 4: Add the field to `TowerLayout`**

```python
@dataclass(frozen=True)
class TowerLayout:
    columns: tuple[Column, ...]
    layers: tuple[LayerBlock, ...]
    participants: tuple[ParticipantBlock, ...]
    retentions: tuple[RetentionBlock, ...]
    ymap: YMap
    ref_lines: tuple[tuple[int, float], ...]  # (dollars, y) at real attachment points
    groups: tuple[GroupBand, ...]
    width: float
    retention_band: float
    chevrons: tuple[Rect, ...] = ()  # one per statutory run, above y=1.0
```

- [ ] **Step 5: Collect the rects**

In `build_layout`, declare the accumulator next to `layer_blocks`:

```python
    layer_blocks: list[LayerBlock] = []
    participant_blocks: list[ParticipantBlock] = []
    chevron_rects: list[Rect] = []
```

and inside the `for layer in drawable:` loop, right after `runs` is computed and the statutory branch sets y:

```python
        if layer.statutory:
            y0, y1 = 0.0, 1.0  # the whole column; the chevron band marks "continues"
            chevron_rects.extend(
                Rect(run.x0, 1.0, run.x1, 1.0 + CHEVRON_BAND) for run in runs
            )
        else:
            y0, y1 = ymap.y(layer.attach), ymap.y(layer.top)
```

and pass it in the return:

```python
    return TowerLayout(
        ...
        retention_band=RETENTION_BAND if retention_blocks else 0.0,
        chevrons=tuple(chevron_rects),
    )
```

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest tests/test_layout.py tests/test_conventions.py -v`
Expected: PASS. `test_conventions.py` confirms `layout.py` still imports no plotting library.

- [ ] **Step 7: Commit**

```bash
git add src/towerkit/layout.py tests/test_layout.py
git commit -m "layout: chevron band as real geometry, one rect per statutory run

Modelled in layout.py rather than reinvented per renderer, per ascii.py's own
rule. Added ONLY when a statutory layer exists, so existing programs keep
their geometry and SCHEMATIC_GOLDEN_SHA does not churn."
```

---

### Task 6: Validator — new rules, and two checks that are wrong today

**Files:**
- Modify: `src/towerkit/validate.py:168-209` (`_check_layer`), `:247-255` (`_check_line_stack`)
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `Layer.statutory` (Task 1).
- Produces: diagnostic codes `statutory-limit`, `statutory-attach`, `statutory-follows`, `statutory-line-shared`.

**Note on scope:** `statutory-line-shared` is not named in the spec, but the spec puts "a statutory bar with something attaching above it" out of scope. This rule is how "out of scope" becomes "rejected" rather than "silently mis-drawn" — a dollar layer on a statutory line would render at some scaled height straight through the full-height bar.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validate.py`. **`make_program(**overrides)` in this file is KEYWORD-only** — it builds a fixed clean program and applies overrides, so a statutory program means overriding `lines`, `layers` and `retentions` together. `codes(program)` returns the set of diagnostic codes and already exists.

```python
def _stat(**kw) -> Layer:
    base = dict(
        id="wc-stat", name="Workers Compensation", applies_to=["wc"],
        attach=0, limit=0, statutory=True,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
    )
    return Layer(**{**base, **kw})


def _wc_program(*layers: Layer) -> Program:
    return make_program(
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=list(layers),
        retentions=[],
    )


def test_statutory_layer_is_exempt_from_the_positive_limit_rule() -> None:
    assert "layer-limit" not in codes(_wc_program(_stat()))


def test_statutory_with_a_limit_is_an_error() -> None:
    assert "statutory-limit" in codes(_wc_program(_stat(limit=1_000_000)))


def test_statutory_with_an_attachment_is_an_error() -> None:
    assert "statutory-attach" in codes(_wc_program(_stat(attach=500_000)))


def test_statutory_cannot_follow_underlying() -> None:
    assert "statutory-follows" in codes(_wc_program(_stat(follows_underlying=True)))


def test_statutory_line_reports_no_phantom_gap() -> None:
    """A line covered only by a statutory layer is fully covered. Left alone,
    the limit > 0 filter drops it from the stack and the line reads as empty —
    which would tint the WC column danger-red in the live preview."""
    found = codes(_wc_program(_stat()))
    assert "line-empty" not in found
    assert "line-base" not in found
    assert "line-gap" not in found


def test_statutory_line_rejects_a_second_layer() -> None:
    program = _wc_program(
        _stat(),
        Layer(
            id="wc-xs", name="WC Excess", applies_to=["wc"],
            attach=0, limit=1_000_000,
            participants=[Participant(carrier="A", share_bps=10_000)],
        ),
    )
    assert "statutory-line-shared" in codes(program)


def test_statutory_unplaced_is_reported_as_a_share_not_dollars() -> None:
    program = _wc_program(
        _stat(participants=[Participant(carrier="Travelers", share_bps=6_000)])
    )
    messages = [d.message for d in validate_program(program).items]
    unplaced = next(m for m in messages if "open" in m or "unplaced" in m)
    assert "$0" not in unplaced
    assert "40% open" in unplaced
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_validate.py -k statutory -v`
Expected: FAIL — `layer-limit` fires, the `statutory-*` codes do not exist, `line-empty` fires, and the unplaced warning says `$0 unplaced`.

- [ ] **Step 3: Rewrite the limit and unplaced checks in `_check_layer`**

Replace the opening of `_check_layer`:

```python
def _check_layer(layer: Layer, line_ids: list[str], diags: Diagnostics) -> None:
    ref = ("layer", layer.id)
    if layer.statutory:
        # statutory => limit == 0 is the invariant the whole design rests on:
        # it is what keeps the layer out of every dollar total by construction
        if layer.limit != 0:
            diags.error(
                "statutory-limit",
                f"{layer.name}: statutory cover has no dollar limit, but limit "
                f"is {format_money(layer.limit)}",
                ref,
            )
        if layer.attach != 0:
            diags.error(
                "statutory-attach",
                f"{layer.name}: statutory cover owns its column from $0, but "
                f"attaches at {format_money(layer.attach)}",
                ref,
            )
        if layer.follows_underlying:
            diags.error(
                "statutory-follows",
                f"{layer.name}: statutory cover cannot follow underlying — "
                f"nothing sits beneath it",
                ref,
            )
    elif layer.limit <= 0:
        diags.error("layer-limit", f"{layer.name}: non-positive limit {layer.limit}", ref)
```

and replace the unplaced branch at the end of the same function:

```python
    signed = layer.signed_bps
    if signed > BPS_SCALE:
        diags.error(
            "layer-oversigned",
            f"{layer.name}: shares sum to {signed / 100:.2f}% — over-signed",
            ref,
        )
    elif signed < BPS_SCALE and layer.statutory:
        # no dollar limit to apportion: quantify the hole as a share
        diags.warn(
            "layer-unplaced",
            f"{layer.name}: {signed / 100:g}% placed — {(BPS_SCALE - signed) / 100:g}% open",
            ref,
        )
    elif signed < BPS_SCALE and layer.limit > 0:
        unplaced = layer.limit * (BPS_SCALE - signed) // BPS_SCALE
        diags.warn(
            "layer-unplaced",
            f"{layer.name}: {signed / 100:g}% placed — {format_money(unplaced)} unplaced",
            ref,
        )
```

- [ ] **Step 4: Teach `_check_line_stack` that statutory covers the column**

Replace the opening of `_check_line_stack`:

```python
def _check_line_stack(program: Program, line_id: str, diags: Diagnostics) -> None:
    line = next(ln for ln in program.lines if ln.id == line_id)
    covering = program.layers_for_line(line_id)
    statutory = [ly for ly in covering if ly.statutory]
    stack = [ly for ly in covering if not ly.statutory and ly.limit > 0]
    if statutory:
        # A statutory bar owns its column floor to top. Base/gap/overlap are
        # dollar questions and do not apply; the only error possible here is
        # something ELSE trying to share the column, which would draw straight
        # through the full-height bar.
        if stack:
            diags.error(
                "statutory-line-shared",
                f"{line_id}: {statutory[0].name} is statutory and covers the whole "
                f"column, but {stack[0].name!r} also covers {line.name}",
                ("line", line_id),
            )
        return
    stack.sort(key=lambda ly: _effective_attach(program, ly, line_id))
    if not stack:
        diags.error("line-empty", f"{line_id}: no layers cover {line.name}", ("line", line_id))
        return
```

Delete the now-duplicated `line = next(...)` further down, and leave the rest of the function (base / gap / overlap loops) untouched.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest tests/test_validate.py -v`
Expected: PASS, including every pre-existing validator test.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/validate.py tests/test_validate.py
git commit -m "validate: statutory rules, and two checks that were wrong for it

New: statutory-limit, statutory-attach, statutory-follows, and
statutory-line-shared (the spec puts a shared statutory column out of scope;
this is how that becomes rejected rather than silently mis-drawn).

Fixed: _check_line_stack filtered on limit > 0, so a line covered ONLY by a
statutory layer read as empty and reported a phantom gap — which would tint
the WC column danger-red in the live preview. And the unplaced warning
apportioned a zero limit, printing '\$0 unplaced'; it now reports the open
share."
```

---

### Task 7: ASCII preview — the caret row

**Files:**
- Modify: `src/towerkit/render/ascii.py:76-157` (`_render_layout`), `:160-176` (`_stamp_initial`), `:197-221` (`_attach_labels`)
- Test: `tests/test_ascii.py`

**Interfaces:**
- Consumes: `TowerLayout.chevrons` (Task 5).
- Produces: no new public symbols. `_attach_labels` gains a `chev_rows: int` parameter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ascii.py`. **This file has no `make_program` helper and no theme fixture** — it has `render(colour=False, **kw)`, which renders a fixed SAMPLE file with `load_theme()`. Build the program inline and call `render_ascii` directly, matching that idiom. Add `Layer`, `Line`, `Participant`, `Period`, `Placement`, `Program` to the existing `towerkit.model` import.

```python
def _wc_program(*layers: Layer) -> Program:
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=list(layers),
    )


def test_statutory_draws_a_caret_row() -> None:
    program = _wc_program(
        Layer(
            id="wc-stat", name="Workers Compensation", applies_to=["wc"],
            attach=0, limit=0, statutory=True,
            participants=[Participant(carrier="Travelers", share_bps=10_000)],
        )
    )
    out = render_ascii(program, load_theme(), colour=False)
    assert "^" in out.splitlines()[0]


def test_no_caret_row_without_statutory_cover() -> None:
    program = _wc_program(
        Layer(
            id="gl-primary", name="Primary", applies_to=["wc"],
            attach=0, limit=5_000_000,
            participants=[Participant(carrier="A", share_bps=10_000)],
        )
    )
    assert "^" not in render_ascii(program, load_theme(), colour=False)
```

`date` needs importing from `datetime` if the file does not already have it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_ascii.py -k caret -v`
Expected: FAIL — no `^` anywhere in the output.

- [ ] **Step 3: Reserve the row and offset every row index**

In `_render_layout`, replace the sizing and the two mapping helpers:

```python
    label_w = 0 if width < 46 else (14 if width < 76 else 20)
    chart_w = max(len(tower.columns) * 2, width - label_w - 1)
    ret_rows = 2 if tower.retentions else 0
    # one reserved row above the tower for the statutory chevron band; the
    # band lives at y > 1.0, which to_row would otherwise map to a negative row
    chev_rows = 1 if tower.chevrons else 0
    tower_rows = max(4, height - ret_rows - 2 - chev_rows)
    carrier_colours = theme.carrier_colours(program.carriers())

    def to_col(x: float) -> int:
        return round(x / tower.width * chart_w)

    def to_row(y: float) -> int:
        """Tower y ∈ [0,1] → grid row; y=1 is the first row under the chevron
        band, y=0 is the zero line."""
        return chev_rows + round((1.0 - y) * tower_rows)

    grid = [
        [_Cell() for _ in range(chart_w)]
        for _ in range(chev_rows + tower_rows + 1 + ret_rows + 1)
    ]
    zero_row = chev_rows + tower_rows
```

- [ ] **Step 4: Offset the no-cover background loop**

```python
    for column in tower.columns:
        broken = column.line_id in error_lines
        bg = ansi256(DANGER) if broken else ansi256(theme.chrome.grid)
        for row in range(chev_rows, zero_row):
            for col in range(to_col(column.x0), to_col(column.x1)):
                grid[row][col] = _Cell(NO_COVER, bg, dim=not broken)
```

- [ ] **Step 5: Clamp participant blocks to the zero line, not `tower_rows`**

In the participant loop, replace the two clamps and the `_stamp_initial` call:

```python
            for row in range(max(r0, chev_rows), min(r1, zero_row)):
                for col in range(c0, min(c1, chart_w)):
                    grid[row][col] = _Cell(cell.char, cell.colour, cell.dim)
            _stamp_initial(grid, block.carrier, r0, r1, c0, c1, chev_rows, zero_row)
```

and update `_stamp_initial`:

```python
def _stamp_initial(
    grid: list[list[_Cell]],
    carrier: str | None,
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    top_row: int,
    zero_row: int,
) -> None:
    if carrier is None or c1 - c0 < 3:
        return
    row = (max(r0, top_row) + min(r1, zero_row)) // 2
    col = (c0 + c1) // 2
    if top_row <= row < zero_row and 0 <= col < len(grid[0]):
        base = grid[row][col]
        grid[row][col] = _Cell(carrier[0], base.colour, dim=False)
```

- [ ] **Step 6: Draw the carets**

Immediately before the "heavy zero line" block:

```python
    # statutory chevron band: the bar's top edge, marking cover that continues
    for rect in tower.chevrons:
        for col in range(to_col(rect.x0), min(to_col(rect.x1), chart_w)):
            for row in range(chev_rows):
                grid[row][col] = _Cell("^", ansi256(theme.chrome.ink))
```

- [ ] **Step 7: Fix the right-gutter labels**

Change the call site and the function:

```python
    lines = [_row_to_text(row_cells, colour) for row_cells in grid]
    if label_w:
        _attach_labels(lines, tower, label_w, zero_row, ret_rows, chev_rows)
    return "\n".join(lines)
```

```python
def _attach_labels(
    lines: list[str],
    tower: TowerLayout,
    label_w: int,
    zero_row: int,
    ret_rows: int,
    chev_rows: int = 0,
) -> None:
    """Right-gutter labels: layer names at their mid-height, $0 at the rule,
    'Retention' under it, and a not-to-scale caveat."""
    tower_rows = zero_row - chev_rows
    labels: dict[int, str] = {}
    for block in sorted(tower.layers, key=lambda b: -b.attach):
        mid = chev_rows + round((1.0 - (block.y0 + block.y1) / 2) * tower_rows)
        mid = min(max(mid, chev_rows), zero_row - 1)
        while mid in labels and mid + 1 < zero_row:
            mid += 1
        placed = min(100.0, block.signed_bps / 100)
        suffix = "" if block.signed_bps >= 10_000 else f" ({placed:g}%)"
        labels[mid] = f"{block.name}{suffix}"
    labels[zero_row] = "$0"
    if tower.layers:
        top_label = f"top {format_money_compact(tower.ymap.max_dollars)}"
        labels.setdefault(chev_rows, top_label)
    if ret_rows:
        labels[zero_row + 1] = "Retention"
    labels[zero_row + ret_rows + 1] = "(not to scale)" if tower.ymap.gamma != 1.0 else ""
    for row, text in labels.items():
        if row < len(lines) and text:
            lines[row] = f"{lines[row]}  {text[: label_w - 2]}"
```

- [ ] **Step 8: Run the tests**

Run: `uv run --group dev pytest tests/test_ascii.py tests/test_tui.py -v`
Expected: PASS. `test_tui.py` matters here — the live preview renders through this path.

- [ ] **Step 9: Commit**

```bash
git add src/towerkit/render/ascii.py tests/test_ascii.py
git commit -m "ascii: reserved caret row for the statutory chevron band

to_row maps y=1.04 to a negative row, so the band needs a reserved row rather
than falling out of the uniform treatment. Every row index shifts by chev_rows
— background, blocks, initials, the zero line and the gutter labels."
```

---

### Task 8: Graphic — zigzag top edge

**Files:**
- Modify: `src/towerkit/render/mpl_program.py:164-176` (the outline loop)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `TowerLayout.chevrons` and `LayerBlock.statutory`.
- Produces: module constant `CHEVRON_TEETH_PER_UNIT: float = 8.0`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`. **The theme fixture in this file is named `theme`, not `marsh`**, and the real signature is `render_program(program, theme, out_dir, stem, formats) -> list[Path]` — copy the call shape from the neighbouring `test_two_renders_are_byte_identical_svg`. There is no `make_program` helper; build the program inline.

**Import order matters on this machine:** never `import matplotlib` before something from `towerkit.render` — `towerkit/render/__init__.py` sets `MPL_IGNORE_SYSTEM_FONTS=1`, without which matplotlib's system-font scan crashes on macOS 27. The module already imports `render_program` at the top, so importing `draw_tower` from `towerkit.render.mpl_program` inside the test is safe; `matplotlib.figure` may then be imported after it.

```python
def _wc_program() -> Program:
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=[Layer(
            id="wc-stat", name="Workers Compensation", applies_to=["wc"],
            attach=0, limit=0, statutory=True,
            participants=[Participant(carrier="Travelers", share_bps=10_000)],
        )],
    )


def test_statutory_svg_is_byte_identical_across_runs(theme, tmp_path) -> None:
    """The project rule, exercised on the new code path."""
    program = _wc_program()
    a = render_program(program, theme, tmp_path / "a", "tower", ["svg"])[0]
    b = render_program(program, theme, tmp_path / "b", "tower", ["svg"])[0]
    assert a.read_bytes() == b.read_bytes()


def test_statutory_draws_no_closed_outline_box(theme) -> None:
    """The chevron REPLACES the top edge. A closed Rectangle plus carets
    would read as a bounded bar wearing a hat."""
    from towerkit.render.mpl_program import draw_tower  # sets MPL env first
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    ax = Figure().add_subplot()
    tower = draw_tower(ax, _wc_program(), theme)
    assert tower.chevrons
    stat = next(b for b in tower.layers if b.statutory)
    unfilled_full_height = [
        p for p in ax.patches
        if isinstance(p, Rectangle)
        and abs(p.get_height() - (stat.y1 - stat.y0)) < 1e-9
        and p.get_facecolor()[3] == 0.0
    ]
    assert unfilled_full_height == []
```

Add `Layer`, `Line`, `Participant`, `Period`, `Placement`, `Program` to the `towerkit.model` import and `date` from `datetime`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_render.py -k statutory -v`
Expected: FAIL on the zigzag test — an unfilled full-height `Rectangle` outline is present.

- [ ] **Step 3: Add the constant**

Near the top of `src/towerkit/render/mpl_program.py`:

```python
# Chevron teeth per 1.0 of column width, so teeth stay the same visual size
# whatever the column count (Grant: the carets read as "continues upward").
CHEVRON_TEETH_PER_UNIT = 8.0
```

- [ ] **Step 4: Draw statutory outlines open-topped, and the zigzag**

Replace the outline loop:

```python
    # layer outlines: solid for placed layers, dashed for pending ones.
    # A statutory layer is drawn open-topped — its top edge is the chevron
    # band below, not a line.
    for layer in tower.layers:
        is_pending = layer.layer_id in pending
        if layer.statutory:
            for outline in layer.outlines:
                ax.plot(
                    [outline.x0, outline.x0, outline.x1, outline.x1],
                    [outline.y1, outline.y0, outline.y0, outline.y1],
                    color=chrome.ink, linewidth=1.1, zorder=3,
                    solid_capstyle="butt",
                )
            continue
        for outline in layer.outlines:
            ax.add_patch(
                Rectangle(
                    (outline.x0, outline.y0), outline.width, outline.height,
                    facecolor="none", edgecolor=chrome.ink,
                    linewidth=1.2 if is_pending else 1.1,
                    linestyle=(0, (4, 3)) if is_pending else "solid",
                    zorder=3,
                )
            )

    # the chevron band: a zigzag standing in for the top edge of unlimited cover
    for band in tower.chevrons:
        teeth = max(3, round(band.width * CHEVRON_TEETH_PER_UNIT))
        xs = [band.x0 + band.width * i / (2 * teeth) for i in range(2 * teeth + 1)]
        ys = [band.y0 if i % 2 == 0 else band.y1 for i in range(2 * teeth + 1)]
        ax.plot(
            xs, ys, color=chrome.ink, linewidth=1.1, zorder=3,
            solid_capstyle="butt", solid_joinstyle="miter",
        )
```

No `set_ylim` change is needed: it is already `(bottom, 1.06)` and the band tops out at `1.04`.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest tests/test_render.py -v`
Expected: PASS, including the existing byte-identity and golden tests.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/render/mpl_program.py tests/test_render.py
git commit -m "render: statutory bars draw open-topped under a zigzag band

The chevron REPLACES the top edge rather than sitting above a closed box —
otherwise it reads as a bounded bar wearing a hat. Tooth count scales with
column width so teeth stay the same visual size at any line count. set_ylim
already had headroom to 1.06, so the axes do not move."
```

---

### Task 9: XLSX schematic — caret band, open-topped blocks

**Files:**
- Modify: `src/towerkit/render/schematic_xlsx.py:227-262` (`_label_spans`), `:265-304` (`x_boundaries` / `y_boundaries`), `:434-481` (the participant loop), and `add_schematic_sheet` after the retention loop
- Test: `tests/test_schematic_xlsx.py`

**Interfaces:**
- Consumes: `TowerLayout.chevrons`, `LayerBlock.statutory`.
- Produces: no new public symbols.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schematic_xlsx.py`. **`make_program` in this file is imported from `test_soi` and takes no arguments** — do not call it with lines/layers. Build the program inline, following the file's own `_mini_program()` helper. The `marsh` and `program` fixtures do exist and are used as written below.

```python
def _statutory_program() -> Program:
    return Program(
        insured="T",
        program="T",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=[Layer(
            id="wc-stat", name="Workers Compensation", applies_to=["wc"],
            attach=0, limit=0, statutory=True,
            participants=[Participant(carrier="Travelers", share_bps=10_000)],
        )],
    )


class TestStatutory:
    def test_caret_band_is_written_above_the_tower(self, marsh) -> None:
        wb = Workbook()
        wb.remove(wb.active)
        add_schematic_sheet(wb, _statutory_program(), marsh)
        ws = wb.worksheets[0]
        carets = [
            c.value for row in ws.iter_rows() for c in row
            if isinstance(c.value, str) and set(c.value) == {"^"}
        ]
        assert carets, "no caret band written"

    def test_statutory_block_has_no_top_border(self, marsh) -> None:
        wb = Workbook()
        wb.remove(wb.active)
        add_schematic_sheet(wb, _statutory_program(), marsh)
        ws = wb.worksheets[0]
        filled = [
            c for row in ws.iter_rows() for c in row
            if c.fill is not None and c.fill.fgColor is not None
            and c.border.left is not None and c.border.left.style == "thin"
        ]
        assert filled
        assert all(c.border.top is None or c.border.top.style is None for c in filled)

    def test_existing_programs_keep_their_geometry(self, program, marsh) -> None:
        """The band is added only when a statutory layer exists. A program
        without one must quantize to exactly the rows it did before."""
        layout = build_layout(program)
        assert layout.chevrons == ()
        rows = quantize_boundaries(y_boundaries(layout))
        assert max(rows.values()) == TOTAL_ROWS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_schematic_xlsx.py::TestStatutory -v`
Expected: FAIL — no caret cells, and the statutory block carries a top border.

- [ ] **Step 3: Include the band in the boundary sets**

In `x_boundaries`, after the retention loop:

```python
    for band in layout.chevrons:
        edges.add(band.x0)
        edges.add(band.x1)
    return tuple(sorted(edges))
```

In `y_boundaries`, after the retention loop:

```python
    for band in layout.chevrons:
        ys.add(band.y0)
        ys.add(band.y1)
    return tuple(sorted(ys))
```

- [ ] **Step 4: Floor the band's row height**

At the end of `_label_spans`, after the retention `spans.extend(...)`:

```python
    # the caret band carries text, so quantization must not collapse it
    spans.extend(
        (band.y0, band.y1, label_row_floor(1)) for band in layout.chevrons
    )
    return spans
```

- [ ] **Step 5: Drop the top border on statutory blocks**

In the participant loop in `add_schematic_sheet`, replace the single `border=` argument. Compute it once before the `for rect in block.rects:` loop:

```python
        border = Border(left=edge, right=edge, top=edge, bottom=edge)
        if layer_by_id[block.layer_id].statutory:
            # the chevron band IS this bar's top edge; a line here would close
            # the box and read as bounded cover
            border = Border(left=edge, right=edge, bottom=edge)
        for rect in block.rects:
            _block(
                ws, rect, rows, col_of, col_of_close,
                text=anchor_text if rect is anchor_rect else "",
                fill=fill,
                border=border,
                font=Font(name=chrome.font, size=8, color=_argb(text_colour)),
                shrink=shrink if rect is anchor_rect else False,
                occupied=occupied,
            )
```

- [ ] **Step 6: Write the caret band**

In `add_schematic_sheet`, immediately after the retention loop and **before** `_axis_lines`:

```python
    # statutory chevron band: fills its merged width with carets, no fill and
    # no border — it stands in for the bar's top edge, it is not a block
    for band in layout.chevrons:
        width_units = band.width * chars_per_unit
        _block(
            ws, band, rows, col_of, col_of_close,
            text="^" * max(3, int(width_units)),
            fill=None,
            border=Border(),
            font=Font(name=chrome.font, size=8, color=_argb(chrome.ink)),
            occupied=occupied,
        )
```

Passing `occupied` matters: `_gridlines` runs afterwards and must not draw a hairline through the band.

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest tests/test_schematic_xlsx.py tests/test_soi_xlsx.py -v`
Expected: PASS. If a golden-SHA assertion fails for a program **without** statutory cover, stop — that means the band leaked into existing geometry, which Step 3's conditional is supposed to prevent.

- [ ] **Step 8: Commit**

```bash
git add src/towerkit/render/schematic_xlsx.py tests/test_schematic_xlsx.py
git commit -m "schematic: caret band above statutory bars, open-topped blocks

The band enters y_boundaries like any other edge, so quantization, sheet_rows
and the axis all shift consistently; a label_span floor stops it collapsing.
Marked occupied so _gridlines does not draw a hairline through it. Programs
with no statutory layer have no band, so existing goldens are untouched."
```

---

### Task 10: TUI — the statutory checkbox

**Files:**
- Modify: `src/towerkit/tui/screens/editor.py:656-676` (the layer form), `:1244-1256` (`_commit_layer_field` money handling), `:1371-1425` (`_checkbox_changed`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `Layer.statutory` (Task 1).
- Produces: widget id `f-layer-statutory`.

- [ ] **Step 1: Write the failing tests**

Add these as methods on the existing editor test class in `tests/test_tui.py` (the one whose tests take `sample_copy, monkeypatch` — e.g. alongside `test_money_field_commit_updates_program`). They follow that file's established pilot idiom exactly: set `editor.selected`, `await editor._rebuild_detail()`, `await pilot.pause()`, then act.

`Checkbox` is not currently imported in this file — add it to the existing `from textual.widgets import Button, Input` line.

```python
    @pytest.mark.asyncio
    async def test_statutory_checkbox_forces_the_invariant(
        self, sample_copy, monkeypatch
    ) -> None:
        """Checking it must FORCE limit=0 / attach=0, not merely record
        intent — otherwise the user keeps a limit that is silently invalid,
        which is the exact failure visible-validation exists to prevent."""
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            editor.query_one("#f-layer-statutory", Checkbox).value = True
            await pilot.pause()
            layer = editor._layer("umbrella")
            assert layer.statutory is True
            assert layer.limit == 0
            assert layer.attach == 0
            assert layer.follows_underlying is False

    @pytest.mark.asyncio
    async def test_unchecking_statutory_clears_the_flag(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            checkbox = editor.query_one("#f-layer-statutory", Checkbox)
            checkbox.value = True
            await pilot.pause()
            checkbox.value = False
            await pilot.pause()
            assert editor._layer("umbrella").statutory is False

    @pytest.mark.asyncio
    async def test_limit_edits_ignored_while_statutory(
        self, sample_copy, monkeypatch
    ) -> None:
        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            editor.selected = ("layer", "umbrella")
            await editor._rebuild_detail()
            await pilot.pause()
            editor.query_one("#f-layer-statutory", Checkbox).value = True
            await pilot.pause()
            limit = editor.query_one("#f-layer-limit")
            limit.value = "5m"
            editor._commit_input(limit)
            await pilot.pause()
            assert editor._layer("umbrella").limit == 0
```

Note: the sample program's `umbrella` layer has a real attach and limit, so the first test genuinely exercises the zeroing rather than asserting on values that were already 0.

Reuse the file's existing app/layer helpers rather than inventing `_app_with_layer` / `_select_layer` if equivalents already exist — match what the neighbouring tests do.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest tests/test_tui.py -k statutory -v`
Expected: FAIL with `NoMatches: #f-layer-statutory`.

- [ ] **Step 3: Add the checkbox to the form**

In the layer form widget list, immediately before the `Label("Limit", ...)`:

```python
            Checkbox(
                "Statutory — no dollar limit (WC Part A)",
                value=layer.statutory,
                id="f-layer-statutory",
            ),
            Label("Limit", classes="field-label"),
```

- [ ] **Step 4: Handle the toggle**

In `_checkbox_changed`, immediately before the `if wid == "f-layer-follows":` branch:

```python
        if wid == "f-layer-statutory":
            kind, key = self._commit_ref
            layer = self._layer(key) if kind == "layer" else None
            if layer is None:
                return
            flag = bool(event.value)

            def set_statutory(p: Program) -> None:
                layer.statutory = flag
                if flag:
                    # force the invariant rather than recording intent: a
                    # statutory layer owns its column from $0 with no limit,
                    # and has nothing beneath it to follow
                    layer.limit = 0
                    layer.attach = 0
                    layer.follows_underlying = False

            self._mutate_and_refresh(set_statutory)
            for widget_id in ("#f-layer-attach", "#f-layer-limit"):
                try:
                    self.query_one(widget_id, MoneyInput).set_amount(None if flag else 0)
                except Exception:
                    pass
            try:
                self.query_one("#f-layer-follows", Checkbox).value = layer.follows_underlying
            except Exception:
                pass
            return
```

- [ ] **Step 5: Ignore limit and attach edits while statutory**

In `_commit_layer_field`, guard the money branch:

```python
        if layer.statutory and wid in ("f-layer-attach", "f-layer-limit"):
            return  # the invariant owns these; the checkbox is the only writer
        if wid == "f-layer-attach" and amount is not None:
            ...
```

Place the guard so it runs after `layer` is resolved from `self._commit_ref` and before the attach/limit/premium dispatch. Field commits resolve against the stamped `_commit_ref`, never the live selection — do not reach for `self.selected` here.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest tests/test_tui.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "tui: statutory checkbox on the layer form

Checking it FORCES limit=0, attach=0 and follows=off rather than recording
intent, and limit/attach edits are ignored while it is set — otherwise the
user types a value that is silently invalid, which is the exact failure the
editor's visible-validation rule exists to prevent."
```

---

### Task 11: Schema-copy sync test, full gate, changelog

**Files:**
- Modify: `tests/test_conventions.py`, `changelog.md`

- [ ] **Step 1: Pin the two schema copies together**

There are TWO copies of the program schema: `schema/program.schema.json` (published reference) and `src/towerkit/schema/program.schema.json` (packaged). `validate.py:313` loads the **packaged** one via `resources.files("towerkit")`, so that is the copy used at runtime — but nothing keeps them in sync. A field added to only the reference copy makes runtime validation silently disagree with the model, which is how `statutory` nearly shipped broken.

Add to `tests/test_conventions.py`:

```python
def test_schema_copies_are_identical() -> None:
    """validate.py loads the PACKAGED schema via resources.files('towerkit');
    the root copy is the published reference. A field added to only one makes
    runtime validation disagree with model.py, and no other test would catch
    it — the canonical round-trip never goes through jsonschema."""
    import json

    root = json.loads((REPO / "schema" / "program.schema.json").read_text("utf-8"))
    packaged = json.loads(
        (REPO / "src" / "towerkit" / "schema" / "program.schema.json").read_text("utf-8")
    )
    assert root == packaged
```

Use whatever repo-root constant `tests/test_conventions.py` already defines; if it has none, derive it as `Path(__file__).parent.parent`.

- [ ] **Step 2: Run the full gate**

```bash
uv run --group dev pytest -q > /tmp/pytest.txt 2>&1; echo "exit=$?"; tail -20 /tmp/pytest.txt
uv run --group dev ruff check src tests
uv run --group dev mypy src/towerkit
```

Expected: 1 failed (the pre-existing `test_connector.py` environmental failure), all else passed; ruff clean; mypy clean.

- [ ] **Step 3: Render a real statutory program end to end**

Build a scratch program with a WC statutory column and an EL column with a $1M limit, then:

```bash
./towerctl render <scratch>.json --format svg --out dist
./towerctl soi <scratch>.json --out dist
```

Open both. Confirm: the WC bar runs floor to top with a chevron top and no flat top line; EL is unaffected; the SOI Limits cell reads `Statutory`; program totals show EL's $1M only.

- [ ] **Step 4: Update the changelog**

Add an entry describing the feature, the `statutory ⇒ limit == 0` invariant, and the silent exclusion from limit totals.

- [ ] **Step 5: Commit**

```bash
git add changelog.md
git commit -m "changelog: statutory (unlimited) coverage"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Model — `Layer.statutory`, invariant | 1 |
| File format — emit-when-true, key order, schema | 1 |
| Geometry — admit, withhold from scale, full column | 4 |
| Geometry — chevron band, degenerate case | 5 |
| Rendering — `labels.py` terms slot | 2 |
| Rendering — `soi.py` `limits_text` | 3 |
| Rendering — chevron replaces top edge (mpl) | 8 |
| Rendering — chevron replaces top edge (xlsx) | 9 |
| Rendering — ascii band | 7 |
| Validation — 4 new rules | 6 |
| Validation — `:249` phantom gap, `:203` `$0 unplaced` | 6 |
| TUI — checkbox forcing the invariant | 10 |
| Testing — invariant, canonical, goldens, byte-identity | 1, 4, 5, 8, 9, 11 |

Two deviations from the spec, both deliberate and flagged in place:

1. **Task 6 adds `statutory-line-shared`,** which the spec does not name. The spec puts a shared statutory column out of scope; this rule turns "out of scope" into "rejected" rather than "silently mis-drawn."
2. **Task 6 pins the unplaced wording** to `"N% open"`. The spec settled on reporting a share rather than dollars but did not fix the string; the test asserts it, so it needs to be one thing.

**Type consistency:** `LayerBlock.statutory` (Task 4) is read in Tasks 8 and 9; `TowerLayout.chevrons` (Task 5) is read in Tasks 7, 8 and 9; `layer_terms(..., statutory=)` (Task 2) is called only from `layer_heading` (Task 4). `_attach_labels` gains `chev_rows` in Task 7 and has exactly one call site, updated in the same task.

**Sequencing note:** Task 2 changes `layer_terms` but not `layer_heading`, because `LayerBlock.statutory` does not exist until Task 4. Task 4 completes the wiring. This keeps both tasks independently green.
