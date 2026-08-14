# TowerKit Editor MCP — design

Date: 2026-08-14
Status: approved (Grant, 2026-08-14) — server hosted in towerkit with a
re-sync hook; scope is structure + create + see; guard both sides against
drift; semantic errors stay soft with no strict mode.

## Goal

Design a tower in conversation. Lines, retentions, sublimits, the shape of
the stack — the work Grant described as "walk through the process in
towerkit". The assistant reads a program, sees it, changes its structure,
and reports what the validator thinks, through the same rules the TUI
editor obeys.

## The boundary this is the other half of

bookkit's MCP edits the facts that arise from book events — premiums
firming, markets binding, dates moving — through `sync.write_through`
(`2026-08-13-mcp-policy-records-design.md`). Tower DESIGN stays in
towerkit. That spec deliberately shipped without line/retention/sublimit
tools and named this phase as where they would land. The line holds: no
design vocabulary enters bookkit, and towerkit still knows nothing about
accounts, placements, or the CRM.

One consequence decided the hosting question. bookkit's `project()`
refuses any file without a confirmed account link, and
`_resolve_linked_placement` demands an exact `PLC` ref with a
`program_path`. Design happens upstream of all that — hypothetical towers,
renewal clones, prospect work, which towerkit's own working rules call a
first-class use case. A design server that rides bookkit's transport could
not touch them. So the server lives here.

## Where the code lands

New in towerkit:

- `src/towerkit/edit.py` — the structural edit API (§ below).
- `src/towerkit/mcpserver.py` — the server, mirroring bookkit's file
  layout (one module at package root, stdio, thin tools over the real API).
- `towerctl mcp` — the subcommand that runs it.
- `mcp>=2.0` in `[project.dependencies]` → the new-dep drill: add to
  pyproject, `python3 -m pip download mcp --no-deps -d wheelhouse`, re-zip,
  `gh release upload v0.2.0 … --clobber`, extend install.sh's
  stale-wheelhouse probe.

Modified in towerkit: `tui/session.py` (delegate to `edit`, plus the
stale-file guard), `tui/screens/editor.py` (call `edit.*` instead of inline
lambdas; one new modal).

New in bookkit: `bookctl sync --path FILE`, projecting a single file.
That is the whole bookkit-side change.

## `towerkit.edit` — the extraction that carries this phase

There is no reusable edit API to expose today. `EditSession` offers only
`add_layer`, `restack`, and `unique_id`; every line, retention, and
sublimit mutation is an inline lambda inside `editor.py` — a 79K screen
file. Exposing that over MCP by copying the lambdas would fork the rules
in two, and the fork would drift.

So the mutations move into one module both surfaces call:

```
lines:      add_line, rename_line, set_line_group, move_line, remove_line
retentions: add_retention, edit_retention, remove_retention
sublimits:  add_sublimit, edit_sublimit, remove_sublimit
layers:     remove_layer, set_applies_to, set_follows_underlying
moved:      add_layer, restack, suggested_attach, unique_id, slugify
```

Plain functions over a `Program`, no session state. The editor keeps
wrapping them in `session.mutate` for undo and follows-underlying healing;
the MCP calls them inside its own load → mutate → dump cycle. `session.py`
imports from `edit`, never the reverse.

`remove_line` cascades: the id comes out of every layer's, retention's, and
sublimit's `appliesTo`, and anything left with an empty list is removed
too. `appliesTo` is `min_length=1`, so a partial cascade would raise on
assignment rather than produce a diagnostic.

`rename_line` keeps the 67ac42f behaviour — the id follows the name, via
`unique_id(slugify(name), exclude=current_id)`. It is the only rename that
moved into `edit`: the editor's layer rename stayed inline (`editor.py`,
the `layers-sheet` cell edit and the layer detail form) because it re-slugs
the id only while `PLACEHOLDER_ID` still matches, which is not what
`unique_id`-based renaming does — a shared `rename_layer` would start
churning layer ids on every rename, the opposite of what this extraction is
for. There is no MCP consumer either: layer naming belongs to bookkit's
`program_layer_edit`, not to this server.

Guard test: nothing under `tui/` mutates `program.lines`, `.layers`,
`.retentions`, or `.sublimits` directly. Same shape as bookkit's
no-raw-SQL-in-tui convention test.

## Addressing programs

`towerctl mcp --programs DIR [DIR…]`, defaulting to `./programs` and its
`private/` subdirectory — the browser screen's current hardcoded pair,
lifted into an argument because an MCP server is launched with whatever
working directory the client chooses.

Tools name a program by stem: `atomic-2027`, or `private/endeavour-2026`.
The name resolves against the roots; anything resolving outside them is
refused. This server is not a general file writer.

## Tool surface

Read:

- `program_list()` — names, insured, period, placement, layer count.
- `program_read(name)` — lines (id/name/abbr/group), layers
  (id/name/appliesTo/attach/limit/premium/participants/followsUnderlying),
  retentions and sublimits with their array indices, period, placement,
  and the file's `sha`.
- `program_view(name)` — `render.ascii.render_ascii(colour=False)`. This
  is how the assistant sees a tower: gaps, overlaps, and column shape are
  visible in the picture in a way they are not in a layer list.
- `program_check(name)` — `validate_program` errors and warnings with
  their refs.

Create:

- `program_create(name, insured, program, placement, period_from,
  period_to, lines=[…])` — refuses an existing path.
- `program_clone_renewal(source, dest)` — wraps `Program.clone_as_renewal`.

