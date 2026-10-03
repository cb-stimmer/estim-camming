"""Qt-free helpers of the rules editor."""

from estim_camming.gui import rules_model
from estim_camming.plugins import PATTERNS

DRAFT = [
    {"name": "special", "label": "Special", "tokens": 69, "intensity": 0.5, "duration": 20},
    {
        "name": "range",
        "min_tokens": 25,
        "max_tokens": 99,
        "pattern": "wave",
        "intensity": 0.5,
        "duration": 10,
        "duration_max": 30,
    },
    {
        "name": "big",
        "min_tokens": 100,
        "intensity": 1.0,
        "duration": 10,
        "duration_per_token": 0.5,
        "show_in_menu": False,
        "channels": {"A": {"pattern": "pulse"}, "B": {}},
    },
]


def test_summary_columns():
    assert rules_model.summary(DRAFT[0]) == ("69", "Special", "constant", "20 s")
    assert rules_model.summary(DRAFT[1]) == ("25–99", "range", "wave", "10–30 s")
    assert rules_model.summary(DRAFT[2]) == ("100+", "big  (hidden)", "per channel", "10 s +0.5/tk")
    assert rules_model.summary({})[0] == "?"


def test_try_amount_uses_first_match_and_the_cap():
    assert rules_model.try_amount(DRAFT, 69, 60) == 'rule 1 "Special", 20 s'
    assert rules_model.try_amount(DRAFT, 30, 60) == 'rule 2 "range", 10–30 s'
    assert rules_model.try_amount(DRAFT, 200, 60) == 'rule 3 "big", 60 s'
    assert rules_model.try_amount(DRAFT, 5, 60) == "no rule: nothing happens"
    broken = [{"name": "x", "tokens": 5}, *DRAFT]
    assert rules_model.try_amount(broken, 5, 60).endswith("(1 invalid rule(s) skipped)")


def test_param_fields_from_schemas():
    fields = {
        f.name: f
        for f in rules_model.param_fields(PATTERNS.get("random_level").Options.model_json_schema())
    }
    assert fields["low"].kind == "number" and (fields["low"].minimum, fields["low"].maximum) == (
        0,
        1,
    )
    assert fields["seed"].kind == "integer" and fields["seed"].optional
    pulse = rules_model.param_fields(PATTERNS.get("pulse").Options.model_json_schema())
    period = pulse[0]
    assert period.name == "period" and period.exclusive_minimum and period.default == 1.0
    assert rules_model.param_fields(PATTERNS.get("constant").Options.model_json_schema()) == []


def test_intensity_hint_uses_channel_limits():
    limits = {"max_level": 0.5, "channel_max_level": {"B": 0.8}}
    assert rules_model.intensity_hint(0.6, ["A", "B"], limits) == (
        "≈ A 30%, B 48% of the device range"
    )
    assert rules_model.intensity_hint(0.6, [], limits) == ""


def test_preview_is_a_line_or_a_band():
    low, high = rules_model.preview("pulse", {"period": 1.0}, 0.5, 2.0, samples=5)
    assert low == high == [0.5, 0.0, 0.5, 0.0, 0.5]
    low, high = rules_model.preview("random_level", {"low": 0.2, "high": 0.9}, 1.0, 5.0)
    assert all(0.2 <= lo <= hi <= 0.9 for lo, hi in zip(low, high, strict=True))
    assert high[0] > low[0]  # different instances drew different levels
    assert rules_model.preview("nope", {}, 1.0, 5.0) is None
    assert rules_model.preview("pulse", {"period": -1}, 1.0, 5.0) is None
