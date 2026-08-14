# Statutory (unlimited) coverage — design

2026-08-14. Workers Compensation Part A is statutory: benefits are whatever
state law requires, so the cover has no dollar limit. On a schematic it is
drawn as a bar running the full height of its column with a chevron
(`^^^^^^`) top edge, signalling that the cover continues past the top of the
chart.

This is towerkit's first piece of cover that is deliberately **outside the
dollar scale**. The affordance is therefore generic — a layer with no dollar
limit — not a Workers Comp feature. towerkit does not learn what Workers Comp
is, the same way it does not learn what an account is (`ingest.py`'s boundary
rule). WC statutory is simply the first user of it.

## Shape of the thing

WC and Employers Liability are **two separate lines** — two columns, usually
the same carrier. Nothing stacks above a statutory bar: it owns its column
from the floor to the top. EL carries ordinary dollar limits in its own
column and is unaffected by any of this.

```
        ^^^^^^^^
  WC              EL
┌────────┐      ╷
│        │      │
│  STAT  │  ┌────────┐
│ Carrier│  │  $1M   │
└────────┘  └────────┘
──────────────────────
```

## The load-bearing constraint

`scale.py` builds ONE global piecewise map over the whole program's
breakpoint set, and the entire design hangs on the resulting property: $52M
sits at the same height in every column. A statutory layer has no dollar top.
Modelled naively — as a layer with some large `limit` — it would inject a
breakpoint and silently re-scale every other column in the program.

So the rule is: **a statutory layer contributes no breakpoints.** `scale.py`
does not change at all; it is simply never shown the layer.

## Model

```python
class Layer(_Model):
    ...
    limit: int
    statutory: bool = False   # no dollar limit; limit MUST be 0
```

**Invariant: `statutory ⇒ limit == 0`.** This is the whole trick. It is not a
convenience — it is what makes six existing behaviours correct with no code
change at all:

| Existing code | Behaviour at `limit == 0` | Wanted |
|---|---|---|
| `Program.total_limit()` | contributes 0 | ✅ excluded from limit totals |
| `_group_bands()` — skips `limit <= 0` | no roll-up contribution | ✅ |
| `EditSession.restack()` — skips `limit <= 0` | never restacked | ✅ it has no top to stack from |
| `underlying_tops()` — requires `other.limit > 0` | nothing follows-underlying onto it | ✅ |
| `build_y_map()` | never called with it | ✅ global scale untouched |
| `Program.total_premium()` | sums `premium` regardless | ✅ WC premium still counts |

Statutory cover is **excluded from limit totals silently** — no annotation on
a program total or a group band. (Recorded disagreement: I would have
annotated it, e.g. `Limit $200M + statutory`, because a total that reads
`$200M` on a program containing unlimited cover is false to anyone who reads
the total without reading the columns. Grant chose silent exclusion.)

## File format

`statutory` is emitted **only when true**:

```python
"statutory": layer.statutory or None,     # _ordered() drops None
```

This is the established `followsUnderlying` / `soiSchematic` pattern
(`model.py:271-274`). Two properties follow: untouched programs re-save
byte-identically, and older towerkit wheels only reject files that actually
*use* the feature.

Canonical key order — `statutory` goes immediately after `limit` in
`_LAYER_KEYS`, because it qualifies the limit:

```python
_LAYER_KEYS = (
    "id", "name", "policyNumber", "period", "followsUnderlying", "appliesTo",
    "attach", "limit", "statutory", "premium", "limitsDetail",
    "retentionDetail", "participants", "notes",
)
```

`schema/program.schema.json`, in the `layer` def:

```json
"statutory": {
  "description": "Coverage with no dollar limit (WC statutory). limit must be 0; excluded from limit totals.",
  "type": "boolean"
}
```

`limit` stays in `required` and stays `0`. No schema constraint on it changes:
`limit` is already a plain `integer` there, with positivity documented as "a
semantic rule so drafts stay loadable". The validator, not the schema, is
where positivity lives — and that is exactly where the exemption belongs.

## Geometry — `layout.py`

Three changes, all inside `build_layout`:

1. **Admit it.** The `drawable` filter becomes
   `layer.limit > 0 or layer.statutory`.
2. **Keep it out of the scale.** `build_y_map` is fed only the
   non-statutory drawables.
3. **Give it the full column.** `y0 = 0.0`, `y1 = 1.0`.

Participant allocation is unchanged — `_allocate` runs over that span as it
does for any layer, so carrier splits, unplaced hatching and pending dashes
all work with no special casing.

`LayerBlock` gains `statutory: bool`. `ParticipantBlock` does not: every
renderer already resolves a block's owner through `layer_by_id`.

### The chevron band is geometry, not decoration

`ascii.py`'s docstring states the rule: *"If a drawing decision needs geometry
that is not already in the layout, it belongs in layout.py."* So the band is
modelled, not reinvented per renderer:

```python
CHEVRON_BAND = 0.04

@dataclass(frozen=True)
class TowerLayout:
    ...
    chevrons: tuple[Rect, ...]   # one per statutory run, spanning [1.0, 1.04]
```

Two consequences:

- **The xlsx schematic gets it nearly free.** `y_boundaries()` picks up `1.04`
  from the chevron rects, `quantize_boundaries` allocates the band rows
  proportionally, and `sheet_rows`' `top = max(rows.values())` shifts
  everything consistently. `_axis`, `_gridlines` and `_axis_lines` all key off
  `ref_lines` and `rows[0.0]`, none of which move.
- **The ascii preview needs one explicit fix.** `to_row(y) = round((1.0 - y) *
  tower_rows)` returns a *negative* row for `y = 1.04`, so `_render_layout`
  reserves an extra top row when `tower.chevrons` is non-empty. This is the
  one place the uniform treatment does not simply fall out.

