"""Macro-plan and safety serialization helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from endury.planning.wire.types import (
    MacroPlanPayload,
    MacroWeekPayload,
    SerializedSessionPayload,
)

if TYPE_CHECKING:
    from endury.planner.foundations.types import AmbitionLevel, PhasePlan


def serialize_macro_plan(
    phase_plan: PhasePlan,
    sessions: list[SerializedSessionPayload],
    *,
    ambition: AmbitionLevel,
    plan_start: str,
    coaching_brief: str,
    revision: int = 1,
) -> MacroPlanPayload:
    """Synthesize the lean macro payload from a planner phase plan.

    Per-week TSS/hours/session counts are aggregated from the materialized
    sessions; phase, recovery flag, and sport split come from the WeekSpecs.

    Args:
        phase_plan: The L1 phase plan.
        sessions: Materialized session payloads (``serialize_planned_session``).
        ambition: Ambition level of the request.
        plan_start: ISO plan start date.
        coaching_brief: Deterministic coach brief for the plan.
        revision: Macro revision number.

    Returns:
        The serialized macro plan.
    """
    tss_by_week: dict[int, float] = {}
    minutes_by_week: dict[int, float] = {}
    count_by_week: dict[int, int] = {}
    for session in sessions:
        week = session["week_index"]
        tss_by_week[week] = tss_by_week.get(week, 0.0) + session["target_tss"]
        minutes_by_week[week] = (
            minutes_by_week.get(week, 0.0) + session["duration_minutes"]
        )
        count_by_week[week] = count_by_week.get(week, 0) + 1

    weeks: list[MacroWeekPayload] = [
        {
            "week_index": w.week,
            "phase": w.phase.value,
            "is_recovery": w.mode.value == "recovery",
            "tss_target": round(tss_by_week.get(w.week, 0.0), 1),
            "hours_target": round(minutes_by_week.get(w.week, 0.0) / 60.0, 2),
            "sport_split": {s.value: share for s, share in w.sport_split.items()},
            "session_count": count_by_week.get(w.week, 0),
        }
        for w in phase_plan.weeks
    ]
    return {
        "goal_type": phase_plan.a_goal.goal_type.value,
        "goal_date": phase_plan.a_goal.race_date.isoformat(),
        "plan_start": plan_start,
        "total_weeks": len(phase_plan.weeks),
        "ambition_level": ambition.value,
        "coaching_brief": coaching_brief,
        "revision": revision,
        "weeks": weeks,
    }
