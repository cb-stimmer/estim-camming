"""Rules as config data: round trips, checks, and writing config.toml."""

import tomllib
from importlib.resources import files

import pytest

from estim_camming.rules import Rule
from estim_camming.rules_io import (
    RulesFile,
    RulesFileError,
    check_rules,
    rule_to_data,
    rules_to_toml,
    sha256,
    splice_rules,
)

EXAMPLE = (files("estim_camming") / "example_config.toml").read_text()

RULE_SHAPES = [
    {"name": "exact", "tokens": 5, "intensity": 0.3, "duration": 5},
    {"name": "range", "min_tokens": 10, "max_tokens": 20, "intensity": 0.5, "duration": 2.5},
    {
        "name": "open",
        "label": "Big",
        "min_tokens": 100,
        "pattern": "wave",
        "params": {"period": 3.0, "low": 0.1},
        "intensity": 1.0,
        "duration": 10,
        "duration_max": 20,
        "duration_per_token": 0.1,
        "show_in_menu": False,
    },
    {"name": "listed", "tokens": 7, "intensity": 0.4, "duration": 3, "channels": ["B"]},
    {
        "name": "split",
        "tokens": 99,
        "duration": 20,
        "intensity": 0.2,
        "pattern": "pulse",
        "params": {"period": 0.5},
        "channels": {
            "A": {"intensity": 0.8},
            "B": {"pattern": "wave", "params": {"period": 4.0}},
        },
    },
]


def rules(*shapes):
    return [Rule.model_validate(shape) for shape in shapes or RULE_SHAPES]


@pytest.mark.parametrize("shape", RULE_SHAPES, ids=[s["name"] for s in RULE_SHAPES])
def test_rule_data_round_trip(shape):
    rule = Rule.model_validate(shape)
    data = rule_to_data(rule)
    assert rule_to_data(Rule.model_validate(data)) == data
    text = rules_to_toml([rule])
    assert tomllib.loads(text)["rules"] == [data]


def test_rule_data_is_compact():
    exact, ranged = rules(RULE_SHAPES[0], RULE_SHAPES[1])
    assert rule_to_data(exact) == {"name": "exact", "tokens": 5, "intensity": 0.3, "duration": 5}
    assert rule_to_data(ranged)["duration"] == 2.5
    assert "tokens" not in rule_to_data(ranged)


def test_toml_style():
    text = rules_to_toml(rules())
    assert "\n\n[[rules]]" in text and "\n\n\n" not in text
    assert "params = { period = 3.0, low = 0.1 }" in text
    assert "[rules.channels.A]\nintensity = 0.8\n[rules.channels.B]" in text


def test_splice_keeps_everything_outside_the_rules():
    text = EXAMPLE + "\n# about the extra section\n[custom]\nvalue = 20\n"
    new = splice_rules(text, rules_to_toml(rules()))
    old_data, new_data = tomllib.loads(text), tomllib.loads(new)
    old_data.pop("rules")
    assert new_data.pop("rules") == [rule_to_data(r) for r in rules()]
    assert new_data == old_data
    before = text[: text.index("[[rules]]")]
    assert new.startswith(before)  # comments above the rules are kept
    assert new.endswith("\n\n# about the extra section\n[custom]\nvalue = 20\n")


def test_splice_without_rule_tables():
    text = '[device]\ntype = "dummy"\n'
    new = splice_rules(text, rules_to_toml(rules(RULE_SHAPES[0])))
    assert tomllib.loads(new)["device"] == {"type": "dummy"}
    assert tomllib.loads(new)["rules"][0]["name"] == "exact"


def test_splice_refuses_rules_split_by_other_sections():
    text = (
        '[[rules]]\nname = "a"\ntokens = 1\nintensity = 0.1\nduration = 1\n'
        '[device]\ntype = "dummy"\n'
        '[[rules]]\nname = "b"\ntokens = 2\nintensity = 0.1\nduration = 1\n'
    )
    with pytest.raises(RulesFileError, match="split up"):
        splice_rules(text, rules_to_toml(rules(RULE_SHAPES[0])))


# -- checks ---------------------------------------------------------------------


def check(*shapes, channels=("A", "B"), cap=60.0):
    return check_rules(list(shapes), channels, cap)


def messages(result, level):
    return [(p.rule, p.field, p.message) for p in result.problems if p.level == level]


def test_valid_rules_pass():
    result = check(*RULE_SHAPES)
    assert result.ok and len(result.rules) == len(RULE_SHAPES)


