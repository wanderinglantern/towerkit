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

### Client data

`programs/private/` is **gitignored** — put real client programs there. The
TUI browser lists it alongside `programs/`, but nothing in it can reach the
public repository or CI.

## MCP connector (design assist)

The `towerctl mcp` command exposes an MCP server for a design assistant. The
connector panel is hand-entry, so ask for the values rather than typing them
from memory — run this **on the machine the connector will run on**:

```
towerctl mcp --connector-info
```

It prints one line per field, ready to paste:

```
Add MCP Connector — paste one line per field:

  Name         towerkit
  Command      /Users/you/Developer/towerkit/.venv/bin/towerctl
  Arguments    mcp, --programs, /Users/you/programs
  Env Secrets  (none)
  Mode         both
```

Three things that block a hand-typed connector, all handled above:

- **Command is absolute.** `towerctl` is not on `PATH` — install.sh builds
  `./.venv` inside the checkout — and the panel's launcher inherits neither
  your `PATH` nor a shell alias, so a bare `towerctl` never starts.
- **Arguments are comma-separated**, because that is what the panel splits on.
  Written space-separated they arrive as a single argument, `--programs` is
  never seen, and the server silently falls back to `./programs`.
- **Program roots are always emitted.** The `./programs` default is wrong for
  a server the client launches from its own working directory, and getting it
  wrong is invisible: the server starts fine and reports an empty shelf. With
  no roots to emit, `--connector-info` refuses (exit 2) rather than print a
  config that fails that way. Pass `--programs <dir> [<dir>…]` to set them;
  if bookkit is installed, they are read from `bookctl roots --json` so the
  roots you configured once are not typed again.

Then verify before you trust it:

```
towerctl mcp --check
```

It exits 0 only when the console script is executable, every root exists and
holds at least one program file (with a count, so a wrong-but-present
directory is obvious), and startup writes nothing to stdout — stdout is the
MCP wire, and one stray `print` corrupts the protocol.

Set `TOWERKIT_POST_WRITE_CMD` in Env Secrets only to notify something
downstream after each write (e.g. `bookctl sync --path {path}`).

The assistant designs the tower: read a program, draw it, add and remove
coverage lines, set retentions and sublimits, change which lines a layer spans,
mark a layer follows-underlying, restack, and start a program from scratch or as
next year's renewal. Book facts — premiums, market shares, policy dates — belong
to bookkit's connector, not this one.

Two rules shape every write. **Validation errors do not block a write**: a tower
under construction is invalid by construction, so a new line reports `line-empty`
and a half-built stack reports `line-gap`, and those come back in the tool's
result while the write lands. Only a file towerkit could not load is refused.
And **a write refuses against a file this session has not read, or one that
changed since it read it** — re-read and retry. The TUI editor refuses the
mirror image: it will not save over a file that moved underneath it, offering
reload, overwrite, or keep editing.

Every write leaves a pre-image in `.mcp-snapshots/` beside the program;
`program_revert_write` puts one back, but only while the file still holds
exactly what that write produced.

Set `TOWERKIT_POST_WRITE_CMD` to have something re-read a file after every
write; `{path}` is substituted, and the command never fails the write:

    export TOWERKIT_POST_WRITE_CMD='bookctl sync --path {path}'

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
