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


def _stat(**kw) -> Layer:
    base = dict(
        id="wc-stat", name="Workers Compensation", applies_to=["wc"],
        attach=0, limit=0, statutory=True,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
    )
    return Layer(**{**base, **kw})


def _wc_program(*layers: Layer) -> Program:
    return make_program(
        lines=[Line(id="wc", name="Workers Compensation")],
        layers=list(layers),
        retentions=[],
    )


class TestStatutoryRules:
    def test_statutory_layer_is_exempt_from_the_positive_limit_rule(self) -> None:
        assert "layer-limit" not in codes(_wc_program(_stat()))

    def test_statutory_with_a_limit_is_an_error(self) -> None:
        assert "statutory-limit" in codes(_wc_program(_stat(limit=1_000_000)))

    def test_statutory_with_an_attachment_is_an_error(self) -> None:
        assert "statutory-attach" in codes(_wc_program(_stat(attach=500_000)))

    def test_statutory_cannot_follow_underlying(self) -> None:
        assert "statutory-follows" in codes(_wc_program(_stat(follows_underlying=True)))

    def test_statutory_line_reports_no_phantom_gap(self) -> None:
        """A line covered only by a statutory layer is fully covered. Left alone,
        the limit > 0 filter drops it from the stack and the line reads as empty —
        which would tint the WC column danger-red in the live preview."""
        found = codes(_wc_program(_stat()))
        assert "line-empty" not in found
        assert "line-base" not in found
        assert "line-gap" not in found

    def test_statutory_line_rejects_a_second_layer(self) -> None:
        program = _wc_program(
            _stat(),
            Layer(
                id="wc-xs", name="WC Excess", applies_to=["wc"],
                attach=0, limit=1_000_000,
                participants=[Participant(carrier="A", share_bps=10_000)],
            ),
        )
        assert "statutory-line-shared" in codes(program)

    def test_two_statutory_layers_on_one_line_is_an_error(self) -> None:
        """Two statutory layers stacked on one line have no dollar layer to
        trip the shared-column rule the old way — both still (y0, y1) = (0, 1)
        and silently draw over each other, so the second one must trip it too."""
        program = _wc_program(
            _stat(id="wc-stat", name="WC Statutory"),
            _stat(id="wc-stat-2", name="WC Statutory (dup)"),
        )
        assert "statutory-line-shared" in codes(program)

    def test_statutory_unplaced_is_reported_as_a_share_not_dollars(self) -> None:
        program = _wc_program(
            _stat(participants=[Participant(carrier="Travelers", share_bps=6_000)])
        )
        messages = [d.message for d in validate_program(program).items]
        unplaced = next(m for m in messages if "open" in m or "unplaced" in m)
        assert "$0" not in unplaced
        assert "40% open" in unplaced


class TestLayerPolicyData:
    def test_backwards_period_is_error(self) -> None:
        from datetime import date as _date

        from towerkit.model import Period as _Period

        program = load_program(SAMPLE)
        program.layers[0].period = _Period(
            start=_date(2026, 6, 1), end=_date(2026, 6, 1)
        )
        assert "layer-period" in {d.code for d in validate_program(program).errors}

    def test_backwards_program_period_is_error(self) -> None:
        program = make_program()
        program.period = Period(start=date(2027, 1, 1), end=date(2026, 1, 1))
        assert "program-period" in error_codes(program)

    def test_policy_number_and_period_round_trip(self) -> None:
        program = load_program(SAMPLE)
        umbrella = next(ly for ly in program.layers if ly.id == "umbrella")
        assert umbrella.policy_number is not None
        prop = next(ly for ly in program.layers if ly.id == "primary-pr")
        assert prop.period is not None and prop.period.start.month == 4


# --- layer detail fields (2026-08-18) ----------------------------------------


def _el_layer(**kw) -> Layer:
    """A dollar-limited layer on the `wc` line — the shape Employers Liability
    takes. Deliberately NOT statutory: it is the counterexample every
    statutory-only rule below has to refuse."""
    base = dict(
        id="el", name="Employers Liability", applies_to=["wc"],
        attach=0, limit=1_000_000,
        participants=[Participant(carrier="Travelers", share_bps=10_000)],
    )
    return Layer(**{**base, **kw})


