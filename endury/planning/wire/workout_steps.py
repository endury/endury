"""Structured-workout step serialization for planner session payloads.

Recursive step/target/duration serialization plus the shared rounding
helpers used by both session producers in ``sessions.py``.
"""

from __future__ import annotations

from typing import Any

from endury.planner.activity.config import SWIM_STEP_METERS
from endury.planning.wire.types import (
    WorkoutDurationPayload,
    WorkoutStepPayload,
    WorkoutTargetPayload,
)


def _serialize_workout_step(step: Any) -> WorkoutStepPayload:
    """Serialize one structured workout step recursively."""
    duration_seconds = _round_step_seconds(
        float(step.duration.value_seconds)
        if step.duration and step.duration.value_seconds is not None
        else 0.0
    )
    duration_meters = _round_distance_meters(
        float(step.duration.value_meters)
        if step.duration and step.duration.value_meters is not None
        else 0.0
    )
    payload: WorkoutStepPayload = {
        "type": step.type,
        "description": step.description or "",
        "duration_seconds": duration_seconds,
        "duration_meters": duration_meters,
        "duration": _serialize_duration(duration_seconds, duration_meters),
        "target": _serialize_target(step.target),
        "secondary_target": _serialize_target(step.secondary_target),
    }
    raw_additional = getattr(step, "additional_targets", None) or []
    serialized_additional = [
        t for t in (_serialize_target(t) for t in raw_additional) if t is not None
    ]
    if serialized_additional:
        payload["additional_targets"] = serialized_additional
    if step.iterations is not None:
        payload["iterations"] = int(step.iterations)
    if step.steps:
        payload["steps"] = [_serialize_workout_step(child) for child in step.steps]
    return payload


def _serialize_duration(
    seconds: float,
    meters: float,
) -> WorkoutDurationPayload:
    """Serialize step duration for frontend consumption."""
    if meters > 0:
        return {
            "duration_type": "distance",
            "value_meters": meters,
            "value_seconds": seconds,
        }
    if seconds > 0:
        return {"duration_type": "time", "value_seconds": seconds}
    return {"duration_type": "open"}


_TARGET_RANGE_FIELDS: tuple[str, ...] = (
    "low_min_per_km",
    "high_min_per_km",
    "low_sec_per_100m",
    "high_sec_per_100m",
    "low_bpm",
    "high_bpm",
    "low_watts",
    "high_watts",
    "low_rpm",
    "high_rpm",
    "low_kph",
    "high_kph",
)


def _serialize_target(target: Any) -> WorkoutTargetPayload | None:
    """Serialize a TargetDef to a JSON-friendly dict."""
    if target is None:
        return None
    result: WorkoutTargetPayload = {"target_type": target.type}
    if target.zone is not None:
        result["zone"] = int(target.zone)
    if getattr(target, "rpe", None) is not None:
        result["rpe"] = int(target.rpe)
    for field in _TARGET_RANGE_FIELDS:
        value = getattr(target, field, None)
        if value is not None:
            result[field] = value  # type: ignore[literal-required]
    return result


def _total_workout_distance_meters(workout: Any) -> float:
    """Return total distance meters across all workout steps."""
    _, total_meters = _sum_step_quantities(getattr(workout, "steps", []) or [])
    return total_meters


def _sum_step_quantities(steps: list[Any]) -> tuple[float, float]:
    """Accumulate time and distance over structured workout steps."""
    total_seconds = 0.0
    total_meters = 0.0
    for step in steps:
        if step.type == "repeat" and step.steps:
            nested_seconds, nested_meters = _sum_step_quantities(step.steps)
            iterations = float(step.iterations or 1)
            total_seconds += nested_seconds * iterations
            total_meters += nested_meters * iterations
            continue
        if step.duration is None:
            continue
        total_seconds += float(step.duration.value_seconds or 0.0)
        total_meters += float(step.duration.value_meters or 0.0)
    return total_seconds, total_meters


def _round_duration_minutes(minutes: float) -> float:
    """Round session duration to nearest 5 min for long sessions, 1 min for short."""
    if minutes >= 10.0:
        return float(round(minutes / 5) * 5)
    return float(round(minutes))


def _headline_duration_minutes(steps: list[Any]) -> float | None:
    """Return the served headline duration: the sum the athlete can add up.

    Every step is rounded on its own by :func:`_round_step_seconds`, so an
    independently rounded headline disagrees with the step list underneath
    it (a 60 min run whose steps add up to 56). The wire headline is
    therefore derived from the already-rounded steps.

    Args:
        steps: Serialized workout steps (the payload shape, not the
            ``StructuredWorkout`` objects).

    Returns:
        Whole minutes over the rounded steps, or ``None`` when there are no
        timed steps — nothing to add up, so the caller keeps its own
        headline.
    """
    total_seconds = _sum_payload_step_seconds(steps)
    if total_seconds <= 0.0:
        return None
    return float(round(total_seconds / 60.0))


def _sum_payload_step_seconds(steps: list[Any]) -> float:
    """Sum rounded step seconds the way the UI displays them.

    The payload twin of :func:`_sum_step_quantities`: a repeat block shows
    its children once with a ``xN`` badge, so the athlete's mental sum —
    and this one — is children x iterations.

    Args:
        steps: Serialized workout steps.

    Returns:
        Total seconds across the step tree.
    """
    total = 0.0
    for step in steps:
        if not isinstance(step, dict):
            continue
        children = step.get("steps") or []
        if children:
            total += _sum_payload_step_seconds(children) * float(
                step.get("iterations") or 1
            )
            continue
        total += float(step.get("duration_seconds") or 0.0)
    return total


def _round_step_seconds(seconds: float) -> float:
    """Round step duration: 1 min for >= 5 min, 30 s for >= 1 min, 5 s for short.

    Short steps keep 5 s so a 15 s recovery is not pushed as 20 s.
    """
    if seconds == 0.0:
        return 0.0
    if seconds >= 300.0:
        return float(round(seconds / 60) * 60)
    if seconds >= 60.0:
        return float(round(seconds / 30) * 30)
    return float(round(seconds / 5) * 5)


def _round_distance_meters(meters: float) -> float:
    """Round distance to the 50 m step the activity library emits."""
    if meters == 0.0:
        return 0.0
    rounded = round(meters / SWIM_STEP_METERS) * SWIM_STEP_METERS
    return float(max(SWIM_STEP_METERS, rounded))
