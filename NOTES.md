# NOTES

Running log of anything in `reference/` that turned out to be wrong, unclear, or missing.

- **2026-08-11 — `reference/` is entirely absent.** The working directory was empty at
  project start: no `insurance-schematics-method.md`, no `HANDOFF.md`, no
  `render_program.py` / `render_tower.py`, no `program.json`, no
  `program.schema.json`, no reference PNGs. Everything the brief says to "port" or
  "copy frozen" had to be authored from the brief itself (§1–§8 are specific enough
  to reconstruct the domain logic exactly; the visual design of the matplotlib
  output and the sample program data are my reconstruction and cannot be checked
  against the reference PNGs). If the reference material turns up, diff
  `schema/program.schema.json` and `programs/atomic-2026.json` against it first,
  then compare renders side by side.
- The §8 defect list (asserts, float shares, mathtext escaping, wall-clock SVG
  timestamps, fake git SHA, positional argv) is treated as authoritative even
  though the prototype it describes is not present — the new code simply never
  introduces those defects.
