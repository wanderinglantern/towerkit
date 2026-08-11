"""Table-driven validator tests: one case per rule, both directions."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Placement,
    Program,
    Retention,
    RetentionType,
    load_program,
    program_to_jsonable,
)
from towerkit.validate import validate_against_schema, validate_file, validate_program

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"
REPO = Path(__file__).parent.parent


def make_program(**overrides) -> Program:
    """A minimal clean program: one line, one primary layer, one retention."""
    base = dict(
        insured="Test Co",
        program="Casualty",
        placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        layers=[
            Layer(
                id="primary",
                name="Primary",
                applies_to=["gl"],
                attach=0,
                limit=2_000_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            )
        ],
        retentions=[
            Retention(applies_to=["gl"], type=RetentionType.DEDUCTIBLE, amount=250_000)
        ],
    )
    base.update(overrides)
    return Program(**base)


def codes(program: Program) -> set[str]:
    return {d.code for d in validate_program(program).items}


def error_codes(program: Program) -> set[str]:
    return {d.code for d in validate_program(program).errors}


def warning_codes(program: Program) -> set[str]:
    return {d.code for d in validate_program(program).warnings}


def layer(id: str, applies, attach: int, limit: int, shares=None) -> Layer:
    return Layer(
        id=id,
        name=id,
        applies_to=applies,
        attach=attach,
        limit=limit,
        participants=[Participant(carrier=c, share_bps=b) for c, b in (shares or [])],
    )


class TestCleanProgram:
    def test_no_diagnostics(self) -> None:
        assert codes(make_program()) == set()


class TestLineRules:
    def test_line_with_no_layers(self) -> None:
        program = make_program(layers=[])
        assert "line-empty" in error_codes(program)

    def test_lowest_layer_not_at_zero(self) -> None:
        program = make_program(
            layers=[layer("l1", ["gl"], 1_000_000, 2_000_000, [("Zurich", 10_000)])]
        )
        assert "line-base" in error_codes(program)

    def test_gap_between_layers(self) -> None:
        program = make_program(
            layers=[
                layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 10_000)]),
                layer("l2", ["gl"], 2_500_000, 5_000_000, [("AIG", 10_000)]),
            ]
        )
        diags = validate_program(program)
        gap = [d for d in diags.errors if d.code == "line-gap"]
        assert len(gap) == 1
        # both attachment points are reported
        assert "$2,000,000" in gap[0].message and "$2,500,000" in gap[0].message

    def test_overlap_between_layers(self) -> None:
        program = make_program(
            layers=[
                layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 10_000)]),
                layer("l2", ["gl"], 1_500_000, 5_000_000, [("AIG", 10_000)]),
            ]
        )
        diags = validate_program(program)
        overlap = [d for d in diags.errors if d.code == "line-overlap"]
        assert len(overlap) == 1
        assert "$2,000,000" in overlap[0].message and "$1,500,000" in overlap[0].message

    def test_contiguous_stack_is_clean(self) -> None:
        program = make_program(
            layers=[
                layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 10_000)]),
                layer("l2", ["gl"], 2_000_000, 5_000_000, [("AIG", 10_000)]),
            ]
        )
        assert error_codes(program) == set()


class TestLayerRules:
    def test_oversigned_is_error(self) -> None:
        program = make_program(
            layers=[layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 6_000), ("AIG", 5_000)])]
        )
        assert "layer-oversigned" in error_codes(program)

    def test_undersigned_is_warning_with_dollars(self) -> None:
        program = make_program(
            layers=[layer("l1", ["gl"], 0, 100_000_000, [("Zurich", 8_000)])]
        )
        diags = validate_program(program)
        unplaced = [d for d in diags.warnings if d.code == "layer-unplaced"]
        assert len(unplaced) == 1
        assert "$20,000,000" in unplaced[0].message
        assert diags.ok  # warnings never block

    def test_fully_signed_is_clean(self) -> None:
        assert "layer-unplaced" not in codes(make_program())

    def test_unknown_line_in_applies_to(self) -> None:
        program = make_program(
            layers=[layer("l1", ["gl", "nope"], 0, 2_000_000, [("Zurich", 10_000)])]
        )
        assert "layer-unknown-line" in error_codes(program)

    def test_non_positive_limit(self) -> None:
        program = make_program(
            layers=[
                layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 10_000)]),
                layer("l2", ["gl"], 2_000_000, 0),
            ]
        )
        assert "layer-limit" in error_codes(program)

    def test_duplicate_layer_id(self) -> None:
        program = make_program(
            layers=[
                layer("l1", ["gl"], 0, 2_000_000, [("Zurich", 10_000)]),
                layer("l1", ["gl"], 2_000_000, 5_000_000, [("AIG", 10_000)]),
            ]
        )
        assert "layer-duplicate-id" in error_codes(program)


class TestRetentionRules:
    def test_aggregate_below_per_occurrence(self) -> None:
        program = make_program(
            retentions=[
                Retention(
                    applies_to=["gl"],
                    type=RetentionType.DEDUCTIBLE,
                    amount=500_000,
                    aggregate=250_000,
                )
            ]
        )
        assert "retention-aggregate" in error_codes(program)

    def test_captive_without_vehicle(self) -> None:
        program = make_program(
            retentions=[
                Retention(applies_to=["gl"], type=RetentionType.CAPTIVE, amount=1_000_000)
            ]
        )
        assert "retention-vehicle" in error_codes(program)

    def test_captive_with_vehicle_is_clean(self) -> None:
        program = make_program(
            retentions=[
                Retention(
                    applies_to=["gl"],
                    type=RetentionType.CAPTIVE,
                    amount=1_000_000,
                    vehicle="Test Re",
                )
            ]
        )
        assert "retention-vehicle" not in codes(program)

    def test_line_without_retention_is_warning(self) -> None:
        program = make_program(retentions=[])
        assert "line-no-retention" in warning_codes(program)

    def test_unknown_retention_type_rejected_at_model_layer(self) -> None:
        data = program_to_jsonable(make_program())
        data["retentions"][0]["type"] = "franchise"
        with pytest.raises(ValueError):
            Program.model_validate(data)


class TestSeededSample:
    """Take the real sample, seed it with defects, assert each is caught."""

    def seeded(self, mutate) -> set[str]:
        program = load_program(SAMPLE)
        data = program_to_jsonable(program)
        mutate(data)
        from towerkit.model import program_from_jsonable

        return {d.code for d in validate_program(program_from_jsonable(data)).errors}

    def test_gap_overlap_oversign_all_caught(self) -> None:
        def seed(data: dict) -> None:
            for lyr in data["layers"]:
                if lyr["id"] == "xs-1":
                    lyr["attach"] = 30_000_000  # gap below (was contiguous at 27M)
                if lyr["id"] == "xs-2":
                    lyr["attach"] = 50_000_000  # overlap with xs-1 top at 55M
                if lyr["id"] == "umbrella":
                    lyr["participants"][0]["share"] = 0.8  # 0.8 + 0.4 = over-signed

        found = self.seeded(seed)
        assert {"line-gap", "line-overlap", "layer-oversigned"} <= found

    def test_sample_itself_has_no_errors(self) -> None:
        program, diags = validate_file(SAMPLE)
        assert program is not None
        assert diags.ok
        # exactly the one deliberate warning: 3rd Excess is 80% placed
        assert [d.code for d in diags.warnings] == ["layer-unplaced"]
        assert "$20,000,000" in diags.warnings[0].message


class TestSchemaValidation:
    def test_sample_passes_schema(self) -> None:
        data = json.loads(SAMPLE.read_text())
        assert validate_against_schema(data) == []

    def test_float_money_fails_schema(self) -> None:
        data = json.loads(SAMPLE.read_text())
        data["layers"][0]["attach"] = 0.5
        assert any("attach" in d.message for d in validate_against_schema(data))

    def test_unknown_key_fails_schema(self) -> None:
        data = json.loads(SAMPLE.read_text())
        data["layers"][0]["colour"] = "red"
        assert validate_against_schema(data)

    def test_packaged_schema_matches_frozen_copy(self) -> None:
        frozen = (REPO / "schema" / "program.schema.json").read_bytes()
        packaged = (REPO / "src" / "towerkit" / "schema" / "program.schema.json").read_bytes()
        assert frozen == packaged, "run: cp schema/program.schema.json src/towerkit/schema/"

    def test_every_program_in_programs_dir_is_valid(self) -> None:
        for path in sorted((REPO / "programs").glob("*.json")):
            program, diags = validate_file(path)
            assert program is not None, f"{path} failed to load"
            assert diags.ok, f"{path}: {[str(d) for d in diags.errors]}"


class TestCliExitCodes:
    def run_validate(self, *paths: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "towerkit.cli", "validate", *map(str, paths)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )

    def test_valid_file_exits_zero(self) -> None:
        assert self.run_validate(SAMPLE).returncode == 0

    def test_invalid_file_exits_nonzero(self, tmp_path: Path) -> None:
        program = load_program(SAMPLE)
        data = program_to_jsonable(program)
        data["layers"] = [lyr for lyr in data["layers"] if lyr["id"] != "umbrella"]  # gap
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(data))
        result = self.run_validate(bad)
        assert result.returncode == 1
        assert "GAP" in result.stdout

    def test_validation_survives_python_O(self, tmp_path: Path) -> None:
        # The prototype's `assert not errs` was stripped under -O. Prove ours is not.
        program = load_program(SAMPLE)
        data = program_to_jsonable(program)
        data["layers"] = [lyr for lyr in data["layers"] if lyr["id"] != "umbrella"]
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(data))
        result = subprocess.run(
            [sys.executable, "-O", "-m", "towerkit.cli", "validate", str(bad)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 1
