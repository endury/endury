"""Materialize a planner ``PlanResult`` into API session payloads.

Bridges the planner's hours-space output (``PlacedSession`` +
``ActivityAssignment``) to the frontend/persistence session shape
(``SerializedSessionPayload``): each assignment's hours become a target
TSS via the activity's own physics, the activity builds its structured
workout, and the existing session serializer produces the wire payload.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from endury.common import clock
from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import (
    activity_brief,
    activity_display_name,
    assignment_target_tss,
    workout_total_meters,
)
from endury.planner.activity.sets import set_occurrences, with_set
from endury.planner.activity.steps import session_coaching_notes
from endury.planner.foundations.types import (
    ActivityFamily,
    PlacedSession,
    SessionOrigin,
    WeekMode,
)
from endury.planner.pipeline import PlanResult
from endury.planner.sessions.why import compose_why_details
from endury.planning.wire.sessions import serialize_planned_session
from endury.planning.wire.types import SerializedSessionPayload
from endury.training.load import ResolverProtocol

#: The title prefix of an optional session (R-SESS-037).
OPTIONAL_TITLE_PREFIX = "Optional: "


def drop_past(
    sessions: list[SerializedSessionPayload],
    training_start: date,
    today: date | None = None,
) -> list[SerializedSessionPayload]:
    """Drop the days of a plan's opening week that are already behind us.

    A plan generated today starts on *this* week's Monday so the athlete
    begins immediately, but week 0 can then open on days already elapsed;
    trimming here keeps the preview, the persisted rows, and the pushed
    intervals.icu events in agreement. Only a plan starting in the current
    week is trimmed — a start further back is a deliberate historical plan
    (replan replays, reports) and is returned whole.

    Args:
        sessions: Materialized sessions, ordered by date.
        training_start: The plan's first Monday.
        today: Reference date (defaults to the app clock).

    Returns:
        The sessions dated today onwards, in input order.
    """
    today = today or clock.today()
    if training_start != today - timedelta(days=today.weekday()):
        return sessions
    cutoff = today.isoformat()
    return [session for session in sessions if session["date"] >= cutoff]


def materialize_sessions(
    result: PlanResult,
    resolver: ResolverProtocol | None,
) -> list[SerializedSessionPayload]:
    """Materialize every assigned session of a plan into API payloads.

    Args:
        result: Pipeline output (phase plan, sized sessions, assignments).
        resolver: Athlete threshold resolver (``None`` → registry defaults).

    Returns:
        Serialized sessions ordered by date then slot_id.
    """
    ensure_registry_loaded()
    by_slot: dict[str, PlacedSession] = {s.slot_id: s for s in result.sessions}
    weeks = result.phase_plan.weeks
    goal = result.phase_plan.a_goal.goal_type
    ordered = sorted(result.assignments, key=lambda a: (a.date.isoformat(), a.slot_id))

    # First pass: build every workout, so the why-composer can read the
    # whole plan's realized durations before any payload is serialized. A
    # session type that recurs rotates through its sets.
    built: dict[str, tuple[Any, Any, float, float]] = {}
    materialized: dict[str, tuple[float, float]] = {}
    occurrences = set_occurrences(ordered)
    for assignment in ordered:
        activity = with_set(
            get_activity(assignment.activity_key), occurrences[assignment.slot_id]
        )
        target_tss = assignment_target_tss(activity, assignment.target_hours, resolver)
        workout = activity.build(target_tss, resolver=resolver)
        duration_min = activity.duration_minutes_exact(target_tss, resolver=resolver)
        meters = workout_total_meters(workout.steps)
        built[assignment.slot_id] = (activity, workout, target_tss, duration_min)
        materialized[assignment.slot_id] = (duration_min, meters)

    why_details = compose_why_details(result, materialized)

    payloads: list[SerializedSessionPayload] = []
    for assignment in ordered:
        placed = by_slot[assignment.slot_id]
        week_spec = weeks[placed.week]
        activity, workout, target_tss, duration_min = built[assignment.slot_id]
        family = (
            ActivityFamily.TEST
            if placed.origin is SessionOrigin.TEST
            else activity.family
        )

        payloads.append(
            serialize_planned_session(
                slot_id=assignment.slot_id,
                session_date=assignment.date,
                week_index=placed.week,
                phase_type=week_spec.phase.value,
                sport=placed.sport.value,
                activity_key=assignment.activity_key.value,
                family=activity.family.value,
                is_recovery_week=week_spec.mode is WeekMode.RECOVERY,
                coaching_intent=activity_brief(assignment.activity_key, family),
                hardness=activity.hardness,
                workout=workout,
                # An optional session (R-SESS-037) says so in its title, on
                # the card and on the intervals.icu calendar.
                workout_name=(OPTIONAL_TITLE_PREFIX if placed.optional_reason else "")
                + activity_display_name(assignment.activity_key),
                target_tss=target_tss,
                achieved_tss=activity.achieved_tss(target_tss, resolver=resolver),
                duration_minutes=duration_min,
                duration_target_minutes=round(assignment.target_hours * 60.0, 1),
                tid_fractions=activity.tid_fractions(target_tss, resolver=resolver),
                coaching_notes=session_coaching_notes(
                    activity,
                    workout,
                    duration_min=duration_min,
                    resolver=resolver,
                    goal=goal,
                ),
                why_details=why_details.get(assignment.slot_id, []),
                optional=placed.optional_reason is not None,
            )
        )
    return payloads
