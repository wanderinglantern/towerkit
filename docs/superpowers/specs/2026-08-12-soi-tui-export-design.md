# SOI export from the TUI editor — design

2026-08-12. Follow-up to `2026-08-11-soi-export-design.md`, which delivered
the SOI pure core (`soi.py`), the workbook writer (`render/soi_xlsx.py`), and
the `towerctl soi` CLI command. This spec closes the remaining accessibility
gap: the TUI editor can render the chart with one keystroke (`r`) but has no
way to export the SOI workbook. Goal: SOI export from the editor, exactly as
accessible as render.

## Approach

A dedicated key binding, mirroring `r`'s pattern. Alternatives considered and
rejected:

- **Fold into `r`** (render also writes the SOI): couples chart output to a
  data export not wanted on every render.
- **Export menu modal** (formats list): builds UI for formats that don't
  exist yet. YAGNI.

## Behavior

New binding on `EditorScreen`: `("x", "export_soi", "SOI")`, listed with the
Output group (`r`, `t`).

`action_export_soi` mirrors `action_render` step for step:

1. `_drain_focused_input()` — text sitting in a focused Input is committed
   first, same as save/render.
2. Validation gate: if `session.diagnostics().errors` is non-empty, notify
   `"N validation errors — fix before exporting"` (error severity) and stop.
   Same rule as render; the two outputs stay consistent.
3. Build and write via the existing modules — no new export logic:
   - sections: `build_soi(self.session.program)`
   - title: `sheet_title(program)`
   - theme: `self.tower_theme` (the editor's live theme, as render uses)
   - output path: `Path("dist") / default_filename(program)` →
     `dist/<Insured> - Schedule of Insurance.xlsx`
   - `show_premiums=_opts(self).show_premiums` — follows the live `t`-menu
     toggle, same as the chart; no separate SOI setting.
4. Notify the written path; if `OPEN_CMD` is set, open the file with it
   (same subprocess pattern as `action_render`).

Help text gains one line under Output:
`x          export SOI workbook (.xlsx to dist/)`.

## Deliberate choices

- **Output goes to `dist/`**, the TUI's output convention (render writes
  there), not the CWD default the CLI uses. Filename stays
  `default_filename(program)` so TUI and CLI produce identically named files.
- **Unsaved programs export fine** — the SOI filename derives from
  `program.insured`, not the file path, so no save is required first.
- **No program mutation.** Export writes a new xlsx only; the program JSON
  and undo stack are untouched. Repeated exports overwrite the same dist/
  file, matching render's overwrite behavior.

## Error handling

- Validation errors: blocked with a notify (step 2 above).
- Filesystem errors: `write_soi` already creates parent dirs
  (`soi_xlsx.py` normalizer does `mkdir(parents=True)`), so a missing
  `dist/` is not an error. No try/except beyond that — `action_render`
  has none either, and inventing extra handling here would diverge from
  the pattern being mirrored.

## Testing

Pilot tests in `tests/test_tui.py`:

1. Pressing `x` on a valid program writes
   `dist/<Insured> - Schedule of Insurance.xlsx` and notifies the path.
2. Pressing `x` on a program with validation errors writes nothing and
   notifies the error count.
3. The premiums toggle carries: with `show_premiums` off via the options
   flow, the exported workbook omits the Premium column (assert via
   openpyxl read-back, as `test_soi_xlsx.py` already does).

No changes to `soi.py` or `render/soi_xlsx.py`, so the existing determinism
and mapping tests stand unchanged.
