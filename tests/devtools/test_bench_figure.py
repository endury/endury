"""The README figure is drawn from the results file and names every contestant."""

from __future__ import annotations

import xml.dom.minidom

from devtools.bench.figure import render, summarise


def _sample(
    met: int,
    total: int,
    cost: float | None,
    off: dict,
    provider: str = "Fake",
    latency: float | None = None,
) -> dict:
    return {
        "summary": {
            "rules_met": met,
            "rules_total": total,
            "strong_met": 1,
            "strong_total": 1,
            "composite": 0.1,
            "off_band": off,
        },
        "per_rule": {},
        "call": {"cost_usd": cost, "provider": provider, "latency_s": latency},
    }


def _cell(goal: str, contestants: dict) -> dict:
    return {
        "scenario": {"goal": goal},
        "contestants": {
            name: {
                "samples": samples,
                "aggregate": (
                    {
                        "valid": 1,
                        "rules_met_median": samples[0]["summary"]["rules_met"],
                        "rules_total": samples[0]["summary"]["rules_total"],
                    }
                    if samples[0].get("summary")
                    else {"valid": 0}
                ),
            }
            for name, samples in contestants.items()
        },
        "ceiling": {"rules_nobody_met": []},
    }


RESULTS = {
    "rulebook_version": 1,
    "measured_on": "2026-09-26",
    "scenarios": {
        "marathon_18wk_moderate": _cell(
            "running_marathon",
            {
                "endury": [
                    _sample(25, 26, None, {"taper_volume_drop": 0.1}, latency=0.19)
                ],
                "x-ai/grok-4.7": [
                    _sample(20, 28, 0.08, {"fuelling_cues": 1.0}, latency=169.4)
                ],
                "anthropic/claude-opus-5.5": [
                    _sample(23, 28, 0.3, {}, "claude-code subagent")
                ],
                "anthropic/claude-sonnet-5": [
                    _sample(22, 28, None, {}, "claude-code subagent")
                ],
            },
        ),
        "ironman_32wk_competitive": _cell(
            "ironman",
            {
                "endury": [_sample(35, 36, None, {}, latency=0.54)],
                "x-ai/grok-4.7": [
                    {
                        "error": "no plan: invalid JSON",
                        "call": {"cost_usd": 0.19, "latency_s": 436.5},
                    }
                ],
                "anthropic/claude-opus-5.5": [
                    _sample(29, 37, 0.6, {"fuelling_cues": 1.0}, "claude-code subagent")
                ],
                "anthropic/claude-sonnet-5": [
                    _sample(26, 37, None, {}, "claude-code subagent")
                ],
            },
        ),
    },
}


def test_summary_counts_families_failures_costs_and_misses():
    rows = {r["name"]: r for r in summarise(RESULTS)}
    assert next(r["name"] for r in summarise(RESULTS)) == "endury"
    grok = rows["x-ai/grok-4.7"]
    assert grok["met"]["marathon"] == 20
    assert grok["total"]["marathon"] == 28
    assert grok["failed"] == {"long": 1}
    assert grok["cost"] == {"marathon": [0.08], "long": [0.19]}
    assert grok["time"] == {"marathon": [169.4], "long": [436.5]}
    assert grok["misses"] == {"fuelling_cues": 1}
    assert rows["anthropic/claude-opus-5.5"]["cost"] == {
        "marathon": [0.3],
        "long": [0.6],
    }
    assert rows["anthropic/claude-sonnet-5"]["cost"] == {"marathon": [], "long": []}
    assert rows["anthropic/claude-sonnet-5"]["time"] == {"marathon": [], "long": []}


def test_render_is_well_formed_svg_naming_every_contestant():
    svg = render(RESULTS)
    xml.dom.minidom.parseString(svg)
    assert "Endury's planner" in svg
    assert "Grok 4.7" in svg
    assert "no plan in 1" in svg
    assert "8 ¢ · 19 ¢" in svg
    assert "30 ¢ · 60 ¢" in svg
    assert 'class="n">-</text>' in svg
    assert "free · free" in svg
    assert "0.2 s · 0.5 s" in svg
    assert "3 min · 7 min" in svg
    assert "few fuelling cues (1/1)" in svg


def test_light_render_uses_its_own_palette():
    light = render(RESULTS, "light")
    xml.dom.minidom.parseString(light)
    assert "#19191B" not in light
    assert 'fill="#F6F8FA" stroke="#D0D7DE"' in light
