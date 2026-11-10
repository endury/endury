"""Put a race from the race table on the athlete's intervals.icu calendar.

A race the coach records (``upsert_race``) is the athlete's own fact, so it
goes on their calendar straight away as a RACE_A/B/C event keyed by the
race's id, whether or not a plan is built around it. Only an admissible race
goes up: still planned, today or later, and on a day with no race event yet,
so a plan's own race event is never doubled. Cancelling the race takes its
event back off.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from typing import Any

from endury.domain.models.race import AthleteRace
from endury.intervals.push import EVENT_TAG, event_start
from endury.planner.foundations.types import SportType
from endury.planning.race_day import race_sport

logger = logging.getLogger(__name__)

#: The race categories intervals.icu knows, for the one-day read.
RACE_CATEGORIES = "RACE_A,RACE_B,RACE_C"


def race_calendar_id(race_id: int) -> str:
    """Return the external_id of a race-table race's calendar event.

    Args:
        race_id: The race's id in the race table.

    Returns:
        The ``coachie:race:{race_id}`` external_id.
    """
    return f"coachie:race:{race_id}"


def _race_event(race: AthleteRace) -> dict[str, Any]:
    """Build the race's event payload, typed the way a plan's race event is."""
    goal_type = race.goal_type or ""
    event: dict[str, Any] = {
        "category": f"RACE_{race.priority}",
        "external_id": race_calendar_id(race.id),
        "start_date_local": event_start(date.fromisoformat(race.race_date)),
        "type": "Run" if race_sport(goal_type) is SportType.RUNNING else "Other",
        "name": f"{race.name} ({race.priority}-Race)",
    }
    if race.notes:
        event["description"] = race.notes
    event["tags"] = [EVENT_TAG]
    return event


def sync_race_event(
    race: AthleteRace, *, client: Any | None, today: date
) -> dict[str, Any]:
    """Keep one race's intervals.icu event in step with its race-table row.

    Never raises: the race is already saved, so a remote failure is
    reported in the returned block instead.

    Args:
        race: The race as just stored.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: The day a race must not be before to go on the calendar.

    Returns:
        ``{"status", "message"}``: ``ok`` (on the calendar), ``removed``
        (cancelled, taken off), ``already_on_calendar`` (the day holds a race
        event already), ``unchanged`` (past or no longer planned),
        ``skipped`` (no intervals.icu) or ``error``.
    """
    if client is None:
        return {
            "status": "skipped",
            "message": "intervals.icu is not connected, so the race is saved in "
            "Endury only.",
        }
    external_id = race_calendar_id(race.id)
    try:
        if race.status == "cancelled":
            client.bulk_delete_events([external_id])
            return {
                "status": "removed",
                "message": "The race is off the intervals.icu calendar.",
            }
        day = date.fromisoformat(race.race_date)
        if race.status != "planned" or day < today:
            return {
                "status": "unchanged",
                "message": "Only a planned race from today on goes on the calendar.",
            }
        others = [
            event
            for event in client.list_events(day, day, category=RACE_CATEGORIES)
            if event.external_id != external_id
        ]
        if others:
            # A race of ours moved onto this day must not linger on its old one.
            client.bulk_delete_events([external_id])
            return {
                "status": "already_on_calendar",
                "message": f"The calendar already has {others[0].name} on "
                f"{day.isoformat()}, so no second race event was added.",
            }
        client.bulk_upsert_events([_race_event(race)])
    except Exception as exc:
        logger.warning("Race calendar sync failed for race %s: %s", race.id, exc)
        return {
            "status": "error",
            "message": "The race is saved, but updating the intervals.icu "
            f"calendar failed: {exc}",
        }
    return {"status": "ok", "message": "The race is on the intervals.icu calendar."}


def reconcile_race_events(
    races: Iterable[AthleteRace], *, client: Any | None, today: date
) -> list[dict[str, Any]]:
    """Put every planned race from today on whose day has no race event back on.

    A race event is written when the race is saved, and a day that already
    held a race was skipped; the athlete may have removed an event since.
    Run at boot, this writes the missing ones through
    :func:`sync_race_event` and leaves a day that holds any race event
    alone, so a boot writes nothing while the calendar is in step. Never
    raises.

    Args:
        races: The race table (``AthleteGoalsRepository.list``).
        client: intervals.icu client, or ``None`` when unconfigured.
        today: The first day a race still counts as ahead.

    Returns:
        One :func:`sync_race_event` result per race that was put back.
    """
    due = [
        race
        for race in races
        if race.status == "planned" and date.fromisoformat(race.race_date) >= today
    ]
    if client is None or not due:
        return []
    days = [date.fromisoformat(race.race_date) for race in due]
    try:
        events = client.list_events(min(days), max(days), category=RACE_CATEGORIES)
    except Exception as exc:
        logger.warning("Could not read the race calendar: %s", exc)
        return []
    busy = {str(event.start_date_local)[:10] for event in events}
    return [
        sync_race_event(race, client=client, today=today)
        for race in due
        if race.race_date not in busy
    ]


__all__ = [
    "RACE_CATEGORIES",
    "race_calendar_id",
    "reconcile_race_events",
    "sync_race_event",
]
