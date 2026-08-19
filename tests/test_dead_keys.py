"""Every key a hint line names must be bound, and every hidden binding must
be named somewhere a user can find it.

The editor demotes most of its keys off the footer and gives them two homes:
the dim per-node hint line above the footer, and `?` help. That is a good
design with one failure mode — the hint text and the BINDINGS list drift
apart, and the editor advertises a key that does nothing. Nothing caught
that here until now; this is the arbiter.

Ported in shape from bookkit's test_dead_keys.py. When it fails, check which
side is wrong before editing: a hint naming an unbound key and a binding no
hint mentions are different bugs with different fixes.
"""

from __future__ import annotations

import re

from towerkit.tui.screens import editor as editor_module
from towerkit.tui.screens.editor import (
    EDITOR_HELP,
    LAYERS_SHEET_HINT,
    NODE_HINTS,
    PARTICIPANTS_SHEET_HINT,
    EditorScreen,
)
from towerkit.tui.widgets.sheet import SheetCellEditor, SheetTable

# hint lines print the key the way a user sees it; BINDINGS spell the same
# key the way Textual names it
DISPLAY_TO_KEYS: dict[str, tuple[str, ...]] = {
    "=": ("equals_sign",),
    ">": ("greater_than_sign",),
    "?": ("question_mark",),
    "[ ]": ("left_square_bracket", "right_square_bracket"),
    "del": ("delete",),
    "esc": ("escape",),
}

_BOLD = re.compile(r"\[b\](.*?)\[/b\]")


def _named_keys(text: str) -> set[str]:
    """The keys a hint line advertises, as displayed."""
    return {match.replace("\\", "").strip() for match in _BOLD.findall(text)}


def _bound_keys() -> set[str]:
    """Every key reachable from the editor: the screen's own bindings plus
    the sheet widgets', which is where i / a / del / tab actually live."""
    keys: set[str] = set()
    for source in (EditorScreen, SheetTable, SheetCellEditor):
        for binding in source.BINDINGS:
            key = binding[0] if isinstance(binding, tuple) else binding.key
            keys.update(part.strip() for part in key.split(","))
    return keys


def _hint_lines() -> dict[str, str]:
    """Every hint line in the editor module, DISCOVERED rather than listed.

    The two sheet hints used to be named here one by one, which makes this
    arbiter blind to the next hint constant somebody adds — the failure mode
    it exists to catch, arriving through the test itself. Any module-level
    `*_HINT` string is a hint line and is checked.
    """
    lines = {f"NODE_HINTS[{kind!r}]": text for kind, text in NODE_HINTS.items()}
    for name, value in vars(editor_module).items():
        if name.endswith("_HINT") and isinstance(value, str):
            lines[name] = value
    assert lines["LAYERS_SHEET_HINT"] == LAYERS_SHEET_HINT  # discovery works
    assert lines["PARTICIPANTS_SHEET_HINT"] == PARTICIPANTS_SHEET_HINT
    return lines


def test_every_key_a_hint_line_names_is_bound() -> None:
    bound = _bound_keys()
    dead = []
    for where, text in _hint_lines().items():
        for display in _named_keys(text):
            expected = DISPLAY_TO_KEYS.get(display, (display,))
            missing = [key for key in expected if key not in bound]
            if missing:
                dead.append(f"{where}: names {display!r}, unbound {missing}")
    assert not dead, "hint lines advertise keys nothing binds:\n  " + "\n  ".join(dead)


def test_every_hidden_editor_binding_is_documented() -> None:
    """A demoted binding that appears in no hint and no help text is
    unreachable in practice — it exists only for whoever read the source."""
    # the two terminal fallbacks for [ / ]: help documents them as the arrow
    # form ("shift+up/down also works"), which no hint line can spell
    exempt = {"shift+up", "shift+down"}
    undocumented = []
    hint_text = "\n".join(_hint_lines().values())
    for binding in EditorScreen.BINDINGS:
        if isinstance(binding, tuple) or binding.show:
            continue  # visible on the footer: self-documenting
        if binding.key in exempt:
            continue
        action = binding.action.split("(")[0]
        display = {
            "equals_sign": "=",
            "greater_than_sign": ">",
            "question_mark": "?",
            "left_square_bracket": "[",
            "right_square_bracket": "]",
            "delete": "del",
        }.get(binding.key, binding.key)
        if display in _named_keys(hint_text):
            continue
        # help prints keys in a left column, but a few ride inline
        # ("u  undo   ctrl+r  redo"), so match the token, not the position
        if re.search(rf"(?:^|\s){re.escape(display)}(?:\s|$)", EDITOR_HELP):
            continue
        undocumented.append(f"{binding.key} -> {action}")
    assert not undocumented, (
        "hidden bindings with no hint line and no help entry:\n  "
        + "\n  ".join(undocumented)
    )


def test_the_participants_jump_is_advertised_where_the_cost_was() -> None:
    """`p` is the whole point of the change — it has to be named on the
    layer node, on the layers sheet (where the picker lands) and in help,
    or it is a key only the source knows about."""
    assert "p" in _named_keys(NODE_HINTS["layer"])
    assert "p" in _named_keys(NODE_HINTS["layers-group"])
    assert "p" in _named_keys(NODE_HINTS["program"])
    assert "p" in _named_keys(LAYERS_SHEET_HINT)
    assert re.search(r"^\s*p\s", EDITOR_HELP, re.MULTILINE), EDITOR_HELP


def test_the_named_limits_jump_is_advertised_the_same_way() -> None:
    """`n` reaches the one field that needs a grid rather than an input. It
    is advertised exactly where `p` is — the layer node, the group, the
    program root, the layers sheet (where the picker lands) and help — or it
    is a key only the source knows about."""
    assert "n" in _named_keys(NODE_HINTS["layer"])
    assert "n" in _named_keys(NODE_HINTS["layers-group"])
    assert "n" in _named_keys(NODE_HINTS["program"])
    assert "n" in _named_keys(LAYERS_SHEET_HINT)
    assert re.search(r"^\s*n\s", EDITOR_HELP, re.MULTILINE), EDITOR_HELP


# `#key-hint` is one row of a height-1 Static with a column of padding either
# side, so at a 140-column terminal it has 138 columns of content and anything
# past them is not scrolled — it is gone, silently, ending with the `? all
# keys` escape hatch. bookkit learned this on its footer; the same widget
# shape is here. The live-widget proof of the 138 sits in
# tests/test_layer_detail_editing.py::TestHintLinesFit.
HINT_COLUMNS = 138

# what _refresh_hint appends to every NODE_HINTS entry before printing it
HINT_SUFFIX = " · ? all keys"


def _plain(text: str) -> str:
    return re.sub(r"\[/?b\]", "", text).replace("\\", "")


def test_no_hint_line_overflows_the_row_it_is_printed_on() -> None:
    too_wide = []
    for where, text in _hint_lines().items():
        line = _plain(text)
        if where.startswith("NODE_HINTS"):
            line += HINT_SUFFIX
        if len(line) > HINT_COLUMNS:
            too_wide.append(f"{where}: {len(line)} columns > {HINT_COLUMNS}")
    assert not too_wide, (
        "hint lines cropped at 140 columns (the tail, where `? all keys` "
        "lives, is what disappears):\n  " + "\n  ".join(too_wide)
    )
