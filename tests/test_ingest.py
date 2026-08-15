"""ingest: drafts, tower-text parsing, tabular rows, template round-trips."""

from datetime import date

import pytest

from towerkit.ingest import DraftProgram
from towerkit.model import Layer, Line, Participant, Period, Placement
from towerkit.validate import ProgramInvalidError


def _complete_draft() -> DraftProgram:
    draft = DraftProgram(insured="Atomic Industries", program="Property")
    draft.period = Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    draft.lines = [Line(id="cover", name="Property")]
    draft.layers = [
        Layer(
            id="primary", name="Primary", applies_to=["cover"], attach=0,
            limit=10_000_000,
            participants=[Participant(carrier="Chubb", share_bps=10_000)],
        )
    ]
    return draft


def test_complete_draft_becomes_program() -> None:
    program = _complete_draft().to_program()
    assert program.insured == "Atomic Industries"
    assert program.placement is Placement.PROPOSED
    assert program.layers[0].limit == 10_000_000


def test_draft_missing_period_refuses() -> None:
    draft = _complete_draft()
    draft.period = None
    with pytest.raises(ProgramInvalidError) as exc:
        draft.to_program()
    assert any("period" in d.message for d in exc.value.diagnostics.errors)


def test_draft_missing_insured_refuses() -> None:
    draft = _complete_draft()
    draft.insured = "  "
    with pytest.raises(ProgramInvalidError):
        draft.to_program()


def test_draft_runs_full_validation() -> None:
    draft = _complete_draft()
    draft.layers[0] = draft.layers[0].model_copy(update={"applies_to": ["nope"]})
    with pytest.raises(ProgramInvalidError):
        draft.to_program()


# --- parse_tower --------------------------------------------------------------

from towerkit.ingest import parse_tower  # noqa: E402
from towerkit.model import RetentionType  # noqa: E402

PASTE = """
Primary 10M — Chubb 100% — 250,000
15M xs 10M — AXA XL 60%, Sompo 40% — 180k
SIR 500k
"""


def test_parse_tower_layers() -> None:
    draft = parse_tower(PASTE, insured="Atomic Industries", program="Property")
    assert [layer.attach for layer in draft.layers] == [0, 10_000_000]
    assert draft.layers[0].limit == 10_000_000
    assert draft.layers[0].premium == 250_000
    assert draft.layers[1].participants[1].carrier == "Sompo"
    assert draft.layers[1].participants[1].share_bps == 4000
    assert draft.retentions[0].type is RetentionType.SIR
    assert draft.retentions[0].amount == 500_000


def test_parse_tower_single_carrier_means_full_share() -> None:
    draft = parse_tower("Primary 5M — Chubb")
    assert draft.layers[0].participants[0].share_bps == 10_000


def test_parse_tower_synthesizes_line_with_warning() -> None:
    draft = parse_tower("Primary 5M — Chubb", program="Casualty")
    assert draft.lines[0].name == "Casualty"
    assert any(d.code == "paste.line" for d in draft.diagnostics.warnings)


def test_parse_tower_bad_line_is_diagnostic_not_crash() -> None:
    draft = parse_tower("Primary 5M — Chubb\ntotal nonsense here")
    assert len(draft.layers) == 1
    assert any("line 2" in d.message for d in draft.diagnostics.errors)


def test_parse_tower_pipe_and_hyphen_separators() -> None:
    draft = parse_tower("5M xs 5M | Zurich 50%, Allianz 50% | 90k")
    assert draft.layers[0].signed_bps == 10_000
    assert draft.layers[0].premium == 90_000


def test_parse_tower_separator_only_line_is_diagnostic_not_crash() -> None:
    draft = parse_tower("Primary 5M — Chubb\n|\n")
    assert len(draft.layers) == 1
    assert any("line 2" in d.message for d in draft.diagnostics.errors)


# --- program_from_rows / rows_from_program -------------------------------------

