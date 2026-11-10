"""Serializer for ``get_races`` — the wizard's "Your races" chip row."""

from __future__ import annotations

from datetime import date
from typing import Any

from typing_extensions import TypedDict

from endury.common import clock


class RacePayload(TypedDict):
    """One ``athlete_goals`` row, trimmed to the wizard chip's own fields."""

    id: int
    name: str
    race_date: str
    goal_type: str | None
    priority: str
    distance_km: float | None
    climb_m: float | None
    status: str


def _race_payload(race: Any) -> RacePayload:
    """Trim one stored :class:`AthleteRace` to the wire fields the chip needs."""
    return {
        "id": race.id,
        "name": race.name,
        "race_date": race.race_date,
        "goal_type": race.goal_type,
        "priority": race.priority,
        "distance_km": race.distance_km,
        "climb_m": race.climb_m,
        "status": race.status,
    }


def build_races_payload(repos: Any, today: date | None = None) -> dict[str, Any]:
    """Return the athlete's upcoming, still-planned races.

    Args:
        repos: Repository container (races).
        today: Reference date (defaults to the app clock).

    Returns:
        ``{"races": [...]}``, ordered by race_date ascending (the
        repository's own order). Only ``status="planned"`` races dated
        strictly after ``today`` are offered as wizard chips — a race the
        athlete or coach cancelled, already raced, or dated today or
        earlier is not a plan the wizard can still build around.
    """
    cutoff = (today or clock.today()).isoformat()
    races = [
        race
        for race in repos.races.list(include_past_days=0)
        if race.status == "planned" and race.race_date > cutoff
    ]
    return {"races": [_race_payload(race) for race in races]}
