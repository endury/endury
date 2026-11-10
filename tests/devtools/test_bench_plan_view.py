"""The plan view draws every contestant's plan, or the reason there is none."""

from __future__ import annotations

import json
from pathlib import Path

from bench.rules import load_rulebook
from bench.run import Runner
from bench.scenarios import by_label
from bench.schema import Step
from devtools.bench.plan_view import entry, render, saved_models, steps_text


def test_steps_text_folds_repeat_blocks_back_to_the_authors_form():
    steps = [
        Step(1, 10),
        *[Step(4, 5, "on"), Step(2, 2, "off")] * 3,
        Step(5, 1, "on"),
        Step(5, 1, "on"),
        Step(1, 5),
    ]
    assert steps_text(steps) == (
        "Z1 10 min → 3x (Z4 5 min / Z2 2 min) → 2x (Z5 1 min) → Z1 5 min"
    )


def test_a_broken_saved_answer_is_shown_as_its_reason(tmp_path: Path):
    scenario = by_label("marathon_12wk_moderate")
    folder = tmp_path / scenario.label / "fake_model-1" / "blind"
    folder.mkdir(parents=True)
    (folder / "0.json").write_text(
        json.dumps(
            {
                "model": "fake/model-1",
                "ok": True,
                "text": '{"weeks": [{"week": 1',
                "finish_reason": "stop",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "reasoning_tokens": None,
                "cost_usd": 0.01,
                "latency_s": 1.0,
            }
        ),
        encoding="utf-8",
    )
    runner = Runner(load_rulebook(), tmp_path, tmp_path, 0.0, {})

    assert saved_models(tmp_path, scenario, "blind") == ["fake/model-1"]
    row = entry(runner, scenario, "fake/model-1", tmp_path, "blind", 0)
    assert row["error"].startswith("no plan")
    assert "weeks" not in row
    missing = entry(runner, scenario, "fake/model-2", tmp_path, "blind", 0)
    assert missing["error"].startswith("no saved answer")


def test_endurys_plan_is_graded_and_drawn_with_every_week(tmp_path: Path):
    scenario = by_label("marathon_12wk_moderate")
    runner = Runner(load_rulebook(), tmp_path, tmp_path, 0.0, {})

    row = entry(runner, scenario, "endury", tmp_path, "blind", 0)
    assert len(row["weeks"]) == scenario.weeks
    assert row["summary"]["rules_met"] > 0
    steps = [s["steps"] for w in row["weeks"] for s in w["sessions"]]
    assert all("/km" in s or "bpm" in s or "RPE" in s for s in steps)  # the cards
    page = render([row | {"name": "</script><b>"}])
    assert "</script><b>" not in page
