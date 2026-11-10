"""The markdown report shows a cost only where the calls carried one."""

from __future__ import annotations

from bench.report import write_report
from bench.rules import load_rulebook
from bench.score import aggregate


def _sample(provider: str | None, cost: float | None) -> dict:
    return {
        "summary": {
            "rules_met": 1,
            "rules_total": 2,
            "strong_met": 1,
            "strong_total": 1,
            "composite": 0.1,
            "off_band": {"b": 0.5},
        },
        "per_rule": {"a": 0, "b": 0.5},
        "facts": {"total_hours": 10.0},
        "call": {"provider": provider, "cost_usd": cost},
    }


def test_report_shows_cost_only_where_calls_carried_one(tmp_path):
    subagent = [_sample("claude-code subagent", None)]
    estimated = [_sample("claude-code subagent", 0.5)]
    router = [_sample("Google", 0.02)]
    payload = {
        "measured_on": "2026-09-25",
        "rulebook_version": 1,
        "registration": "abc",
        "scenarios": {
            "cell": {
                "scenario": {
                    "goal": "running_marathon",
                    "tier": "moderate",
                    "weeks": 18,
                    "athlete": {"label": "typical"},
                    "weekly_cap_hours": None,
                },
                "contestants": {
                    "m": {"samples": subagent, "aggregate": aggregate(subagent)},
                    "e": {"samples": estimated, "aggregate": aggregate(estimated)},
                    "n": {"samples": router, "aggregate": aggregate(router)},
                },
                "ceiling": {"rules_nobody_met": ["b"]},
            }
        },
    }
    out = tmp_path / "README.md"
    write_report(payload, load_rulebook(), out)
    text = out.read_text()
    assert "| m | 1 | 1 |" in text
    assert "10 h, - |" in text
    assert "| e | 1 | 1 |" in text
    assert "10 h, $0.500 |" in text
    assert "| n | 1 | 1 |" in text
    assert "10 h, $0.020 |" in text
    assert "(via Claude Code)" not in text
    assert "The Claude models ran through Claude Code" in text
    assert "athlete on a typical training volume" in text
