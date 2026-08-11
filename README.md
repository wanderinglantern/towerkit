# towerkit

Publication-quality schematic diagrams of corporate insurance programs from
JSON descriptions, plus a terminal UI for creating and editing those files.

The JSON files in `programs/` are the source of truth: the TUI edits files,
the renderers draw files, and nothing else holds state.

## Quick start

```bash
uv sync

# validate program files (exit 1 on any error)
uv run towerctl validate programs/*.json

# render one program to SVG/PDF/PNG
uv run towerctl render programs/atomic-2026.json --theme themes/marsh.json --out dist --format svg,pdf,png

# renewal comparison: two files and a mode, not a second schema
uv run towerctl compare programs/atomic-2026.json programs/atomic-2027.json --theme themes/marsh.json --out dist

# edit in the TUI (or `towerctl new` for a blank program);
# --theme drives the live preview and the in-app `r` render
uv run towerctl edit programs/atomic-2026.json --theme themes/marsh.json

make render   # regenerate everything in dist/
```

Set `OPEN_CMD` (e.g. `export OPEN_CMD=open`) to auto-open rendered files.

### Corporate networks

The repo ships a `uv.toml` with `native-tls = true`, so uv trusts the system
certificate store — this is what makes installs work behind a TLS-inspecting
corporate proxy (`invalid peer certificate: UnknownIssuer` otherwise). If your
company also requires an internal package index, point uv at it:

```bash
export UV_INDEX_URL=https://artifactory.example.com/api/pypi/pypi-remote/simple
```

If PyPI is blocked outright, the repo ships a one-command offline installer —
the only network access it needs is github.com:

```bash
./install.sh      # downloads the release wheelhouse once, installs into ./.venv
./towerctl edit   # wrapper around .venv/bin/towerctl
```

The wheelhouse holds towerkit plus every runtime dependency as prebuilt wheels
for macOS (Intel and Apple Silicon, Python 3.12–3.13). `install.sh` installs
the *current checkout* editable, so re-running it after `git pull` picks up
code changes without a new release. Maintainers rebuild the wheelhouse with
`make wheelhouse` after changing dependencies and attach it to the release.

## Design in one minute

- **Lines are columns; layers carry `appliesTo`.** An umbrella spanning three
  lines, a monoline tower, and a buffer layer are all the same object with a
  different `appliesTo` array. No special-casing in the renderer.
- **One global compressed vertical scale** (piecewise over the breakpoint set,
  γ = 0.35 by default) — $52M sits at the same height in every column. When
  γ ≠ 1 there is no y-axis: reference lines at real attachment points, dollar
  labels in the gutter, and a visible "not to scale" caveat.
- **Money is integer dollars; shares are integer basis points.** Sums are
  exact; `sum(shares) <= 10000` needs no tolerance. On disk, shares are
  decimal fractions and the conversion is lossless both ways.
- **Shares may sum to less than 1.0** — unplaced capacity is a legitimate
  state, rendered hatched grey with a warning, never an error.
- **Retention is not insurance**: drawn below a heavy zero line on its own
  compressed scale, typed (deductible / SIR / captive), never a carrier
  colour.
- **Deterministic output**: two identical runs produce byte-identical SVG,
  with real git provenance stamped in the footer.
- **Canonical serialisation**: saving a file you didn't edit produces a zero
  diff, so `git diff` between renewal years stays readable.

See `DECISIONS.md` for choices the brief left open and `NOTES.md` for what was
missing or unclear in the reference material.

## Layout of the code

```
schema/program.schema.json   frozen JSON Schema (copy shipped in the package)
themes/                      marsh.json, default.json
programs/                    program files — the source of truth
src/towerkit/
  model.py                   Pydantic models, canonical (de)serialisation
  validate.py                semantic rules → Diagnostics(errors, warnings)
  scale.py, layout.py        pure geometry: no plotting imports (tested)
  compare.py                 renewal delta table generation
  theme.py, money.py         colours; money/share parsing and formatting
  render/mpl_program.py      single placement → SVG/PDF/PNG
  render/mpl_renewal.py      two placements + generated change table
  render/ascii.py            terminal preview (drives the TUI's live pane)
  tui/                       Textual app: browser, editor, renewal diff
  cli.py                     towerctl
```

## Development

```bash
make check       # ruff + pytest + validate all programs
make typecheck   # mypy (strict on the pure core)
```

CI validates every file in `programs/` against the schema and the semantic
rules — a PR with an invalid program fails the build.
