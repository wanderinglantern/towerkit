# towerkit — project context

## Marsh brand colours (authoritative)

Verified against the official .ase swatches and brand Specifications sheet.
`themes/marsh.json` must stay consistent with these. Full reference lives in
the session memory (`marsh-brand-colors`); the load-bearing rules:

- Core: Midnight `#000F47` (ink), Sky `#CEECFF` (light fill), white bg.
- Categorical series order: `#000F47 #0B4BFF #82BAFF #FFBF00 #CB7E03
  #FFD98A #8F20DE #6ABF30` (blues/golds before purples/greens).
- Traffic lights are STATUS ONLY, never data series: success `#14853D`,
  warning `#FFBE00`, danger `#C53532`. Status *text* on white uses Green
  1000 `#2F7500` for success; danger crimson is shared.
- No gradients/shadows/opacity washes; separate adjacent fills with white
  strokes (the renderer's block edges already do this).
- Contrast: white text fails on gold (any), Blue 500, Green 750, Sky —
  `theme.contrast_text` enforces this via luminance.
- Type: serif headings Regular only (Marsh Serif has no bold; Noto Serif
  fallback), Noto Sans body.

## Working rules

- `programs/*.json` are the source of truth; saves must stay canonical
  (zero-diff round trip is tested).
- `scale.py` and `layout.py` never import plotting libraries (tested).
- Rendered SVG/PDF must stay byte-identical across runs (tested).
- Record open choices in DECISIONS.md; reference-material gaps in NOTES.md.
- v2 goal: Schedules of Insurance generated from layer policyNumber/period
  data — capture those fields even though rendering doesn't use them yet.

## Grant's chart & product preferences (flagged 2026-08-11)

Rendering:
- Primaries are quoted by limit alone — never "xs $0".
- Layer titles are part of the cell text stack (lead carrier's cell, same
  font/colour as the carrier label) — no bold banner labels.
- No visible provenance footer and no scale caveat on charts (both were
  deliberate removals; provenance lives in file metadata).
- Totals, premiums, and per-cell premiums are toggleable (CLI flags and the
  TUI `t` menu) — hypothetical program designs are a first-class use case.
- All column gutters close; reference-line $ labels sit above the line; full
  line names under columns.
- Carrier colours: walk the palette blues-first in first-appearance order;
  no maintained carrier list.

Data / TUI:
- Ids auto-generate from names (slug on first naming, stable afterwards).
- Dates accept human forms (Dateparser); files stay ISO.
- Multi-entity programs: one column per entity; shared umbrellas over
  differing limits use `followsUnderlying` (stepped bottom, flat top).
- Client programs go in gitignored `programs/private/` — never commit real
  client data to this public repo.
- v2: Schedules of Insurance from policyNumber/period data.
