"""Pydantic models — the ONLY definition of a program.

On disk, money is integer whole dollars and shares are decimal fractions
(0.35). In memory, money stays integer dollars and shares become integer basis
points (3500). The conversion happens here, at the model boundary, and is
lossless in both directions.

`load_program` / `dump_program` implement the canonical file format: stable
key order matching the schema, 2-space indent, integers never in float form.
Round-tripping an untouched file must produce a zero diff.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from typing import Annotated, Any, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo

from .atomicio import atomic_write_text
from .money import bps_to_json_number, share_to_bps

SCHEMA_ID = "https://towerkit.dev/schema/program.schema.json"


class _MoneyTag:
    """Marker carried inside `Money`'s annotation: this integer is whole dollars.

    Nothing structural distinguishes money from the other integers in this
    file. `Layer.attach` is `int` with `ge=0` and so is `Participant.
    share_bps`; `Layer.limit` carries no constraint at all, deliberately, and
    is money anyway. A surface that wants to know which fields take
    '$5,000,000' therefore has to be TOLD, and the type is the only honest
    place to say it — the alternative is a second hand-written list of money
    fields somewhere else, which is the exact rot the derived MCP surface
    exists to stop.

    Pydantic ignores unrecognised `Annotated` members, so the tag costs
    nothing at validation time and survives in `FieldInfo.metadata` (and, for
    an optional money field, in the annotation's own `__metadata__`).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # so a derived error message reads sensibly
        return "MONEY"


MONEY = _MoneyTag()
Money = Annotated[int, Field(ge=0), MONEY]


class _OmitEmptyTag:
    """Marker: write this field only when it is truthy — otherwise no key.

    `followsUnderlying` set the precedent and `statutory`, `namedLimits`,
    `states` and `soiSchematic` follow it: a program that does not use the
    feature must not gain the key, so a file written before the field existed
    re-saves byte-identically, and an older towerkit wheel rejects only a file
    that actually USES the feature.

    It is a decision per FIELD, never a rule about falsy values: `attach: 0`,
    `premium: 0`, `showTotals: false` and an empty `participants` list are all
    written, and dropping any of them would rewrite files nobody edited. The
    decision therefore lives on the field, where the next person adding one
    reads it, instead of in a table beside the model — a table beside the model
    is exactly what the canonical serialiser stopped being.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # so a derived error message reads sensibly
        return "OMIT_EMPTY"


OMIT_EMPTY = _OmitEmptyTag()


class Placement(StrEnum):
    BOUND = "bound"
    PROPOSED = "proposed"


class RetentionType(StrEnum):
    DEDUCTIBLE = "deductible"
    SIR = "sir"
    CAPTIVE = "captive"


# The C0 controls minus \t \n \r — exactly the characters that corrupt an
# SOI workbook (openpyxl refuses them with a raw IllegalCharacterError) and
# garble terminals, and never anything a broker types on purpose.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)

    @model_validator(mode="after")
    def _no_control_characters(self) -> _Model:
        """A HARD rule on the model, the tier the branch reserves for
        constraints that must be fatal (see `Program.currency`): round nine
        rode `"Atomic \\x00 Corp"` through the hardened write surface into a
        file that validated exit 0 and crashed `towerctl soi`. On the model,
        every surface — MCP, TUI, library — inherits the refusal, and the
        walk is DERIVED off `model_fields`, never a list of field names.
        Multi-line text keeps \\t \\n \\r; nested models validate themselves.
        """
        for name in type(self).model_fields:
            value = getattr(self, name)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, str) and (hit := _CONTROL_CHARS.search(item)):
                    raise ValueError(
                        f"{name} contains the control character {hit.group()!r}, "
                        f"which corrupts SOI workbooks and terminals"
                    )
        return self


class Line(_Model):
    """One coverage line — one column in the diagram. Array order is display order."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    abbr: str | None = None
    group: str | None = None  # bucket label: project, location, entity…

    @property
    def label(self) -> str:
        """Column label: explicit abbr, else derived from the NAME (initials
        of a multi-word name, the name itself when short) — never from the
        id, which is a machine slug."""
        if self.abbr:
            return self.abbr
        words = self.name.split()
        if len(words) >= 2:
            return "".join(w[0].upper() for w in words)
        return self.name if len(self.name) <= 10 else self.name[:10]


class Participant(_Model):
    """In memory the share is always basis points; the disk fraction is
    converted in `program_from_jsonable`, never here — an integer `1` on disk
    means 100%, which no field validator could tell apart from 1 bps."""

    carrier: str = Field(min_length=1)
    share_bps: int = Field(ge=0, le=10_000)


class Period(_Model):
    start: date
    end: date


class NamedLimit(_Model):
    """One of several COORDINATE limits stated on a single layer.

    Not a `Sublimit`: a sublimit is a cap WITHIN a limit, carved out of it and
    scoped to lines. These are peers — a layer whose cover is quoted as three
    figures side by side rather than one. Employers Liability is one caller of
    this shape (each accident / disease each employee / disease policy limit);
    towerkit does not know that, and must not.

    The amounts are stated, never summed and never compared: which of them —
    if any — is the layer's height is a question about a line of business, and
    `limit` already answers it for the chart."""

    name: str = Field(min_length=1)
    amount: Money


class Layer(_Model):
    """A slab of cover: `limit` xs `attach`, spanning the `applies_to` lines.

    `policy_number` and `period` capture the issued policy behind the layer —
    programs can carry several policy effective/expiry dates, so the period
    is per-layer, defaulting to the program period when absent. This data
    also feeds the planned Schedules of Insurance output (v2)."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    policy_number: str | None = Field(alias="policyNumber", default=None)
    period: Period | None = None
    # Does the carrier AUDIT this policy at expiry? Workers' compensation and
    # general liability normally do — the deposit premium is trued up against
    # actual payroll or sales — while property and most excess layers do not.
    # An administrative fact about the POLICY, which is why it sits with
    # policy_number and period rather than with the cover: two layers of one
    # program can differ, so it cannot live on Program.
    #
    # No validation rule attaches to it. It changes no diagram and no total;
    # it exists so a broker can see, without opening the policy, which renewals
    # will bring an audit with them. OMIT_EMPTY, so "not auditable" writes no
    # key and no existing file changes shape.
    auditable: Annotated[bool, OMIT_EMPTY] = False
    follows_underlying: Annotated[bool, OMIT_EMPTY] = Field(
        alias="followsUnderlying", default=False
    )
    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    attach: Money
    # Money with NO `ge=0`: positivity is a SEMANTIC rule (validate.py reports
    # a non-positive limit as a diagnostic) so a draft stays loadable and
    # editable. The tag says how to read the number, not what it may be.
    limit: Annotated[int, MONEY]
    # Several coordinate limits where `limit` states one. Order is the file's
    # order and is display order — never sorted.
    named_limits: Annotated[list[NamedLimit], OMIT_EMPTY] = Field(
        alias="namedLimits", default_factory=list
    )
    statutory: Annotated[bool, OMIT_EMPTY] = False  # no dollar limit; limit MUST be 0
    # Jurisdictions a STATUTORY layer covers. A coverage fact, not a display
    # string: cover in a state the policy is not filed in is worth nothing, and
    # four states (ND/OH/WA/WY) cannot be covered by a private policy at all —
    # `validate` owns both rules. Meaningless on a dollar-limited layer, and
    # refused there, or it becomes a general-purpose note by accident.
    # Stored VERBATIM: normalising case here would rewrite files nobody edited.
    states: Annotated[list[str], OMIT_EMPTY] = Field(default_factory=list)
    premium: Money | None = None
    limits_detail: str | None = Field(alias="limitsDetail", default=None)
    retention_detail: str | None = Field(alias="retentionDetail", default=None)
    # What the premium cell says INSTEAD of "Included" when the premium is a
    # stated zero — "Included with Part A". Exported verbatim, exactly like the
    # two above: towerkit composes no sentence, so it never learns "Part A".
    # It qualifies a word, never a number; the validator refuses it anywhere
    # the cell would not print it.
    premium_detail: str | None = Field(alias="premiumDetail", default=None)
    participants: list[Participant] = Field(default_factory=list)
    notes: str | None = None

    @property
    def top(self) -> int:
        return self.attach + self.limit

    @property
    def signed_bps(self) -> int:
        return sum(p.share_bps for p in self.participants)


class Retention(_Model):
    """What the insured pays below the tower. Never insurance, never a carrier colour."""

    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    type: RetentionType
    amount: Money
    aggregate: Money | None = None
    vehicle: str | None = None
    notes: str | None = None


class Sublimit(_Model):
    name: str = Field(min_length=1)
    amount: Money
    applies_to: list[str] = Field(alias="appliesTo", min_length=1)
    notes: str | None = None


class RenderSettings(_Model):
    """Saved chart options, so a program remembers how it should render."""

    theme: str | None = None  # theme file path; None = built-in default
    show_totals: bool = Field(alias="showTotals", default=True)
    show_premiums: bool = Field(alias="showPremiums", default=True)
    cell_premiums: bool = Field(alias="cellPremiums", default=False)
    cell_dates: bool = Field(alias="cellDates", default=False)
    soi_schematic: Annotated[bool, OMIT_EMPTY] = Field(
        alias="soiSchematic", default=False
    )


class Program(_Model):
    """A complete placement: one insured, one period, one stack of layers."""

    schema_id: str = Field(alias="$schema", default=SCHEMA_ID)
    insured: str = Field(min_length=1)
    program: str = Field(min_length=1)
    placement: Placement
    period: Period
    # 3 letters, an ISO 4217 code. The bound is on the MODEL and not only in
    # `program.schema.json` because that is the difference between a write
    # being refused and a bad file being written and then reported: currency
    # is writable over MCP (Grant struck it from the denylist), the schema has
    # carried `minLength: 3, maxLength: 3` from the start, and with no rule
    # here `program_edit_field(field="currency", value="EU")` wrote a file
    # `towerctl validate` exits 1 on. The hard tier (`loads_program`) is what
    # blocks a write, and it only ever sees the model.
    currency: str = Field(min_length=3, max_length=3, default="USD")
    render: RenderSettings | None = None
    lines: list[Line] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    retentions: list[Retention] = Field(default_factory=list)
    sublimits: list[Sublimit] = Field(default_factory=list)
    notes: str | None = None

    def line_ids(self) -> list[str]:
        return [line.id for line in self.lines]

    def layers_for_line(self, line_id: str) -> list[Layer]:
        return sorted(
            (layer for layer in self.layers if line_id in layer.applies_to),
            key=lambda layer: layer.attach,
        )

    def total_limit(self) -> int:
        return sum(layer.limit for layer in self.layers)

    def total_premium(self) -> int:
        return sum(layer.premium or 0 for layer in self.layers)

    def clone_as_renewal(self) -> Program:
        """Deep-copy, bump the period by a year, mark proposed — the single
        most common real workflow (browser: 'clone as next renewal')."""
        clone = self.model_copy(deep=True)
        clone.period = Period(
            start=_plus_year(self.period.start), end=_plus_year(self.period.end)
        )
        clone.placement = Placement.PROPOSED
        return clone

    def underlying_tops(self, layer: Layer) -> dict[str, int]:
        """Per-column top of the stack beneath a follows-underlying layer.

        "Beneath" is decided by attachment order — a layer that STARTS below
        this one is underlying, however tall it has grown — so editing an
        underlying limit re-seats the follows layer instead of stranding it.
        A follows layer still at attach 0 uses its top as the seed threshold.
        """
        threshold = layer.attach if layer.attach > 0 else layer.top
        tops: dict[str, int] = {}
        for lid in layer.applies_to:
            candidates = [
                other.top
                for other in self.layers
                if other.id != layer.id
                and not other.follows_underlying
                and lid in other.applies_to
                and other.limit > 0
                and other.attach < threshold
            ]
            tops[lid] = max(candidates, default=0)
        return tops

    def carriers(self) -> list[str]:
        """Every carrier, in first-appearance order (stable colour assignment)."""
        seen: dict[str, None] = {}
        for layer in self.layers:
            for participant in layer.participants:
                seen.setdefault(participant.carrier, None)
        return list(seen)


def _plus_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28)


# --- canonical serialisation -------------------------------------------------
#
# DERIVED from the models, exactly as `program_read` and the MCP surface are.
#
# This used to be a THIRD hand-written field table (`_PROGRAM_KEYS`,
# `_LAYER_KEYS`, …) and it is why "add a field to model.py and it is writable"
# was false end to end: the write reached the in-memory model, the MCP response
# came back `{"wrote": ..., "errors": []}`, and the value was in neither the
# file nor the next read. The guard meant to catch that ran BACKWARDS —
# `set(raw) - set(keys)` over the hand-built dict could only see a key ADDED to
# the table, and was structurally blind to one MISSING from it, which is the
# only failure that ever actually happens.
#
# Key ORDER is model DECLARATION order, and that is not a new convention: every
# one of the deleted tuples was already in declaration order, field for field.
# `Participant` was the single deviation and only in the NAME — `share_bps` in
# memory, `share` on disk, same position — which `_DISK_FORM` carries.


# The one place the file's shape departs from the model's, and the one thing
# here that cannot be derived: a participant's share is basis points in memory
# and a decimal fraction on disk. It is not a validator because an integer `1`
# on disk means 100%, which no field validator could tell apart from 1 bps —
# see `Participant`. (model, python field) -> (disk key, converter).
_DISK_FORM: dict[tuple[type[BaseModel], str], tuple[str, Callable[[Any], Any]]] = {
    (Participant, "share_bps"): ("share", bps_to_json_number),
}


def _has_tag(info: FieldInfo, tag: object) -> bool:
    """Is `tag` on this field's annotation?

    Two shapes, because pydantic flattens them differently: `Annotated[bool,
    OMIT_EMPTY]` lands the tag in `FieldInfo.metadata`, while an optional
    (`Money | None`) keeps it on the inner `Annotated`'s own `__metadata__` and
    leaves `FieldInfo.metadata` empty. `mcpsurface._flatten` pays the same cost
    for MONEY.
    """
    if any(item is tag for item in info.metadata):
        return True
    annotation = info.annotation
    for candidate in (annotation, *get_args(annotation)):
        if any(item is tag for item in getattr(candidate, "__metadata__", ())):
            return True
    return False


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    """Every model class reachable from one annotation — `Period | None`,
    `list[NamedLimit]`, `Annotated[...]` all flatten to the classes inside."""
    found: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        found.append(annotation)
    for arg in get_args(annotation):
        # Recurse into the ARGS only. Including `annotation` itself in this
        # loop makes `list[NamedLimit]` ask about `list[NamedLimit]` forever.
        found.extend(_model_types(arg))
    return found


def money_disk_keys() -> set[str]:
    """Every on-disk key that holds money, derived from the `MONEY` tag.

    The canonical format writes money as whole-dollar INTEGERS, and the test
    guarding that carried a hand-written list of five keys — `"attach"`,
    `"limit"`, `"premium"`, `"amount"`, `"aggregate"`. That is the same second
    table this module just deleted from the serialiser, still standing in the
    suite, and structurally blind to every money field added after it was
    written: the next one rots exactly the way `program_to_jsonable` did.

    Asking the models instead means a money field is covered the moment it
    exists, with nothing to remember.
    """
    keys: set[str] = set()
    seen: set[type[BaseModel]] = set()

    def walk(cls: type[BaseModel]) -> None:
        if cls in seen:
            return
        seen.add(cls)
        for name, info in cls.model_fields.items():
            if _has_tag(info, MONEY):
                keys.add(_disk_key(cls, name, info))
            for nested in _model_types(info.annotation):
                walk(nested)

    walk(Program)
    return keys


def disk_fields(model: type[BaseModel]) -> list[tuple[str, str, FieldInfo]]:
    """(disk key, python name, FieldInfo) for every field `model` writes, in
    DECLARATION order — which is the order the canonical file uses.

    The same question `money_disk_keys` asks, asked for a whole model instead
    of one tag, and for the same reason: `schema/program.schema.json` is a
    hand-typed enumeration of every JSON key with `additionalProperties:
    false` at nine sites, so a field added to a model here and nowhere there
    makes the file towerkit itself just wrote stop validating. Nothing had
    ever compared the two — `test_schema_copies_are_identical` compares the
    two COPIES of the schema to each other, which both stay wrong together.

    A field is included whether or not a given instance writes it: OMIT_EMPTY
    decides what one FILE contains, and the schema has to describe the key
    whenever it does appear.
    """
    return [
        (_disk_key(model, name, info), name, info)
        for name, info in model.model_fields.items()
    ]


def disk_form_is_derived(model: type[BaseModel], name: str) -> bool:
    """Does this field's disk form follow from its annotation alone?

    False for the one field it does not. `Participant.share_bps` is basis
    points in memory and a decimal FRACTION on disk (`_DISK_FORM`), so
    anything reading the annotation would call it an integer where the file
    holds `0.35`. Asked rather than hard-coded, so a second conversion added
    to `_DISK_FORM` is covered the moment it exists.
    """
    return (model, name) not in _DISK_FORM


def _disk_key(model: type[BaseModel], name: str, info: FieldInfo) -> str:
    """The key the FILE uses: the `_DISK_FORM` rename, else the alias, else the
    python name. Aliases are the on-disk names — `policyNumber`, `appliesTo`,
    `$schema` — and the model is the only place they are written down."""
    override = _DISK_FORM.get((model, name))
    return override[0] if override is not None else (info.alias or name)


def _omitted(info: FieldInfo, value: Any) -> bool:
    """Is this field left out of the file entirely?

    A None is always absent: the canonical format never writes `null`, and an
    absent key means the default. A falsy value is absent only when the field
    carries OMIT_EMPTY — see that tag for why it is per-field and not a rule.
    """
    return value is None or (not value and _has_tag(info, OMIT_EMPTY))


def _jsonable(value: Any) -> Any:
    """One value, in the form `json.dumps` must see it.

    The final `raise` is the other half of the derivation's safety: a field
    typed something this does not know (a Decimal, a set, a UUID) fails loudly
    at the boundary instead of getting whatever `json.dumps` guesses, or
    whatever a generic encoder would round to a float — money is integer whole
    dollars and stays that way.
    """
    if isinstance(value, BaseModel):
        return _model_to_jsonable(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):  # before the scalar check: StrEnum IS a str
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool | int | float | str):
        return value
    raise RuntimeError(
        f"the canonical serialiser cannot write a {type(value).__name__}; teach "
        f"`_jsonable` how, rather than letting json.dumps guess"
    )


def _model_to_jsonable(model: BaseModel) -> dict[str, Any]:
    cls = type(model)
    out: dict[str, Any] = {}
    for name, info in cls.model_fields.items():
        value = getattr(model, name)
        if _omitted(info, value):
            continue
        override = _DISK_FORM.get((cls, name))
        if override is not None:
            value = override[1](value)
        out[_disk_key(cls, name, info)] = _jsonable(value)
    _check_nothing_was_dropped(model, out)
    return out


def _check_nothing_was_dropped(model: BaseModel, emitted: dict[str, Any]) -> None:
    """The guard, pointing the direction that actually fails.

    The old one compared the hand-built dict against the hand-written key order
    and so could only fire when a key was ADDED to the dict without a place in
    the order — a mistake that leaves the value visibly in the wrong place. It
    could not fire on a field missing from the dict, which is silent, and
    silent is what shipped.

    This asks the question the other way round, off `model_fields` rather than
    off the loop above: is every field the model declares either in the file or
    absent for a stated reason? A stray `continue` in the loop, or a
    `_DISK_FORM` entry that renames a key into nothing, fails here instead of
    dropping a broker's data into a success receipt.
    """
    cls = type(model)
    for name, info in cls.model_fields.items():
        if _disk_key(cls, name, info) in emitted:
            continue
        if _omitted(info, getattr(model, name)):
            continue
        raise RuntimeError(
            f"{cls.__name__}.{name} is set and was not written to the file"
        )


def program_to_jsonable(program: Program) -> dict[str, Any]:
    """The exact object tree the canonical file contains."""
    return _model_to_jsonable(program)


def dumps_program(program: Program) -> str:
    return json.dumps(program_to_jsonable(program), indent=2, ensure_ascii=False) + "\n"


def dump_program(program: Program, path: Path | str) -> None:
    """Canonical JSON, written durably — a failed write costs the new
    contents, never the old ones. See `towerkit.atomicio`."""
    atomic_write_text(path, dumps_program(program))


def parse_program_json(text: str) -> Any:
    """Parse JSON with shares kept exact: floats become Decimal, never binary floats."""
    return json.loads(text, parse_float=Decimal)


def program_from_jsonable(data: Any) -> Program:
    """Validate a parsed JSON tree, converting disk shares (fractions) to bps."""
    if isinstance(data, dict) and isinstance(data.get("layers"), list):
        data = {**data, "layers": [_layer_from_disk(layer) for layer in data["layers"]]}
    return Program.model_validate(data)


def _layer_from_disk(layer: Any) -> Any:
    if not isinstance(layer, dict) or not isinstance(layer.get("participants"), list):
        return layer
    participants = [
        {**p, "share_bps": share_to_bps(p["share"])} if isinstance(p, dict) and "share" in p else p
        for p in layer["participants"]
    ]
    for p in participants:
        if isinstance(p, dict):
            p.pop("share", None)
    return {**layer, "participants": participants}


def loads_program(text: str) -> Program:
    return program_from_jsonable(parse_program_json(text))


def load_program(path: Path | str) -> Program:
    return loads_program(Path(path).read_text(encoding="utf-8"))
