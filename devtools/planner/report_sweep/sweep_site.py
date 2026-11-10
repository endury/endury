"""The planner sweep as one interactive HTML page: every plan, week by week.

Usage::

    make run-devtool TOOL=planner_sweep_site
    make run-devtool TOOL=planner_sweep_site ARGS='--goals ironman,trail_50k --tiers competitive'

Without ``--goals`` or ``--tiers`` the page holds the plans the menus offer
(``offered:``); either argument picks from every goal and tier instead.
Each plan is built for each horizon in
``HORIZONS``, on the goal's default athlete for that tier
(``default_profile_for``: the menu's assumed weekly hours, split by the goal's
sport split, and its longest recent sessions). The configuration section
below changes the athletes and horizons. The page picks a plan by goal, tier
and length and shows its totals, its weekly hours by sport and load, and any
week's sessions day by day, each with its workout card. It needs no network
and is written outside the repo by default.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import webbrowser
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from devtools.planner._shared.metrics import run_full_pipeline
from devtools.planner._shared.pipeline import DEFAULT_START
from devtools.planner._shared.scenarios import Scenario
from devtools.planner._shared.sessions import (
    assignment_target_tss,
    build_synthetic_resolver,
    card_text,
)
from devtools.planner.report_sweep.report_cli import _run_validation
from endury.planner.activity.materialize import activity_display_name
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.sets import set_occurrences, with_set
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    build_table_steps,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    is_maintenance_goal,
)
from endury.planner.pipeline import default_profile_for
from endury.planner.sessions.menus import BLUEPRINTS


@dataclass(frozen=True)
class Athlete:
    """Changes to a goal's default athlete; an empty one keeps the defaults.

    Attributes:
        volume_scale: Multiplies the default weekly hours of every sport.
        hours: Weekly hours per sport, replacing the (scaled) default.
        longest_min: Longest recent session per sport in minutes, replacing
            the default (a long run or ride, an easy swim).
    """

    volume_scale: float = 1.0
    hours: dict[SportType, float] = field(default_factory=dict)
    longest_min: dict[SportType, int] = field(default_factory=dict)


# ── Configuration: edit, then rerun ───────────────────────────────────────

#: The grid: its rows (goals) and columns (tiers).
GOALS: tuple[GoalType, ...] = tuple(GoalType)
TIERS: tuple[AmbitionLevel, ...] = tuple(AmbitionLevel)

#: Plan lengths in weeks to the A race; one plan per goal, tier and length.
#: One representative length each; add more, e.g. ``(20, 24, 28, 32)``.
HORIZONS: dict[GoalType, tuple[int, ...]] = {
    GoalType.RUNNING_5K: (10,),
    GoalType.RUNNING_10K: (12,),
    GoalType.RUNNING_HALF_MARATHON: (14,),
    GoalType.RUNNING_MARATHON: (18,),
    GoalType.TRAIL_20K: (12,),
    GoalType.TRAIL_50K: (20,),
    GoalType.TRAIL_100K: (24,),
    GoalType.SPRINT_TRIATHLON: (12,),
    GoalType.OLYMPIC_TRIATHLON: (14,),
    GoalType.HALF_IRONMAN: (24,),
    GoalType.IRONMAN: (32,),
    GoalType.RUNNING_MAINTENANCE: (8,),
    GoalType.TRAIL_MAINTENANCE: (8,),
    GoalType.TRIATHLON_MAINTENANCE: (8,),
}

#: The athlete for every goal not in ``ATHLETES``: ``Athlete()`` is the
#: default athlete of each goal and tier; ``Athlete(volume_scale=0.6)`` a
#: lighter one.
ATHLETE: Athlete = Athlete()

#: Per-goal athletes, used at every tier of that goal. Uncomment the example
#: to plan Ironman for an age-grouper already on 10 hours a week; a sport
#: left out of ``hours`` or ``longest_min`` keeps its (scaled) default.
ATHLETES: dict[GoalType, Athlete] = {
    # GoalType.IRONMAN: Athlete(
    #     volume_scale=1.0,  # ignored for the sports given in ``hours``
    #     hours={
    #         SportType.SWIMMING: 2.0,
    #         SportType.CYCLING: 5.0,
    #         SportType.RUNNING: 3.0,
    #     },
    #     longest_min={
    #         SportType.SWIMMING: 45,  # an easy swim
    #         SportType.CYCLING: 180,  # a long ride
    #         SportType.RUNNING: 90,  # a long run
    #     },
    # ),
}

#: Also plan each goal with a B race, and with a B and a C race, at its
#: longest horizon (goals with a step-down race in ``B_RACE_GOAL`` only).
TUNE_UPS: bool = False

# ──────────────────────────────────────────────────────────────────────────

OUT = Path(tempfile.gettempdir()) / "endury_planner_sweep"
TEMPLATE = Path(__file__).with_name("sweep_site.html")
SPORTS = (SportType.SWIMMING, SportType.CYCLING, SportType.RUNNING)

# A B race is a shorter-distance sharpener, not a repeat of the A distance:
# an A-distance B race forces a full post-race recovery block that can starve
# the A goal's build of weeks.
B_RACE_GOAL: dict[GoalType, GoalType] = {
    GoalType.IRONMAN: GoalType.HALF_IRONMAN,
    GoalType.HALF_IRONMAN: GoalType.OLYMPIC_TRIATHLON,
    GoalType.OLYMPIC_TRIATHLON: GoalType.SPRINT_TRIATHLON,
    GoalType.SPRINT_TRIATHLON: GoalType.SPRINT_TRIATHLON,
    GoalType.RUNNING_MARATHON: GoalType.RUNNING_HALF_MARATHON,
    GoalType.RUNNING_HALF_MARATHON: GoalType.RUNNING_10K,
    GoalType.RUNNING_10K: GoalType.RUNNING_5K,
    GoalType.RUNNING_5K: GoalType.RUNNING_5K,
}


def build_scenario(
    goal: GoalType,
    ambition: AmbitionLevel,
    weeks: int,
    athlete: Athlete,
    *,
    include_b: bool = False,
    include_c: bool = False,
) -> Scenario:
    """One grid plan, its athlete written out in full.

    Args:
        goal: Goal type for the A race.
        ambition: Ambition level.
        weeks: Weeks to the A race from ``DEFAULT_START``.
        athlete: Changes to the goal's default athlete for the tier.
        include_b: Add a step-down-distance B race near the middle of the plan.
        include_c: Add a C race, two steps down, five weeks before the B race.
    """
    base = default_profile_for(goal, ambition)
    hours = {
        s: athlete.hours.get(s, st.recent_weekly_hours * athlete.volume_scale)
        for s, st in base.sports.items()
    }
    longest = {
        (s, family): athlete.longest_min.get(s, minutes)
        for s, st in base.sports.items()
        for family, minutes in st.longest_recent_min.items()
    }
    a_date = DEFAULT_START + timedelta(weeks=weeks, days=-1)
    goals: list[RaceGoal] = [RaceGoal(goal, a_date, "A")]
    # The B race week falls before the A peak (``weeks - 6``) and leaves room
    # for a C race five weeks earlier; race dates are Sundays (``days=-1``),
    # so ``c_weeks >= 4`` keeps C at or after the phase planner's week 3.
    b_weeks = min(max(8, weeks // 2), weeks - 6)
    if include_c:
        c_weeks = max(4, b_weeks - 5)
        c_goal = B_RACE_GOAL[B_RACE_GOAL[goal]]
        goals.append(
            RaceGoal(c_goal, DEFAULT_START + timedelta(weeks=c_weeks, days=-1), "C")
        )
    if include_b:
        goals.append(
            RaceGoal(
                B_RACE_GOAL[goal],
                DEFAULT_START + timedelta(weeks=b_weeks, days=-1),
                "B",
            )
        )
    suffix = "_BC" if include_c else "_B" if include_b else ""
    label = f"{goal.value} {weeks}-week plan, {ambition.value} ambition"
    if include_b:
        label += " (with B race)" if not include_c else " (with B+C race)"
    return Scenario(
        name=f"{goal.value}_{ambition.value}_{weeks}wk{suffix}",
        description=label + ".",
        tags=(goal.value, ambition.value, f"{weeks}wk"),
        goals=tuple(goals),
        ambition=ambition,
        recent_hours=hours,
        longest_recent=longest,
    )


def cell_scenarios(goal: GoalType, ambition: AmbitionLevel) -> list[Scenario]:
    """The plans of one grid cell: every horizon, then the tune-up variants."""
    athlete = ATHLETES.get(goal, ATHLETE)
    weeks = HORIZONS[goal]
    scenarios = [build_scenario(goal, ambition, w, athlete) for w in weeks]
    if TUNE_UPS and goal in B_RACE_GOAL and not is_maintenance_goal(goal):
        scenarios += [
            build_scenario(goal, ambition, weeks[-1], athlete, include_b=True),
            build_scenario(
                goal, ambition, weeks[-1], athlete, include_b=True, include_c=True
            ),
        ]
    return scenarios


def _athlete(scenario: Scenario) -> str:
    hours = scenario.recent_hours or {}
    longest = scenario.longest_recent or {}
    return (
        "Recent weekly hours: "
        + ", ".join(f"{s.value} {h:.1f}" for s, h in hours.items())
        + ". Longest recent: "
        + ", ".join(f"{s.value} {m} min" for (s, _family), m in longest.items())
        + "."
    )


def plan_data(scenario: Scenario) -> dict[str, Any]:
    """One plan in the page's shape: its weeks, each day's sessions and their cards.

    A week is ``[phase, mode, load (TSS), [swim, bike, run hours], days]`` and
    a session ``[name, minutes, sport, flags, card]``, ``name`` and ``card``
    indexing the plan's ``names`` and ``cards``; ``flags`` sums 1 for a
    quality session, 2 for a test and 4 for an optional one.
    """
    goal = scenario.goals[0].goal_type
    head: dict[str, Any] = {
        "name": scenario.name,
        "goal": goal.value,
        "tier": scenario.ambition.value,
        "about": scenario.description,
        "athlete": _athlete(scenario),
    }
    try:
        result = run_full_pipeline(scenario)
    except Exception as exc:  # a plan the planner rejects is still listed
        return head | {"error": repr(exc)}
    ensure_registry_loaded()
    resolver = build_synthetic_resolver()
    css = resolver.swim_css_sec_per_100m or DEFAULT_SWIM_CSS_SEC_PER_100M
    by_slot = {s.slot_id: s for s in result.sessions}
    names: list[str] = []
    cards: list[str] = []
    weeks: list[list[Any]] = [
        [ws.phase.value, ws.mode.value, 0.0, [0.0, 0.0, 0.0], [[] for _ in range(7)]]
        for ws in result.plan.weeks
    ]
    # The athlete's cards rotate their sets (endury.planner.activity.sets).
    occurrences = set_occurrences(result.assignments)
    for a in sorted(result.assignments, key=lambda a: (a.date, a.slot_id)):
        placed = by_slot[a.slot_id]
        activity = with_set(get_activity(a.activity_key), occurrences[a.slot_id])
        workout = activity.build(
            assignment_target_tss(activity, a.target_hours, resolver),
            resolver=resolver,
        )
        card = card_text(build_table_steps(workout.steps, swim_css_sec_per_100m=css))
        name = activity_display_name(a.activity_key)
        for table, text in ((names, name), (cards, card)):
            if text not in table:
                table.append(text)
        flags = (
            activity.hardness.is_hard_overall
            + 2 * (activity.family == ActivityFamily.TEST)
            + 4 * (placed.optional_reason is not None)
        )
        week = weeks[placed.week]
        week[2] += a.target_hours * result.session_tss_per_hour.get(a.slot_id, 0.0)
        week[3][SPORTS.index(placed.sport)] += a.target_hours
        week[4][a.date.weekday()].append(
            [
                names.index(name),
                round(a.target_hours * 60),
                placed.sport.value,
                flags,
                cards.index(card),
            ]
        )
    for week in weeks:
        week[2] = round(week[2])
        week[3] = [round(h, 2) for h in week[3]]
    validation = _run_validation(result)
    return head | {
        "races": [
            [e.week, e.day_of_week, e.goal.priority, e.goal.goal_type.value]
            for e in result.plan.race_events
        ],
        "issues": validation["errors"] + validation["warnings"],
        "names": names,
        "cards": cards,
        "weeks": weeks,
    }


def render(plans: list[dict[str, Any]]) -> str:
    """The self-contained page."""
    return TEMPLATE.read_text(encoding="utf-8").replace(
        "/*DATA*/null", json.dumps(plans).replace("</", "<\\/")
    )


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--goals", default="", help="comma-separated; default the plans on offer"
    )
    parser.add_argument(
        "--tiers", default="", help="comma-separated; default the plans on offer"
    )
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument(
        "--no-open", action="store_true", help="do not open the page in a browser"
    )
    args = parser.parse_args(argv)
    goals = [GoalType(g) for g in args.goals.split(",") if g] or list(GOALS)
    tiers = [AmbitionLevel(t) for t in args.tiers.split(",") if t] or list(TIERS)
    everything = bool(args.goals or args.tiers)
    scenarios = [
        s
        for g in goals
        for t in tiers
        if everything or t in BLUEPRINTS[g].offered
        for s in cell_scenarios(g, t)
    ]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        plans = list(pool.map(plan_data, scenarios))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(render(plans), encoding="utf-8")
    rejected = sum("error" in p for p in plans)
    print(
        f"wrote {page}: {len(plans)} plans"
        + (f", {rejected} rejected" if rejected else "")
    )
    if not args.no_open:
        webbrowser.open(page.resolve().as_uri())


if __name__ == "__main__":
    main()
