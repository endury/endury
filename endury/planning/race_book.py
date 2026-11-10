"""Sync a plan's races into the athlete_goals table — their one home.

Matched by natural key (``race_date``, ``goal_type``): a plan's race and an
existing row for the same event are the same row, never a duplicate.
Coach- and athlete-maintained fields on that row (``upsert_race``) are
never overwritten — only the priority is the plan's to set. A maintenance
block is no race: its ``race_date`` only marks where the block ends, so it
never enters the table.
"""

from __future__ import annotations

from typing import Any

from endury.planner.foundations.types import MAINTENANCE_GOALS
from endury.planning.coach_brief import _goal_label

#: The goal types that are blocks, not races.
_BLOCK_GOAL_TYPES = tuple(sorted(goal.value for goal in MAINTENANCE_GOALS))


def _default_name(goal_type: str | None, race_date: str) -> str:
    """A race name from its goal label and date, for one the table lacks."""
    if goal_type is None:
        return f"Race — {race_date}"
    label = _goal_label(goal_type)
    return f"{label[0].upper()}{label[1:]} — {race_date}"


def sync_plan_races(repos: Any, races: list[dict[str, Any]]) -> None:
    """Upsert a plan's races into the athlete_goals table.

    Only the priority is written onto an existing row — a race can
    genuinely move from B to C between replans.

    Args:
        repos: Repository container (races).
        races: The plan's races, as stored in its request payload
            (``[{"goal_type", "race_date", "priority"}, ...]``).
    """
    for race in races:
        goal_type = race.get("goal_type")
        if goal_type in _BLOCK_GOAL_TYPES:
            continue
        race_date = race["race_date"]
        existing = repos.races.get_by_natural_key(race_date, goal_type)
        kept: dict[str, Any] = (
            {
                "race_id": existing.id,
                "name": existing.name,
                "distance_km": existing.distance_km,
                "climb_m": existing.climb_m,
                "status": existing.status,
                "notes": existing.notes,
            }
            if existing is not None
            else {"name": _default_name(goal_type, race_date)}
        )
        repos.races.upsert(
            race_date=race_date,
            goal_type=goal_type,
            priority=race.get("priority", "A"),
            **kept,
        )


def drop_block_ends(repos: Any) -> int:
    """Delete the maintenance block ends earlier commits stored as races.

    Args:
        repos: Repository container (races).

    Returns:
        How many rows were deleted.
    """
    return int(repos.races.delete_goal_types(_BLOCK_GOAL_TYPES))


__all__ = ["drop_block_ends", "sync_plan_races"]
