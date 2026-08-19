# TowerKit MCP hardening — design

Date: 2026-08-19
Status: approved (Grant, 2026-08-19) — towerkit owns the whole program file;
export and import both in scope; history with multi-step revert; denylist
reviewed with `program.currency` struck out.

## The problem

The MCP server shipped 2026-08-14. Everything built since — the three layer
detail fields, `namedLimits`, `states`, the Schedule of Insurance, the
schematic worksheet, the web panel — never reached it. It is a second,
hand-written table of what the model contains, so a field arrives at the
connector only when someone remembers to edit two files. Nobody remembered.

Measured against `model.py`:

- **17 of 20 `Layer` fields cannot be written.** Only `followsUnderlying` and
  `appliesTo` have tools; `id` is derived.
- **There is no `layer_add`.** `edit.add_layer` exists and the TUI calls it;
  no tool registers it. A program created over MCP has lines and no layers,
  and no way to get one short of cloning a renewal.
- **There is no participant tool.** `_program_read` returns carrier shares;
  nothing writes one. Every MCP-built layer is permanently pending.
- **`program_read` returns 7 of 17 `Layer` fields.** `notes` is writable on
  four tools and readable on none.
- **Program identity is frozen at create.** `insured`, `program`,
  `placement`, `period` have no edit path.
- **The cross-process deadlock is open.** `docs/bugs/2026-08-14-mcp-occ-
  cross-server-stale-sha.md`, filed high severity, four proposed repairs,
  none implemented, and the regression test it specifies was never written.

This is the bug bookkit killed on 2026-08-18. The fix is the same shape and
its slogan is bookkit's: **generalise the field table, keep the verbs.**

## What this is not

Not a port of bookkit's module. bookkit derives from FormSpec builders
intersected with pydantic row models; towerkit has no FormSpec, and its
model is not flat rows. The derivation source here is `model.py` alone, and
the guards that bookkit did not need are the hard part — see *The risk*.

The 2026-08-14 spec's boundary ("book facts belong to bookkit's connector")
is **reversed by this one**. towerkit's MCP now writes every field in the
program file, including premium, participants, `policyNumber` and the
per-layer period. One file, one writer surface. README:119 changes with it.

## Decisions

| | Decision |
|---|---|
| Book boundary | towerkit owns the whole program file. |
| Artifacts | Export *and* import, into a declared `--exports DIR`. |
| Undo | History tool plus multi-step revert; `SNAPSHOT_KEEP` 20 → 100. |
| Naming | Everything prefixed. `program_edit_field`; `restack` → `program_restack`. |
| `layer_add` | Composite: participants, premium, policy number, period, statutory inline — and a bulk form taking a list of layers. |
| bookkit's half | Out of scope. towerkit sends and honours `expect_sha`; bookkit gets an issue. The deadlock stays open until that lands. |

**Naming, with the cost stated.** Two connectors are open at once on Grant's
work machine, and a flat tool list holding two tools called `edit_field` is
a live failure mode — the assistant calls bookkit's with a towerkit target.
Server namespacing does not reliably reach the model. Cost: any saved prompt
naming `restack` breaks. Taken deliberately rather than left to rot.

**Bulk add is atomic.** A batch that fails hard validation writes nothing
and the refusal names the index and the reason (`layer 3 of 8: limit '5mm'
is not money`). Soft diagnostics come back per layer and block nothing,
matching the existing tiers. A half-written stack is worse than no tool.

## Architecture

Three modules, following bookkit's split.

### `mcpsurface.py` (new)

Derives `kind -> field -> type` by walking the pydantic models, minus
`DENIED`. Owns `VALUE_RULES` — the money/date/enum prose — so a tool
description and `describe`'s output cannot disagree, which is how bookkit's
two descriptions came to say money was cents and dollars respectively.

Kinds: `program`, `line`, `layer`, `participant`, `retention`, `sublimit`,
`named_limit`.

Scalars nested one level are addressed by dotted path: `period.start`,
`render.showTotals`. The containing object is denied so that setting one
member cannot blank its siblings.

`describe(kind=None)` is a **tool, not a resource** — kinds, fields, types,
`denied_fields` with reasons, and the value rules. No tool description may
reprint the derived field set: that is the second table again, in prose, and
prose is where the first one rotted. A test greps for it.

### `mcpparity.py` (new)

`IMPLEMENTED` / `DEFERRED` / `NON_ENTITY_TOOLS` over kind × (create, read,
update, delete). Every entry carries a reason. Fails in **both** directions:
a registered tool absent from the ledger fails, and a ledger cell naming an
unregistered tool fails. Today every registration assertion in
`test_mcpserver.py` is a subset check, so a 22nd tool changes no assertion
anywhere.

### `mcpserver.py`

Loses the field table, keeps the verbs. A test greps its source so the
hand-written table cannot return.

### `edit.py`

