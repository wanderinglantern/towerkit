# DECISIONS

One line per choice the brief left open, with why. §1 decisions are not
relitigated here.

- **Schema and sample program authored from the brief**, since `reference/` was
  absent (see NOTES.md). Key order in the canonical serialiser is frozen to the
  schema's property order.
- **Period is `{start, end}` ISO dates.** "Bump by a year" on renewal clone is
  then well-defined arithmetic, and the browser can show either date or a year.
- **Retentions and sublimits carry `appliesTo` arrays** like layers — one shape
  for "what does this apply to" everywhere; no special-casing in layout.
- **`limit` is permissive in schema/model (plain integer); positivity is a
  semantic rule** in validate.py — drafts with a zero limit must stay loadable
  in the editor, and the validator is where errors are reported.
- **Unknown retention `type` / unknown `placement` are caught at the
  schema/model layer**, not validate.py — the TUI can never produce them (they
  are pickers), so a file with one is corrupt input, reported as a load
  diagnostic rather than an editable state.
- **Share precision is capped at one basis point.** `0.33333` on disk is
  rejected on load rather than silently rounded — sub-bps shares have no exact
  representation in the unit of account.
- **Money shorthand parser is hand-written** (~30 lines): none of the mandated
  parsing libraries (Humanize/Dateparser/RapidFuzz/Babel) *parse* "2m"/"1.5bn".
  Babel is used for all money *formatting*; RapidFuzz drives carrier
  autocomplete in the TUI.
- **`gamma` is a render option (CLI `--gamma`, default 0.35), not theme or file
  data** — it changes how the picture distorts, not what the program is or
  whose brand it wears.
- **Carrier palette colours are assigned by first appearance in the file**, so
  adding a carrier never recolours existing ones; pinned colours in the theme
  always win.
- **CLI uses argparse** — stdlib, no extra dependency, subcommands are simple.
- **mypy is strict on the pure core** (model/money/scale/layout/validate/
  compare/theme); `towerkit.tui.*` and `towerkit.render.*` relax only the
  untyped-def/generic-parameter rules — framework callback signatures there
  are noise, and the checks that catch real bugs stay on everywhere.
- **The TUI preview and its render action use the built-in default theme**;
  branded output goes through `towerctl render --theme`.
- **matplotlib on macOS 27 beta**: font enumeration crashes upstream
  (empty `system_profiler` output); `towerkit.render` falls back to
  matplotlib's bundled DejaVu fonts when that happens (see NOTES.md).

## Review round 1 (2026-08-11)

- **All interior gutters close** (layout rule): adjacent columns always split
  the gutter between them, so bands and retentions meet edge-to-edge across
  the whole chart. (First pass closed only same-tower gutters; the user chose
  to close them all — noting: visually separate towers no longer read as
  separate placements, the column headers now carry that distinction.)
- **Reference-line dollar labels sit above the line**, not centred on it.
- **Column footers show full line names** (wrapped), abbreviations remain in
  the ASCII preview where width is scarce.
- **Provenance moved from the visible chart into file metadata** (SVG/PDF
  Creator, PNG Software) at the user's request. Noting: §5 required *visible*
  provenance so a chart in circulation identifies its source; metadata
  preserves traceability for anyone who inspects the file, but a printed or
  screenshotted chart no longer carries it. Revisit if that bites.

## Review round 2 (2026-08-11)

- **marsh theme rebuilt on the Marsh McLennan design tokens** extracted from
  the user's `marsh.css` (Typora theme): core midnight ink, sky-blue grid,
  carrier colours from the blue/gold/green/purple 500–1000 ramp steps,
  retention fills from the warm neutral→gold tints.
- **Participant label colour is picked by luminance** (WCAG relative
  luminance, threshold 0.40) so light ramp fills get dark text — replaces
  always-white labels.
- **Noto Sans/Serif are bundled in the package** (SIL OFL, `fonts/OFL.txt`)
  and registered with matplotlib explicitly — determinism holds because the
  exact TTFs ship with towerkit, independent of system fonts. The marsh theme
  uses Noto Sans for body and Noto Serif for headlines (`chrome.titleFont`),
  matching the brand CSS; the default theme stays DejaVu.

## Review round 3 (2026-08-11)

