"""Session serialization helpers for the planner views."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from endury.common import clock

logger = logging.getLogger(__name__)

from endury.database.sqlite.repositories.write_models import SessionInsertRow
from endury.planning.wire.types import (
    ActivePlanPayload,
    CurrentPhasePayload,
    JsonObject,
    PersistedPlanSessionPayload,
    PlannedMetricsPayload,
    SerializedSessionPayload,
    WorkoutPayload,
)
from endury.planning.wire.workout_steps import (
    _headline_duration_minutes,
    _round_distance_meters,
    _round_duration_minutes,
    _serialize_workout_step,
    _total_workout_distance_meters,
)


def serialize_active_plan(
    plan: Any,
    sessions: list[Any],
    *,
    today: date | None = None,
) -> ActivePlanPayload:
    """Build a compact active-plan summary for frontend surfaces."""
    current_date = today or clock.today()
    serialized_sessions = [
        serialize_plan_session_row(item, today=current_date) for item in sessions
    ]
    # An optional session is outside the plan's load (R-SESS-037): neither
    # a workout owed nor a miss.
    booked = [item for item in serialized_sessions if not item["optional"]]
    total = len(booked)
    completed = sum(1 for item in booked if item["completed"])
    # A deliberate skip is neither a miss nor still to come, so it is
    # counted on its own axis and taken out of both of the others.
    skipped = sum(1 for item in booked if item["status"] == "skipped")
    upcoming = sum(
        1
        for item in booked
        if not item["completed"]
        and item["status"] != "skipped"
        and item["date"] >= current_date.isoformat()
    )
    missed = sum(1 for item in booked if item["status"] == "missed")
    completion_pct = (completed / total * 100.0) if total else 0.0
    # After the goal date the plan has no current phase — without this
    # check the walk below reports "taper" forever.
    current_phase: CurrentPhasePayload | None = None
    if current_date.isoformat() <= str(plan.goal_date):
        phase_name = _active_phase_name(serialized_sessions, current_date)
        current_phase = {"name": phase_name.title(), "phase_type": phase_name}
    return {
        "id": plan.id,
        "name": plan.name,
        "goal_type": plan.goal_type,
        "goal_date": plan.goal_date,
        "start_date": plan.plan_start,
        "end_date": plan.goal_date,
        "current_phase": current_phase,
        "compliance": {
            "total_workouts": total,
            "completed": completed,
            "upcoming": upcoming,
            "missed": missed,
            "skipped": skipped,
            "completion_rate_pct": round(completion_pct, 1),
        },
        "sessions_count": total,
    }


def serialize_plan_session_row(
    row: Any,
    *,
    today: date | None = None,
) -> PersistedPlanSessionPayload:
    """Serialize one persisted plan session into the canonical API shape.

    Delivered metrics are not part of this payload: completion comes from
    intervals.icu at read time (see ``endury/planning/calendar_data.py``).

    Args:
        row: Persisted plan session row.
        today: Override for "today" (tests); defaults to the current date.
    """
    workout = _coerce_json_object(getattr(row, "workout_json", None))
    # A row whose workout has no derivable step headline falls back to its
    # stored duration, reconciled here rather than by a migration.
    step_minutes = _headline_duration_minutes(workout.get("steps") or [])
    duration_minutes = (
        step_minutes
        if step_minutes is not None
        else getattr(row, "duration_minutes", None)
    )
    matched_activity_id = getattr(row, "matched_activity_id", None)
    completed = _session_is_completed(row, matched_activity_id)
    status = _session_status(row, completed, today or clock.today())

    description = _session_description(row, workout)
    coaching_intent = str(workout.get("coaching_intent") or "")
    planned_metrics = _planned_metrics(row, duration_minutes, workout)
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "date": row.date,
        "week_index": row.week_index,
        "phase_type": row.phase_type,
        "sport": row.sport,
        "activity_key": row.activity_key,
        "family": row.family,
        "description": description,
        "coaching_intent": coaching_intent,
        "coaching_notes": _string_list(workout.get("coaching_notes")),
        "why_details": _string_list(workout.get("why_details")),
        "target_tss": round(float(row.target_tss), 1),
        "duration_minutes": duration_minutes,
        "workout": workout,
        "status": status,
        "completed": completed,
        "actual_activity_id": matched_activity_id,
        "matched_activity_id": matched_activity_id,
        "planned_metrics": planned_metrics,
        "optional": bool(getattr(row, "optional", False)),
    }


def _string_list(raw: Any) -> list[str]:
    """Coerce a persisted ``workout_json`` list field to ``list[str]``.

    Rows committed before the field existed yield ``[]``.

    Args:
        raw: The raw value read out of ``workout_json``.

    Returns:
        The strings it holds, or an empty list.
    """
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _session_description(row: Any, workout: JsonObject) -> str:
    """Build the user-facing session description."""
    raw_name = str(workout.get("name") or "").strip()
    if not raw_name:
        return getattr(row, "activity_key", "").replace("_", " ").title()
    if "_" not in raw_name or " " in raw_name:
        return raw_name
    stripped = re.sub(r"^(run|bike|swim|trail)_", "", raw_name)
    return stripped.replace("_", " ").title()


def _session_is_completed(row: Any, matched_activity_id: Any | None) -> bool:
    """Return whether a persisted session should be treated as completed."""
    return getattr(row, "status", "") == "completed" or matched_activity_id is not None


def _session_status(row: Any, completed: bool, today: date) -> str:
    """Derive the render status for a persisted session row.

    "missed" is never stored — a past-dated session with no matched
    activity derives it at read time, so a late intervals.icu upload can
    still flip it back to completed. Deliberate "skipped" marks are
    preserved, and an optional session (R-SESS-037) is never missed.
    """
    if completed:
        return "completed"
    status = str(getattr(row, "status", "pending"))
    if (
        status in ("pending", "unlinked")
        and str(row.date) < today.isoformat()
        and not getattr(row, "optional", False)
    ):
        return "missed"
    return status


def _planned_metrics(
    row: Any, duration_minutes: Any, workout: JsonObject
) -> PlannedMetricsPayload:
    """Build planned metrics for a persisted session row."""
    distance = _workout_dict_distance_meters(workout.get("steps") or [])
    return {
        "duration_sec": (
            float(duration_minutes) * 60.0 if duration_minutes is not None else None
        ),
        "distance_m": round(distance) if distance > 0 else None,
        "tss": round(float(row.target_tss), 1),
    }


def _workout_dict_distance_meters(steps: list[Any]) -> float:
    """Sum step distances over a persisted ``workout_json`` step list.

    The dict twin of ``workout_steps._sum_step_quantities`` (which walks
    ``StructuredWorkout`` objects): persisted steps carry a flat
    ``duration_meters`` plus ``iterations``/``steps`` for repeats.
    """
    total = 0.0
    for step in steps:
        if not isinstance(step, dict):
            continue
        children = step.get("steps") or []
        if children:
            nested = _workout_dict_distance_meters(children)
            total += nested * float(step.get("iterations") or 1)
            continue
        total += float(step.get("duration_meters") or 0.0)
    return total


def serialize_planned_session(
    *,
    slot_id: str,
    session_date: date,
    week_index: int,
    phase_type: str,
    sport: str,
    activity_key: str,
    family: str,
    is_recovery_week: bool,
    coaching_intent: str,
    hardness: Any,
    workout: Any,
    workout_name: str,
    target_tss: float,
    achieved_tss: float,
    duration_minutes: float,
    duration_target_minutes: float | None,
    tid_fractions: Any | None,
    coaching_notes: Sequence[str] = (),
    why_details: Sequence[str] = (),
    optional: bool = False,
) -> SerializedSessionPayload:
    """Serialize one materialized planner session into the API shape.

    Same wire payload the frontend consumes for persisted sessions, but fed
    from planner primitives: enum values arrive pre-stringified,
    ``hardness`` and ``tid_fractions`` are the planner value objects, and
    ``workout`` is a ``endury.training`` ``StructuredWorkout``.

    Args:
        slot_id: Stable session slot identifier.
        session_date: Calendar date the session is assigned to.
        week_index: 0-indexed plan week.
        phase_type: Phase value string (``base``/``build``/``peak``/``taper``).
        sport: Sport value string.
        activity_key: Activity key value string.
        family: Activity family value string.
        is_recovery_week: Whether the owning week is a recovery week.
        coaching_intent: Deterministic per-session intent sentence.
        hardness: Object with the three ``is_hard_*`` booleans.
        workout: Structured workout with ``steps``.
        workout_name: Athlete-facing workout title.
        target_tss: Planned TSS for the session.
        achieved_tss: TSS after deterministic quantization.
        duration_minutes: Materialized duration in minutes. The served
            headline is the rounded-step sum; this is the fallback for
            workouts with no timed steps.
        duration_target_minutes: Planner hours-space duration target.
        tid_fractions: Optional TID fractions value object.
        coaching_notes: Session-spanning execution cues (HTML-safe).
        why_details: Athlete-facing "why this session" paragraphs.
        optional: The session is optional, outside the plan's load.

    Returns:
        The serialized session payload.
    """
    distance_meters = _total_workout_distance_meters(workout)
    steps = [_serialize_workout_step(step) for step in workout.steps]
    workout_payload: WorkoutPayload = {"name": workout_name, "steps": steps}
    step_minutes = _headline_duration_minutes(steps)
    return {
        "slot_id": slot_id,
        "date": session_date.isoformat(),
        "week_index": week_index,
        "phase_type": phase_type,
        "sport": sport,
        "activity_key": activity_key,
        "family": family,
        "is_recovery_week": is_recovery_week,
        "coaching_intent": coaching_intent,
        "coaching_notes": list(coaching_notes),
        "why_details": list(why_details),
        "target_tss": round(target_tss, 1),
        "achieved_tss": round(achieved_tss, 1),
        "duration_minutes": (
            step_minutes
            if step_minutes is not None
            else _round_duration_minutes(duration_minutes)
        ),
        "distance_meters": (
            _round_distance_meters(distance_meters)
            if sport == "swimming"
            else float(round(distance_meters / 10) * 10)
        ),
        "hardness": {
            "is_hard_overall": hardness.is_hard_overall,
            "is_hard_physiologically": hardness.is_hard_physiologically,
            "is_hard_mentally": hardness.is_hard_mentally,
        },
        "workout": workout_payload,
        "duration_target_minutes": duration_target_minutes,
        "tid_fractions": (
            {
                "z_low": tid_fractions.z_low,
                "z_mid": tid_fractions.z_mid,
                "z_high": tid_fractions.z_high,
            }
            if tid_fractions is not None
            else None
        ),
        "optional": optional,
    }


def build_commit_session_rows(
    sessions: list[SerializedSessionPayload],
) -> list[SessionInsertRow]:
    """Return typed DB rows for committed planner sessions."""
    rows: list[SessionInsertRow] = []
    for session in sessions:
        workout_dict = dict(session.get("workout", {}))
        if session.get("coaching_intent"):
            workout_dict["coaching_intent"] = session["coaching_intent"]
        if session.get("coaching_notes"):
            workout_dict["coaching_notes"] = list(session["coaching_notes"])
        if session.get("why_details"):
            workout_dict["why_details"] = list(session["why_details"])
        raw_tid = session.get("tid_fractions")
        if raw_tid is not None and not (
            isinstance(raw_tid, dict) and {"z_low", "z_mid", "z_high"} <= raw_tid.keys()
        ):
            logger.warning(
                "Malformed tid_fractions for %s, discarding: %r",
                session.get("slot_id", "?"),
                raw_tid,
            )
            raw_tid = None
        tid_json = json.dumps(raw_tid) if raw_tid is not None else None
        rows.append(
            SessionInsertRow(
                slot_id=session["slot_id"],
                date=session["date"],
                week_index=session["week_index"],
                phase_type=session["phase_type"],
                sport=session["sport"],
                activity_key=session["activity_key"],
                family=session["family"],
                target_tss=session["target_tss"],
                achieved_tss=session["achieved_tss"],
                duration_minutes=session.get("duration_minutes"),
                workout_json=json.dumps(workout_dict),
                duration_target_minutes=session.get("duration_target_minutes"),
                tid_fractions_json=tid_json,
                optional=bool(session.get("optional", False)),
            )
        )
    return rows


def _active_phase_name(
    serialized_sessions: list[PersistedPlanSessionPayload],
    today: date,
) -> str:
    """Pick the current phase from serialized plan sessions."""
    if not serialized_sessions:
        return "base"
    today_str = today.isoformat()
    phase = str(serialized_sessions[0].get("phase_type", "base"))
    for session in sorted(serialized_sessions, key=lambda item: item["date"]):
        if session["date"] <= today_str:
            phase = str(session.get("phase_type", phase))
    return phase


def _coerce_json_object(raw: Any) -> JsonObject:
    """Parse a dict-like JSON payload safely."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
