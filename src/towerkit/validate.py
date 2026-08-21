"""Semantic validation → Diagnostics(errors, warnings).

Never `assert` here: asserts are stripped under `python -O`, which would
silently publish charts from invalid data. Errors are values, and the CLI
turns them into a non-zero exit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, cast

import jsonschema
from pydantic import ValidationError

from .jurisdictions import US_JURISDICTIONS
from .model import (
    Layer,
    Program,
    Retention,
    RetentionType,
    parse_program_json,
    program_from_jsonable,
)
from .money import BPS_SCALE, format_money

ERROR = "error"
WARNING = "warning"

# Four states run monopolistic workers-compensation funds: cover there is
# bought from the state, and a private policy CANNOT write it. A statutory
# layer naming one is therefore an error about the world, in the same class as
# `statutory-line-shared` — not a preference.
MONOPOLISTIC_STATES = frozenset({"ND", "OH", "WA", "WY"})

# The codes the monopolistic check above is able to recognise. Anything else
# gets a WARNING rather than an error: a non-US programme is not invalid, it is
# UNCHECKED, and saying so is the whole point — silence would leave the one
# check this field exists for quietly unapplied on "Ohio" or "Ontario".
#
# Imported, not spelled out: `edit.parse_states` needs the same vocabulary to
# tell a pasted state list apart from prose, and fifty-one codes written twice
# is the copy that quietly differs. See jurisdictions.py for why the
# monopolistic set stays HERE, beside the rule that reads it.


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    # Where the TUI should jump: ("layer", "xs-3"), ("line", "el"),
    # ("retention", 2), ("program", None).
    ref: tuple[str, Any] = ("program", None)

    def __str__(self) -> str:
        mark = "✗" if self.severity == ERROR else "⚠"
        return f"{mark} {self.message}"


@dataclass
class Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str, ref: tuple[str, Any] = ("program", None)) -> None:
        self.items.append(Diagnostic(ERROR, code, message, ref))

    def warn(self, code: str, message: str, ref: tuple[str, Any] = ("program", None)) -> None:
        self.items.append(Diagnostic(WARNING, code, message, ref))

    def for_ref(self, ref: tuple[str, Any]) -> list[Diagnostic]:
        return [d for d in self.items if d.ref == ref]


class ProgramInvalidError(Exception):
    """Raised when an operation (rendering, comparison) is given invalid data."""

    def __init__(self, diagnostics: Diagnostics, source: str = "program") -> None:
        self.diagnostics = diagnostics
        lines = "\n".join(str(d) for d in diagnostics.errors)
        super().__init__(f"{source} has validation errors:\n{lines}")


def validate_program(program: Program) -> Diagnostics:
    """All semantic rules. Pure: model in, diagnostics out."""
    diags = Diagnostics()
    line_ids = program.line_ids()

    _check_unique_ids(program, diags)
    _check_program_period(program, diags)

    for layer in program.layers:
        _check_layer(layer, line_ids, diags)
        if layer.follows_underlying:
            _check_follows(program, layer, diags)

    for line in program.lines:
        _check_line_stack(program, line.id, diags)

    for index, retention in enumerate(program.retentions):
        _check_retention(retention, index, line_ids, diags)

    _check_group_adjacency(program, diags)

    covered = {lid for r in program.retentions for lid in r.applies_to}
    for line in program.lines:
        if line.id not in covered:
            diags.warn(
                "line-no-retention",
                f"{line.id}: no retention recorded for {line.name}",
                ("line", line.id),
            )

    for index, sublimit in enumerate(program.sublimits):
        for lid in sublimit.applies_to:
            if lid not in line_ids:
                diags.error(
                    "sublimit-unknown-line",
                    f"sublimit {sublimit.name!r} applies to unknown line {lid!r}",
                    ("sublimit", index),
                )
    return diags


def _check_program_period(program: Program, diags: Diagnostics) -> None:
    if program.period.end <= program.period.start:
        diags.error(
            "program-period",
            f"program period ends {program.period.end.isoformat()} "
            f"on or before it starts {program.period.start.isoformat()}",
        )


def _check_group_adjacency(program: Program, diags: Diagnostics) -> None:
    """A bucket only reads as one bucket when its columns sit together."""
    seen_done: dict[str, bool] = {}
    previous: str | None = None
    for line in program.lines:
        group = line.group
        if previous is not None and previous != group and previous in seen_done:
            seen_done[previous] = True
        if group is not None:
            if seen_done.get(group):
                diags.warn(
                    "group-scattered",
                    f"group {group!r}: columns are not adjacent — reorder lines "
                    f"([ / ]) to join the bucket",
                    ("line", line.id),
                )
            else:
                seen_done.setdefault(group, False)
        previous = group


def _check_unique_ids(program: Program, diags: Diagnostics) -> None:
    seen_lines: set[str] = set()
    for line in program.lines:
        if line.id in seen_lines:
            diags.error("line-duplicate-id", f"duplicate line id {line.id!r}", ("line", line.id))
        seen_lines.add(line.id)
    seen_layers: set[str] = set()
    for layer in program.layers:
        if layer.id in seen_layers:
            diags.error(
                "layer-duplicate-id", f"duplicate layer id {layer.id!r}", ("layer", layer.id)
            )
        seen_layers.add(layer.id)


def _check_layer(layer: Layer, line_ids: list[str], diags: Diagnostics) -> None:
    ref = ("layer", layer.id)
    if layer.statutory:
        # statutory => limit == 0 is the invariant the whole design rests on:
        # it is what keeps the layer out of every dollar total by construction
        if layer.limit != 0:
            diags.error(
                "statutory-limit",
                f"{layer.name}: statutory cover has no dollar limit, but limit "
                f"is {format_money(layer.limit)}",
                ref,
            )
        if layer.attach != 0:
            diags.error(
                "statutory-attach",
                f"{layer.name}: statutory cover owns its column from $0, but "
                f"attaches at {format_money(layer.attach)}",
                ref,
            )
        if layer.follows_underlying:
            diags.error(
                "statutory-follows",
                f"{layer.name}: statutory cover cannot follow underlying — "
                f"nothing sits beneath it",
                ref,
            )
        if layer.named_limits:
            # `statutory ⇒ no dollar limit` is what keeps the layer out of every
            # dollar total by construction. Named dollar amounts on it are
            # dollar limits by another name.
            diags.error(
                "statutory-named-limits",
                f"{layer.name}: statutory cover has no dollar limit, so it "
                f"cannot state named limits",
                ref,
            )
    elif layer.limit <= 0:
        diags.error("layer-limit", f"{layer.name}: non-positive limit {layer.limit}", ref)
    _check_layer_detail(layer, diags, ref)
    seen: set[str] = set()
    for lid in layer.applies_to:
        if lid not in line_ids:
            diags.error(
                "layer-unknown-line",
                f"{layer.name}: applies to unknown line {lid!r}",
                ref,
            )
        if lid in seen:
            diags.error(
                "layer-duplicate-line",
                f"{layer.name}: line {lid!r} listed twice in appliesTo",
                ref,
            )
        seen.add(lid)

    if layer.period is not None and layer.period.end <= layer.period.start:
        diags.error(
            "layer-period",
            f"{layer.name}: policy period ends {layer.period.end.isoformat()} "
            f"on or before it starts {layer.period.start.isoformat()}",
            ref,
        )

    signed = layer.signed_bps
    if signed > BPS_SCALE:
        diags.error(
            "layer-oversigned",
            f"{layer.name}: shares sum to {signed / 100:.2f}% — over-signed",
            ref,
        )
    elif signed < BPS_SCALE and layer.statutory:
        # no dollar limit to apportion: quantify the hole as a share
        diags.warn(
            "layer-unplaced",
            f"{layer.name}: {signed / 100:g}% placed — {(BPS_SCALE - signed) / 100:g}% open",
            ref,
        )
    elif signed < BPS_SCALE and layer.limit > 0:
        unplaced = layer.limit * (BPS_SCALE - signed) // BPS_SCALE
        diags.warn(
            "layer-unplaced",
            f"{layer.name}: {signed / 100:g}% placed — {format_money(unplaced)} unplaced",
            ref,
        )


def _check_layer_detail(layer: Layer, diags: Diagnostics, ref: tuple[str, Any]) -> None:
    """The three optional detail fields. Every rule here answers one question:
    would this value ever reach the reader? A field that silently does nothing
    is how a modelled fact decays into a general-purpose note."""
    _check_states(layer, diags, ref)

    seen: set[str] = set()
    for named in layer.named_limits:
        if named.name in seen:
            diags.error(
                "named-limit-duplicate",
                f"{layer.name}: two named limits are both called {named.name!r}",
                ref,
            )
        seen.add(named.name)

    # PROSE WINS, and carrying both would discard the structured data in
    # silence — the field would look broken to whoever set it. Refusing the
    # combination is what makes "prose wins" safe to state.
    if layer.limits_detail and layer.named_limits:
        diags.error(
            "limits-detail-conflict",
            f"{layer.name}: limitsDetail is exported verbatim and would hide "
            f"the named limits — state the cover once, in one of the two",
            ref,
        )

    # premiumDetail replaces the word a ZERO premium prints. Anywhere else the
    # premium cell holds a number the subtotals add up, or nothing at all, and
    # the detail would never render.
    if layer.premium_detail is not None and layer.premium != 0:
        stated = "no premium" if layer.premium is None else format_money(layer.premium)
        diags.error(
            "premium-detail-conflict",
            f"{layer.name}: premiumDetail says what a ZERO premium is included "
            f"with, but this layer states {stated}",
            ref,
        )


def _check_states(layer: Layer, diags: Diagnostics, ref: tuple[str, Any]) -> None:
    if layer.states and not layer.statutory:
        diags.error(
            "states-non-statutory",
            f"{layer.name}: states describe where STATUTORY cover is filed; "
            f"a dollar-limited layer cannot carry them",
            ref,
        )
    seen: set[str] = set()
    for state in layer.states:
        # Compared UPPER-CASED, stored verbatim: a lowercase "oh" that slipped
        # past an exact-match set would leave the monopolistic check unapplied,
        # which is the one thing this field exists for.
        code = state.strip().upper()
        if code in seen:
            diags.error(
                "states-duplicate",
                f"{layer.name}: state {state!r} is listed twice",
                ref,
            )
        seen.add(code)
        if code in MONOPOLISTIC_STATES:
            diags.error(
                "states-monopolistic",
                f"{layer.name}: {code} runs a monopolistic state fund — cover "
                f"there cannot be written by a private policy",
                ref,
            )
        elif code not in US_JURISDICTIONS:
            diags.warn(
                "states-unrecognized",
                f"{layer.name}: {state!r} is not a two-letter US code, so the "
                f"monopolistic-fund check could not be applied to it",
                ref,
            )


def _check_follows(program: Program, layer: Layer, diags: Diagnostics) -> None:
    ref = ("layer", layer.id)
    tops = program.underlying_tops(layer)
    highest = max(tops.values(), default=0)
    if layer.attach != highest:
        diags.error(
            "layer-follows-attach",
            f"{layer.name}: follows-underlying attach must equal the highest "
            f"underlying top {format_money(highest)}, not {format_money(layer.attach)}",
            ref,
        )
    for lid in layer.applies_to:
        taller = [
            other.name
            for other in program.layers
            if other.id != layer.id
            and not other.follows_underlying
            and lid in other.applies_to
            and other.limit > 0
            and other.attach < layer.attach < other.top
        ]
        if taller:
            diags.error(
                "layer-follows-overlap",
                f"{layer.name}: overlaps {taller[0]!r} on {lid}",
                ref,
            )


def _effective_attach(program: Program, layer: Layer, line_id: str) -> int:
    if layer.follows_underlying:
        return program.underlying_tops(layer).get(line_id, layer.attach)
    return layer.attach


def _check_line_stack(program: Program, line_id: str, diags: Diagnostics) -> None:
    line = next(ln for ln in program.lines if ln.id == line_id)
    covering = program.layers_for_line(line_id)
    statutory = [ly for ly in covering if ly.statutory]
    stack = [ly for ly in covering if not ly.statutory and ly.limit > 0]
    if statutory:
        # A statutory bar owns its column floor to top. Base/gap/overlap are
        # dollar questions and do not apply; the only error possible here is
        # something ELSE trying to share the column, which would draw straight
        # through the full-height bar. That "something else" can be a dollar
        # layer OR a second statutory layer — two statutory layers stacked on
        # one line silently draw on top of each other with no dollar layer
        # in sight, so len(statutory) > 1 has to trip this too.
        if stack or len(statutory) > 1:
            if stack:
                detail = f"{stack[0].name!r} also covers {line.name}"
            else:
                other_names = ", ".join(repr(ly.name) for ly in statutory[1:])
                detail = (
                    f"so does {other_names} — only one statutory layer may cover "
                    f"{line.name}"
                )
            diags.error(
                "statutory-line-shared",
                f"{line_id}: {statutory[0].name} is statutory and covers the whole "
                f"column, but {detail}",
                ("line", line_id),
            )
        return
    stack.sort(key=lambda ly: _effective_attach(program, ly, line_id))
    if not stack:
        diags.error("line-empty", f"{line_id}: no layers cover {line.name}", ("line", line_id))
        return
    base = _effective_attach(program, stack[0], line_id)
    if base != 0:
        diags.error(
            "line-base",
            f"{line_id}: lowest layer {stack[0].name!r} attaches at "
            f"{format_money(base)}, not $0",
            ("line", line_id),
        )
    for below, above in zip(stack, stack[1:], strict=False):
        above_attach = _effective_attach(program, above, line_id)
        if above_attach > below.top:
            diags.error(
                "line-gap",
                f"{line_id}: GAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above_attach)}",
                ("line", line_id),
            )
        elif above_attach < below.top and not above.follows_underlying:
            diags.error(
                "line-overlap",
                f"{line_id}: OVERLAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above_attach)}",
                ("line", line_id),
            )


def _check_retention(
    retention: Retention, index: int, line_ids: list[str], diags: Diagnostics
) -> None:
    ref = ("retention", index)
    label = "/".join(retention.applies_to)
    for lid in retention.applies_to:
        if lid not in line_ids:
            diags.error(
                "retention-unknown-line",
                f"retention on {label}: unknown line {lid!r}",
                ref,
            )
    if retention.aggregate is not None and retention.aggregate < retention.amount:
        diags.error(
            "retention-aggregate",
            f"retention on {label}: aggregate {format_money(retention.aggregate)} is below "
            f"the per-occurrence amount {format_money(retention.amount)}",
            ref,
        )
    if retention.type is RetentionType.CAPTIVE and not retention.vehicle:
        diags.error(
            "retention-vehicle",
            f"retention on {label}: captive retention has no named vehicle",
            ref,
        )


# --- schema + file-level validation ------------------------------------------


def _schema() -> dict[str, Any]:
    text = resources.files("towerkit").joinpath("schema/program.schema.json").read_text("utf-8")
    return cast(dict[str, Any], json.loads(text))


def validate_against_schema(data: Any) -> list[Diagnostic]:
    """JSON Schema check on a plain-parsed tree (floats as floats)."""
    validator = jsonschema.Draft202012Validator(
        _schema(), format_checker=jsonschema.FormatChecker()
    )
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "$"
        out.append(Diagnostic(ERROR, "schema", f"schema: {where}: {err.message}"))
    return out


def validate_file(path: Path | str) -> tuple[Program | None, Diagnostics]:
    """Parse, schema-check, model-check, semantic-check one file.

    Returns the loaded program when it is at least loadable, plus every
    diagnostic found. `program is None` means the file could not even be
    parsed into the model.
    """
    path = Path(path)
    diags = Diagnostics()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        diags.error("io", f"{path}: {exc}")
        return None, diags

    try:
        plain = json.loads(text)
    except json.JSONDecodeError as exc:
        diags.error("json", f"{path}: invalid JSON: {exc}")
        return None, diags

    diags.items.extend(validate_against_schema(plain))

    try:
        program = program_from_jsonable(parse_program_json(text))
    except (ValidationError, ValueError) as exc:
        diags.error("model", f"{path}: {exc}")
        return None, diags

    diags.items.extend(validate_program(program).items)
    _check_render_theme(program, diags)
    return program, diags


def _check_render_theme(program: Program, diags: Diagnostics) -> None:
    """`render.theme` names a FILE, and `towerctl render` reads it with no net
    underneath — so a value `load_theme` cannot read is a program that
    validates clean and then crashes the renderer with a raw traceback (round
    six, 2026-08-20: the write-says-clean genus, one field over from
    currency). It lives HERE, not in `validate_program`: the check needs the
    filesystem, and the semantic pass stays pure — model in, diagnostics out.

    Resolution is relative to the working directory, deliberately, because
    that is exactly how `towerctl render` resolves it: this check is a
    prediction about the renderer, and a prediction made from anywhere else
    would be about a render nobody is going to run.

    Absolute paths are an error even when they load: program files are
    portable by contract (theme paths stay RELATIVE — render.theme docs), and
    a path that renders here and breaks on the next machine is worse than one
    that breaks now."""
    theme = program.render.theme if program.render else None
    if theme is None:
        return
    from .theme import load_theme, theme_problems

    if Path(theme).is_absolute():
        diags.error(
            "render-theme",
            f"render.theme {theme!r} is absolute — program files are portable, "
            f"store a path relative to where towerctl runs",
        )
        return
    try:
        loaded = load_theme(theme)
    except Exception as exc:
        diags.error(
            "render-theme",
            f"render.theme {theme!r} cannot be loaded ({type(exc).__name__}: {exc}) "
            f"— towerctl render will fail on this file",
        )
        return
    # Loading is not rendering (round seven): a theme with a colour the
    # colour math cannot parse loads clean and crashes the renderer.
    for problem in theme_problems(loaded):
        diags.error(
            "render-theme",
            f"render.theme {theme!r}: {problem} — towerctl render will fail on this file",
        )
