"""Canonical serialisation: load + save with no edits produces zero diff."""

from pathlib import Path

from towerkit.model import (
    Layer,
    Participant,
    Program,
    dumps_program,
    load_program,
    loads_program,
)

SAMPLE = Path(__file__).parent.parent / "programs" / "atomic-2026.json"


def test_sample_round_trips_byte_identical() -> None:
    original = SAMPLE.read_text(encoding="utf-8")
    assert dumps_program(loads_program(original)) == original


def test_double_round_trip_is_fixed_point() -> None:
    once = dumps_program(load_program(SAMPLE))
    assert dumps_program(loads_program(once)) == once


def test_shares_survive_round_trip_including_thirds() -> None:
    program = load_program(SAMPLE)
    xs_pr = next(layer for layer in program.layers if layer.id == "xs-pr")
    assert [p.share_bps for p in xs_pr.participants] == [3334, 3333, 3333]
    reloaded = loads_program(dumps_program(program))
    xs_pr2 = next(layer for layer in reloaded.layers if layer.id == "xs-pr")
    assert [p.share_bps for p in xs_pr2.participants] == [3334, 3333, 3333]


def test_money_is_never_float_in_output() -> None:
    text = dumps_program(load_program(SAMPLE))
    for line in text.splitlines():
        for key in ('"attach"', '"limit"', '"premium"', '"amount"', '"aggregate"'):
            if key in line:
                value = line.split(":", 1)[1].strip().rstrip(",")
                assert "." not in value, f"money serialised as float: {line.strip()}"


def test_full_share_serialises_as_integer_one() -> None:
    program = Program.model_validate(
        {
            "insured": "X",
            "program": "Y",
            "placement": "bound",
            "period": {"start": "2026-01-01", "end": "2027-01-01"},
            "lines": [{"id": "gl", "name": "GL"}],
            "layers": [
                Layer(
                    id="l1",
                    name="Primary",
                    applies_to=["gl"],
                    attach=0,
                    limit=1_000_000,
                    participants=[Participant(carrier="Zurich", share_bps=10_000)],
                ).model_dump(by_alias=True)
            ],
        }
    )
    assert '"share": 1\n' in dumps_program(program) or '"share": 1,' in dumps_program(program)


def test_unicode_survives() -> None:
    program = load_program(SAMPLE)
    program2 = loads_program(dumps_program(program))
    assert program2.insured == program.insured


def test_render_settings_round_trip(tmp_path) -> None:
    from towerkit.model import RenderSettings, dump_program

    program = load_program(SAMPLE)
    program.render = RenderSettings(
        theme="themes/marsh.json", show_totals=True,
        show_premiums=False, cell_premiums=True,
    )
    target = tmp_path / "p.json"
    dump_program(program, target)
    text = target.read_text()
    assert '"theme": "themes/marsh.json"' in text
    reloaded = load_program(target)
    assert reloaded.render is not None
    assert reloaded.render.cell_premiums is True
    assert reloaded.render.show_premiums is False


def test_soi_schematic_round_trips_and_is_omitted_when_off(tmp_path) -> None:
    from towerkit.model import RenderSettings, dump_program
    from towerkit.validate import validate_file

    program = load_program(SAMPLE)
    program.render = RenderSettings(soi_schematic=True)
    target = tmp_path / "p.json"
    dump_program(program, target)
    assert '"soiSchematic": true' in target.read_text()
    reloaded = load_program(target)
    assert reloaded.render is not None and reloaded.render.soi_schematic is True
    _, diags = validate_file(target)
    assert diags.ok  # the schema accepts the new key

    # OFF is the default and is NOT written — existing files re-save unchanged
    program.render = RenderSettings()
    dump_program(program, target)
    assert "soiSchematic" not in target.read_text()


def test_soi_detail_fields_round_trip() -> None:
    program = load_program(SAMPLE)
    layer = program.layers[0]
    layer.limits_detail = "Each Occurrence $1,000,000; Med Pay $5,000"
    layer.retention_detail = "SIR $250,000"
    text = dumps_program(program)
    reloaded = loads_program(text)
    assert reloaded.layers[0].limits_detail == "Each Occurrence $1,000,000; Med Pay $5,000"
    assert reloaded.layers[0].retention_detail == "SIR $250,000"


def test_soi_detail_keys_sit_between_premium_and_participants() -> None:
    program = load_program(SAMPLE)
    program.layers[0].premium = 12_345  # ensure "premium" appears in this layer
    program.layers[0].limits_detail = "L"
    program.layers[0].retention_detail = "R"
    text = dumps_program(program)
    block = text[text.index('"layers"'):]
    assert block.index('"limitsDetail"') < block.index('"retentionDetail"')
    assert block.index('"premium"') < block.index('"limitsDetail"') < block.index('"participants"')


def _program_json(layer_extra: str) -> str:
    return (
        '{\n'
        '  "$schema": "https://towerkit.dev/schema/program.schema.json",\n'
        '  "insured": "Acme",\n'
        '  "program": "Casualty",\n'
        '  "placement": "bound",\n'
        '  "period": {\n'
        '    "start": "2026-01-01",\n'
        '    "end": "2027-01-01"\n'
        '  },\n'
        '  "currency": "USD",\n'
        '  "lines": [\n'
        '    {\n'
        '      "id": "wc",\n'
        '      "name": "Workers Compensation"\n'
        '    }\n'
        '  ],\n'
        '  "layers": [\n'
        '    {\n'
        '      "id": "wc-stat",\n'
        '      "name": "Workers Compensation",\n'
        '      "appliesTo": [\n'
        '        "wc"\n'
        '      ],\n'
        '      "attach": 0,\n'
        '      "limit": 0,\n'
        f'{layer_extra}'
        '      "participants": [\n'
        '        {\n'
        '          "carrier": "Travelers",\n'
        '          "share": 1\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "retentions": [],\n'
        '  "sublimits": []\n'
        '}\n'
    )


