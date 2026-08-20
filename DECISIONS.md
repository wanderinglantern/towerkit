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

## TUI import (2026-08-12)

- **`ImportFileModal` replaces `i`'s three chained `PromptModal`s.** The
  original design (docs/superpowers/specs/2026-08-12-tui-import-design.md)
  chained schedule-path/insured/program prompts, which is a dead end for
  text/csv sources: `parse_tower` has no date syntax, so a source with no
  built-in period could never become an importable program through text
  prompts alone. `ImportFileModal` is `PasteImportModal`'s structural twin
  (a path `Input` instead of a `TextArea`, same insured/program plus the
  SAME optional inception/expiry `Input`s) — one multi-field modal instead
  of a fourth chained prompt, and text-file schedules become importable
  for the first time.
- **Program-period inversion is now a validation error** (`program-period`
  in validate.py, mirroring the existing `layer-period` message/diagnostic
  shape). An inverted whole-program `period.end <= period.start` was
  previously accepted silently. Living in `validate_program` closes the
  gap for `towerctl validate` and the TUI browser's error badge at once —
  one rule serves both surfaces.
- **`w`'s template overwrite now confirms**, deliberately diverging from
  `towerctl template`'s silent overwrite: a filled `.xlsx` template is
  keyed-in user data, and the repo's data-safety rule (CLAUDE.md — never
  run an irreversible data operation without surfacing the risk first)
  applies to the TUI even where the CLI doesn't enforce it.

## Layer detail fields: states / namedLimits / premiumDetail (2026-08-18)

One canonical-format change carrying three findings, because each touches
the layer's canonical key order and the zero-diff round trip. (The
hand-written `_LAYER_KEYS` named here was deleted on 2026-08-19 — see
*Canonical serialisation is derived*.)

- **`states` is on the layer, statutory only** (Grant's C15 call: modelling
  over free text). Cover in a state we are not filed in is worth nothing, so
  it is a coverage fact — and it buys the monopolistic-fund refusal
  (ND/OH/WA/WY), which is the only reason to model it rather than type it.
  A states list on a dollar-limited layer is refused: an unrefused
  meaningless field becomes a general-purpose note by accident.
- **An unrecognised state code WARNS, it does not error.** towerkit knows
  50 states + DC and nothing else; "Ohio" or a non-US jurisdiction is not
  invalid, it is UNCHECKED, and saying so is the point — silence would
  leave the one check the field exists for quietly unapplied. Codes are
  compared upper-cased and stored verbatim (normalising on load would
  rewrite files nobody edited).
- **`namedLimits` is generic — optional named amounts on a layer.**
  Employers Liability's three coordinate limits are one caller. Not
  `Sublimit`, which is a cap WITHIN a limit rather than a peer of one.
  Amounts are stated, never summed or compared against `limit`: which of
  them is the layer's height is a line-of-business question.
- **`premiumDetail` is exported verbatim**, like the two detail fields
  before it — "Included with Part A" is the broker's sentence, never one
  towerkit composes, so it never learns "Part A".
- **Prose versus structured, ruled once.** `limitsDetail` still wins over
  `namedLimits`, AND carrying both is refused — that refusal is what makes
  "prose wins" safe, because otherwise the structured data would be
  discarded in silence and look broken to whoever set it. `retentionDetail`
  is unchanged: its competitor is program-level and its precedence is
  deliberate. `premiumDetail` competes with nothing — it replaces the WORD a
  zero prints, never a number the subtotals add up, and is refused wherever
  the cell would not render it.
- **The states follow Grant's shipped phrase in parentheses** on the SOI —
  `Statutory - State Limits (NY, NJ)` — answering the question that phrase's
  own comment says it raises. `limitsDetail` still wins over the whole
  thing. Judgement call, open to veto: the alternative is to model the
  states for the validator alone and print nothing.
- **Not built: TUI editors.** All three are file- and API-settable only;
  the layer form has no widgets for them, and `namedLimits` needs a
  repeating-row editor on the participants-sheet pattern. Deliberate scope
  line, not an oversight. *(Closed the next day — see below.)*

## Setting the layer detail fields from the editor (2026-08-18, later)