Design (one tool per `edit.*` entry): `line_add`, `line_edit`,
`line_remove`, `line_move`, `retention_add`, `retention_edit`,
`retention_remove`, `sublimit_add`, `sublimit_edit`, `sublimit_remove`,
`layer_remove`, `layer_lines`, `layer_follows`, `restack`.

Retentions and sublimits have no ids — they are array positions. Tools
address them by the index `program_read` reported, guarded by an
`expecting_*` field (`expecting_lines`, `expecting_amount`,
`expecting_name`): compare-and-set lite, the same guard
`program_layer_edit` uses against id drift.

Money arrives as human dollars and goes through `money.parse_money`;
shares through `money.parse_share`. towerkit is dollars-native — there is
no cents conversion on this side of the boundary.

## Validation: semantic errors are soft

bookkit's contract is *a failed validation writes nothing*. That rule is
right for book-facts edits against a finished tower, and wrong here. A
tower under construction is invalid by construction: a new line is
`line-empty` until it carries a layer, a stack being built passes through
`line-gap`, a layer awaiting markets is unplaced. Enforcing the bookkit
contract would refuse the second step of every design conversation.

Two tiers instead:

- **Hard** — the write must parse and model-validate. A write that would
  produce a file towerkit cannot load is refused, always, and nothing is
  written.
- **Soft** — `validate_program` errors do not block the write. Every write
  returns `errors` and `warnings` verbatim, so the assistant sees
  `GAP xs-1→xs-2 at $10,000,000` in the result and keeps building.

This mirrors the editor exactly: it holds invalid programs all day and only
confirms at save. No `strict` mode (Grant, 2026-08-14) — a flag defaulting
to hard-refuse would train the assistant to fight the tool during the one
workflow this server exists for.

The honest consequence: an MCP design session can leave a file that
bookkit's `project()` refuses, because projection requires `diags.ok`. The
re-sync hook reports that in the tool result rather than hiding it, and the
program simply stays unprojected until the tower is whole — which is the
same thing that happens when the tower is built in the TUI.

## Concurrency: guard both sides

Up to three writers now share one file: the TUI's `EditSession`, bookkit's
MCP, and this server. Only two of them check anything.

Server side: the process holds a last-seen sha per path, set by every read
and every successful write. A write against a drifted file refuses —
"changed on disk (towerkit's editor, bookkit, or another tool) — re-read
and retry", the contract compare-and-set already trained the model on. A
write to a path never read in this session also refuses, telling the caller
to read it first: there is no baseline to compare against, and inventing
one is precisely the false safety the snapshot design rejected. The two
creation tools are the exception — they write files that did not exist, and
refuse outright if the path is already there.

Write tools take no sha argument; the comparison is against the server's
own last-seen value, so the model cannot accidentally launder a stale one
back in. The `sha` in `program_read` is there for the human reading the
transcript.

Editor side — a pre-existing hole this phase exposes rather than creates:
`EditSession` stamps `_saved_text` at open and `save()` never re-reads the
file, so an editor left open silently clobbers any external write,
including bookkit's MCP writes today. `EditSession` gains `_disk_sha`,
recorded at open and after each save; `save()` raises `StaleFileError` when
the on-disk sha no longer matches. The editor catches it into a modal —
**Reload / Overwrite / Keep editing** — in the same family as the
esc-with-changes modal. Showing the actual difference via `diff.py` is a
later refinement, not v1.

## Revert

Same story as the bookkit side, for the same reason: file contents are not
event-log rows, so a revert has to be a pre-image.

Every successful write copies the pre-image into the program directory's
`.mcp-snapshots/` — the directory bookkit already uses — under a `TKW-`
prefixed write ref, with a sidecar recording the path and the post-write
sha. Each side's prune globs only its own prefix, so the two never delete
each other's history.

`program_revert_write(write_ref)` restores the pre-image only while the
file still holds exactly what that write produced. Otherwise it refuses and
says what to do. There is no batch concept here — one tool call is one file
write is one revert unit.

## The re-sync hook

`TOWERKIT_POST_WRITE_CMD`: a command template containing `{path}`, run
best-effort after a successful write, with a timeout. towerkit never learns
that bookkit exists; bookkit's install step sets the variable to
`bookctl sync --path {path}`.

The outcome rides in the tool return as `resync: "ok"`,
`"unlinked: …"`, `"failed: …"`, or `"not configured"`. It never fails the
write — by the time the hook runs, the file on disk is already correct, and
a hook failure that rolled back a good write would be a worse lie than a
stale cache.

## Testing

- Round-trip every write tool over the protocol; canonical zero-diff on an
  untouched program after every write path.
- Mid-build invalid states: the write lands and the errors come back in the
  result. A model-invalid write is refused and the file is byte-identical
  after.
- Stale sha refuses on the server side; `StaleFileError` and the modal on
  the editor side (pilot test).
- `remove_line` cascade: layers, retentions, and sublimits left empty are
  gone, and the program still model-validates.
- Snapshot/revert: byte-identical restore, refusal after a later edit,
  and the two prefixes pruning independently in one directory.
- Hook failure leaves the write intact; hook success reports `ok`.
- Real scaffolded program fixtures, never hand-built dicts.
- Gates unchanged: `uv run pytest -q`, `uv run mypy src`,
  `uv run ruff check src tests`.

## Out of scope (v1)

Render and export tools (`towerctl` already does this from the shell);
managing the `programs/` directory itself; multi-file or bulk edits;
merging concurrent edits — this design refuses, never merges; a lock file;
showing the diff inside the stale-save modal; teaching towerkit anything
about accounts or placements.
