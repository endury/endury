"""The sweep site: one interactive page of every plan, week by week."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from devtools.planner.report_sweep import sweep_site
from devtools.planner.report_sweep.sweep_site import (
    HORIZONS,
    Athlete,
    build_scenario,
    cell_scenarios,
    main,
    plan_data,
)
from endury.planner.activity.materialize import activity_display_name
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    SportType,
)
from endury.planner.pipeline import default_profile_for


def test_every_goal_has_a_horizon():
    assert set(HORIZONS) == set(GoalType)


def test_the_default_athlete_is_the_goals_default_for_the_tier():
    tier = AmbitionLevel.COMPETITIVE
    s = build_scenario(GoalType.IRONMAN, tier, 32, Athlete())
    base = default_profile_for(GoalType.IRONMAN, tier)
    assert s.recent_hours == {
        sp: st.recent_weekly_hours for sp, st in base.sports.items()
    }
    assert s.goals[0].race_date.weekday() == 6


def test_athlete_changes_scale_and_replace_the_defaults():
    tier = AmbitionLevel.BASIC
    athlete = Athlete(
        volume_scale=0.5,
        hours={SportType.CYCLING: 6.0},
        longest_min={SportType.RUNNING: 90},
    )
    s = build_scenario(GoalType.IRONMAN, tier, 24, athlete)
    base = default_profile_for(GoalType.IRONMAN, tier).sports
    assert s.recent_hours[SportType.CYCLING] == 6.0
    assert s.recent_hours[SportType.RUNNING] == (
        base[SportType.RUNNING].recent_weekly_hours * 0.5
    )
    assert s.longest_recent[SportType.RUNNING, ActivityFamily.LONG] == 90


def test_tune_ups_add_a_b_and_a_bc_plan_only_where_a_step_down_race_exists(
    monkeypatch,
):
    monkeypatch.setattr(sweep_site, "TUNE_UPS", True)
    ironman = cell_scenarios(GoalType.IRONMAN, AmbitionLevel.COMPETITIVE)
    assert [s.name[-3:] for s in ironman] == ["2wk", "k_B", "_BC"]
    assert [g.priority for g in ironman[-1].goals] == ["A", "C", "B"]
    assert len(cell_scenarios(GoalType.TRAIL_50K, AmbitionLevel.BASIC)) == 1


def test_the_site_is_one_page_with_every_plan_week_by_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The trail 20K at 16 weeks books its threshold test; the 5K's squeezed
    # recovery weeks book none since their last one moved three weeks before
    # the race (R-PHASE-015, 2026-10-06).
    monkeypatch.setitem(sweep_site.HORIZONS, GoalType.TRAIL_20K, (16,))
    main(
        [
            "--goals",
            "running_5k,trail_20k",
            "--tiers",
            "basic",
            "--no-open",
            "--jobs",
            "2",
            "--out",
            str(tmp_path),
        ]
    )

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    data = page.split("const DATA = ", 1)[1].split(";\nconst DAYS", 1)[0]
    plans = json.loads(data)
    assert [p["name"] for p in plans] == [
        "running_5k_basic_10wk",
        "trail_20k_basic_16wk",
    ]
    for plan, weeks, goal in zip(
        plans, (10, 16), ("running_5k", "trail_20k"), strict=True
    ):
        assert "error" not in plan
        assert plan["races"] == [[weeks - 1, 6, "A", goal]]
        assert len(plan["weeks"]) == weeks
        sessions = [s for w in plan["weeks"] for day in w[4] for s in day]
        assert all(plan["names"][s[0]] and plan["cards"][s[4]] for s in sessions)
        hours = sum(sum(w[3]) for w in plan["weeks"])
        assert hours == pytest.approx(sum(s[1] for s in sessions) / 60, abs=0.1)
    trail = [s[3] for w in plans[1]["weeks"] for day in w[4] for s in day]
    assert any(f & 1 for f in trail)
    assert any(f & 2 for f in trail)


def test_without_a_selection_the_site_holds_the_plans_on_offer(tmp_path: Path):
    main(["--no-open", "--jobs", "2", "--out", str(tmp_path)])

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    data = page.split("const DATA = ", 1)[1].split(";\nconst DAYS", 1)[0]
    assert {plan["name"].rsplit("_", 1)[0] for plan in json.loads(data)} == {
        "running_half_marathon_moderate",
        "half_ironman_moderate",
        "ironman_moderate",
        "trail_20k_basic",
        "trail_maintenance_basic",
        "triathlon_maintenance_moderate",
    }


def test_a_recurring_session_shows_the_set_the_athlete_gets():
    """Cards rotate their sets as the athlete's do; the first set alone is all 2x."""
    plan = plan_data(
        build_scenario(GoalType.IRONMAN, AmbitionLevel.BASIC, 22, Athlete())
    )
    name = plan["names"].index(activity_display_name(ActivityKey.BIKE_THRESHOLD))
    cards = {
        plan["cards"][s[4]]
        for w in plan["weeks"]
        for day in w[4]
        for s in day
        if s[0] == name and s[2] == "cycling"
    }
    reps = {re.search(r" → (\d+)x \(", card).group(1) for card in cards}
    assert len(reps) > 1
