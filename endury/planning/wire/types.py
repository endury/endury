"""Typed payload shapes for planner serialization helpers."""

from __future__ import annotations

from datetime import date
from typing import Any, NotRequired

from typing_extensions import TypedDict

JsonObject = dict[str, Any]


class MacroWeekPayload(TypedDict):
    """Lean per-week macro payload synthesized from a planner PhasePlan.

    Exactly the fields the frontend macro charts read (plus hours); the
    planner produces no richer per-week guidance.
    """

    week_index: int
    phase: str
    is_recovery: bool
    tss_target: float
    hours_target: float
    sport_split: dict[str, float]
    session_count: int


class MacroPlanPayload(TypedDict):
    """Serialized planner macro plan payload."""

    goal_type: str
    goal_date: str
    plan_start: str
    total_weeks: int
    ambition_level: str
    coaching_brief: str
    revision: int
    weeks: list[MacroWeekPayload]


class HardnessPayload(TypedDict):
    """Serialized hardness payload."""

    is_hard_overall: bool
    is_hard_physiologically: bool
    is_hard_mentally: bool


class WorkoutDurationPayload(TypedDict, total=False):
    """Serialized workout-duration payload."""

    duration_type: str
    value_meters: float
    value_seconds: float


class WorkoutTargetPayload(TypedDict, total=False):
    """Serialized workout target payload."""

    target_type: str
    zone: int
    rpe: int
    low_min_per_km: float
    high_min_per_km: float
    low_sec_per_100m: float
    high_sec_per_100m: float
    low_bpm: int
    high_bpm: int
    low_watts: int
    high_watts: int
    low_rpm: int
    high_rpm: int
    low_kph: float
    high_kph: float


class WorkoutStepPayload(TypedDict, total=False):
    """Serialized workout-step payload."""

    type: str
    description: str
    duration_seconds: float
    duration_meters: float
    duration: WorkoutDurationPayload
    target: WorkoutTargetPayload | None
    secondary_target: WorkoutTargetPayload | None
    additional_targets: list[WorkoutTargetPayload]
    iterations: int
    steps: list[WorkoutStepPayload]


class WorkoutPayload(TypedDict):
    """Serialized workout payload."""

    name: str
    steps: list[WorkoutStepPayload]


class SerializedSessionPayload(TypedDict):
    """Serialized generated-session payload."""

    slot_id: str
    date: str
    week_index: int
    phase_type: str
    sport: str
    activity_key: str
    family: str
    is_recovery_week: bool
    coaching_intent: str
    coaching_notes: list[str]
    why_details: list[str]
    target_tss: float
    achieved_tss: float
    duration_minutes: float
    distance_meters: float
    hardness: HardnessPayload
    workout: WorkoutPayload
    duration_target_minutes: float | None
    tid_fractions: dict | None
    # Outside the plan's load (R-SESS-037); older stored payloads lack it.
    optional: NotRequired[bool]


class PlannedMetricsPayload(TypedDict):
    """Serialized planned metrics payload."""

    duration_sec: float | None
    distance_m: float | None
    tss: float


class PersistedPlanSessionPayload(TypedDict):
    """Serialized persisted plan-session payload."""

    id: Any
    plan_id: int
    date: str
    week_index: int
    phase_type: str
    sport: str
    activity_key: str
    family: str
    description: str
    coaching_intent: str
    coaching_notes: list[str]
    why_details: list[str]
    target_tss: float
    duration_minutes: float | None  # materialized duration
    workout: JsonObject
    status: str
    completed: bool
    actual_activity_id: Any | None
    matched_activity_id: Any | None
    planned_metrics: PlannedMetricsPayload
    optional: bool


SessionSummaryPayload = SerializedSessionPayload | PersistedPlanSessionPayload
SessionSummaryLike = SessionSummaryPayload | JsonObject


class CurrentPhasePayload(TypedDict):
    """Serialized active-phase payload."""

    name: str
    phase_type: str


class CompliancePayload(TypedDict):
    """Serialized compliance-summary payload."""

    total_workouts: int
    completed: int
    upcoming: int
    missed: int
    skipped: int
    completion_rate_pct: float


class ActivePlanPayload(TypedDict):
    """Serialized active-plan payload."""

    id: Any
    name: str
    goal_type: str
    goal_date: str | date
    start_date: str | date
    end_date: str | date
    current_phase: CurrentPhasePayload | None
    compliance: CompliancePayload
    sessions_count: int


class WeeklySummaryDayPayload(TypedDict):
    """Serialized weekly-summary day payload."""

    date: str
    weekday: str
    weekday_index: int
    session_count: int
    total_tss: float
    total_duration_minutes: float
    sessions: list[SessionSummaryLike]


class WeeklySummaryPayload(TypedDict):
    """Serialized weekly-summary payload."""

    week_index: int
    phase: str
    total_tss: float
    total_duration_minutes: float
    total_distance_meters: float
    session_count: int
    sport_tss: dict[str, float]
    sport_duration: dict[str, float]
    is_recovery_week: bool
    days: list[WeeklySummaryDayPayload]


class TsbFloorsPayload(TypedDict):
    """Serialized TSB floors payload."""

    per_sport: float
    weighted: float


class ProjectionsPayload(TypedDict, total=False):
    """Serialized projections payload."""

    ctl_by_sport: dict[str, list[float]]
    tsb_by_sport: dict[str, list[float]]
    aggregate_ctl: list[float]
    aggregate_tsb: list[float]
    tsb_floors: TsbFloorsPayload
    goal_type: str