- **`premiumDetail` copies `limitsDetail` exactly**: a plain `Input` in the
  layer form, committed on enter/blur, empty meaning None so clearing it
  drops the key. There was a shape to follow and no reason to invent one.
- **`states` is ONE comma-separated field**, not a repeating row: the values
  are two characters long and are typed as a phrase ("NY, NJ"), so a grid
  would cost a row of chrome each. `edit.parse_states` owns the syntax —
  whitespace trimmed, empty pieces dropped, and NOTHING else: codes stay
  verbatim and duplicates are NOT collapsed, because the validator refuses
  them by name and swallowing one would delete the refusal.
- **The states field is shown on every layer, not hidden on the ones that
  refuse it.** A field that vanishes teaches nothing; a field that answers
  says why. Same reasoning as the `p` refusal: a silent no-op reads as a
  broken app.
- **Refusals are spoken, not just filed.** `validate.py` reports these rules
  as diagnostics, which is correct and is also a bar at the bottom of the
  screen that nobody is looking at while they type. `_notify_layer_refusals`
  re-says the layer's detail diagnostics as a notification on every edit that
  could earn one — including from the OTHER side (a premium edit that
  invalidates a standing `premiumDetail`, a `limitsDetail` typed over
  existing named limits, statutory switched off under a states list).
  Nothing blocks: a draft that breaks a semantic rule stays editable, exactly
  as a zero limit does.
- **`namedLimits` is the participants sheet's `SheetTable`, second instance.**
  The previous implementer named that pattern and it fits: repeating rows of
  two fields, in-place editing, `a`/`del` for the rows, one `CellEdited` per
  undo step. A second grid pattern would be a second set of the hard-won
  details (row identity captured at open, the TCSS underscore trap, blur =
  cancel) to get wrong.
- **`n` jumps to it, mirroring `p`** — one shared `_jump_to_layer_grid`
  rather than a copy, so the picker fallback that keeps the key from refusing
  in silence has one implementation. The three fields are laid out as a
  cluster around the grid: states two shift+tabs behind it, premium detail
  two tabs ahead, so one key reaches all three.
- **The row-adding writes go through `towerkit.edit`** (`add_named_limit` /
  `edit_named_limit` / `remove_named_limit`), and `test_conventions.py` now
  bans `.named_limits.append(` in the TUI the way it bans the program-level
  collections. The scalar setters (`set_states`, `set_premium_detail`) live
  there too, so the MCP server inherits one definition of what setting each
  means.
- **The layers-sheet hint line lost words to fit.** Adding `n` to it pushed
  it to 146 columns against a 138-column content box at a 140-column
  terminal, and `#key-hint` is one row of a height-1 `Static` — the overflow
  is not scrolled, it is gone, starting with the `? all keys` escape hatch at
  the end. "enter open layer form" → "enter open form", "v/esc back to form"
  → "v/esc back". `test_dead_keys.py` now measures every hint line, and
  discovers them by name (`*_HINT`) rather than listing them, since a hint
  constant the arbiter does not know about is the exact drift it exists to
  catch.

## Canonical serialisation is derived (2026-08-19)

- **`program_to_jsonable` is computed from `model_fields`, not typed.** It was
  the THIRD hand-written field table in the tree, after the MCP write surface
  and `program_read`, and it is why the branch's claim — "add a field to
  `model.py` and it is writable with no MCP edit" — was false end to end. The
  write reached the in-memory model, the response came back `{"wrote": ...,
  "errors": []}`, and the value was in neither the file nor the next read. A
  success receipt for a lost edit is the worst failure mode this connector has.
- **The guard ran backwards and is now turned round.** `_ordered` computed
  `set(raw) - set(keys)` over the HAND-BUILT dict, so it could only fire on a
  key ADDED to the dict with no place in the order — visible, because the value
  lands somewhere wrong. It was structurally blind to a field MISSING from the
  dict, which is silent. `_check_nothing_was_dropped` reads the model instead
  and refuses to let a field that is SET leave without being written.
