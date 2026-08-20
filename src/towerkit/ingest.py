"""Build Programs from schedules: pasted tower text or canonical tabular rows.

The seam rule (bookkit spec 2026-08-11): callers map their own messy headers
to CANONICAL_FIELDS before calling; this module decides what the tower MEANS.
Carrier names are kept verbatim — alias resolution is the caller's job.

Drafts may be incomplete: parse what you can, surface what you couldn't via
Diagnostics. `to_program()` refuses while errors remain, keeping the strict
Program model free of half-parsed states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .dates import parse_flexible_date
from .model import (
    Layer,
    Line,
    Participant,
    Period,
    Placement,
    Program,
    Retention,
    RetentionType,
)
from .money import (
    MoneyParseError,
    format_money_compact,
    format_share,
    parse_money,
    parse_share,
)
from .validate import Diagnostics, ProgramInvalidError, validate_program

CANONICAL_FIELDS: tuple[str, ...] = (
    "layer", "line", "limit", "attachment", "carrier", "share",
    "premium", "inception", "expiry", "policy_number",
)


@dataclass
class DraftProgram:
    """A Program in the making — same shape, laxer rules, plus diagnostics."""

    insured: str = ""
    program: str = ""
    placement: Placement = Placement.PROPOSED
    period: Period | None = None
    currency: str = "USD"
    lines: list[Line] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    retentions: list[Retention] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    def to_program(self) -> Program:
        """Build the Program, keeping every diagnostic on the draft.

        Validation warnings used to be computed here and dropped on the
        floor whenever the build succeeded, so an import that produced a
        1%-placed tower printed nothing at all while `towerctl validate`
        on the same file printed the warning. Callers now read
        `draft.diagnostics` AFTER this call, not before."""
        gate = Diagnostics()
        if not self.insured.strip():
            gate.error("draft.insured", "insured name is required")
        if not self.program.strip():
            gate.error("draft.program", "program name is required")
        if self.period is None:
            gate.error("draft.period", "policy period (inception and expiry) is required")
        if not gate.ok:
            self._carry(gate)
            raise ProgramInvalidError(gate, source="draft")
        assert self.period is not None  # narrowed by the gate above
        try:
            program = Program(
                insured=self.insured.strip(),
                program=self.program.strip(),
                placement=self.placement,
                period=self.period,
                currency=self.currency,
                lines=list(self.lines),
                layers=list(self.layers),
                retentions=list(self.retentions),
            )
        except ValidationError as exc:
            # The header fields ride the DraftProgram dataclass unvalidated,
            # so the MODEL's refusal (a control character in an
            # ANSI-coloured insured, round eleven) surfaced here as a raw
            # ValidationError — and the CLI and TUI import paths catch only
            # the documented ProgramInvalidError. Same contract as the gate
            # above: the refusal becomes a diagnostic, the raise is ours.
            gate = Diagnostics()
            gate.error("draft.value", _model_refusal(exc))
            self._carry(gate)
            raise ProgramInvalidError(gate, source="draft") from exc
        diags = validate_program(program)
        self._carry(diags)
        if not diags.ok:
            raise ProgramInvalidError(diags, source="draft")
        return program

    def _carry(self, diags: Diagnostics) -> None:
        """Fold diagnostics onto the draft, skipping ones already there so
        a second `to_program()` cannot double them up."""
        for diag in diags.items:
            if diag not in self.diagnostics.items:
                self.diagnostics.items.append(diag)


# --- pasted schedule text -----------------------------------------------------

_SEGMENT_SPLIT = re.compile(r"\s+—\s*|\s+-\s+|\s*\|\s*")
_BAND_XS = re.compile(r"^(?P<limit>\S+)\s+xs\.?\s+(?P<attach>\S+)$", re.IGNORECASE)
_BAND_PRIMARY = re.compile(r"^primary\s+(?P<limit>\S+)$", re.IGNORECASE)
_RETENTION_LINE = re.compile(
    r"^(?P<kind>sir|deductible|ded|retention)\s+(?P<amount>\S+)$", re.IGNORECASE
)
_PARTICIPANT = re.compile(r"^(?P<carrier>.+?)\s+(?P<share>[\d.]+)\s*%$")


def parse_tower(text: str, *, insured: str = "", program: str = "") -> DraftProgram:
    """Pasted schedule text → draft. One layer or retention per line; segments
    split on em-dash, spaced hyphen, or pipe. Unreadable lines become error
    diagnostics with their line number — parsing never gives up early."""
    draft = DraftProgram(insured=insured, program=program)
    try:
        cover = Line(id="cover", name=program.strip() or "Coverage")
    except ValidationError as exc:
        # The `program` kwarg is a header, not a line, so round ten's
        # per-line net never saw it (round eleven). The tower still parses
        # under a placeholder; the refusal is a diagnostic like any other.
        draft.diagnostics.error("paste.value", f"program name: {_model_refusal(exc)}")
        cover = Line(id="cover", name="Coverage")
    draft.lines = [cover]
    draft.diagnostics.warn(
        "paste.line", "no coverage lines in pasted text; synthesized one"
    )
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            if _try_retention(draft, stripped, lineno, cover.id):
                continue
            _try_layer(draft, stripped, lineno, cover.id)
        except ValidationError as exc:
            # The MODEL can refuse a value mid-line — control characters in
            # a carrier since round nine — and that refusal escaped the
            # per-line nets, which caught only MoneyParseError: ANSI-coloured
            # text pasted from a terminal aborted the whole import with a
            # pydantic dump and no line number (round ten). This module's
            # contract is the docstring's: parsing never gives up early.
            draft.diagnostics.error("paste.value", f"line {lineno}: {_model_refusal(exc)}")
    return draft


def _model_refusal(exc: ValidationError) -> str:
    """The model's objection without pydantic's four-line repr and URL."""
    return "; ".join(err["msg"].removeprefix("Value error, ") for err in exc.errors())


def _try_retention(draft: DraftProgram, text: str, lineno: int, line_id: str) -> bool:
    match = _RETENTION_LINE.match(text)
    if match is None:
        return False
    try:
        amount = parse_money(match.group("amount"))
    except MoneyParseError as exc:
        draft.diagnostics.error("paste.retention", f"line {lineno}: {exc}")
        return True
    kind = match.group("kind").lower()
    if kind == "retention":
        draft.diagnostics.warn(
            "paste.retention", f"line {lineno}: 'retention' read as a deductible"
        )
    rtype = RetentionType.SIR if kind == "sir" else RetentionType.DEDUCTIBLE
    draft.retentions.append(Retention(applies_to=[line_id], type=rtype, amount=amount))
    return True


def _try_layer(draft: DraftProgram, text: str, lineno: int, line_id: str) -> None:
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(text) if s.strip()]
    band = _parse_band(segments[0]) if segments else None
    if band is None:
        draft.diagnostics.error(
            "paste.layer", f"line {lineno}: cannot read a layer from {text!r}"
        )
        return
    attach, limit = band
    participants = (
        _parse_participants(draft, segments[1], lineno) if len(segments) > 1 else []
    )
    premium: int | None = None
    if len(segments) > 2:
        try:
            premium = parse_money(segments[2])
        except MoneyParseError as exc:
            draft.diagnostics.error("paste.premium", f"line {lineno}: {exc}")
    name = (
        "Primary"
        if attach == 0
        else f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
    )
    draft.layers.append(
        Layer(
            id=f"layer-{len(draft.layers) + 1}", name=name, applies_to=[line_id],
            attach=attach, limit=limit, premium=premium, participants=participants,
        )
    )


def _parse_band(segment: str) -> tuple[int, int] | None:
    match = _BAND_XS.match(segment)
    if match:
        try:
            return parse_money(match.group("attach")), parse_money(match.group("limit"))
        except MoneyParseError:
            return None
    match = _BAND_PRIMARY.match(segment)
    if match:
        try:
            return 0, parse_money(match.group("limit"))
        except MoneyParseError:
            return None
    return None


def _parse_participants(
    draft: DraftProgram, segment: str, lineno: int
) -> list[Participant]:
    out: list[Participant] = []
    entries = [entry.strip() for entry in segment.split(",") if entry.strip()]
    for entry in entries:
        match = _PARTICIPANT.match(entry)
        if match:
            try:
                share = parse_share(match.group("share"))
            except MoneyParseError as exc:
                draft.diagnostics.error("paste.share", f"line {lineno}: {exc}")
                continue
            out.append(
                Participant(carrier=match.group("carrier").strip(), share_bps=share)
            )
        else:
            out.append(Participant(carrier=entry, share_bps=0))
    if len(out) == 1 and out[0].share_bps == 0:
        out[0] = Participant(carrier=out[0].carrier, share_bps=10_000)
    for participant in out:
        if participant.share_bps == 0:
            draft.diagnostics.warn(
                "paste.share", f"line {lineno}: no share for {participant.carrier!r}"
            )
    return out


# --- canonical tabular rows ----------------------------------------------------


def program_from_rows(
    rows: list[dict[str, object]], *, insured: str = "", program: str = ""
) -> DraftProgram:
    """Canonical rows (CANONICAL_FIELDS keys, values parsed strings or native
    ints/dates) → draft. Rows sharing a `layer` name merge participants onto
    one layer; the first row carrying dates sets the program period, later
    differing dates become that layer's own period (staggered effective
    dates are per-layer, matching the model)."""
    draft = DraftProgram(insured=insured, program=program)
    lines_by_name: dict[str, Line] = {}
    layers_by_name: dict[str, Layer] = {}
    synthesized = False
    for rownum, row in enumerate(rows, start=1):
        try:
            limit = _as_money(row.get("limit"))
            attach = _as_money(row.get("attachment"))
            premium = _as_money(row.get("premium"))
        except MoneyParseError as exc:
            draft.diagnostics.error("rows.money", f"row {rownum}: {exc}")
            continue
        if limit is None:
            draft.diagnostics.error("rows.money", f"row {rownum}: limit is required")
            continue
        try:
            line_ids, synthesized = _line_ids_for(
                draft, lines_by_name, row.get("line"), synthesized
            )
            period = _period_from(draft, row, rownum)
            if draft.period is None and period is not None:
                draft.period = period
            layer_name = str(row.get("layer") or "").strip() or (
                "Primary" if not attach
                else f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
            )
            layer = layers_by_name.get(layer_name)
            if layer is None:
                layer = Layer(
                    id=_slug(layer_name, taken={la.id for la in draft.layers}),
                    name=layer_name, applies_to=line_ids, attach=attach or 0,
                    limit=limit, premium=premium,
                    policy_number=str(row.get("policy_number") or "").strip() or None,
                    period=(
                        period if period is not None and period != draft.period else None
                    ),
                )
                layers_by_name[layer_name] = layer
                draft.layers.append(layer)
            elif layer.limit != limit or layer.attach != (attach or 0):
                draft.diagnostics.error(
                    "rows.band",
                    f"row {rownum}: {layer_name!r} already has a different "
                    f"limit/attachment",
                )
                continue
            carrier = str(row.get("carrier") or "").strip()
            if carrier:
                share_raw = row.get("share")
                try:
                    share = (
                        parse_share(str(share_raw))
                        if share_raw not in (None, "")
                        else 10_000
                    )
                except MoneyParseError as exc:
                    draft.diagnostics.error("rows.share", f"row {rownum}: {exc}")
                    continue
                layer.participants = [
                    *layer.participants, Participant(carrier=carrier, share_bps=share)
                ]
        except ValidationError as exc:
            # Same net as `parse_tower`'s, for the same regression: a model
            # refusal from a cell value (a control character in a carrier)
            # aborted the whole spreadsheet import instead of becoming this
            # row's diagnostic.
            draft.diagnostics.error("rows.value", f"row {rownum}: {_model_refusal(exc)}")
    return draft


def rows_from_program(program: Program) -> list[dict[str, object]]:
    """The exact inverse of `program_from_rows` for tower structure — one row
    per (layer, participant). Feeds the template's worked example and the
    round-trip contract test."""
    names = {line.id: line.name for line in program.lines}
    rows: list[dict[str, object]] = []
    for layer in program.layers:
        period = layer.period or program.period
        base: dict[str, object] = {
            "layer": layer.name,
            "line": ";".join(names[lid] for lid in layer.applies_to),
            "limit": layer.limit, "attachment": layer.attach,
            "inception": period.start.isoformat(), "expiry": period.end.isoformat(),
        }
        if layer.premium is not None:
            base["premium"] = layer.premium
        if layer.policy_number is not None:
            base["policy_number"] = layer.policy_number
        participants: list[Participant | None] = list(layer.participants) or [None]
        for participant in participants:
            row = dict(base)
            if participant is not None:
                row["carrier"] = participant.carrier
                row["share"] = format_share(participant.share_bps)
            rows.append(row)
    return rows


def _as_money(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MoneyParseError(f"cannot parse money value: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return parse_money(str(value))


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return parse_flexible_date(str(value))


def _period_from(
    draft: DraftProgram, row: dict[str, object], rownum: int
) -> Period | None:
    start, end = _as_date(row.get("inception")), _as_date(row.get("expiry"))
    for key, given, parsed in (
        ("inception", row.get("inception"), start),
        ("expiry", row.get("expiry"), end),
    ):
        if given not in (None, "") and parsed is None:
            draft.diagnostics.error(
                "rows.date", f"row {rownum}: cannot read a date from {given!r} ({key})"
            )
    if start is None or end is None:
        return None
    return Period(start=start, end=end)


def _line_ids_for(
    draft: DraftProgram,
    lines_by_name: dict[str, Line],
    cell: object,
    synthesized: bool,
) -> tuple[list[str], bool]:
    names = [n.strip() for n in str(cell or "").split(";") if n.strip()]
    if not names:
        cover_name = draft.program.strip() or "Coverage"
        cover = lines_by_name.get(cover_name)  # reuse a real line of that name
        if cover is None:
            cover = Line(id="cover", name=cover_name)
            lines_by_name[cover_name] = cover
            draft.lines.append(cover)
            draft.diagnostics.warn(
                "rows.line", "rows without a line share one synthesized line"
            )
        return [cover.id], True
    ids: list[str] = []
    for name in names:
        line = lines_by_name.get(name)
        if line is None:
            line = Line(id=_slug(name, taken={li.id for li in draft.lines}), name=name)
            lines_by_name[name] = line
            draft.lines.append(line)
        ids.append(line.id)
    return ids, synthesized


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


# --- one entry point for every schedule source ---------------------------------


def import_schedule(
    source: str | Path | None,
    *,
    text: str | None = None,
    insured: str = "",
    program: str = "",
    inception: str = "",
    expiry: str = "",
) -> DraftProgram:
    """One entry point for every schedule source: pasted text, xlsx
    template, strict-header csv, or free text file. Returns the draft so
    callers surface draft.diagnostics their own way (print vs notify)
    after draft.to_program(), which is what folds validation diagnostics
    onto the draft."""
    if text is not None:
        draft = parse_tower(text, insured=insured, program=program)
    else:
        if source is None:
            raise ValueError("a schedule file or pasted text is required")
        path = Path(source)
        suffix = path.suffix.lower()
        if suffix == ".xlsx":
            from .ingest_template import read_rows

            draft = program_from_rows(read_rows(path), insured=insured, program=program)
        elif suffix == ".csv":
            import csv

            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                headers = [h.strip().lower() for h in reader.fieldnames or []]
                unknown = [h for h in headers if h and h not in CANONICAL_FIELDS]
                if unknown:  # same strictness as the xlsx reader — never drop silently
                    raise ValueError(
                        f"unknown columns {unknown!r}; expected {list(CANONICAL_FIELDS)!r}"
                    )
                rows: list[dict[str, object]] = [
                    {k.strip().lower(): v for k, v in row.items() if v not in (None, "")}
                    for row in reader
                ]
            draft = program_from_rows(rows, insured=insured, program=program)
        else:
            draft = parse_tower(
                path.read_text(encoding="utf-8"), insured=insured, program=program
            )
    if draft.period is None and inception and expiry:
        start = parse_flexible_date(inception)
        end = parse_flexible_date(expiry)
        if start and end:
            draft.period = Period(start=start, end=end)
    return draft