- **The visible "not to scale" caveat is removed from rendered charts** at the
  user's request. Logging the disagreement: §1 treated the visible caveat as
  non-negotiable — with γ = 0.35 a $2M layer draws nearly as tall as a $100M
  one, and the footnote was the explicit guard against reading heights as
  proportional. The dollar-labelled reference lines remain the only cue.
  The ASCII preview keeps its small "(not to scale)" tag (working tool, not
  a deliverable). Revisit if a chart is ever misread in circulation.
- **`--no-totals` and `--no-premiums` render options** (review round 3):
  totals line and all premium figures can be omitted — hypothetical program
  designs have no meaningful premium. With premiums hidden the renewal table
  sorts by |line Δ| instead.
- **`towerctl edit --theme`**: the TUI preview and its `r` render now take a
  theme; previously they always used the built-in default, which made theme
  selection in the app impossible (user-reported confusion).
- **Bundled Noto LGC lacks the Arrows block**: `font.family` is set as an
  explicit list so matplotlib falls back per-glyph to DejaVu for `→`.

## Review round 4 (2026-08-11)

- **marsh theme corrected against the official brand reference** (user
  supplied the verified .ase/spec-sheet palette; recorded in repo CLAUDE.md
  and session memory): categorical series order, traffic-light colours as
  status-only (renewal chips now Green 1000 / danger crimson), serif
  headings Regular weight (Marsh Serif has no bold), light tints always
  carrying midnight text via the luminance rule.
- **Carrier colours are hash-assigned, no maintained list**: md5(name) picks
  a preferred palette slot — stable for a carrier across every program and
  machine — with deterministic probing to a free slot on collision. Theme
  pins still win if a theme chooses to define them; marsh.json defines none.
- **Layers capture `policyNumber` and an optional per-layer `period`**
  (schema addition, recorded per §10): programs carry several policy
  effective/expiry dates, and this data feeds the planned v2 Schedules of
  Insurance output. Rendering does not show them yet.
- **Layer `notes` render as chart footnotes** with superscript markers on
  the layer titles (single-program chart only; the renewal chart stays
  focused on deltas).
- **The retention band collapses when a program has no retentions**, so
  column labels sit directly under the towers.

## Review round 5 (2026-08-11)

- **Carrier colour order follows the brand sequence, not a hash**: palette
  walks Midnight → Sky → Active blue → Blue 500 and only then riffs into the
  dataviz golds/purples/greens, assigned by first appearance. Cross-program
  stability in a renewal comparison is preserved by building one colour map
  over the union of both programs' carriers.
- **Flexible date entry via Dateparser** (the mandated parsing library):
  the TUI accepts "1/15/2026", "Jan 15 2026", "April 2026" etc., echoes the
  canonical ISO form back into the field, and files on disk stay ISO.
- **Primaries are quoted by limit alone** ("Primary D&O — $5M"): the
  "xs $0" suffix only appears on layers that actually attach above zero —
  "excess of nothing" reads as an error to a market audience (user-reported
  on a real program).
- **Layer titles are part of the cell text stack** (user request): the
  title heads the leftmost participant's cell in the same font and colour
  as the carrier label, instead of a bold halo banner at the layer's top
  left; the footnote marker rides on it. Every wrapped carrier-name form
  keeps its premium line, so long names (Indian Harbor) never silently
  drop the per-cell premium.

## Review round 6 (2026-08-11)

- **IDs auto-generate from names**: new layers/lines carry a placeholder id
  until first named, then take a slug of the name ("Primary D&O" →
  "primary-do"), uniquified, with line references cascaded. Ids stay stable
  after that so renewal comparison (which matches by layer id) keeps working.
- **`followsUnderlying` layers** (schema addition): a shared umbrella over
  entity columns with different primary limits draws with a stepped bottom —
  each column's bottom sits on its own underlying top — and a flat top,
  labelled "xs underlying". Validation requires attach == highest underlying
  top and checks contiguity per column using effective attachments. The TUI
  checkbox snaps attach automatically. Rejected alternative: per-column
  attach maps on every layer — far bigger surface for the same picture.
- **Render settings persist in the program file** (`render` block: theme,
  showTotals, showPremiums, cellPremiums): the options menu writes them,
  both the TUI and CLI read them as defaults, CLI flags still override —
  no re-selecting on every session.