from towerkit.ingest import program_from_rows, rows_from_program  # noqa: E402

ROWS: list[dict[str, object]] = [
    {"layer": "Primary", "line": "Property", "limit": "10M", "attachment": 0,
     "carrier": "Chubb", "share": "100%", "premium": 250_000,
     "inception": "2026-10-01", "expiry": "2027-10-01"},
    {"layer": "15M xs 10M", "line": "Property", "limit": "15M", "attachment": "10M",
     "carrier": "AXA XL", "share": "60", "premium": "180k",
     "inception": "2026-10-01", "expiry": "2027-10-01"},
    {"layer": "15M xs 10M", "line": "Property", "limit": "15M", "attachment": "10M",
     "carrier": "Sompo", "share": "40",
     "inception": "2026-10-01", "expiry": "2027-10-01"},
]


def test_rows_build_and_merge_layers() -> None:
    draft = program_from_rows(ROWS, insured="Atomic Industries", program="Property")
    assert draft.period == Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    assert len(draft.layers) == 2
    assert draft.layers[1].signed_bps == 10_000
    assert draft.diagnostics.ok
    assert draft.to_program().total_premium() == 430_000


def test_rows_staggered_dates_become_layer_period() -> None:
    rows = [dict(ROWS[0]), dict(ROWS[1]), dict(ROWS[2])]
    rows[1]["inception"], rows[1]["expiry"] = "2027-01-01", "2028-01-01"
    rows[2]["inception"], rows[2]["expiry"] = "2027-01-01", "2028-01-01"
    draft = program_from_rows(rows, insured="A", program="P")
    assert draft.period == Period(start=date(2026, 10, 1), end=date(2027, 10, 1))
    assert draft.layers[1].period == Period(start=date(2027, 1, 1), end=date(2028, 1, 1))


def test_rows_line_name_matching_program_is_reused_not_duplicated() -> None:
    rows = [dict(ROWS[0]), {**ROWS[1], "line": ""}, {**ROWS[2], "line": ""}]
    draft = program_from_rows(rows, insured="A", program="Property")
    assert len(draft.lines) == 1  # "Property" line reused, no duplicate column
    assert draft.layers[1].applies_to == [draft.lines[0].id]


def test_rows_conflicting_band_is_error() -> None:
    rows = [dict(ROWS[1]), {**ROWS[2], "attachment": "12M"}]
    draft = program_from_rows(rows, insured="A", program="P")
    assert any(d.code == "rows.band" for d in draft.diagnostics.errors)


def test_rows_bad_money_is_diagnostic_with_row_number() -> None:
    draft = program_from_rows([{**ROWS[0], "limit": "banana"}], insured="A", program="P")
    assert any("row 1" in d.message for d in draft.diagnostics.errors)


# --- template workbook ----------------------------------------------------------

from pathlib import Path  # noqa: E402

from towerkit.ingest_template import COLUMNS, read_rows, write_template  # noqa: E402


def test_template_round_trips_clean(tmp_path: Path) -> None:
    out = write_template(tmp_path / "template.xlsx")
    rows = read_rows(out)
    draft = program_from_rows(rows, insured="Example Co", program="Property")
    assert draft.diagnostics.ok
    assert draft.to_program().total_limit() == 25_000_000


def test_template_columns_match_canonical() -> None:
    from towerkit.ingest import CANONICAL_FIELDS

    assert tuple(c.key for c in COLUMNS) == CANONICAL_FIELDS


def test_read_rows_rejects_unknown_header(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["layer", "limit", "wibble"])
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="wibble"):
        read_rows(path)


def test_round_trip_program_rows_program() -> None:
    original = program_from_rows(ROWS, insured="Atomic", program="Property").to_program()
    again = program_from_rows(
        rows_from_program(original), insured="Atomic", program="Property"
    ).to_program()
    assert again.period == original.period
    assert [layer.name for layer in again.layers] == [x.name for x in original.layers]
    assert [layer.limit for layer in again.layers] == [x.limit for x in original.layers]
    assert again.layers[1].participants == original.layers[1].participants