- **Key order is model DECLARATION order, and no file changed.** Every deleted
  tuple (`_PROGRAM_KEYS`, `_LAYER_KEYS`, …) was already in declaration order,
  field for field — checked before the rewrite, not assumed. `Participant` was
  the one deviation and only in the NAME, `share_bps` in memory against `share`
  on disk at the same position, which `_DISK_FORM` carries. Reordering a model
  class now reorders every stored file, so the classes are the canonical order.
- **Omit-when-empty is a tag on the field, not a rule about falsy values.**
  `attach: 0`, `premium: 0`, `showTotals: false` and an empty `participants`
  list are written; `followsUnderlying`, `statutory`, `namedLimits`, `states`
  and `soiSchematic` are not. Dropping any of the first group would rewrite
  files nobody edited, so the decision sits on the field as `OMIT_EMPTY`,
  beside `MONEY`, where the next person adding a field reads it — a table
  beside the model is precisely what this change deleted.
- **An unknown value type raises rather than reaching `json.dumps`.** The other
  half of the derivation's safety: a field typed a Decimal or a set fails at
  the boundary instead of being guessed at or rounded, and money is integer
  whole dollars.

## The schema is the models' fourth table (2026-08-19)

- **`schema/program.schema.json` is now checked against `model.py`, and the
  repair is a script.** It was the FOURTH hand-written field table, after the
  MCP write surface, `program_read` and the canonical serialiser — every JSON
  key typed out by hand, with `additionalProperties: false` at nine sites.
  Reproduced by putting a `brokerRef` on `Layer`: the MCP write answered
  `errors: []` and `towerctl validate` on the file that write produced exited
  1 with "Additional properties are not allowed". The whole suite stayed
  green, because `test_schema_copies_are_identical` compares the two COPIES to
  each other and they go wrong together.
- **Two contract tests, both directions, failing by name.** A model field with
  no schema property and a schema property with no model field. The model side
  is derived (`model.disk_fields`, alongside `money_disk_keys`), so aliases and
  the one renamed field come out right with nothing here knowing about them.
- **`towerkit.schemagen` reconciles the property SET, never the document.**
  `model_json_schema()` would regenerate everything and throw away the
  `minLength`s, the `format: date`, the money `$def`, the `required` lists,
  the descriptions and the `$id` — hand-authored semantics that a pydantic
  model cannot express. So: existing properties are copied byte for byte,
  missing ones are added with a type and no prose, gone ones are dropped
  (and dropped from `required`, which would otherwise demand a forbidden key),
  and the order is model declaration order — already the file's key order.
  Anything it cannot derive raises; a new nested model needs a `$def` a human
  authored, because guessing one is how a schema starts accepting files it
  should refuse.
- **`tools/sync_schema.py`, not a `towerctl` subcommand.** `towerctl` operates
  on the broker's own program files; regenerating this checkout's schema is
  repo maintenance, in the same class as `check_wheelhouse.py`. The derivation
  itself lives in `src/` so it is type-checked and linted; the script is only
  the half that knows where the repo keeps its two copies.
- **`program_check` runs the schema pass now** — `validate_file`, not
  `validate_program`. A client told a file is clean that `towerctl validate`
  rejects has been told the wrong thing, and the one thing that tool exists to
  say is whether the file is good. Cost, stated: the file is parsed twice, and
  the tool can now report `schema:` and `json:` errors no MCP tool can repair.
  It also no longer raises `ValidationError` out of a tool whose job is to
  report what is wrong with a file. The write response still reports only
  semantic diagnostics; the contract tests are what protect that path.

## A read arms the write guard when it returns the sha (2026-08-19)

- **One rule for all four read tools, decided from the RESPONSE.**
  `program_check` called `programs.note()` while returning nothing but
  diagnostics, and `program_view` called it while returning a picture — so an
  agent that had only asked "is this file valid?" was licensed to overwrite it,
  which is exactly what `test_list_does_not_arm_the_write_guard` refuses for
  `program_list`. The guard is a sha comparison, so a caller who was never
  handed a sha cannot reason about staleness, cannot pass `expect_sha`, and
  cannot tell a refusal from a race.
- **Cost: view-then-write now refuses with `not_read`,** naming
  `program_read`. One extra call, and it is the call whose answer the write
  depends on. Writes still arm on success, as before — the rule is about reads.
