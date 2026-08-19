"""The capability ledger — which entity × verb the MCP surface actually serves.

`mcpsurface.py` computes what a FIELD write can reach. Nothing computes what a
VERB can reach, and nothing could: "there is no `layer_add`" is a fact about a
tool that does not exist, and an absent tool leaves no trace to derive from.
Before this module every registration assertion in `test_mcpserver.py` was a
subset check (`{...} <= names`), so a 23rd tool changed no assertion anywhere
in the suite and a *missing* verb changed none either. That is how `layer_add`
went a whole release unnoticed: a program built over MCP got lines and no
layers, and no test had an opinion about it.

So the gaps are TYPED here instead. Every kind × verb is either IMPLEMENTED —
naming the tools that serve it — or DEFERRED, naming the verb that would close
it and saying why it is not built. Every entry carries a reason, including the
implemented ones: a cell that says only "yes" cannot tell a reviewer whether
`participant.read` means a tool exists or that `program_read` happens to return
the rows.

The ledger fails in BOTH directions, which is the whole point:

- a registered tool absent from the ledger fails (a new verb has to be placed);
- a ledger cell naming an unregistered tool fails (a renamed or deleted verb
  cannot rot here);
- a promised-but-unbuilt tool that becomes registered fails, so building
  `program_render` forces its row out of `DEFERRED_TOOLS`.

This is a manifest, not a schedule. It records what is true today; when Phase 2
lands, cells move from DEFERRED to IMPLEMENTED and the tests are what notice.

Imports stay narrow for the same reason `mcpsurface`'s do: `mcpsurface` for the
kind roster (a second list of kinds is the second table again) and the standard
library. NEVER `mcpserver` — the tests reach the registry through the protocol,
which is the only place registration is real, and importing the server here
would make a ledger that agrees with the source rather than with the server.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from . import mcpsurface

# The kinds come from the write surface. A copy here would be a second roster,
# and the two would disagree the first time a model was added.
KINDS: tuple[str, ...] = mcpsurface.KINDS

_EDIT_PY = Path(__file__).with_name("edit.py")

VERBS: tuple[str, ...] = ("create", "read", "update", "delete")

# (kind, verb). A tuple rather than nested dicts so a cell can be named in a
# failure message as one thing: `('layer', 'create')`.
Cell = tuple[str, str]


@dataclass(frozen=True)
class Served:
    """An implemented cell: the tools that serve it, and how.

    `tools` is a tuple because several cells are served by more than one call —
    `line.update` by the specific verb and by the generic setter both — and a
    ledger that named only one of them would let the other disappear silently.
    """

    tools: tuple[str, ...]
    reason: str


# --- the matrix ---------------------------------------------------------------
#
# Read is uniform on purpose: towerkit hands back the WHOLE program document,
# so every kind is read by `program_read` and there is no per-kind reader to
# build. The reasons below say that in each row rather than leaving a reviewer
# to infer it from four identical tool names.

IMPLEMENTED: dict[Cell, Served] = {
    ("program", "create"): Served(
        ("program_create", "program_clone_renewal"),
        "From nothing, or forward a year off last year's file — the two ways a "
        "program actually starts.",
    ),
    ("program", "read"): Served(
        ("program_read", "program_list", "program_view", "program_check"),
        "The whole document (program_read), the roots (program_list), the tower "
        "drawn as text (program_view) and the validator (program_check).",
    ),
    ("program", "update"): Served(
        ("program_edit_field",),
        "Identity, placement, currency and the period and render scalars by "
        "dotted path. The generic setter covers this cell outright, which is why "
        "no program_edit_identity verb was built.",
    ),
    ("line", "create"): Served(
        ("line_add",),
        "A new column. It reports line-empty until a layer covers it, which is "
        "expected and does not block the write.",
    ),
    ("line", "read"): Served(
        ("program_read",),
        "Lines come back with the document. There is no line_read because the "
        "whole file is one read and a partial one would be a second shape of the "
        "same data.",
    ),
    ("line", "update"): Served(
        ("line_edit", "line_move", "program_edit_field"),
        "line_edit owns the rename because the id cascade is part of that verb; "
        "line_move owns order because array order is column order; the generic "
        "setter reaches abbr and group.",
    ),
    ("line", "delete"): Served(
        ("line_remove",),
        "Cascades to every layer, retention and sublimit left covering nothing, "
        "which is why it is a verb and program.lines is denied.",
    ),
    ("layer", "read"): Served(
        ("program_read",),
        "Layers come back with the document, including participants and named "
        "limits — the read is derived from the model, so it cannot go lossy the "
        "way the hand-written one did.",
    ),
    ("layer", "update"): Served(
        ("program_edit_field", "layer_lines", "layer_follows"),
        "The generic setter reaches limit, attach, premium, notes and the three "
        "detail fields under their guards; appliesTo and followsUnderlying stay "
        "verb-owned because each one validates or heals as it writes.",
    ),
    ("layer", "delete"): Served(
        ("layer_remove",),
        "Leaves a line-gap on purpose — program_restack is the separate, "
        "deliberate call that heals it.",
    ),
    ("participant", "read"): Served(
        ("program_read",),
        "Carrier shares come back with each layer. Read is the only participant "
        "verb that exists, which is exactly the asymmetry the rows below record.",
    ),
    ("retention", "create"): Served(
        ("retention_add",),
        "Every appliesTo is checked against the line ids before the write, which "
        "is why program.retentions is denied to the generic setter.",
    ),
    ("retention", "read"): Served(
        ("program_read",),
        "Retentions come back with the document, each carrying the index the "
        "edit and remove verbs address it by — they have no ids.",
    ),
    ("retention", "update"): Served(
        ("retention_edit", "program_edit_field"),
        "retention_edit changes several fields in one guarded call; the generic "
        "setter changes one against an expected value. Both address the row by "
        "index and both take the row guard.",
    ),
    ("retention", "delete"): Served(
        ("retention_remove",),
        "Guarded by the lines the row covered, so an index that has shifted "
        "under the caller refuses instead of removing a neighbour.",
    ),
    ("sublimit", "create"): Served(
        ("sublimit_add",),
        "Every appliesTo is checked against the line ids before the write, which "
        "is why program.sublimits is denied to the generic setter.",
    ),
    ("sublimit", "read"): Served(
        ("program_read",),
        "Sublimits come back with the document, each carrying the index the edit "
        "and remove verbs address it by — they have no ids.",
    ),
    ("sublimit", "update"): Served(
        ("sublimit_edit", "program_edit_field"),
        "sublimit_edit changes several fields in one guarded call; the generic "
        "setter changes one against an expected value. Both take the name the "
        "read reported as the row guard.",
    ),
    ("sublimit", "delete"): Served(
        ("sublimit_remove",),
        "Guarded by the name the row held, so an index that has shifted under "
        "the caller refuses instead of removing a neighbour.",
    ),
    ("named_limit", "read"): Served(
        ("program_read",),
        "Named limits come back with their layer, in file order — which is "
        "display order, and is why a wholesale set is denied.",
    ),
}


# --- what is not built --------------------------------------------------------
#
# Each row names the verb that would close it. That matters more than it looks:
# a refusal message is allowed to cite a tool, and citing one that does not
# exist refuses the retry too. These names are the vocabulary Phase 2 has to
# use, agreed here rather than invented at the keyboard.

DEFERRED: dict[Cell, str] = {
    ("program", "delete"): (
        "A program_delete would close it and is deliberately not built. Deleting "
        "a broker's file is the one operation the snapshot undo cannot make cheap "
        "— the pre-image is kept beside the file it would remove — and no "
        "workflow has asked for it. Removal happens on disk, where it is visible."
    ),
    ("layer", "create"): (
        "layer_add, Phase 2. edit.add_layer exists and the TUI calls it; no tool "
        "registers it, so a program created over MCP gets lines and no layers and "
        "cannot get one short of cloning a renewal. Phase 2 makes it a composite "
        "— participants, premium, policy number, period and statutory inline — "
        "plus an atomic bulk form, and building the thin version first would mean "
        "writing it twice."
    ),
    ("participant", "create"): (
        "participant_add, Phase 2. Nothing writes a carrier share, so every "
        "MCP-built layer is permanently pending and renders as 'To be placed'. It "
        "waits on the over-signing veto: shares have to add up BEFORE the write, "
        "and that guard belongs in edit.py where the TUI inherits it — which is "
        "the work, not the tool."
    ),
    ("participant", "update"): (
        "participant_edit, Phase 2. The generic setter cannot stand in: a "
        "participant hangs off a LAYER as well as an index, which set_field's "
        "single `target` cannot express, and layer.participants is denied "
        "wholesale because of the same over-signing veto."
    ),
    ("participant", "delete"): (
        "participant_remove, Phase 2. Same addressing gap as the update cell — "
        "and removing a share moves a layer back toward pending, so it goes "
        "through the veto in edit.py rather than a list pop in a surface."
    ),
    ("named_limit", "create"): (
        "named_limit_add, Phase 2. edit.add_named_limit exists and the TUI's "
        "grid calls it; no tool registers it. Order is the file's order and is "
        "display order, so an add appends and is never sorted — the verb exists "
        "to keep that true."
    ),
    ("named_limit", "update"): (
        "named_limit_edit, Phase 2. The generic setter refuses the kind by name: "
        "a named limit is addressed by a layer id AND an index, which set_field's "
        "single `target` cannot carry, and layer.namedLimits is denied wholesale "
        "because a set would silently reorder what a broker arranged."
    ),
    ("named_limit", "delete"): (
        "named_limit_remove, Phase 2. Same addressing gap as the update cell."
    ),
}


# --- tools that are not a cell ------------------------------------------------

NON_ENTITY_TOOLS: dict[str, str] = {
    "describe": (
        "Capability introspection, not CRUD. It publishes the write surface — "
        "kinds, fields, types, denied fields with reasons and the value rules — "
        "and it is the ONLY place that list exists."
    ),
    "program_restack": (
        "A bulk heal across every layer, not an update to one. It reseats each "
        "attachment on what its lines already carry, which is a single "
        "whole-tower operation with no per-entity form."
    ),
    "program_revert_write": (
        "File history, not an entity verb. It restores one write's pre-image and "
        "addresses a write ref, not a kind."
    ),
}

# Named, unbuilt, and NOT registered — the assertion is that they are absent.
# Building one without moving its row here fails, which is the only mechanism
# that makes Phase 3 land in the ledger rather than beside it.
DEFERRED_TOOLS: dict[str, str] = {
    "line_transfer": (
        "Phase 2. transfer.transfer_line exists and the TUI calls it. It is not "
        "a cell in the matrix above because it touches TWO programs, and the "
        "write safety here is per-file: expect_sha, the session's seen map and "
        "the snapshot pre-image all address one path. A tool that half-lands "
        "across two files is worse than no tool, which is the whole reason it "
        "waits."
    ),
    "program_render": (
        "Phase 3. Waits on --exports DIR: a render tool with nowhere declared to "
        "write is a tool that writes somewhere surprising."
    ),
    "program_soi": (
        "Phase 3. The Schedule of Insurance and the schematic sheet, same "
        "--exports dependency. The layer policyNumber and period fields it reads "
        "are already writable, which was the point of doing the spine first."
    ),
    "program_compare": (
        "Phase 3. compare.py exists and the CLI calls it; the tool waits on "
        "--exports for the artifact and on nothing else."
    ),
    "program_import": (
        "Phase 3. ingest.import_schedule for a spreadsheet, parse_tower for "
        "pasted text. It writes a NEW program and refuses an existing name, like "
        "program_create — the refusal is the work, not the parse."
    ),
    "program_template": (
        "Phase 3. Writes the ingest template workbook a broker fills in; same "
        "--exports dependency as the rest."
    ),
    "program_search": (
        "Phase 3. program_list names files; searching inside them across the "
        "roots is a different call and no workflow has needed it yet."
    ),
    "program_history": (
        "Phase 3. Lists write refs newest first with the summary, the post-write "
        "sha and whether the ref is still reachable. Multi-step revert lands with "
        "it — program_revert_write undoes exactly one write today."
    ),
}


# --- edit.py's mutations, the verb roster -------------------------------------
#
# The cells above say what the MCP surface offers. This says what towerkit can
# DO — edit.py is the one place structural mutation lives (test_conventions.py
# enforces that), so its public functions are the complete verb roster and a
# mutation no tool reaches is a capability the connector does not have.
#
# The roster is ENUMERATED from edit.py's source rather than listed here (see
# `public_functions`); these three tables only say what each name means. A
# function added to edit.py therefore fails until it is placed, which is the
# check that `layer_add` never had.
#
# `transfer.transfer_line` is deliberately NOT on this roster: it is the one
# mutation that spans two programs, which is why it lives in its own module and
# why it is tracked as a tool (DEFERRED_TOOLS) rather than as a cell.

MUTATIONS: dict[str, Served] = {
    "add_line": Served(("line_add",), "The add verb, one for one."),
    "rename_line": Served(
        ("line_edit",), "line_edit's line_name argument; the id cascade rides with it."
    ),
    "set_line_group": Served(
        ("line_edit", "program_edit_field"),
        "line_edit's group argument, and line.group through the generic setter.",
    ),
    "move_line": Served(("line_move",), "Column order, one step at a time."),
    "remove_line": Served(("line_remove",), "The remove verb, with its cascade."),
    "adopt": Served(
        ("program_clone_renewal",),
        "Copies a program's structure onto a fresh year — the renewal clone is "
        "its only caller and its only reason to be public.",
    ),
    "heal_follows": Served(
        ("program_restack", "layer_follows", "program_edit_field"),
        "Runs on EVERY write: mcpserver._write calls it before validating, so a "
        "follows-underlying attachment is re-derived no matter which tool moved "
        "the layer beneath it.",
    ),
    "remove_layer": Served(("layer_remove",), "The remove verb, one for one."),
    "set_applies_to": Served(
        ("layer_lines",), "layer_lines, which validates every line id before writing."
    ),
    "set_follows_underlying": Served(
        ("layer_follows",), "layer_follows, which heals the attachment as it sets the flag."
    ),
    "restack": Served(("program_restack",), "The whole-tower reseat."),
    "add_retention": Served(("retention_add",), "The add verb, one for one."),
    "edit_retention": Served(("retention_edit",), "The multi-field edit verb."),
    "remove_retention": Served(("retention_remove",), "The remove verb, one for one."),
    "add_sublimit": Served(("sublimit_add",), "The add verb, one for one."),
    "edit_sublimit": Served(("sublimit_edit",), "The multi-field edit verb."),
    "remove_sublimit": Served(("sublimit_remove",), "The remove verb, one for one."),
    "set_states": Served(
        ("program_edit_field",),
        "layer.states through the generic setter. set_states is a thin wrapper "
        "over set_field for the TUI's comma-separated entry, not a second door — "
        "the guard that refuses states on a dollar-limited layer sits under both.",
    ),
    "set_premium_detail": Served(
        ("program_edit_field",),
        "layer.premiumDetail through the generic setter, which reaches all three "
        "detail fields.",
    ),
    "set_field": Served(
        ("program_edit_field",),
        "THE choke point. program_edit_field is a thin compare-and-set shell "
        "around it, so every guard in edit.py applies to every generic write.",
    ),
}

DEFERRED_MUTATIONS: dict[str, str] = {
    "add_layer": (
        "Reached by the TUI and by nothing else. Closed by layer_add — see the "
        "('layer', 'create') row."
    ),
    "add_named_limit": (
        "Reached by the TUI's named-limits grid and by nothing else. Closed by "
        "named_limit_add — see the ('named_limit', 'create') row."
    ),
    "edit_named_limit": (
        "Reached by the TUI's named-limits grid and by nothing else. Closed by "
        "named_limit_edit — see the ('named_limit', 'update') row."
    ),
    "remove_named_limit": (
        "Reached by the TUI's named-limits grid and by nothing else. Closed by "
        "named_limit_remove — see the ('named_limit', 'delete') row."
    ),
    "set_statutory": (
        "Reached by the TUI's statutory checkbox and by nothing else. Closed by "
        "layer_statutory, Phase 2. It cannot be reached through the generic "
        "setter by design: layer.statutory carries `statutory implies limit == 0` "
        "and forces limit, attach and followsUnderlying to zero, so routing it "
        "through set_field would have _guard_limit refuse the write it is "
        "performing. Until the verb exists, a statutory layer is built in the "
        "towerkit editor — and the refusal on layer.limit says so."
    ),
}

# Public, takes a Program, and answers a question instead of changing one. They
# are named rather than filtered by a heuristic: "does this mutate?" is not
# decidable from a signature, and a heuristic that guessed wrong would silently
# excuse a real mutation from the roster.
NOT_A_MUTATION: dict[str, str] = {
    "slugify": "Pure text. Turns a name into an id candidate.",
    "unique_id": "Reads the program to find a free id; writes nothing.",
    "ordinal": "Pure text. 1 -> 'first', for the excess stack's auto-names.",
    "suggested_attach": "Reads the stack to propose an attachment; writes nothing.",
    "parse_states": "Pure text. 'NY, NJ' -> ['NY', 'NJ'], the entry syntax.",
}


# --- derived views ------------------------------------------------------------


def cells() -> tuple[Cell, ...]:
    """Every kind × verb the ledger has to have an opinion about."""
    return tuple((kind, verb) for kind in KINDS for verb in VERBS)


def ledger_tools() -> set[str]:
    """Every tool name the ledger claims is registered.

    The cells, the non-entity rows and the mutation roster all name tools, and
    all three have to be checked — a mutation row pointing at a renamed tool
    rots exactly as quietly as a cell does.
    """
    names: set[str] = set(NON_ENTITY_TOOLS)
    for served in (*IMPLEMENTED.values(), *MUTATIONS.values()):
        names.update(served.tools)
    return names


def public_functions(source: str | None = None) -> tuple[str, ...]:
    """edit.py's public top-level functions, read out of its SOURCE.

    Parsed rather than introspected off the imported module: `dir(edit)` also
    returns everything edit.py imported (`slugify` is ours, `parse_money` is
    not), and filtering that back out by `__module__` would quietly drop a
    function moved behind a re-export. The source says what edit.py defines.
    """
    text = source if source is not None else _EDIT_PY.read_text("utf-8")
    tree = ast.parse(text)
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    )
