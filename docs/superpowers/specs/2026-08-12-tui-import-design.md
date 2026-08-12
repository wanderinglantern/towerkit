# TUI import entry points — design

2026-08-12. The spreadsheet import workflow (`towerctl template` /
`towerctl import`, shipped 2026-08-11) becomes reachable from the TUI
program browser. Three entry points; zero new parsing logic.

## Shared core (refactor, zero behavior change)

`cli._cmd_import`'s pipeline moves into `towerkit.ingest` as:

```python
def import_schedule(
    source: str | Path,          # file path, or raw pasted text via text=
    *,
    text: str | None = None,     # pasted schedule text (mutually exclusive
                                 # with a file source; the TUI paste modal
                                 # and the CLI's `-` stdin path both use it)
    insured: str = "",
    program: str = "",
    inception: str = "",
    expiry: str = "",
) -> Program: ...
```

It owns: xlsx/csv/text dispatch, `parse_tower` for text, meta fallbacks
(insured/program/period from arguments when the source lacks them, dates
via the existing flexible parser), and raising `ProgramInvalidError` (or
the ingest error types it already uses) on unusable input. The CLI handler
shrinks to argument plumbing around it — output naming and the
overwrite-refusal check stay in the CLI handler and are mirrored (not
shared) in the TUI, because the TUI writes into `programs/` while the CLI
writes into CWD. Existing CLI tests must pass unchanged through the
refactor; the exact split is settled during planning against
`_cmd_import`'s real shape.

## Browser entry points

All three live on `ProgramBrowser` (free keys verified against its
BINDINGS):

- **`i` — import a schedule file.** New `ImportFileModal`: a path `Input`
  for the schedule (xlsx/csv/text) plus insured, program, and the SAME
  optional inception/expiry `Input`s the paste modal has — then
  `import_schedule(path, insured=…, program=…, inception=…, expiry=…)` →
  save to `programs/<slug(insured)>-<slug(program)>.json` (the CLI's
  naming rule) → refuse to overwrite an existing file (notify which file,
  write nothing — same rule as `towerctl import`) → refresh the table and
  open the new program in the editor.
  (Amended 2026-08-12 during implementation, then amended again the same
  day: the original single-prompt design assumed the xlsx template carries
  insured/program meta — it does not, and `Program` requires both
  non-empty, so a first pass chained three `PromptModal`s. That turned out
  to be a dead end for text/csv sources: `parse_tower` has no date syntax,
  so a text-file schedule could never supply a period and was
  unimportable via `i`. The optional inception/expiry inputs — already
  proven in `PasteImportModal` — fix that, so `ImportFileModal` replaces
  the chained prompts outright.)
- **`p` — paste a schedule.** New `PasteImportModal`: a multiline
  `TextArea` for the schedule plus four `Input`s — insured, program,
  inception, expiry (the CLI's stdin flags as form fields; dates accept
  human forms via the existing flexible parser). Confirm →
  `import_schedule(..., text=...)` → same save/refuse/open flow as `i`.
- **`w` — write a blank template workbook.**
  `PromptModal("Template file name:", default="template.xlsx")` → if the
  target exists, `ConfirmModal("<file> exists — overwrite?")` and only
  write on confirm; otherwise `write_template(path)` immediately → notify
  the written path.
  (Amended 2026-08-12: this deliberately diverges from `towerctl
  template`, which overwrites silently. A filled template is keyed-in
  user data, and the repo's data-safety rule — CLAUDE.md: never run an
  irreversible data operation without surfacing the risk first — applies
  in the TUI even where the CLI doesn't enforce it.)

## Error handling

- Unreadable/unparseable source: notify the ingest error message,
  severity error, nothing written.
- Overwrite refusal: notify `"<file> exists — not overwriting"`, nothing
  written.
- All entry points are additive: no existing program file is ever
  modified. The only writes are one new `.json` (import/paste) or one
  `.xlsx` (template).

## Out of scope

- Import from the editor screen (browser only — imports create files,
  the browser is the file surface).
- Re-import/merge into an existing program.
- Any change to the import parsing itself.

## Testing

- Refactor: existing `tests/test_cli.py` import/template tests pass
  unchanged.
- Pilot tests on the browser (driving `ImportFileModal`/`PasteImportModal`
  by their `#import-*`/`#paste-*` ids): `i` with a real template-round-trip
  xlsx creates the program file and lands in the editor; `i` refuses an
  existing target (bytes unchanged, notify); `i` imports a `.txt` schedule
  when inception/expiry are filled (the dead-end case that motivated the
  modal); `p` drives `parse_tower` with pasted text and the four fields;
  `w` writes the workbook and notifies on a new target, and on an existing
  target only overwrites after `ConfirmModal` is accepted (declined leaves
  the file's bytes unchanged). Plus `import_schedule` unit tests for the
  text/file dispatch and meta fallbacks.
