"""Daily CTL/TSB projections for a materialized planner plan.

Feeds the Race Day Outlook / macro charts: a per-sport daily Banister
forecast over the plan's schedule, seeded from the same derivation the
macro QP plans from (the opening week's session prices) and converted to
daily-TSS space (÷7, the inverse of the athlete-state bridge's x7).

Uses ``endury.intervals.fitness.advance_training_load`` for the day-step
(42-day CTL, 7-day ATL, TrainingPeaks-style EWA); lives outside planner
because planner works in weekly space.
"""

from __future__ import annotations

from datetime import date, timedelta

from endury.intervals.fitness import advance_training_load
from endury.planner.foundations.request import AthleteState
from endury.planner.foundations.types import ActivityKey, GoalType
from endury.planner.pipeline import initial_load_state
from endury.planning.wire.types import (
    ProjectionsPayload,
    SerializedSessionPayload,
)

#: Weekly→daily TSS bridge (inverse of fitness.DAILY_TO_WEEKLY_TSS).
_WEEKLY_TO_DAILY = 1.0 / 7.0


def build_projections(
    *,
    sessions: list[SerializedSessionPayload],
    athlete: AthleteState,
    goal_type: GoalType,
    plan_start: date,
    goal_date: date,
) -> ProjectionsPayload:
    """Build daily CTL/TSB forecast arrays for the outlook charts.

    Args:
        sessions: Materialized session payloads (date + achieved TSS).
        athlete: Per-sport athlete state (post-defaults) — CTL/ATL seeds.
        goal_type: The A-goal type (chart weighting hint).
        plan_start: First plan day (array index 0).
        goal_date: Race day (last array index).

    Returns:
        Projections payload with per-sport and aggregate daily arrays.
    """
    days = (goal_date - plan_start).days + 1
    opening_week: list[tuple[ActivityKey, float]] = []
    for p in sessions:
        if p["week_index"] != 0:
            continue
        minutes = p.get("duration_target_minutes")
        if minutes is None:
            minutes = p["duration_minutes"]
        opening_week.append((ActivityKey(p["activity_key"]), minutes / 60.0))
    ctl_w, atl_w = initial_load_state(athlete, goal_type, opening_week)

    tss_by_sport_day: dict[str, dict[str, float]] = {}
    for session in sessions:
        per_day = tss_by_sport_day.setdefault(session["sport"], {})
        per_day[session["date"]] = (
            per_day.get(session["date"], 0.0) + session["achieved_tss"]
        )

    ctl_by_sport: dict[str, list[float]] = {}
    tsb_by_sport: dict[str, list[float]] = {}
    for sport in ctl_w:
        daily_tss = tss_by_sport_day.get(sport.value, {})
        ctl = ctl_w[sport] * _WEEKLY_TO_DAILY
        atl = atl_w[sport] * _WEEKLY_TO_DAILY
        ctl_series: list[float] = []
        tsb_series: list[float] = []
        for offset in range(days):
            day = (plan_start + timedelta(days=offset)).isoformat()
            tss = daily_tss.get(day, 0.0)
            ctl, atl = advance_training_load(ctl, atl, tss)
            ctl_series.append(round(ctl, 2))
            tsb_series.append(round(ctl - atl, 2))
        ctl_by_sport[sport.value] = ctl_series
        tsb_by_sport[sport.value] = tsb_series

    aggregate_ctl = [
        round(sum(series[i] for series in ctl_by_sport.values()), 2)
        for i in range(days)
    ]
    aggregate_tsb = [
        round(sum(series[i] for series in tsb_by_sport.values()), 2)
        for i in range(days)
    ]
    return {
        "ctl_by_sport": ctl_by_sport,
        "tsb_by_sport": tsb_by_sport,
        "aggregate_ctl": aggregate_ctl,
        "aggregate_tsb": aggregate_tsb,
        "goal_type": goal_type.value,
    }