Gains every cross-field guard. Guards live where all three surfaces inherit
them — never in a surface. This is the existing convention (`test_
conventions.py` already bans `.named_limits.append(` in the TUI) and the
reason bookkit's `guard_name` went into `repo/`.

## The denylist

Reviewed by Grant 2026-08-19. `program.currency` was struck out and is
writable.

| Field | Reason |
|---|---|
| `program.lines` / `.layers` / `.retentions` / `.sublimits` | Verb-owned. Removing a line cascades to everything left with an empty `appliesTo`. |
| `layer.participants` | Verb-owned, plus the over-signing veto. |
| `layer.namedLimits` | Order is the file's order and is display order, never sorted. A wholesale set silently reorders what a broker arranged. |
| `program.$schema` | Owned by the writer. Setting it makes the file claim conformance it was not validated for. |
| `line.id`, `layer.id` | Ids slug from names on first naming and are stable afterwards. Renaming goes through `name`, and the id cascade is part of that verb. |
| `layer.statutory` | Carries `statutory ⇒ limit == 0`. A bare set writes a file the validator refuses. |
| `layer.followsUnderlying` | Already a verb that heals the attachment. Two ways to set it is how two definitions drift. |
| `layer.appliesTo` | Verb-owned; validates every line id before the write. |
| `program.render`, `layer.period`, `program.period` | The objects. Their scalars stay writable by dotted path. |

### Guarded, not denied

| Field | Guard |
|---|---|
| `layer.attach` | Refused on a follows-underlying layer, where it is derived. The refusal names the fix: change the underlying layer's limit, or clear `followsUnderlying` first. |
| `layer.limit` | Refused on a statutory layer, naming `layer_statutory`. |
| `layer.states` | Refused on a dollar-limited layer — a coverage fact, not a note. |
| `layer.limitsDetail` / `.premiumDetail` / `.retentionDetail` | Written; the layer's own detail diagnostics return on the response. Nothing blocks. |
| `program.currency` | Writable, and the response carries a warning that **no figure was converted** — the amounts are unchanged integers and only the label moved. Silence was the failure mode; saying it out loud removes it. |

## Write safety

**`expect_sha`** becomes an optional argument on every write. Supplied, it is
authoritative; omitted, the in-session map is used exactly as today, so
nothing existing breaks.

**Correction, 2026-08-19 (after surveying bookkit).** The bug doc asserts
bookkit "consults a different `Programs.seen` dict". It does not. bookkit
never imports `towerkit.mcpserver`, never builds a `Programs`, and never
calls `note()`. Its token is a persisted SQLite column,
`placement.source_sha256` (`bookkit/sync.py:226,276`), refreshed by exactly
one function, `sync.project`.

So there are two independent wedges, and `expect_sha` fixes only one:

- *towerkit refuses* because its in-process map goes stale when bookkit or
  the TUI writes. `expect_sha` fixes this, as specified.
- *bookkit refuses* at `bookkit/sync.py:1164` because `source_sha256` goes
  stale when towerkit writes — and **no bookkit MCP tool can re-arm it**.
  `sync.project` is reachable from the CLI, the TUI, the seed and the import
  committer, and from no MCP tool. `expect_sha` does nothing here.

The bookkit-side repair is therefore a re-projection reachable from MCP:
`bookkit/mcpserver.py:1415` (`program_layers`) calls bare `load_program`,
and its own docstring already tells the assistant to call it before any
program write. Making that read project — the way towerkit's `program_read`
arms `note()` — clears the wedge on the read the agent was already doing.
`TOWERKIT_POST_WRITE_CMD='bookctl sync --path {path}'` (README:112, already
built) helps but does not suffice: a hook failure never fails the write.

**Consequence for "towerkit owns the file".** `write_through` refuses on any
byte change, with no field-level diff and no merge, by declared design
(`bookkit/sync.py:20-22`). Two active writers on the same fields makes every
interleaved edit a hard refusal. The re-projection above is what makes the
boundary reversal survivable rather than progressively disabling bookkit.

Refusals must name a call that works. Today's stale-sha message names the
possible writers and no tool; it will name `program_read`.

**Snapshots** keep 100 (was 20), still pruned by mtime, still globbing
`TKW-*` so bookkit's `MCP-` history is untouched.

**`program_history(name)`** lists refs newest first with the summary, the
post-write sha, and whether the ref is still reachable.

**Multi-step revert.** `program_revert_write(write_ref)` restores that ref's
pre-image after checking the file matches the post-sha of the *most recent*
write in the chain, rather than of the ref being reverted. Reverting ref *N*
therefore discards writes *N* onward, which is what walking back means.

## Artifacts

`--exports DIR` beside `--programs`: resolved absolute, created if absent,
covered by `towerctl mcp --check` the way roots are, and defaulting to a
temp directory so a tool never writes somewhere surprising. Tools return the
written path.