- **The test derives its tool set from `_register_read_tools`,** so a fifth
  read tool fails before it can arrive un-ruled.

## Refusals name registered verbs, and say so plainly when there is none (2026-08-19)

- **The denylist reasons name their verbs.** `program.lines` said "verb-owned"
  and named nothing, while `GUARDS["layer.attach"]` named `layer_follows` —
  the same inconsistency, one file apart, that the spine was built to end.
  Where Phase 2 owns the verb (`layer_add`, participants, named-limit rows)
  the reason says there is no tool yet and what to do instead, rather than
  naming a call that refuses the retry.
- **The unregistered-tool guard no longer needs a parenthesis.** It matched
  only `name(`, which left the entire denylist unchecked — every reason names
  its verbs in prose. It now matches bare identifiers and subtracts a
  VOCABULARY derived from the live tool schemas' argument names, the write
  surface's fields and the kind names, so `layer_id`, `line_ids` and
  `named_limit` stay out of it without a hand-written exclusion list.

## Round six: the perimeter joins the error contract (2026-08-20)

- **Every `load_program` a tool runs now goes through `mcpserver._load`,**
  which maps a corrupt or model-invalid file to `[invalid_file]` pointing at
  `program_check`. The stable-code net was strung around `_write.mutate`
  only; both reads, the write's own pre-image load, clone's source and
  `program_create`'s enum/model raises all leaked raw exceptions. Same
  repair as round five's `TypeError`, one ring further out.
- **`render.theme` gets a validator diagnostic (`render-theme`),** in
  `validate_file` not `validate_program` (it needs the filesystem; the
  semantic pass stays pure). Junk wrote clean, validated clean, and crashed
  `towerctl render` — the currency genus, one field over. Absolute paths are
  errors even when they load: program files are portable by contract.
- **`schemagen` raises on two shapes it used to get quietly wrong:** an enum
  with non-string values (stringified into a schema that rejects the model's
  own output — proven against Draft202012Validator) and a Money bound the
  money `$def` cannot express (silently dropped). Both latent; both now
  refuse loudly and name the hand-authored repair.

## OPEN — two policy calls for Grant, raised by round six (2026-08-20)

- **Import-time total failure for an unclassifiable int.** `mcpsurface`
  builds SURFACE at module scope, so one innocent count field added to a
  model raises `RuntimeError` at import and bricks all 23 tools until it is
  tagged — `towerctl mcp` will not start. Loud is right; TOTAL is the
  question. The alternative is a per-field denial with the same message, so
  the rest of the surface keeps working. Not changed here.
- **A stale hand-authored `enum` survives retyping silently.** A
  stricter-than-model `required` must be declared in `STRICTER_THAN_MODEL`,
  but retyping an Enum field to `str` leaves the schema's old `enum` behind
  as "hand-authored" — stricter than the model with no declaration. The
  asymmetry is inherited, not decided. Not changed here.

## Round seven: the ring becomes structural (2026-08-20)

