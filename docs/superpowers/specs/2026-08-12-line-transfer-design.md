# Line transfer between programs — design

2026-08-12. Move or copy a line — with its dependent stack — from the open
program into another program file, from the TUI editor.

Driving use cases (in priority order): scenario building for renewals
(copy), reusing a built tower as a template (copy), fixing misfiled data
(move). Program split/merge is explicitly out of scope.

## Granularity

The unit of transfer is **one line plus its exclusive dependents**. Single
layers do not transfer on their own; whole programs are not merged.

## Pure core: `src/towerkit/transfer.py`

New module, pure (no rendering imports — the existing repo rule), fully
unit-testable:

```python
@dataclass
class TransferSummary:
    travels: list[str]        # human lines: "Layer: 1st Excess", "SIR $250,000"
    stays: list[str]          # "Umbrella — shared with AL, EL"
    renames: list[tuple[str, str]]  # (old_id, new_id) applied in the target

@dataclass
class TransferResult:
    src_after: Program
    dst_after: Program
    summary: TransferSummary

def transfer_line(
    src: Program, dst: Program, line_id: str, *, move: bool
) -> TransferResult: ...
```

Inputs are never mutated; both returned programs are fresh copies.
`line_id` not present in `src` raises `KeyError` (the TUI cannot trigger
this; it guards the API).

### Semantics: exclusive travels, shared stays, move narrows

- **The line always travels** — id, name, abbr, group, all fields.
- **Layers:** a layer travels iff `applies_to == [line_id]` (exclusive).
  Attachments, participants, premiums, periods, policy numbers,
  follows_underlying — all carried verbatim. Shared layers (umbrellas over
  several lines) stay in the source; in move mode their `applies_to` drops
  the departing id. "Shared" means ≥2 line refs, so a narrowed layer can
  never end up with an empty `applies_to`. Shared layers are never copied
  narrowed into the target — that would fabricate a placement that does
  not exist.
- **Retentions and sublimits:** identical rule. Exclusive entries travel;
  shared entries stay and, in move mode, drop the departing id. Any source
  entry whose `applies_to` would become empty in move mode is removed.
  Refusing to fabricate the shared layer in the target necessarily means
  the sent line can arrive with a gap under its excess layers; the confirm
  screen surfaces the target's would-be validation errors and the user
  accepts them knowingly.
- **Copy vs move:** copy leaves `src_after` identical to `src`; move
  removes the line, its exclusive dependents, and narrows shared refs as
  above.
- **Id collisions in the target:** if the line id or a travelling layer id
  already exists in `dst` (checking the same id namespace `unique_id`
  uses: lines ∪ layers), append the `-2`, `-3`, … suffix per the existing
  `unique_id` convention and cascade the rename through the transferred
  bundle's `applies_to` references. Renames are recorded in
  `summary.renames`.
- **Ordering:** the transferred line appends to the end of `dst.lines`;
  transferred layers append to `dst.layers` in their source order.
  Reordering afterwards is the existing `[`/`]` workflow.

## Editor flow

With a line selected, **`>`** ("send line to program…") opens a modal:

1. **Target picker:** program files under `programs/` (including
   `programs/private/`), current file excluded. Listed by relative path.
2. **Mode:** copy (default) or move.
3. **Confirm screen:** renders `TransferSummary` verbatim — what travels,
   what stays behind and why, renames applied in the target.

On confirm:

- The target file is loaded and validated **first**. Parse or validation
  failure → error notify, nothing written anywhere.
- `transfer_line` runs; `dst_after` is saved canonically to the target
  file. This write is **additive-only**: no existing content in the target
  is removed or overwritten, so the manual rollback is deleting the
  grafted line — and the canonical zero-diff round-trip rule keeps all
  untouched target content byte-identical.
- The source change applies through `session.mutate` (one undo step;
  normal `u` undoes it). The source **file** is untouched until the user's
  ordinary save.

Data-safety rationale: this feature writes a second program file on disk,
and `programs/private/` is gitignored (client data — no git safety net).
Additive-only writes + canonical round-trip + explicit confirm showing the
exact graft are the backup/rollback story. No `.bak` files.

Unsaved/untitled source programs can still send lines (the transfer reads
the in-session program, not the file).

Help text (`?` overlay) gains: `>          send line to another program`.

## Out of scope (deliberate)

- Program split/merge, multi-line transfer, whole-tower transfer.
- CLI command (`towerctl mv`) — add later only if the workflow demands it.
- Cross-session clipboard (hidden state on disk).
- Stripping carriers/premiums for template reuse — copy carries
  everything; pruning afterwards is normal editing.
- Concurrent-edit protection for the target file (single-user tool).

## Testing

Pure core (`tests/test_transfer.py`):
- exclusive layer travels; shared layer stays; move narrows shared
  `applies_to`; copy leaves source identical (dumps_program equality)
- retention/sublimit narrowing and empty-`applies_to` removal on move
- id collision re-slug with cascade inside the bundle; renames reported
- inputs not mutated (dumps_program of inputs unchanged after call)
- `KeyError` on unknown line_id

TUI pilot tests (`tests/test_tui.py`):
- `>` flow copies a line into a second program file; target gains the
  line + exclusive stack, byte-content of untouched target JSON regions
  canonical; source session unchanged in copy mode
- move mode: source loses the line in-session, `u` restores it, source
  file on disk untouched before save
- invalid target file (malformed JSON): notify, target file bytes
  unchanged
