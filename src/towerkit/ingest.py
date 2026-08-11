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
from .money import MoneyParseError, format_money_compact, parse_money, parse_share
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
        gate = Diagnostics()
        if not self.insured.strip():
            gate.error("draft.insured", "insured name is required")
        if not self.program.strip():
            gate.error("draft.program", "program name is required")
        if self.period is None:
            gate.error("draft.period", "policy period (inception and expiry) is required")
        if not gate.ok:
            raise ProgramInvalidError(gate, source="draft")
        assert self.period is not None  # narrowed by the gate above
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
        diags = validate_program(program)
        if not diags.ok:
            raise ProgramInvalidError(diags, source="draft")
        return program


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
    cover = Line(id="cover", name=program.strip() or "Coverage")
    draft.lines = [cover]
    draft.diagnostics.warn(
        "paste.line", "no coverage lines in pasted text; synthesized one"
    )
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _try_retention(draft, stripped, lineno, cover.id):
            continue
        _try_layer(draft, stripped, lineno, cover.id)
    return draft


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
    band = _parse_band(segments[0])
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