class TestDiagnosticsSurvive:
    @staticmethod
    def _draft_with_an_unplaced_layer():
        from datetime import date

        from towerkit.ingest import DraftProgram
        from towerkit.model import Layer, Line, Participant, Period

        draft = DraftProgram(insured="Acme Ltd", program="Casualty")
        draft.period = Period(start=date(2026, 1, 1), end=date(2027, 1, 1))
        draft.lines = [Line(id="gl", name="General Liability", abbr="GL")]
        draft.layers = [
            Layer(
                id="primary",
                name="Primary",
                applies_to=["gl"],
                attach=0,
                limit=10_000_000,
                participants=[Participant(carrier="Chubb", share_bps=8_000)],
            )
        ]
        return draft

    def test_a_successful_build_keeps_its_validation_warnings(self) -> None:
        draft = self._draft_with_an_unplaced_layer()

        program = draft.to_program()

        assert program.insured == "Acme Ltd"
        assert any(
            "unplaced" in str(d) for d in draft.diagnostics.warnings
        ), f"warnings were discarded: {[str(d) for d in draft.diagnostics.items]}"

    def test_a_failed_build_keeps_its_errors_on_the_draft(self) -> None:
        from towerkit.validate import ProgramInvalidError

        draft = self._draft_with_an_unplaced_layer()
        draft.insured = ""  # trips the gate

        with pytest.raises(ProgramInvalidError):
            draft.to_program()

        assert any("insured" in str(d) for d in draft.diagnostics.errors)

    def test_building_twice_does_not_duplicate_diagnostics(self) -> None:
        draft = self._draft_with_an_unplaced_layer()

        draft.to_program()
        first = len(draft.diagnostics.items)
        draft.to_program()

        assert len(draft.diagnostics.items) == first

    def test_a_validation_failure_after_the_gate_also_lands_on_the_draft(self) -> None:
        from towerkit.validate import ProgramInvalidError

        draft = self._draft_with_an_unplaced_layer()
        # Gate passes (insured, program, period all present); the failure
        # comes from validate_program itself, via a bad `applies_to`
        # reference — same shape as test_draft_runs_full_validation.
        draft.layers[0] = draft.layers[0].model_copy(update={"applies_to": ["nope"]})

        with pytest.raises(ProgramInvalidError):
            draft.to_program()

        assert any("nope" in str(d) for d in draft.diagnostics.errors), (
            f"post-gate validation errors were discarded: "
            f"{[str(d) for d in draft.diagnostics.items]}"
        )


# --- import_schedule ----------------------------------------------------------

from towerkit.ingest import import_schedule  # noqa: E402


class TestImportSchedule:
    def test_text_routes_through_parse_tower(self) -> None:
        draft = import_schedule(None, text=PASTE, insured="Atomic", program="Property")
        assert [layer.attach for layer in draft.layers] == [0, 10_000_000]

    def test_txt_file_routes_through_parse_tower(self, tmp_path) -> None:
        src = tmp_path / "sched.txt"
        src.write_text(PASTE, encoding="utf-8")
        draft = import_schedule(src, insured="Atomic", program="Property")
        assert draft.layers[0].limit == 10_000_000

    def test_csv_unknown_column_raises_value_error(self, tmp_path) -> None:
        src = tmp_path / "sched.csv"
        src.write_text("line,mystery\nGL,1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown columns"):
            import_schedule(src)

    def test_period_fallback_parses_human_dates(self) -> None:
        draft = import_schedule(
            None, text=PASTE, insured="Atomic", program="Property",
            inception="Jan 1 2026", expiry="1/1/2027",
        )
        assert draft.period is not None
        assert draft.period.start.isoformat() == "2026-01-01"
