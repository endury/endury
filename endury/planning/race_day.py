"""Which activity was a race, and closing a race the athlete has run.

A race in the race table and the activity the watch recorded on its day are
joined here, once, so the debrief sheet, the coach and the race table never
have to guess whether a long run on race day was the race.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from typing import Any

from endury.domain.models.race import AthleteRace
from endury.intervals.models import IntervalsActivity, training_sport
from endury.planner.foundations.types import SportType

logger = logging.getLogger(__name__)


def race_sport(goal_type: str | None) -> SportType | None:
    """Return the one sport a race goal is run in; None for a multisport race.

    Args:
        goal_type: The race's ``GoalType`` value, or ``None``.
    """
    if goal_type is not None and goal_type.startswith(("running_", "trail_")):
        return SportType.RUNNING
    return None


def race_activity_ids(
    race: AthleteRace, activities: Iterable[IntervalsActivity]
) -> set[str]:
    """Return the ids of the activities that were ``race``.

    A run or trail race is the day's longest run; every leg of a multisport
    race counts, so each swim, ride and run of its day is the race.

    Args:
        race: The race-table row.
        activities: Activities that may include the race day's.
    """
    day = [
        a
        for a in activities
        if a.id
        and (a.start_date_local or "")[:10] == race.race_date
        and training_sport(a) is not None
    ]
    sport = race_sport(race.goal_type)
    if sport is None:
        return {str(a.id) for a in day}
    runs = [a for a in day if training_sport(a) is sport]
    if not runs:
        return set()
    return {str(max(runs, key=lambda a: a.moving_time or 0).id)}


def race_of_activity(
    races: Any, client: Any, activity_id: str, day: str
) -> AthleteRace | None:
    """Return the race ``activity_id`` was, or None for a training session.

    Never raises: an unread race day leaves the sheet a training debrief.

    Args:
        races: The race repository (``get_by_date``).
        client: The intervals.icu client.
        activity_id: The activity's intervals.icu id.
        day: The activity's local ISO date.
    """
    race = races.get_by_date(day)
    if race is None or race.status == "dns":
        return None
    try:
        on_day = client.list_activities(
            date.fromisoformat(day), date.fromisoformat(day)
        )
    except Exception as exc:
        logger.warning("Could not read the race day's activities: %s", exc)
        return None
    return race if activity_id in race_activity_ids(race, on_day) else None


def complete_raced_races(
    races: Any, *, client: Any | None, today: date
) -> list[AthleteRace]:
    """Mark each planned race up to today that has its activity as completed.

    Run at boot, so a race the athlete has run reads as done to the coach
    and stops counting as ahead. Never raises.

    Args:
        races: The race repository (``list``, ``upsert``).
        client: intervals.icu client, or ``None`` when unconfigured.
        today: The last day a race may be on to count as run.

    Returns:
        The races marked completed.
    """
    due = [
        race
        for race in races.list()
        if race.status == "planned" and race.race_date <= today.isoformat()
    ]
    if client is None or not due:
        return []
    days = [date.fromisoformat(race.race_date) for race in due]
    try:
        activities = client.list_activities(min(days), max(days))
    except Exception as exc:
        logger.warning("Could not read the race-day activities: %s", exc)
        return []
    return [
        races.upsert(
            race_id=race.id,
            name=race.name,
            race_date=race.race_date,
            goal_type=race.goal_type,
            priority=race.priority,
            distance_km=race.distance_km,
            climb_m=race.climb_m,
            status="completed",
            notes=race.notes,
        )
        for race in due
        if race_activity_ids(race, activities)
    ]
