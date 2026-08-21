"""A jurisdiction list as it actually arrives: pasted off a policy.

Commas were the whole entry syntax until 2026-08-21, when Grant pasted a
workers-compensation schedule of bare two-letter codes with no commas in it and
the whole run was stored as ONE state. A policy prints these lists however it
likes — one per line, a space run, slash-separated — and nobody is going to
retype a schedule to suit a parser.

What is asserted here is the pair of rules that make that safe:

* what the table RECOGNISES is normalised to a USPS code, so the same
  jurisdiction is one value however it was typed or sent; and
* what it does not recognise is NEVER guessed at — it travels verbatim so
  `validate` can say it is not a US code. A near-miss silently corrected is a
  coverage fact invented by a parser, which is worse than the refusal it
  replaces.
"""

from __future__ import annotations

import pytest

from towerkit import edit, jurisdictions, validate


class TestSeparators:
    """However the schedule was printed."""

    @pytest.mark.parametrize(
        "text",
        [
            "IL, WI, IN",
            "IL,WI,IN",
            "IL WI IN",
            "IL\nWI\nIN",
            "IL\r\nWI\r\nIN",
            "IL\tWI\tIN",
            "IL; WI; IN",
            "IL/WI/IN",
            "IL | WI | IN",
            "  IL   WI   IN  ",
            "IL,\nWI,\nIN,\n",
        ],
    )
    def test_every_shape_a_policy_prints_reads_the_same(self, text: str) -> None:
        assert edit.parse_states(text) == ["IL", "WI", "IN"]

    def test_the_paste_that_started_this(self) -> None:
        """Bare codes, no commas — one long run, which used to become a single
        twenty-nine character "state"."""
        pasted = "AL AK AZ AR CA CO CT DE FL GA"
        assert edit.parse_states(pasted) == [
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        ]

    def test_order_is_the_order_it_was_given(self) -> None:
        assert edit.parse_states("TX IL AK") == ["TX", "IL", "AK"]


class TestNormalisation:
    def test_a_code_is_stored_upper_cased(self) -> None:
        assert edit.parse_states("ny, nJ") == ["NY", "NJ"]

    def test_a_full_name_resolves_to_its_code(self) -> None:
        assert edit.parse_states("Illinois, wisconsin") == ["IL", "WI"]

    def test_a_multi_word_name_is_one_state_not_two_words(self) -> None:
        """The reason space-splitting cannot be unconditional."""
        assert edit.parse_states("New York") == ["NY"]
        assert edit.parse_states("New Hampshire, Rhode Island") == ["NH", "RI"]

    def test_names_and_codes_mix_in_one_unpunctuated_run(self) -> None:
        """Greedy longest-first over the name table — exact matching, not a
        guess about where the boundaries are."""
        assert edit.parse_states("New York NJ Rhode Island CT") == [
            "NY", "NJ", "RI", "CT",
        ]

    def test_the_district_reaches_the_same_row_by_every_name_it_is_given(self) -> None:
        for text in ("DC", "d.c.", "District of Columbia", "washington d.c."):
            assert edit.parse_states(text) == ["DC"], text

    def test_the_list_form_normalises_where_the_typed_form_does(self) -> None:
        """Both doors, one rule. `{"states": ["ny"]}` used to store "ny" while
        `{"states": "ny"}` stored "NY" — the same field meaning two things
        depending on how it was sent."""
        assert edit.canonical_states(["ny", "illinois"]) == edit.parse_states("ny, illinois")