def test_field_errors_point_at_the_field():
    result = check(
        {"name": "a", "tokens": 1, "intensity": 2, "duration": 5},
        {
            "name": "b",
            "tokens": 2,
            "intensity": 0.5,
            "duration": 5,
            "pattern": "pulse",
            "params": {"period": -1},
        },
        {"name": "c", "tokens": 3, "intensity": 0.5, "duration": 5, "channels": ["C"]},
        {
            "name": "d",
            "tokens": 4,
            "intensity": 0.5,
            "duration": 5,
            "channels": {"B": {"pattern": "nope"}},
        },
    )
    assert not result.ok and result.rules is None
    errors = {(p.rule, p.field) for p in result.errors}
    assert errors == {
        (0, "intensity"),
        (1, "params.period"),
        (2, "channels"),
        (3, "channels.B.pattern"),
    }


def test_list_level_errors():
    assert check().errors[0].message == "at least one rule is required"
    assert not check_rules({"rules": []}, ("A",), 60).ok
    dup = check(RULE_SHAPES[0], RULE_SHAPES[0] | {"tokens": 6})
    assert [(p.rule, p.field) for p in dup.errors] == [(1, "name")]


def test_shadowed_rule_is_a_warning():
    result = check(
        {"name": "range", "min_tokens": 25, "max_tokens": 99, "intensity": 0.5, "duration": 5},
        {"name": "special", "tokens": 69, "intensity": 0.5, "duration": 5},
    )
    assert result.ok
    [(rule, field, message)] = messages(result, "warning")
    assert rule == 1 and field == "tokens"
    assert message.startswith('never matches: rule 1 "range" is checked first')


def test_partly_covered_rule_and_gaps_are_info():
    result = check(
        {"name": "special", "tokens": 77, "intensity": 0.5, "duration": 5},
        {"name": "range", "min_tokens": 50, "max_tokens": 98, "intensity": 0.5, "duration": 5},
    )
    assert messages(result, "warning") == []
    assert messages(result, "info") == [
        (1, "tokens", 'tips of 77 go to rule 1 "special" first'),
        (None, "", "tips of 1–49, 99+ tokens trigger nothing"),
    ]


def test_capped_durations_are_warnings():
    result = check(
        {"name": "random", "tokens": 1, "intensity": 0.5, "duration": 30, "duration_max": 90},
        {"name": "always", "tokens": 2, "intensity": 0.5, "duration": 61},
        {
            "name": "per",
            "min_tokens": 3,
            "max_tokens": 150,
            "intensity": 0.5,
            "duration": 10,
            "duration_per_token": 0.5,
        },
        {
            "name": "fits",
            "min_tokens": 200,
            "max_tokens": 300,
            "intensity": 0.5,
            "duration": 1,
            "duration_per_token": 0.1,
        },
        cap=60,
    )
    capped = "cut to safety.max_action_seconds (60 s)"
    assert messages(result, "warning") == [
        (0, "duration_max", f"random durations above 60 s are {capped}"),
        (1, "duration", f"always {capped}"),
        (2, "duration_per_token", f"tips of 101+ tokens can be {capped}"),
    ]


# -- the file ---------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE)
    return path


def test_write_saves_rules_and_keeps_a_backup(config_file):
    store = RulesFile(config_file, sha256(config_file.read_bytes()))
    assert store.info() == {"path": str(config_file), "writable": True, "changed_on_disk": False}
    store.write(rules())
    assert tomllib.loads(config_file.read_text())["rules"] == [rule_to_data(r) for r in rules()]
    assert (config_file.parent / "config.toml.bak").read_text() == EXAMPLE
    assert not (config_file.parent / "config.toml.tmp").exists()
    # A second save works (the stored hash follows our own writes) and keeps the backup.
    store.write(rules(RULE_SHAPES[0]))
    assert len(tomllib.loads(config_file.read_text())["rules"]) == 1
    assert (config_file.parent / "config.toml.bak").read_text() == EXAMPLE


def test_write_refuses_after_a_hand_edit(config_file):
    store = RulesFile(config_file, sha256(config_file.read_bytes()))
    config_file.write_text(EXAMPLE + "\n# edited by hand\n")
    assert store.changed_on_disk()
    with pytest.raises(RulesFileError, match="changed by hand"):
        store.write(rules())
    assert config_file.read_text().endswith("# edited by hand\n")
