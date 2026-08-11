"""Renewal comparison logic and the renewal renderer."""

from pathlib import Path

import pytest

from towerkit.compare import LAPSED, NEW, RENEWED, compare_programs
from towerkit.model import Placement, load_program
from towerkit.render.mpl_renewal import render_renewal
from towerkit.theme import load_theme

REPO = Path(__file__).parent.parent
OLD = REPO / "programs" / "atomic-2026.json"
NEW_FILE = REPO / "programs" / "atomic-2027.json"


@pytest.fixture(scope="module")
def delta():
    return compare_programs(load_program(OLD), load_program(NEW_FILE))


class TestCloneAsRenewal:
    def test_bumps_period_and_marks_proposed(self) -> None:
        program = load_program(OLD)
        clone = program.clone_as_renewal()
        assert clone.period.start.year == program.period.start.year + 1
        assert clone.period.end.year == program.period.end.year + 1
        assert clone.placement is Placement.PROPOSED
        assert program.placement is Placement.BOUND  # original untouched

    def test_deep_copy(self) -> None:
        program = load_program(OLD)
        clone = program.clone_as_renewal()
        clone.layers[0].participants[0].carrier = "Changed"
        assert program.layers[0].participants[0].carrier != "Changed"


class TestCompare:
    def row(self, delta, carrier, layer_id):
        return next(
            r for r in delta.rows if r.carrier == carrier and r.layer_id == layer_id
        )

    def test_lapsed_carrier(self, delta) -> None:
        liberty = self.row(delta, "Liberty", "xs-2")
        assert liberty.status == LAPSED
        assert liberty.share_new_bps is None
        assert liberty.share_old_bps == 2_500
        assert liberty.line_delta == -12_500_000  # lost 25% of a $50M layer

    def test_new_carrier(self, delta) -> None:
        fairfax = self.row(delta, "Fairfax", "xs-3")
        assert fairfax.status == NEW
        assert fairfax.share_old_bps is None
        assert fairfax.line_new == 20_000_000  # 20% of $100M

    def test_renewed_with_share_change(self, delta) -> None:
        swiss = self.row(delta, "Swiss Re", "xs-2")
        assert swiss.status == RENEWED
        assert swiss.share_delta_bps == 1_500  # 40% → 55%

    def test_headline_metrics_are_exact_integers(self, delta) -> None:
        assert delta.premium_old == 25_300_000
        assert delta.premium_new == 26_850_000
        assert delta.premium_delta == 1_550_000
        assert delta.limit_old == delta.limit_new == 381_000_000

    def test_rows_sorted_by_premium_impact(self, delta) -> None:
        impacts = [abs(r.premium_delta) for r in delta.rows]
        assert impacts == sorted(impacts, reverse=True)

    def test_premium_shares_stay_integer(self, delta) -> None:
        for row in delta.rows:
            for value in (row.premium_old, row.premium_new):
                assert value is None or isinstance(value, int)


class TestRenewalRender:
    def test_renders_from_two_json_files_deterministically(self, tmp_path) -> None:
        expiring, proposed = load_program(OLD), load_program(NEW_FILE)
        theme = load_theme(REPO / "themes" / "marsh.json")
        a = render_renewal(expiring, proposed, theme, tmp_path / "a", "r", ["svg"])[0]
        b = render_renewal(expiring, proposed, theme, tmp_path / "b", "r", ["svg"])[0]
        assert a.read_bytes() == b.read_bytes()
        text = a.read_text()
        assert "LAPSED" in text and "NEW" in text
        assert "renewal comparison" in text


class TestPremiumToggle:
    def test_no_premiums_hides_every_premium_figure(self, tmp_path) -> None:
        expiring, proposed = load_program(OLD), load_program(NEW_FILE)
        theme = load_theme(REPO / "themes" / "marsh.json")
        out = render_renewal(
            expiring, proposed, theme, tmp_path, "r", ["svg"], show_premiums=False
        )[0]
        text = out.read_text()
        assert "Premium" not in text
        assert "LAPSED" in text  # the structural story is still there
