import random

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


@pytest.mark.parametrize("name", ["constant", "pulse", "ramp", "wave", "random_level"])
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


def test_random_level_holds_one_level_within_range():
    levels = set()
    for seed in range(50):
        pattern = PATTERNS.create("random_level", {"low": 0.3, "high": 0.6, "seed": seed})
        level = pattern.level(0, 10)
        assert 0.3 <= level <= 0.6
        assert all(pattern.level(t, 10) == level for t in (0.5, 3, 9.9))  # steady
        levels.add(level)
    assert len(levels) > 40  # different per tip
    assert PATTERNS.create("random_level", {"low": 0.5, "high": 0.5}).level(0, 1) == 0.5
    with pytest.raises(PluginError):
        PATTERNS.create("random_level", {"low": 0.8, "high": 0.2})


def test_random_level_per_channel_draws_independently():
    e = engine(
        {
            "name": "r",
            "tokens": 1,
            "pattern": "random_level",
            "intensity": 1,
            "duration": 1,
        }
    )
    a, b = (PATTERNS.create(o.pattern, o.params) for o in e.action_for(tip(1)).outputs)
    assert a.level(0, 1) != b.level(0, 1)


def test_random_duration_between_duration_and_duration_max():
    rule = {"name": "r", "min_tokens": 1, "intensity": 1, "duration": 5, "duration_max": 15}
    e = RuleEngine([Rule(**rule)], ("A", "B"), 60, rng=random.Random(1))
    durations = [e.action_for(tip(1)).duration for _ in range(200)]
    assert all(5 <= d <= 15 for d in durations)
    assert max(durations) - min(durations) > 8  # actually random
    menu = e.menu()[0]
    assert (menu["duration"], menu["duration_max"]) == (5, 15)


def test_random_duration_still_capped_and_adds_per_token_time():
    rule = {
        "name": "r",
        "min_tokens": 1,
        "intensity": 1,
        "duration": 10,
        "duration_max": 20,
        "duration_per_token": 1,
    }
    e = RuleEngine([Rule(**rule)], ("A", "B"), 25, rng=random.Random(2))
    durations = [e.action_for(tip(10)).duration for _ in range(100)]
    assert all(20 <= d <= 25 for d in durations)  # 10..20 + 10 tokens, capped at 25
    assert 25 in durations


def test_duration_max_must_not_be_below_duration():
    with pytest.raises(ValidationError, match="duration_max"):
        Rule(name="x", tokens=1, intensity=1, duration=10, duration_max=5)
