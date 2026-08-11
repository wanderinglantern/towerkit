"""Build Programs from schedules: pasted tower text or canonical tabular rows.

The seam rule (bookkit spec 2026-08-11): callers map their own messy headers
to CANONICAL_FIELDS before calling; this module decides what the tower MEANS.
Carrier names are kept verbatim — alias resolution is the caller's job.

Drafts may be incomplete: parse what you can, surface what you couldn't via
Diagnostics. `to_program()` refuses while errors remain, keeping the strict
Program model free of half-parsed states.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Layer, Line, Period, Placement, Program, Retention
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