Export: `program_render` (SVG/PDF), `program_soi` (with the schematic
sheet), `program_compare`. Import: `program_import` (spreadsheet via
`ingest.import_schedule`, pasted text via `parse_tower`), `program_template`.

Import writes a new program and refuses an existing name, like
`program_create`.

## Error contract

Today every failure is a bare `ValueError` and a client cannot tell "re-read
and retry" from "that file does not exist". Refusals gain a stable `code`
alongside the message: `stale_sha`, `not_read`, `outside_roots`,
`no_such_program`, `no_such_target`, `denied_field`, `guard_refused`,
`bad_value`, `exists`, `no_snapshot`.

Two standards borrowed from bookkit's 2026-08-18 findings, both of which
cost real debugging there:

- **A refusal names a value that would be accepted.** Money comes back as
  `'$5,000,000'`, never raw cents — a model handed cents writes 100× the
  amount on the retry.
- **No enum reprs leak.** `<Placement.BOUND: 'bound'>` is not something a
  client can pass back, so a refusal printing it refuses the retry too.

## Testing

Every contract test below is claimed only once **mutation-verified**: break
the production code, observe the named failure, restore. Four assertions in
the statutory feature passed for the wrong reason; negative and
absent-by-default assertions are the usual culprits.

1. `test_a_new_model_field_becomes_writable_with_no_mcp_edit` — **the
   point.** Add a field to a model, recompute the surface, assert the write
   lands. Nothing in `mcpserver.py` or `mcpsurface.py` changes.
2. `test_every_model_field_is_writable_or_denied_with_a_reason` — a field
   that is neither fails by name.
3. `test_a_denied_field_stays_denied` — the inverse half.
4. `test_program_read_returns_every_model_field` — read is derived, not
   hand-listed, so it cannot go lossy again.
5. `test_every_registered_tool_appears_in_the_ledger` and its inverse.
6. `test_every_edit_py_mutation_is_reachable_or_deferred` — `edit.py`'s
   public mutations are the verb roster. Seven are unreachable today.
7. `test_mcpserver_keeps_no_second_field_table` — source grep.
8. `test_no_tool_description_reprints_the_derived_field_table`.
9. `test_every_tool_has_a_docstring`.
10. **One test per guard**, each mutating the guard away and confirming the
    refusal disappears: attach-on-follows, limit-on-statutory,
    states-on-dollar-limited, currency-warns-no-conversion.
11. `test_a_stale_second_reader_can_write_with_expect_sha` — the
    cross-process regression the bug doc specified and nobody wrote.
12. `test_bulk_layer_add_is_atomic` — one bad layer in eight writes nothing.
13. Protocol round-trip coverage for every tool, not 3 of 21, and at least
    one asserting `result.is_error` and the message shape.

## Phasing

**Phase 1 — the spine.** `mcpsurface.py`, `DENIED`, `program_edit_field`,
lossless `program_read`, `describe`, `mcpparity.py`, `expect_sha`, the
guards, contract tests 1–11.

**Phase 2 — the verbs.** `layer_add` composite and bulk, participants,
named limits, program-level edits, `layer_statutory`, `line_transfer`,
tests 12–13.

Phase 2 lands a **required bookkit change with it**. `bookkit/tests/
test_conventions.py:151-155` asserts the old boundary by name: "If towerkit
ever grows `edit.add_participant`, add `.participants.append(` here and
delegate." Phase 2 grows exactly that, so bookkit's hand-rolled append at
`bookkit/sync.py:892` must delegate to towerkit's new API and the convention
test must be extended. The test failing is the system working; it is not
optional to fix.

**Phase 3 — artifacts.** `--exports`, render, SOI, compare, import,
template, `program_search`, `program_history`, multi-step revert.

The spine lands first because it is the risky part and everything else sits
on it; built the other way round, the verbs get rewritten onto the
derivation afterwards.

## The risk

bookkit's model is flat CRM rows, so a generic setter over it is safe.
towerkit's is not. `attach` is freely settable on an ordinary layer and
derived on a follows-underlying one; `id` follows `name`; statutory forces
the limit to zero. A generic setter walks past all of it unless every rule
is a guard inside `edit.py`.

Always-derived fields are on the denylist. The **conditional** ones cannot
be, and if one is wrong the future-proof spine becomes a quiet way to
corrupt a tower. Mitigation is guards in `edit.py` plus test 10 — a test per
invariant that mutates the guard away. This is why the spine ships before
anything is built on it.

## Out of scope

- bookkit's `expect_sha` half (issue filed).
- A lock file. The TOCTOU window between the sha check and the atomic write
  stays, as in the 2026-08-14 spec.
- A strict mode. Semantic errors stay soft: a tower under construction is
  invalid by construction.
- Teaching towerkit any line of business. `namedLimits` and `states` stay
  generic; the connector never composes a sentence about state law.
