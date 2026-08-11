# Schedule of Insurance (SOI) xlsx export — design

2026-08-11. The v2 goal: generate a themed Schedule of Insurance workbook from
program data. Theming mirrors the reviewed sample workbook ("Endeavour SOI -
25-26"); structure extends it with per-layer detail, coverage-group sections,
and labeling. The sample itself is real client data and stays out of the repo;
its style values are recorded here.

## Command

```
towerctl soi program.json [-o out.xlsx] [--theme themes/x.json] [--no-premiums]
```

- Default output name: `<Insured> - Schedule of Insurance.xlsx`.
- Sheet name: `<program> SOI - YY-YY` (two-digit years from the program
  period, matching the sample's convention).
- Export writes a new file only; program JSON is never touched by export.

## Data mapping

**Row = one layer.** Ordering: coverage-group sections in order of each
group's first appearance in `lines` (same rule as the chart buckets); within a
group, line display order; within a line, attach ascending. Ungrouped lines
form an unlabeled section after the named groups. Layers whose lines span
multiple groups (shared umbrellas) land in a final **"Program-wide"** section
— so no premium is ever double-counted in section roll-ups.

Nine columns, mirroring the sample:

| Column | Source |
|---|---|
| Insured | `program.insured` on every row |
| Line of Coverage | Line name; when a line has >1 layer, append the layer name: "Excess Liability — 1st Excess". Multi-line layers: layer name + covered line labels: "Umbrella (GL, Auto, EL)" |
| Carrier | Participants: single carrier plain; quota-share as "Carrier A (60%), Carrier B (40%)" (bps → %). No participants → "To be placed" |
| Policy Number | `layer.policyNumber`; blank when absent |
| Effective Date / Expiration Date | `layer.period`, falling back to `program.period` |
| Limits | `layer.limitsDetail` verbatim when set; else composed: primary quoted by limit alone ("$1,000,000", never "xs $0"), excess "$5,000,000 xs $5,000,000"; sublimits whose `appliesTo` intersects the layer's lines appended ("Sublimit: <name> $amt; …"). Babel money formatting |
| Deductible / SIR / Retention | `layer.retentionDetail` verbatim when set; else composed from `retentions` intersecting the layer's lines, primary layers only ("SIR $250,000; Aggregate $1,000,000"); excess rows blank |
| Premium | `layer.premium` as `$#,##0.00`; `--no-premiums` omits the whole column |

**Section header rows:** one full-width band per group — merged `A:H` with
the group label; the Premium cell on that row carries the section roll-up
(straight sum of the section's rows) in currency format. With `--no-premiums`
the table is eight columns and the band merges across all of them, label only.
Zebra banding restarts inside each section.

## Model changes

`Layer` gains two optional strings:

- `limitsDetail` — SOI limits prose, exported verbatim.
- `retentionDetail` — SOI deductible/SIR/retention prose, exported verbatim.

Both join the frozen canonical key order after `premium`, the JSON schema,
and the TUI layer form as multiline inputs. The canonical serialiser omits
`None` fields, so existing files round-trip zero-diff. No migration; nothing
destructive.

## Theming

Theme JSON gains an optional `soi` block; the built-in default reproduces the
sample workbook exactly:

```json
"soi": {
  "headerFill": "#003865", "headerText": "#FFFFFF",
  "bodyText":   "#3D3C37", "bandFill":   "#F7F3EE",
  "border":     "#B9B6B1", "font": "Noto Sans", "size": 11
}
```

Header text runs through the existing `theme.contrast_text` luminance check so
a light header fill can never get white text. `themes/marsh.json` may later
override with the Marsh kit (Midnight `#000F47` etc.); this iteration ships
only the sample-mirroring default.

Cell treatments (from the sample):

- Header row: bold, header fill, centered both ways, wrapped, 36pt tall.
- Body: body-text colour, top-left, wrapped, thin `border`-colour border on
  every cell; zebra white/`bandFill` alternating, restarting per section.
- Column widths verbatim from the sample: 23.33, 37.83, 39.83, 15, 11.83,
  13, 100 (Limits), 34.83, 12.16. `--no-premiums` drops the last entry.
- Premium cells `$#,##0.00`, right-aligned.
- Row heights: openpyxl cannot auto-fit; estimate wrapped line count from the
  prose columns' widths and set `18pt × lines` (deterministic; sample uses
  fixed 36/54 heights).

Deliberate deviations from the sample (invisible on screen, better in use):

1. Dates are real Excel dates with `mm/dd/yyyy` number format, not text.
2. Freeze pane below the header row.

## Structure

- `towerkit/soi.py` — pure, strict mypy: program → ordered sections/rows;
  all text composition, carrier formatting, ordering, roll-ups. Never imports
  openpyxl or plotting libraries.
- `towerkit/render/soi_xlsx.py` — openpyxl glue: rows + theme → workbook
  (styles, widths, merges, heights).

## Dependency & determinism

`openpyxl>=3.1` joins runtime dependencies. Consequences, handled explicitly:

- **Wheelhouse rebuild** required before the next work-machine release;
  recorded in the changelog as a release step.
- **Byte-identical output** (repo rule) is preserved by (1) pinning workbook
  `created`/`modified` properties to a fixed date and setting the creator to
  the same provenance string the charts embed in metadata, and (2) a
  post-save zip normalization pass rewriting every archive entry with a DOS
  epoch timestamp and fixed compression settings.

Decision record: a stdlib hand-rolled writer was proposed (no new dependency,
determinism for free) and openpyxl chosen; the two costs above are the
accepted trade-off. openpyxl also joins the dev group's test toolchain as the
independent reader for content assertions.

## TUI scope this iteration

Capture only: `limitsDetail` and `retentionDetail` as multiline fields in the
layer detail form (with the usual commit-against-stamped-node rule). Export
remains CLI-only; a TUI export action is a follow-up once the writer exists.

## Tests

- Pure mapping: section/row ordering; composed limits text (primary by limit
  alone, "xs" for excess); retention composition and primary-only rule;
  quota-share carrier strings; "To be placed"; period fallback to program.
- Canonical round-trip: zero-diff for files with and without the new fields.
- Byte-identical: render the same program twice, compare bytes.
- Content: re-open output with openpyxl; assert headers, fills, zebra restart
  per section, merged section bands, currency formats against the values in
  this spec.
- Schema validation covers the two new optional properties.

## Open choices (DECISIONS.md on implementation)

- No grand-total row at the bottom (sample has none); revisit if wanted.
- Multi-line-layer group assignment: all-lines-same-group → that group, else
  Program-wide.
