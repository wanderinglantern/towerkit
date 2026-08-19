"""The capability ledger, checked against the running server in both directions.

Every registration assertion in `test_mcpserver.py` is a subset check
(`{...} <= names`), so before this file a 23rd tool changed no assertion
anywhere in the suite, and a MISSING verb changed none either. These tests are
the two halves of that gap:

- a registered tool the ledger does not place fails, so a new tool cannot ship
  without someone saying which cell it serves;
- a ledger row naming a tool the server does not register fails, so a rename or
  a deletion cannot rot here.

The tool roster comes from a real protocol round-trip (`client.list_tools()`),
never from grepping `mcpserver.py`. Registration is a thing the SDK does at
`build_server` time; a source grep would agree with the file and could still
disagree with the server — which is the precise failure this file exists to
make impossible.

`pyproject` sets asyncio_mode = "auto", so bare `async def test_` functions are
collected without a marker.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from towerkit import mcpparity
from towerkit.mcpserver import build_server

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


@pytest.fixture()
def roots(tmp_path):
    programs = tmp_path / "programs"
    programs.mkdir()
    shutil.copy(SAMPLE, programs / "atomic-2026.json")
    return [programs]


async def _registered(roots) -> set[str]:
    """Every tool name the built server actually advertises."""
    from mcp.client import Client

    async with Client(build_server(roots)) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


# --- the two directions -------------------------------------------------------


async def test_every_registered_tool_appears_in_the_ledger(roots) -> None:
    """A tool nobody placed. This is the half that was missing entirely: with
    only subset checks in the suite, adding a tool asserted nothing anywhere,
    so a verb could ship with no cell, no reason and no reviewer.

    A new tool belongs in IMPLEMENTED (it serves a kind × verb) or in
    NON_ENTITY_TOOLS (it does not) — and either way it arrives with a reason.
    """
    unplaced = sorted(await _registered(roots) - mcpparity.ledger_tools())
    assert not unplaced, (
        f"registered but absent from the ledger: {unplaced} — add each to "
        "IMPLEMENTED (with its kind and verb) or NON_ENTITY_TOOLS, with a reason"
    )


async def test_every_tool_the_ledger_names_is_registered(roots) -> None:
    """The inverse half: a cell citing a tool that is not there.

    It matters beyond tidiness because refusals cite tools by name. A ledger
    that still names `restack` after the rename to `program_restack` is a
    ledger that tells a caller to make a call which does not exist.
    """
    names = await _registered(roots)
    missing = sorted(mcpparity.ledger_tools() - names)
    assert not missing, (
        f"the ledger names tools the server does not register: {missing} — "
        "rename the row or move it to DEFERRED"
    )


async def test_a_deferred_tool_that_got_built_forces_its_row_to_move(roots) -> None:
    """DEFERRED_TOOLS is a set of NEGATIVE claims — 'program_render does not
    exist yet' — and a negative assertion is exactly the kind that passes for
    the wrong reason once the world changes underneath it.

    So it is checked in the live direction: build `program_render` and this
    fails until its row moves out. Without it, Phase 3 could land whole and
    leave the ledger still describing it as unbuilt.
    """
    names = await _registered(roots)
    built = sorted(set(mcpparity.DEFERRED_TOOLS) & names)
    assert not built, (
        f"{built} are registered but still listed as deferred — move each row "
        "into IMPLEMENTED or NON_ENTITY_TOOLS with the cell it serves"
    )


# --- the matrix itself --------------------------------------------------------


def test_every_cell_is_implemented_or_explicitly_deferred() -> None:
    """Seven kinds times four verbs, and no cell may be silent.

    Silence is what the old surface had: `layer_add` was not refused, not
    documented and not deferred — it simply was not there, and nothing in the
    codebase held an opinion about that. A gap has to be countable to be
    closed.
    """
    unanswered = [
        cell
        for cell in mcpparity.cells()
        if cell not in mcpparity.IMPLEMENTED and cell not in mcpparity.DEFERRED
    ]
    assert not unanswered, (
        f"no ledger row for {unanswered} — every kind x verb is IMPLEMENTED "
        "(naming its tools) or DEFERRED (naming the verb that would close it)"
    )


def test_the_ledger_has_no_stale_cells() -> None:
    """The other direction on the matrix: a row for something that is not a
    cell at all.

    A kind renamed in `mcpsurface.KIND_MODELS` leaves its old rows behind
    reading as authoritative, and a typo'd verb ('remove' for 'delete') creates
    a row that satisfies nothing and is checked by nothing. A cell in BOTH
    tables is the same failure by another route: a half-finished move where the
    deferred note still argues the verb is unbuilt.
    """
    known = set(mcpparity.cells())
    stray = sorted((set(mcpparity.IMPLEMENTED) | set(mcpparity.DEFERRED)) - known)
    assert not stray, (
        f"{stray} name no real kind x verb — kinds come from mcpsurface.KINDS "
        f"{list(mcpparity.KINDS)}, verbs are {list(mcpparity.VERBS)}"
    )
    both = sorted(set(mcpparity.IMPLEMENTED) & set(mcpparity.DEFERRED))
    assert not both, f"{both} are implemented AND deferred — one of the two rows is stale"


def test_every_entry_carries_a_reason() -> None:
    """A cell that says only 'yes' or only 'no' is a checkbox, and a checkbox
    is what the hand-written field table was.

    The reason is the whole value of the ledger: it is what tells a reviewer
    that `participant.read` is served by the whole-document read rather than by
    a participant tool, and what makes a deferred cell a decision instead of an
    oversight. A prose rule ("says a sentence") cannot be tested; a non-empty
    one that is not just the tool name back again can.
    """
    bare: list[str] = []
    for label, served in (
        *((f"IMPLEMENTED{cell}", s) for cell, s in mcpparity.IMPLEMENTED.items()),
        *((f"MUTATIONS[{name!r}]", s) for name, s in mcpparity.MUTATIONS.items()),
    ):
        if len(served.reason.strip()) < 20 or served.reason.strip() in served.tools:
            bare.append(label)
        if not served.tools:
            bare.append(f"{label} (no tool named)")
    for label, reason in (
        *((f"DEFERRED{cell}", r) for cell, r in mcpparity.DEFERRED.items()),
        *((f"NON_ENTITY_TOOLS[{n!r}]", r) for n, r in mcpparity.NON_ENTITY_TOOLS.items()),
        *((f"DEFERRED_TOOLS[{n!r}]", r) for n, r in mcpparity.DEFERRED_TOOLS.items()),
        *((f"DEFERRED_MUTATIONS[{n!r}]", r) for n, r in mcpparity.DEFERRED_MUTATIONS.items()),
        *((f"NOT_A_MUTATION[{n!r}]", r) for n, r in mcpparity.NOT_A_MUTATION.items()),
    ):
        if len(reason.strip()) < 20:
            bare.append(label)
    assert not bare, f"entries with no usable reason: {bare}"


# --- the verb roster ----------------------------------------------------------


def test_every_edit_py_mutation_is_reachable_or_deferred() -> None:
    """`edit.py` is the only place structural mutation lives — test_conventions
    enforces that for the TUI — so its public functions ARE the verb roster,
    and a mutation no tool reaches is a capability the connector does not have.

    Seven were unreachable before this pass. `program_edit_field` closed
    `set_field`, `set_states` and `set_premium_detail`; the five still deferred
    are Phase 2 verbs, each named in DEFERRED_MUTATIONS.

    The roster is read out of edit.py's SOURCE, so a function added there fails
    here until someone places it. That is the check `add_layer` never had: it
    was written, called by the TUI, and reached by no tool for a whole release
    with nothing in the suite objecting.
    """
    roster = set(mcpparity.public_functions())
    placed = (
        set(mcpparity.MUTATIONS)
        | set(mcpparity.DEFERRED_MUTATIONS)
        | set(mcpparity.NOT_A_MUTATION)
    )
    unplaced = sorted(roster - placed)
    assert not unplaced, (
        f"edit.py defines {unplaced} and the ledger says nothing about them — "
        "add each to MUTATIONS (naming the tool that reaches it), to "
        "DEFERRED_MUTATIONS (naming the verb that would), or to NOT_A_MUTATION"
    )
    stale = sorted(placed - roster)
    assert not stale, f"the ledger names {stale}, which edit.py no longer defines"
    overlap = sorted(
        name
        for name in placed
        if sum(
            name in table
            for table in (
                mcpparity.MUTATIONS,
                mcpparity.DEFERRED_MUTATIONS,
                mcpparity.NOT_A_MUTATION,
            )
        )
        > 1
    )
    assert not overlap, f"{overlap} appear in more than one mutation table"


def test_the_deferred_mutations_are_the_phase_2_verbs() -> None:
    """The count is the point: four, and every one of them named.

    An assertion on the SET rather than a floor, because both directions are
    interesting. One appearing means a mutation quietly lost its tool; one
    disappearing without its cell moving means the ledger's two halves —
    verb-level and cell-level — have come apart.

    `edit_named_limit` was on this list until 2026-08-19 and did not belong:
    `mcpsurface._SETTERS` routes `named_limit.*` to it, so `program_edit_field`
    had been reaching it since the day the generic setter shipped.
    """
    assert set(mcpparity.DEFERRED_MUTATIONS) == {
        "add_layer",
        "add_named_limit",
        "remove_named_limit",
        "set_statutory",
    }
    # Each deferred mutation belongs to a deferred CELL. `set_statutory` is the
    # exception with a stated reason: statutory is a layer FIELD denied to the
    # generic setter, so `('layer', 'update')` is implemented and the flag still
    # is not reachable — which is exactly why the verb roster is checked
    # separately from the matrix instead of being derived from it.
    assert ("layer", "create") in mcpparity.DEFERRED
    assert all(
        ("named_limit", verb) in mcpparity.DEFERRED for verb in ("create", "delete")
    )
    assert "layer_statutory" in mcpparity.DEFERRED_MUTATIONS["set_statutory"]


def test_public_functions_reads_the_source_not_the_module() -> None:
    """`dir(edit)` also returns everything edit.py IMPORTS — `parse_money` is
    not a towerkit edit verb — and filtering those out by `__module__` would
    quietly drop a function moved behind a re-export.

    Fed a source string, the enumeration returns exactly that file's public
    top-level defs: a private one is skipped, an import is not a def, and a
    method on a class is not top-level.
    """
    source = (
        "from .money import parse_money\n"
        "def public(program): ...\n"
        "def _private(program): ...\n"
        "class Thing:\n"
        "    def method(self): ...\n"
        "async def also_public(program): ...\n"
    )
    assert mcpparity.public_functions(source) == ("public", "also_public")


# --- the reasons, not just the names -------------------------------------------
#
# Both tests above compare TOOL NAMES. A cell can therefore carry a reason that
# is factually wrong and nothing in the suite notices, which is exactly what
# happened: ('named_limit', 'update') sat in DEFERRED arguing "the generic
# setter refuses the kind by name: a named limit is addressed by a layer id AND
# an index, which set_field's single target cannot carry". It carried both, and
# a named limit's amount had been settable over MCP since the day the generic
# setter shipped. The ledger stated a falsehood about its own server for a
# whole phase.
#
# So the claim is probed against the running code, in the one direction the
# name checks cannot see: for every kind, does `program_edit_field` actually
# reach it?

# One real write per kind, in the vocabulary the `update` cells argue about.
# Every value differs from what the sample holds, so a write that quietly did
# nothing cannot be mistaken for one that landed.
SETTER_PROBES: dict[str, dict[str, object]] = {
    "program": {"field": "currency", "value": "GBP", "expecting": "USD"},
    "line": {"field": "abbr", "target": "gl", "value": "GLI", "expecting": "GL"},
    "layer": {
        "field": "notes",
        "target": "umbrella",
        "value": "bound at expiring terms",
        "expecting": None,
    },
    "participant": {
        "field": "share_bps",
        "target": "primary-gl",
        "index": 0,
        "expecting_row": "Zurich",
        "value": 9_000,
        "expecting": 10_000,
    },
    "retention": {
        "field": "amount",
        "index": 0,
        "expecting_row": ["gl"],
        "value": 300_000,
        "expecting": 250_000,
    },
    "sublimit": {
        "field": "amount",
        "index": 0,
        "expecting_row": "Flood",
        "value": 30_000_000,
        "expecting": 25_000_000,
    },
    "named_limit": {
        "field": "amount",
        "target": "primary-gl",
        "index": 0,
        "expecting_row": "Sexual Abuse",
        "value": 2_000_000,
        "expecting": 1_000_000,
    },
}


@pytest.fixture()
def roots_with_a_named_limit(roots):
    """The sample carries no named limits, so the one kind whose cell was
    wrong would be the one kind that could not be probed. Added through
    `edit.add_named_limit` rather than by editing JSON, so the row is built the
    way the TUI builds it."""
    from towerkit.edit import add_named_limit
    from towerkit.model import dumps_program, load_program

    path = roots[0] / "atomic-2026.json"
    program = load_program(path)
    add_named_limit(program, "primary-gl", "Sexual Abuse", 1_000_000)
    path.write_text(dumps_program(program), "utf-8")
    return roots


def _cell_claims_the_generic_setter(kind: str) -> bool:
    served = mcpparity.IMPLEMENTED.get((kind, "update"))
    return served is not None and "program_edit_field" in served.tools


def test_every_kind_is_probed() -> None:
    """A kind added to `mcpsurface.KIND_MODELS` with no probe would be a kind
    whose ledger reason nobody checks — the gap this section closes, reopened
    silently."""
    assert set(SETTER_PROBES) == set(mcpparity.KINDS)


def test_the_generic_setter_reaches_exactly_the_kinds_the_ledger_claims(
    roots_with_a_named_limit,
) -> None:
    """The cell reasons, checked against the server rather than against prose.

    Set equality, both directions and both of them live: a cell that says
    DEFERRED about a kind the setter reaches fails here (the named_limit bug),
    and so does a cell naming `program_edit_field` for a kind that refuses.

    Mutation drill (2026-08-19), both ways. Putting the false
    `('named_limit', 'update')` DEFERRED row back failed with
    `program_edit_field reached [... 'named_limit' ...] but the ledger's update
    cells claim [...]` and `Refusals seen: {}` — every probe wrote, so the
    positive half is not passing vacuously either. Making
    `mcpsurface.apply` raise on the `edit_named_limit` branch failed the
    same assertion the other way round, with the refusal quoted. Restored.
    """
    from towerkit.mcpserver import Programs, _program_edit_field, _program_read

    programs = Programs(roots_with_a_named_limit)
    _program_read(programs, "atomic-2026")  # arms the session's baseline

    reached: set[str] = set()
    refused: dict[str, str] = {}
    for kind, probe in SETTER_PROBES.items():
        try:
            _program_edit_field(programs, "atomic-2026", kind=kind, **probe)
        except ValueError as exc:
            refused[kind] = str(exc)
        else:
            reached.add(kind)

    claimed = {kind for kind in mcpparity.KINDS if _cell_claims_the_generic_setter(kind)}
    assert reached == claimed, (
        f"program_edit_field reached {sorted(reached)} but the ledger's update "
        f"cells claim {sorted(claimed)} — a cell is describing a capability the "
        f"server does not have, or denying one it does. Refusals seen: {refused}"
    )