class TestStatesRules:
    def test_states_on_a_statutory_layer_are_clean(self) -> None:
        program = _wc_program(_stat(states=["NY", "NJ", "CT"]))
        assert error_codes(program) == set()

    def test_a_monopolistic_state_is_refused(self) -> None:
        """ND, OH, WA and WY have monopolistic state funds: a private policy
        cannot cover them, so naming one is a coverage error, not a note."""
        for code in ("ND", "OH", "WA", "WY"):
            program = _wc_program(_stat(states=["NY", code]))
            assert "states-monopolistic" in error_codes(program), code

    def test_the_monopolistic_state_is_named_in_the_message(self) -> None:
        program = _wc_program(_stat(states=["NY", "OH"]))
        message = next(
            d.message for d in validate_program(program).errors
            if d.code == "states-monopolistic"
        )
        assert "OH" in message and "NY" not in message

    def test_a_non_monopolistic_state_is_not_refused(self) -> None:
        program = _wc_program(_stat(states=["NY", "WV", "CA"]))
        assert "states-monopolistic" not in error_codes(program)

    def test_states_on_a_non_statutory_layer_are_refused(self) -> None:
        """Cover in a state we are not filed in is worth nothing — which makes
        this a coverage fact about STATUTORY cover. On a dollar-limited layer
        it means nothing at all, and an unrefused meaningless field becomes a
        general-purpose note by accident."""
        program = _wc_program(_el_layer(states=["NY"]))
        assert "states-non-statutory" in error_codes(program)

    def test_a_dollar_layer_with_no_states_is_clean(self) -> None:
        assert "states-non-statutory" not in codes(_wc_program(_el_layer()))

    def test_a_repeated_state_is_refused(self) -> None:
        assert "states-duplicate" in error_codes(_wc_program(_stat(states=["NY", "NY"])))

    def test_case_does_not_smuggle_a_monopolistic_state_past_the_check(self) -> None:
        """A lowercase code that slipped past an exact-match set would leave the
        one check this field exists for silently unapplied."""
        assert "states-monopolistic" in error_codes(_wc_program(_stat(states=["oh"])))

    def test_an_unrecognised_code_warns_that_the_check_did_not_run(self) -> None:
        """towerkit knows US two-letter codes and nothing else. "Ohio" is not
        one, so the monopolistic check cannot vouch for it — silence there
        would be the failure the check exists to prevent. A warning, not an
        error: a non-US programme is not invalid, it is unchecked."""
        program = _wc_program(_stat(states=["Ohio"]))
        assert "states-unrecognized" in warning_codes(program)
        assert "states-unrecognized" not in error_codes(program)

    def test_a_recognised_code_does_not_warn(self) -> None:
        assert "states-unrecognized" not in codes(_wc_program(_stat(states=["NY", "DC"])))


class TestNamedLimitRules:
    def test_named_limits_on_a_dollar_layer_are_clean(self) -> None:
        program = _wc_program(
            _el_layer(named_limits=[
                {"name": "Each Accident", "amount": 1_000_000},
                {"name": "Disease - Each Employee", "amount": 1_000_000},
                {"name": "Disease - Policy Limit", "amount": 1_000_000},
            ])
        )
        assert error_codes(program) == set()

    def test_named_limits_on_a_statutory_layer_are_refused(self) -> None:
        """`statutory ⇒ no dollar limit` is the invariant the whole design
        rests on. Named dollar amounts on a statutory layer are dollar limits
        by another name."""
        program = _wc_program(
            _stat(named_limits=[{"name": "Each Accident", "amount": 1_000_000}])
        )
        assert "statutory-named-limits" in error_codes(program)

    def test_two_named_limits_sharing_a_name_are_refused(self) -> None:
        program = _wc_program(
            _el_layer(named_limits=[
                {"name": "Each Accident", "amount": 1_000_000},
                {"name": "Each Accident", "amount": 2_000_000},
            ])
        )
        assert "named-limit-duplicate" in error_codes(program)

    def test_limits_prose_and_named_limits_together_are_refused(self) -> None:
        """limits_detail is exported verbatim and wins over everything
        composed. Carrying both would discard the structured data in silence,
        which is exactly how a field comes to look broken to whoever set it."""
        program = _wc_program(
            _el_layer(
                limits_detail="Each Accident $1,000,000",
                named_limits=[{"name": "Each Accident", "amount": 1_000_000}],
            )
        )
        assert "limits-detail-conflict" in error_codes(program)

    def test_either_one_alone_is_clean(self) -> None:
        prose = _wc_program(_el_layer(limits_detail="Each Accident $1,000,000"))
        structured = _wc_program(
            _el_layer(named_limits=[{"name": "Each Accident", "amount": 1_000_000}])
        )
        assert "limits-detail-conflict" not in codes(prose)
        assert "limits-detail-conflict" not in codes(structured)