- **Every tool registration goes through `_tool`,** which codes anything
  that is not already a `Refusal` as `[internal_error]` before the SDK can
  emit its bare string. Rounds five, six and seven each caught one more
  exception family per-site nets had missed (TypeError, load_program's
  families, then OSError / a meta read / describe's KeyError); the sequence
  ends only if the ring is the registration path itself. A conventions test
  refuses any bare `server.tool()` in the source. Specific sites keep their
  better codes — the ring is the code of last resort.
- **Snapshot before write.** `_atomic_write` used to run before
  `snapshot()`, so a snapshot failure (disk full — live on this machine —
  or a stray file named `.mcp-snapshots`) left the file CHANGED while the
  tool raised: a landed write with a failure receipt and no pre-image.
  Reversed; `snapshot()` now takes the post-sha as an argument since the
  file still holds the pre-image when it runs. An orphaned snapshot for a
  write that never landed is inert — revert compares the post-sha.
- **Theme colours are #RRGGBB, and that is towerkit's own contract** —
  `relative_luminance` parses exactly six hex digits, so `"white"` renders
  a chart and crashes the SOI contrast pick. `theme_problems` walks every
  colour slot (derived off dataclass field defaults, not a name list) and
  the validator reports each as `render-theme`. Cost, stated: a hand-made
  theme using matplotlib colour names that happened to render charts now
  validates dirty. It was one crash away from proving why.

## Round eight: the listing, the last unguarded step, and typed theme slots (2026-08-20)

- **`program_list` resolves inside the per-entry try.** One `*.json`
  symlink pointing outside the roots emptied the whole listing with a
  nonsensical `outside_roots`; a corrupt FILE already got the graceful
  per-entry error. One broken entry must not hide the rest, whatever broke.
- **`_atomic_write` joins the coded perimeter** — `_write` coded six
  failure points and leaked the seventh, the write itself (read-only file,
  writable parent). Now `[internal_error]`, file unchanged, the
  already-recorded snapshot an inert orphan.
- **Theme slots are type-checked against their defaults** — `"size":
  "enormous"` loaded clean and crashed `towerctl soi`. Derived off the
  dataclass fields like the colour walk; no name list.

## Round nine: control characters, one theme walk, one meta reader (2026-08-20)

- **The C0 controls minus `\t \n \r` are refused ON THE MODEL** (`_Model`
  after-validator, derived off `model_fields`). `"Atomic \x00 Corp"` rode
  the hardened write surface into a file that validated exit 0 and crashed
  `towerctl soi` — round eight's theme finding on program content. Hard
  tier deliberately: a file already carrying one crashes the SOI export
  today, so refusing the load names the problem instead of deferring it.
- **`theme_problems` is one walk with one verdict per slot** — round
  eight's parallel colour/type walks had to exactly partition the field
  set, and `titleFont: 12` fell through both. Types now come from the
  ANNOTATION (also covering a future `default_factory` field, closing
  round nine's MISSING-trap ledger candidate); a float size is accepted
  (it renders — the false positive round nine costed out); a bool is not.
- **`_read_meta` is the one reader of snapshot metadata** — two readers
  with divergent nets was the scar; a meta without `post_sha256` now
  refuses `[no_snapshot]` instead of claiming a towerkit bug. Revert also
  refuses `[outside_roots]` for a meta naming a file outside the roots —
  the one write that bypassed the sandbox, sha-bounded but not refused.
- **Listing precedence matches `resolve`** — a shadowed same-name file in
  a second root no longer lists twice with the first root's content — and
  a blank program name refuses `bad_value` instead of creating `.json`.
- **Noted for Phase 2** (round nine design judgement, not acted on):
  `_program_create` / `_program_clone_renewal` remain a hand-mirrored copy
  of the write ritual, ring-coded only; Phase 2 builds on exactly these
  paths and should flatten them first.

## Round ten: names are filenames, and the boundary honours its own contract (2026-08-20)

- **Program names refuse the whole C0 set plus DEL** (`Programs.resolve`)
  — stricter than model content, where `\t \n` are legitimate in notes,
  because a name becomes a FILENAME: `create(name="we\x1bird")` put an ESC
  on disk, and a NUL raised `embedded null character` out of `.resolve()`
  as a "towerkit bug" for a value the caller sent.
- **`parse_tower` / `program_from_rows` catch the model's refusal per
  row** — round nine's validator had regressed the module's own contract
  ("parsing never gives up early"): ANSI-coloured terminal paste aborted
  the whole import with a pydantic dump and no line number. The refusal is
  now that line's numbered diagnostic; partial rows keep the layer and
  drop the refused participant, matching the share-error path.
- **`restore()` and `_write`'s step zero join the coded perimeter** — the
  recovery tool's sha read, image read and final write, and every write
  tool's opening `file_sha256`, were the last bare filesystem touches.
- **Ledger, model-change-gated (round ten's audit):** a theme dataclass
  field annotated bare `Any` would make `_slot_problems` raise
  `TypeError` (isinstance on Any); a future dict-valued model field's
  values would escape `_no_control_characters`, which walks strs and list
  elements only. Neither shape exists today; both need a code change to
  arise, and whoever adds one owns extending the walk.
