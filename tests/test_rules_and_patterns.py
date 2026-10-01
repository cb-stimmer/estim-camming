import pytest
from pydantic import ValidationError

from estim_camming.events import TipEvent
from estim_camming.plugins import PATTERNS, PluginError
from estim_camming.rules import Rule, RuleEngine


def tip(tokens: int) -> TipEvent:
    return TipEvent(platform="test", username="u", tokens=tokens)


def engine(*rules: dict, max_seconds: float = 60) -> RuleEngine:
    return RuleEngine([Rule(**r) for r in rules], ("A", "B"), max_seconds)


def test_first_matching_rule_wins():
    e = engine(
        {"name": "exact", "tokens": 10, "intensity": 0.5, "duration": 1},
        {"name": "range", "min_tokens": 1, "max_tokens": 20, "intensity": 0.1, "duration": 1},
    )
    assert e.match(10).name == "exact"
    assert e.match(11).name == "range"
    assert e.match(21) is None


def test_action_duration_scales_and_is_capped():
    e = engine(
        {
            "name": "big",
            "min_tokens": 100,
            "intensity": 1,
            "duration": 10,
            "duration_per_token": 0.1,
        },
        max_seconds=25,
    )
    assert e.action_for(tip(100)).duration == pytest.approx(20)
    assert e.action_for(tip(1000)).duration == 25


def test_action_defaults_to_all_channels():
    e = engine({"name": "r", "min_tokens": 1, "intensity": 1, "duration": 1})
    assert e.action_for(tip(5)).channels == ("A", "B")


def test_rule_validation():
    with pytest.raises(ValidationError):
        Rule(name="x", intensity=1, duration=1)  # no token amount
    with pytest.raises(ValidationError):
        Rule(name="x", tokens=5, min_tokens=5, intensity=1, duration=1)
    with pytest.raises(ValidationError):
        Rule(name="x", min_tokens=10, max_tokens=5, intensity=1, duration=1)


def test_engine_rejects_unknown_pattern_params_and_channels():
    with pytest.raises(PluginError, match="unknown pattern"):
        engine({"name": "r", "tokens": 1, "pattern": "nope", "intensity": 1, "duration": 1})
    with pytest.raises(PluginError, match="invalid options"):
        engine(
            {
                "name": "r",
                "tokens": 1,
                "pattern": "pulse",
                "params": {"bogus": 1},
                "intensity": 1,
                "duration": 1,
            }
        )
    with pytest.raises(PluginError, match="unknown channel"):
        engine({"name": "r", "tokens": 1, "channels": ["C"], "intensity": 1, "duration": 1})


@pytest.mark.parametrize("name", ["constant", "pulse", "ramp", "wave"])
def test_patterns_stay_in_range(name):
    pattern = PATTERNS.create(name)
    for i in range(200):
        assert 0.0 <= pattern.level(i * 0.05, 10.0) <= 1.0


def test_pulse_and_ramp_shapes():
    pulse = PATTERNS.create("pulse", {"period": 1.0, "duty": 0.25})
    assert pulse.level(0.1, 10) == 1.0
    assert pulse.level(0.5, 10) == 0.0
    ramp = PATTERNS.create("ramp", {"start": 0.0, "end": 1.0})
    assert ramp.level(5, 10) == pytest.approx(0.5)
    assert ramp.level(20, 10) == 1.0


def test_per_channel_rule_settings():
    e = engine(
        {
            "name": "split",
            "tokens": 99,
            "pattern": "pulse",
            "params": {"period": 0.5},
            "intensity": 0.6,
            "duration": 10,
            "channels": {
                "A": {},  # everything from the rule
                "B": {"pattern": "wave", "intensity": 0.3},  # own pattern, default params
            },
        }
    )
    a, b = e.action_for(tip(99)).outputs
    assert (a.channel, a.pattern, a.params, a.intensity) == ("A", "pulse", {"period": 0.5}, 0.6)
    assert (b.channel, b.pattern, b.params, b.intensity) == ("B", "wave", {}, 0.3)


def test_per_channel_intensity_only_and_single_channel():
    e = engine(
        {"name": "b-only", "tokens": 5, "duration": 1, "channels": {"B": {"intensity": 0.9}}},
        {"name": "list", "tokens": 6, "intensity": 0.4, "duration": 1, "channels": ["A"]},
    )
    [b] = e.action_for(tip(5)).outputs
    assert (b.channel, b.pattern, b.intensity) == ("B", "constant", 0.9)
    assert e.action_for(tip(6)).channels == ("A",)


@pytest.mark.parametrize(
    "rule",
    [
        {"channels": ["A"]},  # no intensity anywhere
        {"channels": {"A": {"intensity": 0.5}, "B": {}}},  # B has no intensity
        {"intensity": 0.5, "channels": ["A", "A"]},
        {"intensity": 0.5, "channels": []},
        {"intensity": 0.5, "channels": {"A": {"bogus": 1}}},
    ],
)
def test_invalid_channel_settings(rule):
    with pytest.raises(ValidationError):
        Rule(name="x", tokens=1, duration=1, **rule)


def test_engine_validates_per_channel_patterns_and_channels():
    with pytest.raises(PluginError, match="channel B"):
        engine(
            {
                "name": "r",
                "tokens": 1,
                "intensity": 1,
                "duration": 1,
                "channels": {"A": {}, "B": {"pattern": "pulse", "params": {"bogus": 1}}},
            }
        )
    with pytest.raises(PluginError, match="unknown channel"):
        engine({"name": "r", "tokens": 1, "duration": 1, "channels": {"C": {"intensity": 1}}})