- **Per-cell policy term option** (`--cell-dates` / menu / `render.cellDates`):
  renders between the carrier share and the premium, using the layer's own
  period when set, else the program period; US short dates (m/d/yy).
- **Line ids are locked** (display-only, auto-generated); the column label
  falls back to the NAME's initials ("Directors and Officers" → "DAO"),
  never the slug id — auto-ids were leaking into rendered column labels.
- **Lines reorder with shift+↑/↓** in the editor: array order is column
  order, and the move is a normal undoable edit.
- **Pending layers render as placeholders** (user-specified formatting):
  a layer with zero participants draws as a dashed-outline empty box with
  "To be placed" in ink — distinct from a partially-placed layer, which
  keeps the grey hatch and "% open" on its remainder.

## Review round 7 (2026-08-11)

- **Coverage grouping** (`Line.group`, one optional bucket per line — user
  chose single-dimension over cross-cutting tags): adjacent same-group
  columns render flush with an open gutter between buckets; an accent band
  under the column names carries the bucket label and pro-rata roll-ups
  (a layer spanning n lines contributes covered/n of its limit and premium,
  integer division). Scattered groups draw as separate bands and warn
  ("group-scattered") suggesting a reorder.

## SOI export (2026-08-11)

- **No grand-total row** at the bottom of the SOI — the sample has none;
  section roll-ups carry the sums. Revisit if a full-program total is wanted.
- **Multi-line layers section into their lines' shared group**, else the
  final "Program-wide" section — so section premium roll-ups never double
  count a shared layer.
- **Follows-underlying layers compose limits as "xs underlying"** — their
  attachment is per-column derived state, so no single dollar figure is
  honest; `limitsDetail` overrides where prose is wanted.
- **openpyxl chosen over a hand-rolled stdlib xlsx writer** (user call;
  hand-rolled was recommended to avoid the new dep). Costs accepted and
  handled: wheelhouse rebuild on release, and a zip-normalization pass in
  soi_xlsx.py to keep byte-identical output.
- **SOI dates are real Excel dates** formatted mm/dd/yyyy (sample stores
  text) — sortable/filterable, visually identical.
- **SOI detail fields are single-line `Input`s in the TUI**, not the spec's
  "multiline" — matching the Notes field and reusing the stamped-ref commit
  flow; a `TextArea` would need its own commit path. Long prose still fits
  (the field scrolls); revisit if editing long schedules in place hurts.

## TUI SOI export (2026-08-12)

- **Output goes to `dist/`** (the TUI's convention, matching `action_render`),
  while the CLI's `towerctl soi` still defaults to CWD — the TUI already
  treats `dist/` as its working output directory; no reason to special-case
  SOI.
- **Filename keys on insured + period years via `default_filename`**, same
  as the CLI. Originally shipped insured-only with the year in the sheet
  title; revisited same day because renewal-year exports clobbered each
  other (`atomic-2025` and `atomic-2026` wrote the same file). Now
  `<Insured> - Schedule of Insurance YY-YY.xlsx`, matching the sheet
  title's year convention. Changes CLI default output names too — chosen
  deliberately.
- **Write errors notify instead of crashing** (this fix wave): both
  `action_render` and `action_export_soi` wrap only their write call
  (`render_program` / `write_soi`) in a try/except and notify
  `"{render,export} failed: {exc}"` on failure rather than letting the
  exception propagate and crash Textual — an `IllegalCharacterError` from
  control chars in user text, or a `PermissionError` from a locked `dist/`
  file, previously took the whole app down with unsaved edits. Same pattern
  in both actions, kept mirrored.

## Line transfer (2026-08-12)

- **Shared layers stay in the source, never fabricated into the target** —
  the send flow refuses to invent a placement that does not exist. The
  trade-off: the sent line can arrive in the target with a gap under its
  excess layers (nothing left to attach to). Rather than block the send,
  the confirm screen runs `validate_program` on `dst_after` and appends any
  errors to the summary text; the user accepts the send knowingly, since
  hypothetical program designs are a first-class use case.
- **Follows-underlying attachments are re-derived in the graft**, exactly
  as `session.mutate` heals them post-edit: after appending the travelling
  layers into `dst_after`, every `follows_underlying` layer's `attach` is
  recomputed from `dst_after.underlying_tops(layer)` rather than carrying
  whatever attach it had in the source. A stale attach is derived-state
  rot, not data worth preserving verbatim.
