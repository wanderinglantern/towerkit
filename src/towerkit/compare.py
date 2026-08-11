"""Renewal comparison: two programs in, one generated change table out.

Pure logic — the renewal chart and the TUI diff screen both consume this.
Rows are keyed by (carrier, layer id): a carrier appearing on a layer this
year but not last year is NEW, the reverse is LAPSED, both is RENEWED.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Program
from .money import BPS_SCALE, premium_share

NEW = "NEW"
RENEWED = "RENEWED"
LAPSED = "LAPSED"


@dataclass(frozen=True)
class DeltaRow:
    carrier: str
    layer_id: str
    layer_name: str
    status: str
    share_old_bps: int | None
    share_new_bps: int | None
    line_old: int | None  # carrier's slice of the limit, dollars
    line_new: int | None
    premium_old: int | None
    premium_new: int | None

    @property
    def share_delta_bps(self) -> int:
        return (self.share_new_bps or 0) - (self.share_old_bps or 0)

    @property
    def line_delta(self) -> int:
        return (self.line_new or 0) - (self.line_old or 0)

    @property
    def premium_delta(self) -> int:
        return (self.premium_new or 0) - (self.premium_old or 0)


@dataclass(frozen=True)
class RenewalDelta:
    rows: tuple[DeltaRow, ...]
    limit_old: int
    limit_new: int
    premium_old: int
    premium_new: int

    @property
    def premium_delta(self) -> int:
        return self.premium_new - self.premium_old

    @property
    def premium_delta_pct(self) -> float | None:
        if self.premium_old == 0:
            return None
        return 100.0 * self.premium_delta / self.premium_old


def _cells(program: Program) -> dict[tuple[str, str], tuple[str, int, int, int | None]]:
    """(carrier, layer_id) → (layer name, share bps, line size, premium share)."""
    out: dict[tuple[str, str], tuple[str, int, int, int | None]] = {}
    for layer in program.layers:
        for participant in layer.participants:
            line_size = layer.limit * participant.share_bps // BPS_SCALE
            prem = (
                premium_share(layer.premium, participant.share_bps)
                if layer.premium is not None
                else None
            )
            key = (participant.carrier, layer.id)
            if key in out:  # same carrier twice on one layer: merge shares
                name, bps, line, p = out[key]
                merged_prem = None if prem is None and p is None else (prem or 0) + (p or 0)
                out[key] = (name, bps + participant.share_bps, line + line_size, merged_prem)
            else:
                out[key] = (layer.name, participant.share_bps, line_size, prem)
    return out


def compare_programs(expiring: Program, proposed: Program) -> RenewalDelta:
    old, new = _cells(expiring), _cells(proposed)
    layer_names = {layer.id: layer.name for layer in [*expiring.layers, *proposed.layers]}

    rows: list[DeltaRow] = []
    for key in sorted(old.keys() | new.keys(), key=lambda k: (k[1], k[0])):
        carrier, layer_id = key
        o, n = old.get(key), new.get(key)
        rows.append(
            DeltaRow(
                carrier=carrier,
                layer_id=layer_id,
                layer_name=layer_names[layer_id],
                status=RENEWED if o and n else (LAPSED if o else NEW),
                share_old_bps=o[1] if o else None,
                share_new_bps=n[1] if n else None,
                line_old=o[2] if o else None,
                line_new=n[2] if n else None,
                premium_old=o[3] if o else None,
                premium_new=n[3] if n else None,
            )
        )
    rows.sort(key=lambda r: -abs(r.premium_delta))
    return RenewalDelta(
        rows=tuple(rows),
        limit_old=expiring.total_limit(),
        limit_new=proposed.total_limit(),
        premium_old=expiring.total_premium(),
        premium_new=proposed.total_premium(),
    )