class TestPremiumDetailRules:
    def test_premium_detail_on_a_zero_premium_is_clean(self) -> None:
        program = _wc_program(
            _el_layer(premium=0, premium_detail="Included with Part A")
        )
        assert error_codes(program) == set()

    def test_premium_detail_beside_a_real_premium_is_refused(self) -> None:
        """The premium cell holds a NUMBER that the sheet's subtotals add up.
        premiumDetail replaces only the word a ZERO prints; on a priced layer
        it would never render, and a field that silently does nothing is the
        general-purpose note this rule exists to prevent."""
        program = _wc_program(
            _el_layer(premium=50_000, premium_detail="Included with Part A")
        )
        assert "premium-detail-conflict" in error_codes(program)

    def test_premium_detail_with_no_premium_at_all_is_refused(self) -> None:
        """An absent premium prints a BLANK cell, not "Included" — there is no
        word for the detail to qualify, so it would not render either."""
        program = _wc_program(_el_layer(premium=None, premium_detail="Included with Part A"))
        assert "premium-detail-conflict" in error_codes(program)

    def test_a_zero_premium_without_detail_is_clean(self) -> None:
        assert "premium-detail-conflict" not in codes(_wc_program(_el_layer(premium=0)))


class TestNewFieldsAgainstTheSchema:
    def test_the_schema_accepts_all_three(self, tmp_path) -> None:
        """validate_file goes through jsonschema, which the canonical round
        trip never touches — a key added to model.py alone passes every other
        test in this repo and fails at runtime on a real file."""
        program = _wc_program(
            _stat(states=["NY", "NJ"]),
        )
        program.layers.append(
            _el_layer(
                premium=0,
                premium_detail="Included with Part A",
                named_limits=[{"name": "Each Accident", "amount": 1_000_000}],
            )
        )
        target = tmp_path / "wc.json"
        from towerkit.model import dump_program

        dump_program(program, target)
        text = target.read_text()
        assert '"states"' in text and '"namedLimits"' in text and '"premiumDetail"' in text
        _, diags = validate_file(target)
        assert [d.message for d in diags.items if d.code == "schema"] == []


class TestRenderTheme:
    """`render.theme` names a FILE, and `towerctl render` reads it with no net
    underneath. Round six (2026-08-20): any junk string wrote clean, validated
    clean (exit 0), then crashed the renderer with a raw JSONDecodeError — the
    write-says-clean genus, one field over from currency. The check lives in
    `validate_file`, not `validate_program`: it needs the filesystem, and the
    semantic pass stays pure."""

    def _file(self, tmp_path, theme: str):
        from towerkit.model import RenderSettings, dumps_program

        program = make_program(render=RenderSettings(theme=theme))
        path = tmp_path / "p.json"
        path.write_text(dumps_program(program), encoding="utf-8")
        return path

    def test_a_theme_that_cannot_load_is_an_error(self, tmp_path) -> None:
        _, diags = validate_file(self._file(tmp_path, "no/such/theme.json"))
        codes = [d.code for d in diags.errors]
        assert "render-theme" in codes

    def test_an_absolute_theme_path_is_an_error_even_when_it_loads(self, tmp_path) -> None:
        """Program files are portable by contract (CLAUDE.md: theme paths stay
        RELATIVE); an absolute path renders here and breaks on the next
        machine, which is worse than breaking now."""
        from importlib import resources

        theme = tmp_path / "t.json"
        theme.write_text(
            resources.files("towerkit").joinpath("themes/default.json").read_text("utf-8"),
            encoding="utf-8",
        )
        _, diags = validate_file(self._file(tmp_path, str(theme)))
        offending = [d for d in diags.errors if d.code == "render-theme"]
        assert offending and "relative" in offending[0].message

    def test_a_loadable_relative_theme_is_clean(self, tmp_path, monkeypatch) -> None:
        """Passed trivially at RED, so it owes mutation evidence (CLAUDE.md,
        2026-08-14). Drill: `_check_render_theme` mutated to error
        unconditionally — `AssertionError: assert not [Diagnostic(...,
        code='render-theme', ...)]`. Restored."""
        from importlib import resources

        (tmp_path / "themes").mkdir()
        (tmp_path / "themes" / "t.json").write_text(
            resources.files("towerkit").joinpath("themes/default.json").read_text("utf-8"),
            encoding="utf-8",
        )
        path = self._file(tmp_path, "themes/t.json")
        monkeypatch.chdir(tmp_path)
        _, diags = validate_file(path)
        assert not [d for d in diags.items if d.code == "render-theme"]