The band is added **only when a statutory layer exists**. Every existing
program's schematic keeps its current geometry and `SCHEMATIC_GOLDEN_SHA`
does not churn.

### Degenerate case

A program whose only layer is statutory feeds `build_y_map([])`, which
returns the existing degenerate `YMap(breakpoints=(0,), positions=(0.0,))`.
The bar still draws floor-to-top; there are simply no axis labels. Correct,
and already handled by code that exists.

## Rendering

### Text — `labels.py`

```python
def layer_terms(attach: int, limit: int, statutory: bool = False) -> str:
    if statutory:
        return "Statutory"
    ...
```

`layer_heading` checks `statutory` before `follows` — they are mutually
exclusive, since nothing underlies a statutory bar. Result:
`Workers Compensation — Statutory`.

`layer_terms` has no callers outside `layer_heading` and the tests, so the
change is contained.

### Schedule of Insurance — `soi.py`

`limits_text` gains a statutory branch, placed **after** the existing
`limits_detail` override. That ordering matters: it means typing "Benefits as
required by NY state law" into a layer's `limitsDetail` gives the long-form
wording on the SOI, without towerkit inventing a default sentence about state
law. Absent that, the cell reads `Statutory`.

(`limitsDetail` is not rendered on the schematic today, only in SOI. This
design does not change that.)

### The chevron replaces the top edge

A statutory layer draws left, right and bottom only; the zigzag in the
reserved band **is** its top edge. A flat solid top line with carets floating
above it would read as a bounded bar wearing a hat.

| Surface | Implementation |
|---|---|
| `mpl_program` | Unfilled zigzag polyline per chevron rect in `chrome.ink` at linewidth 1.1, matching the outline weight. Tooth count derived from rect width so teeth stay uniform across column counts. `set_ylim` already tops out at 1.06, so the axes do not move. |
| `schematic_xlsx` | The band rect goes through `_block` with a caret string sized from `rect.width * chars_per_unit`; no fill, no border. It gets a `label_span` floored to `label_row_floor(1)` so quantization cannot collapse it. Statutory participant blocks pass a `Border` with `top=None`. |
| `ascii` | `chev_rows = 1 if tower.chevrons else 0`; the grid grows one row, `to_row` and `_attach_labels`' row indices shift by it, and the band columns fill with `^`. |

## Validation — `validate.py`

Four new rules, and two existing checks that are **wrong** for statutory cover
and must be fixed. The two fixes are the real risk in this section; the four
rules are bookkeeping.

New rules:

- `layer-limit` (`limit <= 0`) — skipped when `statutory`.
- `statutory-limit` — `statutory` with `limit != 0` is an error.
- `statutory-attach` — `statutory` with `attach != 0` is an error. The bar
  owns its column from the floor; there is nothing for it to sit on.
- `statutory-follows` — `statutory and follows_underlying` is an error;
  the two are contradictory.

Existing checks that break:

- **`validate.py:249`** filters `layer.limit > 0` when walking a line's stack
  for coverage gaps. Left alone, a WC line covered *only* by a statutory layer
  reads as having no cover and reports a phantom gap — which, per the
  validation-visibility rule, would tint the WC column danger-red in the live
  preview. Statutory layers must count as covering their lines.
- **`validate.py:203`** quantifies unplaced capacity as
  `layer.limit * (BPS_SCALE - signed) // BPS_SCALE`. For statutory that is
  `$0 unplaced`, which is nonsense. It reports the open **share** instead —
  "35% unplaced" rather than a dollar figure. Staying silent was the
  alternative and is wrong: a half-signed statutory layer is a real placement
  problem and must still surface.

## TUI

A `statutory` checkbox on the layer form. Checking it zeroes and disables the
limit and attach inputs — otherwise the user types a limit that is silently
invalid, which is precisely the "validation must be visible where you look"
failure the editor is built to avoid. The layers sheet shows `Statutory` in
the limit column.

Field commits resolve against the form's stamped `_commit_ref`, never the live
selection (the blur-race rule).

## Testing

- **The load-bearing test:** adding a statutory layer to a program must not
  shift any other layer's `y0`/`y1` by a single float. That test *is* the
  global-scale invariant, and it is the one that would catch the naive
  "give it a big limit" regression.
- Canonical: a statutory program round-trips zero-diff, **and** an existing
  program without the field re-saves byte-identically.
- Golden SHA: a new fixture for the statutory schematic; existing goldens
  asserted unchanged.
- SVG/PDF byte-identical across two runs with a statutory layer present.
- `layout`: chevron rects exist, span `[1.0, 1.04]`, one per run; the
  statutory-only degenerate program.
- `validate`: the four new rules, plus no phantom gap on a statutory-only
  line and no `$0 unplaced` message.
- `labels` / `soi`: `Statutory` in the terms slot; `limitsDetail` still wins
  on the SOI.
- `ascii`: caret row present with statutory, absent without.
- `tui`: the checkbox forces limit and attach to 0.

## Out of scope

`ingest.py` recognising a spreadsheet limits cell of `"Statutory"` / `"Stat"`
/ `"As per statute"` and setting the flag on import. It belongs in towerkit
rather than bookkit by the existing `ingest` boundary rule ("towerkit.ingest
owns what a tower means"), and it is a natural follow-on — but it is a
separate fuzzy-matching problem with its own questions. Flagged, not built.

Statutory cover that is *not* the whole column — a statutory bar with
something attaching above it — is also out of scope. It would need a defined
dollar top, which reintroduces the scale problem this design exists to avoid.
