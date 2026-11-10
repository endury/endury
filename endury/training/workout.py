"""Pydantic models for structured workout steps and export.

The pure workout schema: container, steps, durations, and targets, plus
JSON round-tripping.
"""

from __future__ import annotations

import json

from pydantic import BaseModel


class DurationDef(BaseModel):
    """Duration definition for a workout step."""

    type: str  # "time" | "distance" | "lap_button" | "open" | "calories"
    value_seconds: float | None = None
    value_meters: float | None = None
    value_calories: float | None = None


class TargetDef(BaseModel):
    """Target definition for a workout step."""

    # open|pace|pace_zone|swim_pace|swim_pace_zone|heart_rate_zone|heart_rate|
    # power|power_zone|cadence|speed|rpe
    type: str
    zone: int | None = None
    low_min_per_km: float | None = None
    high_min_per_km: float | None = None
    low_sec_per_100m: float | None = None
    high_sec_per_100m: float | None = None
    low_bpm: int | None = None
    high_bpm: int | None = None
    low_watts: int | None = None
    high_watts: int | None = None
    low_rpm: int | None = None
    high_rpm: int | None = None
    low_kph: float | None = None
    high_kph: float | None = None
    rpe: int | None = None  # 1-10 rate of perceived exertion


class WorkoutStep(BaseModel):
    """A single step (or repeat group) within a structured workout."""

    type: str  # "warmup" | "cooldown" | "interval" | "recovery" | "rest" | "repeat"
    duration: DurationDef | None = None
    target: TargetDef | None = None
    secondary_target: TargetDef | None = None
    additional_targets: list[TargetDef] | None = None
    iterations: int | None = None  # repeat only
    steps: list[WorkoutStep] | None = None  # repeat only (recursive)
    description: str | None = None


WorkoutStep.model_rebuild()


class StructuredWorkout(BaseModel):
    """A structured workout with a sequence of steps."""

    workout_name: str | None = None
    steps: list[WorkoutStep]

    @classmethod
    def from_json(cls, json_str: str) -> StructuredWorkout:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls.model_validate(data)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json()


__all__ = ["DurationDef", "StructuredWorkout", "TargetDef", "WorkoutStep"]
