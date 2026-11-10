import math

import pytest
import yaml

from bench.readings import read
from bench.rules import GRADES, Band, grade, load_rulebook
from bench.schema import GOALS, TIERS


@pytest.fixture(scope="module")
def rulebook():
    return load_rulebook()


def test_every_benchmark_rule_is_complete_and_sourced(rulebook):
    benchmark = [rule for rule in rulebook.rules if rule.benchmark]
    assert len(benchmark) >= 35
    for rule in benchmark:
        assert rule.grade in GRADES
        if rule.grade != "S":
            assert rule.sources, rule.name
            for source in rule.sources:
                assert source["verified"] in ("crossref", "isbn"), (rule.name, source)
                assert source.get("doi") or source.get("isbn"), (rule.name, source)


def test_every_benchmark_rule_reads_something_the_readings_produce(rulebook, marathon):
    readings, _ = read(marathon, {"run": 3.0}, {"run": 60})
    for rule in rulebook.rules:
        if rule.benchmark:
            assert rule.reading in readings, rule.name


def test_a_rule_outside_the_benchmark_has_no_reading_and_is_never_graded(rulebook):
    own = [rule for rule in rulebook.rules if not rule.benchmark]
    assert own
    for rule in own:
        assert rule.reading is None, rule.name
        assert rule.grade is None, rule.name
    for goal in GOALS:
        graded = {rule.name for rule, _ in rulebook.applicable(goal, "moderate")}
        assert not graded & {rule.name for rule in own}, goal


def test_bands_resolve_by_goal_and_tier(rulebook):
    peak = rulebook.by_name("peak_weekly_hours")
    # ``ramp: width``: the distance reaches 1 one band-width outside the band.
    assert peak.band("ironman", "competitive") == Band(14, 18, 4.0)
    assert peak.band("half_ironman", "basic") == Band(8, 12, 4.0)
    assert peak.band("trail_50k", "competitive") == Band(9, 12, 3.0)
    assert peak.band("running_marathon", "moderate") is None
    cadence = rulebook.by_name("recovery_cadence")
    assert cadence.band("running_marathon", "basic").low == 0.2
    for goal in GOALS:
        for tier in TIERS:
            assert rulebook.applicable(goal, tier)


def test_a_goals_own_band_replaces_the_rules_fallback(rulebook):
    taper = rulebook.by_name("taper_volume_drop")
    assert taper.band("running_marathon", "moderate") == Band(0.58, 0.64, 0.4)
    assert taper.band("running_10k", "moderate") == Band(0.5, 0.7, 0.4)
    rest = rulebook.by_name("rest_days")
    assert rest.band("running_marathon", "basic") == Band(0.75, math.inf, 1.5)
    assert rest.band("ironman", "basic") == Band(0.75, 2.25, 1.5)
    assert rest.band("running_10k", "basic") is None  # not a goal it covers


def test_distance_ramps_linearly_outside_the_band():
    band = Band(0.4, 0.6, 0.4)
    assert band.distance(0.5) == 0
    assert band.distance(0.6) == 0
    assert band.distance(0.7) == pytest.approx(0.25)
    assert band.distance(1.5) == 1.0
    assert Band(0, 0, 0).distance(1) == 1.0


def test_missing_readings_score_their_missing_distance_or_are_skipped(rulebook):
    readings = {rule.reading: None for rule in rulebook.rules}
    distances = grade(rulebook, readings, "ironman", "competitive")
    assert distances["taper_volume_drop"] == 1.0  # no taper is the defect
    assert "warmup_before_hard" not in distances  # no steps, not graded


_RULE = {
    "name": "some_rule",
    "family": "taper",
    "reads": "a share",
    "benchmark": False,
    "band": {"low": 0.2, "high": 0.4, "why": "One line."},
    "ramp": 0.1,
}


def _load(tmp_path, **changes):
    rule = {k: v for k, v in {**_RULE, **changes}.items() if v is not None}
    path = tmp_path / "rulebook.yaml"
    path.write_text(yaml.safe_dump({"version": "t", "rules": [rule]}))
    return load_rulebook(path)


def test_the_loader_reads_a_rule_outside_the_benchmark(tmp_path):
    rule = _load(tmp_path).by_name("some_rule")
    assert rule.goals == GOALS  # no `goals`: every race goal
    assert rule.weight == 1.0
    assert rule.band("ironman", "basic") == Band(0.2, 0.4, 0.1)
    wide = _load(tmp_path, ramp="width").by_name("some_rule")
    assert wide.band("ironman", "basic") == Band(0.2, 0.4, pytest.approx(0.2))
    capped = _load(tmp_path, band={"high": 0.4}).by_name("some_rule")
    assert capped.band("ironman", "basic") == Band(-math.inf, 0.4, 0.1)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"band": None}, "lacks"),
        ({"colour": "red"}, "unknown keys"),
        ({"family": "mood"}, "unknown family"),
        ({"reading": "taper_volume_drop"}, "need benchmark: true"),
        ({"benchmark": True}, "lacks"),
        ({"goals": ["running_ultra"]}, "unknown goal"),
        ({"ramp": 0}, "ramp is a positive number"),
        ({"ramp": "wide"}, "ramp is a positive number"),
        ({"band": {"low": 0.4, "high": 0.2}}, "is above high"),
        ({"band": {"low": 0.2, "high": 0.4, "colour": "red"}}, "unknown band keys"),
        ({"band": {"low": 0.2, "high": 0.4, "why": "two\nlines"}}, "one line of text"),
        ({"band": {"by_tier": {"basic": {"low": 0, "high": 1}}}}, "names every tier"),
        (
            {"band": {"by_tier": {t: {"low": 0, "hihg": 1} for t in TIERS}}},
            "holds only low and high",
        ),
        ({"band": {"low": 0.2, "high": 0.4, "ramp": 0.1}}, "unknown band keys"),
        (
            {"goals": ["ironman"], "band": {"by_goal": {"trail_50k": {"low": 0}}}},
            "which it does not cover",
        ),
        (
            {"band": {"by_goal": {"ironman": {"low": 0, "high": 1}}}},
            "has no band for it",
        ),
        ({"band": {"low": 4.0, "high": None}, "ramp": "width"}, "ramp in numbers"),
    ],
)
def test_the_loader_rejects_a_malformed_rule(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, **changes)
