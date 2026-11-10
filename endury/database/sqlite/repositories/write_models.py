"""Database write payloads for planner persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionInsertRow:
    """Typed row for PlanRepository.bulk_insert_sessions().

    Args:
        slot_id: Unique identifier for the session slot.
        date: ISO-format date string for the session.
        week_index: Zero-based week index within the plan.
        phase_type: Training phase (base, build, peak, taper).
        sport: Sport type string (running, cycling, swimming).
        activity_key: Activity definition key.
        family: Activity family (easy, long, tempo, threshold, etc.).
        target_tss: Target training stress score.
        achieved_tss: Achieved TSS after materialization.
        duration_minutes: Session duration in minutes, if known.
        workout_json: JSON-encoded structured workout.
        duration_target_minutes: The macro QP's hours-space duration target in
            minutes (the immutable base later week-adjustments scale from).
        tid_fractions_json: JSON-encoded TIDFractions (z_low/z_mid/z_high).
        optional: The session is optional, outside the plan's load.
    """

    slot_id: str
    date: str
    week_index: int
    phase_type: str
    sport: str
    activity_key: str
    family: str
    target_tss: float
    achieved_tss: float
    duration_minutes: float | None
    workout_json: str
    duration_target_minutes: float | None = None
    tid_fractions_json: str | None = None
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for repository insertion."""
        return {
            "slot_id": self.slot_id,
            "date": self.date,
            "week_index": self.week_index,
            "phase_type": self.phase_type,
            "sport": self.sport,
            "activity_key": self.activity_key,
            "family": self.family,
            "target_tss": self.target_tss,
            "achieved_tss": self.achieved_tss,
            "duration_minutes": self.duration_minutes,
            "workout_json": self.workout_json,
            "duration_target_minutes": self.duration_target_minutes,
            "tid_fractions_json": self.tid_fractions_json,
            "optional": self.optional,
        }


__all__ = ["SessionInsertRow"]
