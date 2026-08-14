# MCP: cross-server OCC deadlock — bookkit's `program_layer_add` can never succeed after any towerkit write

**Reported** 2026-08-14, from a work-machine session building a real program
(`aalo-2025.json`, PLC-0004) through the towerkit + bookkit MCP servers.

**Severity: high.** The failure is unrecoverable from inside the toolset, and
its escape hatch is the worst possible one — the agent gave up and started
hand-writing the program JSON directly, which bypasses canonical
serialisation, validation, the write-ref snapshot trail, and the OCC guard
itself.

## Symptom

Every call to bookkit's `program_layer_add` fails with a stale-projection
error ("changed on disk since last projection"), no matter how recently the
file was read. The agent burned an entire session trying ~8 distinct
resynchronisation strategies, all of which failed:

- calling `mcp_BookKit_program_layers` to refresh the projection
- calling it again immediately before the write, with no intervening calls
- calling `mcp_TowerKit_program_read` to refresh the sha
- calling `mcp_BookKit_program_summary` to force a full record read
- calling `mcp_BookKit_program_bind`
- running `restack` to force a stable commit point
- verifying by hand that the file sha was stable (`b96c18b2…`) across reads
- checking the file for a sha field (there isn't one; it is a content hash)

## Root cause

Two server processes maintain two independent OCC token maps over one file.

`mcpserver.py:137` — the guard is per-process, in-memory:

```python
def note(self, path):
    """Record the sha this session has now seen for a file, and return it."""
    sha = file_sha256(path)
    self.seen[str(path)] = sha
    return sha
```

- towerkit's MCP server calls `note()` on `program_read` (`:306`) and after
  every successful write (`:720`), updating **towerkit's** `seen` map.
- bookkit's `program_layer_add` "goes through towerkit's full cycle" — but it
  runs towerkit as a library **inside bookkit's process**, so it consults a
  *different* `Programs.seen` dict.
- Therefore every towerkit MCP write advances the file sha and updates only
  towerkit's map. Bookkit's map still holds whatever sha it captured on its
  last read, and **nothing the agent can call updates it**:
  `program_layers` reads bookkit's own DB projection, not the file, so it
  never reaches `note()`.

The session is then permanently wedged: bookkit's token can only be refreshed
by a bookkit call that touches the file through towerkit, and no such
read-only call exists.

`"resync": run_hook(path)` (`:260, :693, :749, :775`) reported **"not
configured"** throughout, so the one designed escape was inert.

## Contributing cause: layers are the only entity towerkit's MCP cannot create

From `_register_write_tools` (`mcpserver.py:828`):

| Entity | add | edit | remove | other |
|---|---|---|---|---|
| line | ✅ `line_add` | ✅ `line_edit` | ✅ `line_remove` | `line_move` |
| retention | ✅ `retention_add` | ✅ `retention_edit` | ✅ `retention_remove` | |
| sublimit | ✅ `sublimit_add` | ✅ `sublimit_edit` | ✅ `sublimit_remove` | |
| **layer** | ❌ **none** | ❌ **none** | ✅ `layer_remove` | `layer_lines`, `layer_follows` |

Layers are the only entity with no `add` and no `edit` on towerkit's side —
presumably because bookkit owns premium, policy number and market shares. The
consequence is that **no program can be built end to end on one server.** Any
realistic workflow interleaves towerkit writes (lines, retentions) with
bookkit writes (layers), which is exactly the interleaving that invalidates
bookkit's token. The agent found this dead end explicitly:

> "Looking at TowerKit tools available — there's layer_lines, layer_remove,
> layer_follows, but no layer_add in TowerKit directly. The
> mcp_BookKit_program_layer_add is the only way to add layers."

## Proposed repair

1. **Make the OCC token caller-supplied, not server-remembered.** The write
   tools should take an explicit `expect_sha` that the caller passes from its
   last read, and reject on mismatch. Stateless tokens are correct across
   process boundaries by construction; a per-process `seen` map cannot be.
   This is the actual fix — 2 and 3 are mitigations.
2. **Add `layer_add` and `layer_edit` to towerkit's MCP**, with premium and
   policy number optional. This removes the common trigger by letting a
   program be built without crossing servers. It does not fix the OCC bug.
3. **Give bookkit a real resync path** — either wire the `resync` hook (it
   reported "not configured" for the whole session) or have
   `program_layers` call through to towerkit's `note()` so a read genuinely
   refreshes the token the next write is checked against.
4. **Fail loudly and actionably.** The current error tells the caller to
   "re-sync and retry" while providing no call that re-syncs. It should name
   the tool to call, or say plainly that the token cannot be refreshed from
   this server.

## Regression test to add alongside the fix

Interleave the two surfaces in one session and assert the second write
succeeds:

1. read the program through bookkit (captures its token)
2. perform any towerkit MCP write (`line_edit` is enough)
3. call bookkit's `program_layer_add`

Today step 3 always fails. It must pass — or, if the write genuinely
conflicts, fail with an error naming the refresh call that fixes it.

## Verbatim transcript

Pasted from the work machine, unedited.

> Let me start — first fix the typo and add the missing Umbrella line, then remove the placeholder layer:
>
> Typo fixed and Umbrella line added. Now remove the stale placeholder layer, then I'll add all the real layers.
> Good — slate is clean. Now I'll add all 11 layers via BookKit (which handles policy numbers, premiums, and periods). I'll batch these in groups since they're independent of each other. Starting with the first 4 (GL, Umbrella, WC, Cyber):
> Rounding the premium to whole dollars.
> I need to re-read the program first to get a fresh sha before writing.
> Good — sha is refreshed, all lines look correct. Now adding all layers sequentially (each must complete before next due to sha dependency):
> The file sha keeps changing due to concurrent writes from my earlier edits. Let me re-read and immediately write in one flow:
> SHA is stable now (same as before). The BookKit program_layer_add appears to use its own internal state tracking separately from TowerKit's sha. Let me try the add again — the file sha hasn't changed between the two reads:
> The BookKit tool has its own cached projection of the file from before my TowerKit edits. I need to re-sync BookKit's view. Let me call mcp_BookKit_program_layers again to force a re-sync of BookKit's projection:
> BookKit is now synced. Now adding all layers one at a time, each building on the previous write:
> The issue persists. The problem may be that TowerKit and BookKit maintain separate projections/locks. Since I made several TowerKit writes (line_edit, line_add, layer_remove) before BookKit re-synced, BookKit's OCC token may be stale in a way that requires a different approach. Let me try using the TowerKit restack call to force a stable commit point, then re-sync:
> The restack write itself advanced the sha, so BookKit's projection from my last program_layers read is already stale. The pattern is: every TowerKit write advances the sha. I need to call program_layers (to sync BookKit) and then immediately add a layer without any TowerKit calls in between. Let me do exactly that:
> The error persists even immediately after program_layers. This suggests BookKit's internal projection token is not being refreshed by program_layers alone — perhaps program_layers reads from BookKit's own DB, not directly from the TowerKit file. Let me look at this differently and try using TowerKit's own layer creation approach by checking if there are write tools I haven't yet loaded:
> I see the issue now — mcp_BookKit_program_layer_add says it "goes through towerkit's full cycle" with an OCC check. The resync field in TowerKit responses says "not configured," meaning BookKit auto-resync isn't set up. The program_layers call only reads BookKit's own DB view, not the file.
> The correct workflow is: use mcp_BookKit_program_layer_add which goes through TowerKit internally. But the OCC guard requires that TowerKit's last-seen sha matches the current file. Since I only need program_layers to update BookKit's internal sha token, and that isn't working, let me try the mcp_BookKit_program_bind tool which might force a full resync:
> The bind tool is for markets/shares, not for the OCC issue. Let me look at the actual TowerKit file directly to understand what sha it currently holds, and then try editing the file directly:
> The file itself has no sha field — TowerKit computes the sha externally (it's a content hash of the file). The "last projection" is tracked in BookKit's server-side memory, not in the file. The issue is that BookKit's MCP server cached the sha from the first program_layers call, and the file has changed since then due to multiple TowerKit writes.
> The key insight: I need to have BookKit re-read the file. The program_layers call should do this — but it seems it's reading from BookKit's own database rather than the file. Let me check if there's a resync mechanism or if I should proceed entirely through TowerKit's layer tools (which don't have the OCC issue with BookKit):
> Looking at TowerKit tools available — there's layer_lines, layer_remove, layer_follows, but no layer_add in TowerKit directly. The mcp_BookKit_program_layer_add is the only way to add layers.
> Let me try a different approach: call mcp_BookKit_program_summary on PLC-0004 to force a full record read, which may refresh BookKit's projection:
> The BookKit MCP server is holding a stale projection from an earlier session. The "re-sync and retry" message is the key — it's telling me BookKit needs to re-sync its file projection. Since program_layers isn't triggering a re-sync, let me check if there's a specific resync mechanism or if I should touch the file to force it.
> Let me look at the actual BookKit/TowerKit integration — the issue is BookKit's internal sha snapshot was taken during an earlier program_layers call (when the file had a different content), and it isn't updating. The solution may be to use mcp_TowerKit_program_read which also updates the server-side sha, and see if that syncs BookKit:
> Actually, re-reading the error carefully: "changed on disk since last projection" — BookKit's "projection" is set when you call program_layers. Since program_layers shows 0 layers and correct lines (which matches the current file), the sha BookKit computed during program_layers should match. But wait — the restack I did after the last program_layers call changed the file again.
> The fix: call program_layers ONE MORE TIME (to pick up the post-restack sha), then immediately add a layer before any other writes:
> Something else is going on. program_layers is returning the correct state but the sha it's computing doesn't match the file on disk. Let me check what's in the file right now vs what BookKit thinks the sha should be:
> The file sha is b96c18b2... — the same sha that mcp_TowerKit_program_read returned earlier. The file is stable and correct. Yet BookKit keeps saying "changed on disk since last projection."
> This is a BookKit MCP server state issue — its internal projection cache isn't matching the file. The program_layers call is reading from BookKit's own database view, not from the file directly, so it isn't resetting the sha guard.
> Let me try a fresh mcp_TowerKit_program_read — since TowerKit tools are what successfully refreshed earlier — and then immediately call BookKit:
> Actually wait — looking more carefully at the TowerKit program_read response earlier, it showed sha b96c18b2... and the restack write_ref TKW-20260814T195752-fc6a was the LAST TowerKit write. The file sha after restack is still b96c18b2... (confirmed). So the current file sha IS b96c18b2....
> The problem must be that BookKit's projection sha was set from a state where the file was different, and program_layers is NOT reading from the file to update that projection — it's reading from BookKit's own internal record.
> Let me check if there's a way to directly edit the JSON file with the layers and bypass this issue:
> There's an import command that builds a program from a schedule! And an edit TUI. But more relevantly — I can construct the complete JSON file directly since the schema is simple. The cleanest solution is to write the complete aalo-2025.json with all layers populated, which bypasses the BookKit OCC issue entirely. Let me build the full JSON:
