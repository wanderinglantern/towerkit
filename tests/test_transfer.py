"""transfer_line: exclusive travels, shared stays, move narrows."""

from __future__ import annotations

import pytest

from towerkit.model import Program, dumps_program
from towerkit.transfer import transfer_line


def make_src() -> Program:
    return Program.model_validate({
        "insured": "Src Co", "program": "Casualty", "placement": "bound",
        "period": {"start": "2026-01-01", "end": "2027-01-01"},
        "lines": [
            {"id": "gl", "name": "General Liability"},
            {"id": "al", "name": "Auto Liability"},
        ],
        "layers": [
            {"id": "gl-primary", "name": "Primary GL", "appliesTo": ["gl"],
             "attach": 0, "limit": 1_000_000},
            {"id": "umbrella", "name": "Umbrella", "appliesTo": ["gl", "al"],
             "attach": 1_000_000, "limit": 5_000_000},
        ],
        "retentions": [
            {"appliesTo": ["gl"], "type": "sir", "amount": 250_000},
            {"appliesTo": ["gl", "al"], "type": "deductible", "amount": 10_000},
        ],
        "sublimits": [
            {"name": "Flood", "amount": 100_000, "appliesTo": ["gl"]},
        ],
    })


def make_dst() -> Program:
    return Program.model_validate({
        "insured": "Dst Co", "program": "Scenario", "placement": "proposed",
        "period": {"start": "2026-01-01", "end": "2027-01-01"},
        "lines": [{"id": "el", "name": "Employers Liability"}],
        "layers": [{"id": "el-primary", "name": "Primary EL",
                    "appliesTo": ["el"], "attach": 0, "limit": 1_000_000}],
        "retentions": [], "sublimits": [],
    })


class TestCopy:
    def test_exclusive_travels_shared_stays(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=False)
        dst_line_ids = [ln.id for ln in r.dst_after.lines]
        dst_layer_ids = [ly.id for ly in r.dst_after.layers]
        assert dst_line_ids == ["el", "gl"]              # appended at end
        assert dst_layer_ids == ["el-primary", "gl-primary"]
        assert "umbrella" not in dst_layer_ids           # shared stays behind
        # exclusive retention + sublimit travel; shared retention does not
        assert [ret.amount for ret in r.dst_after.retentions] == [250_000]
        assert [s.name for s in r.dst_after.sublimits] == ["Flood"]

    def test_copy_leaves_source_identical(self) -> None:
        src = make_src()
        r = transfer_line(src, make_dst(), "gl", move=False)
        assert dumps_program(r.src_after) == dumps_program(src)

    def test_inputs_never_mutated(self) -> None:
        src, dst = make_src(), make_dst()
        before_src, before_dst = dumps_program(src), dumps_program(dst)
        transfer_line(src, dst, "gl", move=True)
        assert dumps_program(src) == before_src
        assert dumps_program(dst) == before_dst

    def test_unknown_line_raises(self) -> None:
        with pytest.raises(KeyError):
            transfer_line(make_src(), make_dst(), "nope", move=False)


class TestMove:
    def test_move_removes_line_and_exclusives_and_narrows_shared(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=True)
        assert [ln.id for ln in r.src_after.lines] == ["al"]
        src_layer_ids = [ly.id for ly in r.src_after.layers]
        assert src_layer_ids == ["umbrella"]             # exclusive gone
        umbrella = r.src_after.layers[0]
        assert umbrella.applies_to == ["al"]             # narrowed, not empty
        # shared retention narrowed; exclusive retention/sublimit removed
        assert len(r.src_after.retentions) == 1
        assert r.src_after.retentions[0].applies_to == ["al"]
        assert r.src_after.sublimits == []

    def test_shared_never_copied_narrowed_into_target(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=True)
        assert "umbrella" not in [ly.id for ly in r.dst_after.layers]


class TestSummary:
    def test_travels_and_stays_are_named(self) -> None:
        r = transfer_line(make_src(), make_dst(), "gl", move=False)
        joined = "\n".join(r.summary.travels)
        assert "Line: General Liability" in joined
        assert "Layer: Primary GL" in joined
        assert "SIR" in joined and "$250,000" in joined
        assert "Sublimit: Flood" in joined
        stays = "\n".join(r.summary.stays)
        assert "Umbrella" in stays and "Auto Liability" in stays
        assert r.summary.renames == []
