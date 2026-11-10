"""Activity capability metadata used by the selector and materializer layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from endury.planner.foundations.types import (
    ActivityFamily,
    GoalType,
    PhaseType,
    SportType,
)

ALL_GOAL_TYPES: tuple[GoalType, ...] = tuple(GoalType)
ALL_PHASE_TYPES: tuple[PhaseType, ...] = tuple(PhaseType)


class ActivityStimulusTag(StrEnum):
    """Controlled vocabulary for activity-level training stimulus tags."""

    AEROBIC = "aerobic"
    SUPPORT = "support"
    BRICK_SUPPORT = "brick_support"
    LONG_ENDURANCE = "long_endurance"
    RECOVERY = "recovery"
    LOW_COST = "low_cost"
    TEMPO = "tempo"
    THRESHOLD = "threshold"
    VO2 = "vo2"
    ANAEROBIC = "anaerobic"
    PROGRESSION = "progression"
    RACE_SPECIFIC = "race_specific"
    FARTLEK = "fartlek"
    STRENGTH = "strength"
    HILL = "hill"
    TECHNIQUE = "technique"
    SPEED = "speed"
    NEUROMUSCULAR = "neuromuscular"
    CSS = "css"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class ActivityCapabilityProfile:
    """Explicit semantic metadata for an activity definition."""

    sport: SportType
    family: ActivityFamily
    stimulus_tags: tuple[ActivityStimulusTag, ...]
    goal_suitability: tuple[GoalType, ...] = ALL_GOAL_TYPES
    phase_suitability: tuple[PhaseType, ...] = ALL_PHASE_TYPES
    recovery_ok: bool = False
    taper_ok: bool = False
    earliest_phase_week: int = 0
    """Week offset within the BASE phase before this activity becomes available.

    Only checked during BASE — in BUILD, PEAK, and TAPER the activity is
    available from week 0 regardless of this value.  Useful for delaying
    high-intensity work (e.g. VO2max) until aerobic foundations are laid.
    """

    def supports_goal(self, goal_type: GoalType) -> bool:
        """Return whether the activity is valid for the given goal."""
        return goal_type in self.goal_suitability

    def supports_phase(self, phase_type: PhaseType) -> bool:
        """Return whether the activity is valid for the given phase."""
        return phase_type in self.phase_suitability


__all__ = [
    "ALL_GOAL_TYPES",
    "ALL_PHASE_TYPES",
    "ActivityCapabilityProfile",
    "ActivityStimulusTag",
]
