"""Endury as a benchmark contestant: its export carries steps and the hours the scorecard counts."""

from __future__ import annotations

import pytest

from bench.readings import read
from bench.rules import grade, load_rulebook
from bench.scenarios import START, by_label
from devtools.bench.endury_contestant import endury_plan, export, plan_request
from devtools.planner._shared.metrics import run_full_pipeline
from devtools.planner._shared.scenarios import DEFAULT_START
from devtools.quality.planner.score_quality import (
    _compute_metrics,
    _weekly_total,
    build_default_scenario,
)
from endury.planner.foundations.types import AmbitionLevel, GoalType, SportType
from endury.planner.pipeline import generate_plan


@pytest.mark.parametrize(
    "label", ["marathon_18wk_moderate", "ironman_32wk_competitive"]
)
def test_export_carries_every_session_with_steps_and_the_hours_the_plan_sized(
    label,
):
    scenario = by_label(label)
    assert START.weekday() == DEFAULT_START.weekday()  # both Mondays: same layout
    result = generate_plan(plan_request(scenario))
    plan = export(result, scenario)

    sessions = [s for w in plan.weeks for s in w.sessions]
    assert len(sessions) == len(result.assignments)
    assert all(s.steps for s in sessions)
    assert all(1 <= st.zone <= 5 for s in sessions for st in s.steps)
    assert all(
        abs(sum(st.minutes for st in s.steps) - s.minutes) < 1e-6 for s in sessions
    )
    assert all(s.metres for s in sessions if s.sport == "swim")
    # Whole seconds: a 10-minute warm-up is not 9.999999 minutes of float noise.
    assert not any(9.99 < st.minutes < 10.0 for s in sessions for st in s.steps)

    # The scorecard grades this export of the same plan (same layout: both
    # starts are Mondays): week by week it carries exactly the hours the plan
    # sized.
    goal, tier = GoalType(scenario.goal), AmbitionLevel(scenario.tier)
    official = run_full_pipeline(build_default_scenario(scenario.weeks, tier, goal))
    totals = _weekly_total(_compute_metrics(official))
    assert [w.hours for w in plan.weeks] == pytest.approx(
        [totals[k] for k in sorted(totals)], abs=1e-6
    )
    new, _ = read(plan, scenario.athlete.hours, scenario.athlete.longest_min)
    assert new["peak_weekly_hours"] == pytest.approx(
        max(totals[k] for k in sorted(totals)[:-1]), abs=1e-6
    )


def test_endury_grades_and_a_weekly_cap_reaches_the_planner():
    rb = load_rulebook()
    capped = by_label("ironman_32wk_competitive_cap12")
    plan = endury_plan(capped)
    readings, _ = read(plan, capped.athlete.hours, capped.athlete.longest_min)
    distances = grade(rb, readings, capped.goal, capped.tier)
    assert distances
    uncapped = endury_plan(by_label("ironman_32wk_competitive"))
    uncapped_peak = max(w.hours for w in uncapped.weeks)
    assert (
        readings["peak_weekly_hours"] < uncapped_peak - 2
    )  # the cap is soft, but it bites


def test_card_steps_keep_the_repeat_block_and_take_the_highest_zone():
    from devtools.bench.endury_contestant import card_steps
    from devtools.planner._shared.sessions import build_synthetic_resolver
    from endury.training.workout import DurationDef, TargetDef, WorkoutStep

    r = build_synthetic_resolver()

    def step(kind: str, seconds: float, *targets: TargetDef) -> WorkoutStep:
        return WorkoutStep(
            type=kind,
            duration=DurationDef(type="time", value_seconds=seconds),
            target=targets[0],
            additional_targets=list(targets[1:]),
        )

    hr1 = TargetDef(type="heart_rate_zone", zone=1)
    watts = round(r.cycling_ftp_watts * 0.95)  # power Z4 by watts
    steps, repeats = card_steps(
        [
            step("warmup", 600, TargetDef(type="rpe", rpe=3)),  # effort only: Z2
            WorkoutStep(
                type="repeat",
                iterations=3,
                steps=[
                    step(
                        "interval",
                        480,
                        hr1,
                        TargetDef(type="power", low_watts=watts, high_watts=watts),
                    ),
                    step("recovery", 120, hr1),
                ],
            ),
            step("cooldown", 300, TargetDef(type="open")),
        ],
        SportType.CYCLING,
        r,
    )
    assert [(s.zone, s.minutes, s.role) for s in steps] == [
        (2, 10.0, "straight"),
        *[(4, 8.0, "on"), (1, 2.0, "off")] * 3,
        (1, 5.0, "straight"),
    ]
    assert [(p.reps, p.on_zone, p.on_minutes, p.off_zone) for p in repeats] == [
        (3, 4, 8.0, 1)
    ]


def test_endurys_threshold_sessions_export_their_repeat_blocks():
    plan = endury_plan(by_label("ironman_32wk_competitive"))
    threshold = [s for w in plan.weeks for s in w.sessions if s.kind == "threshold"]
    assert threshold
    assert all(s.repeats for s in threshold if s.sport != "swim")
    assert all(max(st.zone for st in s.steps) >= 4 for s in threshold)


def test_the_rulebook_knows_every_race_goal_the_planner_plans():
    # The scorecard reads every race plan through the benchmark's table, which
    # refuses a goal it does not know.
    from bench.schema import GOALS
    from endury.planner.foundations.types import is_maintenance_goal

    assert set(GOALS) == {g.value for g in GoalType if not is_maintenance_goal(g)}
