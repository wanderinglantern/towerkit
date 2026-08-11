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