class TestNothingIsGuessed:
    def test_an_unrecognised_piece_travels_verbatim(self) -> None:
        """So `validate` can name it. See TestTheValidatorStillSpeaks below."""
        assert edit.parse_states("NY, Ontario") == ["NY", "Ontario"]

    def test_a_near_miss_is_not_corrected(self) -> None:
        """No fuzzy matching, deliberately: "Onterio" is a typo a HUMAN must
        fix, and a parser that repaired it would be inventing where cover is
        filed. bookkit's own rule — ambiguous entry is refused, never guessed."""
        assert edit.parse_states("Onterio") == ["Onterio"]
        assert edit.parse_states("Illinios") == ["Illinios"]

    def test_prose_containing_a_state_name_is_not_mined_for_it(self) -> None:
        """All-or-nothing on a space run: half-resolving makes the parser a
        sentence reader."""
        assert edit.parse_states("all states except New York") == [
            "all states except New York"
        ]

    def test_duplicates_are_kept_for_the_validator_to_refuse(self) -> None:
        assert edit.parse_states("NY, NY") == ["NY", "NY"]

    def test_case_differing_duplicates_become_real_duplicates(self) -> None:
        """Normalising turns a duplicate the validator could only catch by
        upper-casing into one that is visibly duplicated in the file."""
        assert edit.parse_states("NY, ny") == ["NY", "NY"]

    def test_empty_and_punctuation_only_input_is_no_states(self) -> None:
        assert edit.parse_states("") == []
        assert edit.parse_states("  ,  ") == []
        assert edit.parse_states(" / ; | ") == []


class TestTheTable:
    def test_the_code_set_the_validator_checks_is_the_same_table(self) -> None:
        """One home for the vocabulary. It used to be a literal in validate.py
        and is now derived from the name table `parse_states` reads."""
        assert validate.US_JURISDICTIONS is jurisdictions.US_JURISDICTIONS
        assert jurisdictions.US_JURISDICTIONS == frozenset(jurisdictions.US_STATES)

    def test_fifty_states_and_the_district(self) -> None:
        assert len(jurisdictions.US_STATES) == 51

    def test_the_territories_stay_out(self) -> None:
        """Absent from the set this replaced, and adding one would silence
        `states-unrecognized` for it — a validation decision, not a refactor's."""
        for code in ("PR", "VI", "GU", "AS", "MP"):
            assert code not in jurisdictions.US_STATES

    def test_the_window_width_is_derived_from_the_longest_name(self) -> None:
        """A name added to the table must not need a second edit to be
        reachable by the greedy matcher."""
        assert jurisdictions.LONGEST_NAME_TOKENS == 3  # District of Columbia

    def test_canonical_refuses_to_answer_for_what_it_does_not_know(self) -> None:
        assert jurisdictions.canonical("il") == "IL"
        assert jurisdictions.canonical(" Illinois ") == "IL"
        assert jurisdictions.canonical("Ontario") is None
        assert jurisdictions.canonical("") is None


class TestTheValidatorStillSpeaks:
    """Normalising at entry must not quiet any check. The monopolistic-fund
    refusal is the one thing this field exists for."""

    def _codes(self, states: list[str]) -> set[str]:
        """Through the suite's own WC helpers, not a hand-built dict — the
        program shape belongs to test_validate.py and a second copy here would
        be a hand-written model table by another name (see conftest)."""
        from test_validate import _stat, _wc_program, codes

        return codes(_wc_program(_stat(states=states)))

    def test_a_monopolistic_state_is_still_an_error_after_normalising(self) -> None:
        codes = self._codes(edit.parse_states("il oh wi"))
        assert "states-monopolistic" in codes

    def test_a_lower_case_monopolistic_state_now_arrives_upper_cased(self) -> None:
        """It was caught before too — the validator upper-cases to compare —
        but the file now says OH, which is what the check reasons about."""
        assert edit.parse_states("oh") == ["OH"]
        assert "states-monopolistic" in self._codes(["OH"])

    def test_an_unrecognised_jurisdiction_still_warns(self) -> None:
        codes = self._codes(edit.parse_states("NY, Ontario"))
        assert "states-unrecognized" in codes

    def test_a_duplicate_is_still_refused_by_name(self) -> None:
        codes = self._codes(edit.parse_states("NY ny"))
        assert "states-duplicate" in codes
