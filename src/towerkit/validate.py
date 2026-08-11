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

    for layer in program.layers:
        _check_layer(layer, line_ids, diags)

    for line in program.lines:
        _check_line_stack(program, line.id, diags)

    for index, retention in enumerate(program.retentions):
        _check_retention(retention, index, line_ids, diags)

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
    if layer.limit <= 0:
        diags.error("layer-limit", f"{layer.name}: non-positive limit {layer.limit}", ref)
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
    elif signed < BPS_SCALE and layer.limit > 0:
        unplaced = layer.limit * (BPS_SCALE - signed) // BPS_SCALE
        diags.warn(
            "layer-unplaced",
            f"{layer.name}: {signed / 100:g}% placed — {format_money(unplaced)} unplaced",
            ref,
        )


def _check_line_stack(program: Program, line_id: str, diags: Diagnostics) -> None:
    stack = [
        layer for layer in program.layers_for_line(line_id) if layer.limit > 0
    ]
    line = next(ln for ln in program.lines if ln.id == line_id)
    if not stack:
        diags.error("line-empty", f"{line_id}: no layers cover {line.name}", ("line", line_id))
        return
    if stack[0].attach != 0:
        diags.error(
            "line-base",
            f"{line_id}: lowest layer {stack[0].name!r} attaches at "
            f"{format_money(stack[0].attach)}, not $0",
            ("line", line_id),
        )
    for below, above in zip(stack, stack[1:], strict=False):
        if above.attach > below.top:
            diags.error(
                "line-gap",
                f"{line_id}: GAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above.attach)}",
                ("line", line_id),
            )
        elif above.attach < below.top:
            diags.error(
                "line-overlap",
                f"{line_id}: OVERLAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above.attach)}",
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
    return program, diags