def test_statutory_round_trips_zero_diff() -> None:
    text = _program_json('      "statutory": true,\n')
    assert dumps_program(loads_program(text)) == text


def test_statutory_omitted_when_false() -> None:
    """The followsUnderlying precedent: a program that does not use the
    feature must not gain the key, so untouched files re-save byte-identically
    and older wheels keep loading them."""
    text = _program_json("")
    assert dumps_program(loads_program(text)) == text
    assert "statutory" not in text


def test_statutory_key_sits_after_limit() -> None:
    text = _program_json('      "statutory": true,\n')
    out = dumps_program(loads_program(text))
    assert out.index('"limit"') < out.index('"statutory"') < out.index('"participants"')


# --- layer detail fields: states / namedLimits / premiumDetail ---------------
#
# All three are ADDITIVE and OPTIONAL. The rules they must obey are the
# followsUnderlying/statutory precedent: absent means the key is not written
# at all, so every existing file re-saves byte-identically and an older wheel
# only rejects a file that actually USES the feature.


def test_old_file_round_trips_byte_identical_through_the_new_fields() -> None:
    """The load-bearing one. A file written before any of these fields existed
    must come back out of the new code byte for byte — if `_ordered` gained a
    key that is emitted as an empty list rather than dropped, EVERY stored
    program's canonical form changes and every one of them looks dirty."""
    original = SAMPLE.read_text(encoding="utf-8")
    assert dumps_program(loads_program(original)) == original
    for key in ("states", "namedLimits", "premiumDetail"):
        assert key not in original


def test_states_round_trip_zero_diff() -> None:
    text = _program_json('      "statutory": true,\n      "states": [\n        "NY",\n'
                         '        "NJ"\n      ],\n')
    assert dumps_program(loads_program(text)) == text


def test_states_preserve_file_order_never_sorted() -> None:
    """The broker's order is data. Sorting it is a silent rewrite of a file
    nobody edited, and it would show up as a diff on every save."""
    text = _program_json('      "statutory": true,\n      "states": [\n        "NY",\n'
                         '        "CT",\n        "NJ"\n      ],\n')
    reloaded = loads_program(text)
    assert reloaded.layers[0].states == ["NY", "CT", "NJ"]
    assert dumps_program(reloaded) == text


def test_states_omitted_when_empty() -> None:
    text = _program_json("")
    assert dumps_program(loads_program(text)) == text
    assert "states" not in text


def test_named_limits_round_trip_zero_diff() -> None:
    text = _program_json(
        '      "namedLimits": [\n'
        '        {\n'
        '          "name": "Each Accident",\n'
        '          "amount": 1000000\n'
        '        },\n'
        '        {\n'
        '          "name": "Disease - Each Employee",\n'
        '          "amount": 1000000\n'
        '        }\n'
        '      ],\n'
    )
    assert dumps_program(loads_program(text)) == text


def test_named_limit_amounts_are_never_floats() -> None:
    text = _program_json(
        '      "namedLimits": [\n'
        '        {\n'
        '          "name": "Each Accident",\n'
        '          "amount": 1000000\n'
        '        }\n'
        '      ],\n'
    )
    out = dumps_program(loads_program(text))
    amount = next(ln for ln in out.splitlines() if '"amount"' in ln)
    assert "." not in amount.split(":", 1)[1]


def test_named_limits_omitted_when_empty() -> None:
    text = _program_json("")
    assert dumps_program(loads_program(text)) == text
    assert "namedLimits" not in text


def test_premium_detail_round_trips_zero_diff() -> None:
    text = _program_json(
        '      "premium": 0,\n      "premiumDetail": "Included with Part A",\n'
    )
    assert dumps_program(loads_program(text)) == text


def test_premium_detail_omitted_when_absent() -> None:
    text = _program_json("")
    assert dumps_program(loads_program(text)) == text
    assert "premiumDetail" not in text


def test_new_layer_keys_sit_in_the_canonical_order() -> None:
    """_ordered's hand-written order is the thing that breaks silently. Pin
    every new key's neighbours, not just its presence."""
    text = _program_json(
        '      "namedLimits": [\n'
        '        {\n'
        '          "name": "Each Accident",\n'
        '          "amount": 1000000\n'
        '        }\n'
        '      ],\n'
        '      "statutory": true,\n'
        '      "states": [\n        "NY"\n      ],\n'
        '      "premium": 0,\n'
        '      "limitsDetail": "L",\n'
        '      "retentionDetail": "R",\n'
        '      "premiumDetail": "Included with Part A",\n'
    )
    out = dumps_program(loads_program(text))
    order = [
        '"limit"', '"namedLimits"', '"statutory"', '"states"', '"premium"',
        '"limitsDetail"', '"retentionDetail"', '"premiumDetail"', '"participants"',
    ]
    found = [out.index(key) for key in order]
    assert found == sorted(found), dict(zip(order, found, strict=True))


def test_canonical_order_refuses_a_field_it_has_not_learned() -> None:
    """The guard behind all of the above: _ordered raises rather than dropping
    a key it does not know, so a future field added to the model without a
    place in the order fails loudly instead of vanishing from the file."""
    import pytest

    from towerkit.model import _LAYER_KEYS, _ordered

    with pytest.raises(RuntimeError, match="canonical key order is missing"):
        _ordered({"id": "x", "notAField": 1}, _LAYER_KEYS)
