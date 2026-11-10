"""Activity registry and semantic query helpers for the planner.

The activity library defines the canonical workout templates the planner can
materialize. This module owns the registry used to look up those definitions
by stable keys, plus semantic query helpers used by the selector and
materializer layers.
"""

from __future__ import annotations

from endury.planner.activity.capabilities import ActivityStimulusTag
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    GoalType,
    PhaseType,
    SportType,
)

ACTIVITY_REGISTRY: dict[ActivityKey, ActivityDefinition] = {}


def register_activity(activity: ActivityDefinition) -> None:
    """Register an activity definition by unique key.

    Args:
        activity: The activity definition to register.

    Raises:
        ValueError: If the key is already registered or stimulus tags are invalid.
    """
    if activity.key in ACTIVITY_REGISTRY:
        raise ValueError(f"duplicate activity key: {activity.key}")
    if activity.capability_profile is not None:
        invalid = [
            t
            for t in activity.capability_profile.stimulus_tags
            if not isinstance(t, ActivityStimulusTag)
        ]
        if invalid:
            raise ValueError(
                f"invalid stimulus_tags for '{activity.key}': {invalid!r} — "
                f"use ActivityStimulusTag enum members"
            )
    ACTIVITY_REGISTRY[activity.key] = activity


def get_activity(activity_key: ActivityKey) -> ActivityDefinition:
    """Lookup an activity by key.

    Args:
        activity_key: The ActivityKey enum member to look up.

    Returns:
        The matching ActivityDefinition.

    Raises:
        KeyError: If no activity with that key is registered.
    """
    try:
        return ACTIVITY_REGISTRY[activity_key]
    except KeyError as exc:
        raise KeyError(f"unknown activity key: {activity_key}") from exc


def ensure_registry_loaded() -> None:
    """Load sport modules once to populate the registry."""
    if ACTIVITY_REGISTRY:
        return
    from endury.planner.activity.catalog import bike, run, swim  # noqa: F401


def build_catalog_enum() -> list[str]:
    """Return the canonical activity keys for runtime schema validation.

    Returns:
        Sorted list of registered activity key string values.
    """
    ensure_registry_loaded()
    return sorted(k.value for k in ACTIVITY_REGISTRY)


def query_activities(
    *,
    sport: SportType | None = None,
    family: ActivityFamily | None = None,
    stimulus_tags: set[ActivityStimulusTag] | None = None,
    phase: PhaseType | None = None,
    goal: GoalType | None = None,
    recovery_ok: bool | None = None,
    taper_ok: bool | None = None,
) -> list[ActivityDefinition]:
    """Query the activity registry by semantic criteria.

    All provided filters are applied as AND conditions.  Omit a filter to
    match any value for that dimension.

    Args:
        sport: Restrict to activities for this sport.
        family: Restrict to activities in this family (e.g. ``"tempo"``).
        stimulus_tags: Restrict to activities whose capability profile contains
            at least one of the provided tags.
        phase: Restrict to activities suitable for this training phase.
        goal: Restrict to activities suitable for this goal type.
        recovery_ok: If ``True``, only activities flagged as recovery-safe.
            If ``False``, exclude them.
        taper_ok: If ``True``, only activities flagged as taper-safe.
            If ``False``, exclude them.

    Returns:
        Activities matching all criteria, sorted by ``tss_min`` ascending.
    """
    ensure_registry_loaded()
    results: list[ActivityDefinition] = []
    for activity in ACTIVITY_REGISTRY.values():
        if sport is not None and activity.sport != sport:
            continue
        if family is not None and activity.family != family:
            continue
        profile = activity.capability_profile
        if profile is None:  # pragma: no cover
            continue  # pragma: no cover
        if stimulus_tags is not None and not stimulus_tags.intersection(
            profile.stimulus_tags
        ):
            continue
        if phase is not None and not profile.supports_phase(phase):
            continue
        if goal is not None and not profile.supports_goal(goal):
            continue
        if recovery_ok is not None and profile.recovery_ok != recovery_ok:
            continue
        if taper_ok is not None and profile.taper_ok != taper_ok:
            continue
        results.append(activity)
    return sorted(results, key=lambda a: a.tss_min)


__all__ = [
    "ACTIVITY_REGISTRY",
    "build_catalog_enum",
    "ensure_registry_loaded",
    "get_activity",
    "query_activities",
    "register_activity",
]
