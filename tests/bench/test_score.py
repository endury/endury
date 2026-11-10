from bench.rules import load_rulebook
from bench.score import aggregate, ceiling, summarise


def test_summary_counts_by_grade_and_family_with_equal_family_weight():
    rb = load_rulebook()
    distances = {
        rule.name: 0.0 for rule, _ in rb.applicable("running_marathon", "moderate")
    }
    distances["taper_session_frequency"] = 0.5  # a grade-A rule of the taper family
    s = summarise(rb, distances)
    assert s["rules_total"] == len(distances)
    assert s["rules_met"] == len(distances) - 1
    assert s["strong_met"] == s["strong_total"] - 1
    assert s["off_band"] == {"taper_session_frequency": 0.5}
    taper_rules = s["total_by_family"]["taper"]
    families = sum(1 for v in s["total_by_family"].values() if v)
    assert s["composite"] == round(0.5 / taper_rules / families, 4)


def test_aggregate_reports_median_range_and_rules_met_in_every_sample():
    samples = [
        {
            "summary": {
                "rules_met": 10,
                "rules_total": 12,
                "strong_met": 3,
                "strong_total": 4,
                "composite": 0.1,
            },
            "per_rule": {"a": 0, "b": 0.2},
            "call": {"cost_usd": 0.01},
        },
        {
            "summary": {
                "rules_met": 12,
                "rules_total": 12,
                "strong_met": 4,
                "strong_total": 4,
                "composite": 0.0,
            },
            "per_rule": {"a": 0, "b": 0},
            "call": {"cost_usd": 0.02},
        },
        {"error": "boom", "call": {"cost_usd": 0.005}},
    ]
    agg = aggregate(samples)
    assert agg["samples"] == 3
    assert agg["valid"] == 2
    assert agg["rules_met_median"] == 11
    assert agg["rules_met_range"] == [10, 12]
    assert agg["met_in_every_sample"] == ["a"]
    assert agg["cost_usd"] == 0.035
    assert aggregate([{"error": "x"}]) == {"samples": 1, "valid": 0}


def test_ceiling_flags_rules_nobody_met():
    rows = {
        "x": [{"per_rule": {"a": 0, "b": 1}}],
        "y": [{"per_rule": {"a": 0.5, "b": 0.3}}],
    }
    assert ceiling(rows) == {"rules_nobody_met": ["b"], "rules_total": 2}


def test_aggregate_cost_is_none_only_for_unmetered_calls():
    graded = {
        "summary": {
            "rules_met": 1,
            "rules_total": 1,
            "strong_met": 0,
            "strong_total": 0,
            "composite": 0.0,
        },
        "per_rule": {"a": 0},
    }
    assert aggregate([{**graded, "call": {"cost_usd": None}}])["cost_usd"] is None
    assert aggregate([{**graded, "call": {"latency_s": 1.0}}])["cost_usd"] == 0.0
    mixed = [
        {**graded, "call": {"cost_usd": None}},
        {**graded, "call": {"cost_usd": 0.5}},
    ]
    assert aggregate(mixed)["cost_usd"] == 0.5
