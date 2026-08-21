"""US jurisdictions — the ONE table, and the one rule for reading one.

Split out of `validate.py` when a second module needed it. The set of codes
lived there as a literal beside the monopolistic-fund rule that reads it;
`edit.parse_states` now needs the same vocabulary to tell a pasted state list
apart from prose, and a second copy of fifty-one codes is the copy that
quietly differs.

What lives here is VOCABULARY — which jurisdictions exist and what they are
called. The RULES about them stay where they are enforced: the monopolistic
funds are a fact about workers-compensation cover and belong beside the check
that refuses them (`validate.MONOPOLISTIC_STATES`), not in a lookup table.

Territories (PR, VI, GU, AS, MP) are deliberately ABSENT, exactly as they were
from the set this replaces. Adding one would silence `states-unrecognized` for
it, which is a validation decision and not a refactor's to make.
"""

from __future__ import annotations

# code -> the name a policy schedule prints. Derived from this, below: the
# code set the validator checks against, and the name lookup the parser uses.
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

US_JURISDICTIONS = frozenset(US_STATES)

# name (lower-cased) -> code. "Washington, D.C." and "D.C." reach the same row
# as "District of Columbia"; nothing else is aliased, because an alias is a
# guess about what somebody meant and this table refuses to make one.
_BY_NAME: dict[str, str] = {name.lower(): code for code, name in US_STATES.items()}
_BY_NAME.update({"d.c.": "DC", "dc": "DC", "washington d.c.": "DC"})

# The longest name in tokens ("District of Columbia"), so a greedy longest-first
# match knows how wide a window to try. Derived, never a literal — a name added
# above must not need a second edit here.
LONGEST_NAME_TOKENS = max(len(name.split()) for name in US_STATES.values())


def canonical(token: str) -> str | None:
    """The USPS code for one token, or None when it names no jurisdiction.

    EXACT matching only, case-insensitive: a two-letter code in any case, or a
    full state name in any case. Nothing fuzzy, and nothing is guessed — "Onterio"
    comes back None and travels on verbatim so `validate` can say it is not a
    US code, which is the honest outcome. A near-miss silently corrected is the
    failure mode this project refuses everywhere else it accepts typed input.
    """
    stripped = token.strip()
    if not stripped:
        return None
    upper = stripped.upper()
    if upper in US_STATES:
        return upper
    return _BY_NAME.get(stripped.lower())
