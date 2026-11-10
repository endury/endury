"""Pydantic model for a single athlete race.

The wire/domain shape of one ``athlete_goals`` row — the single home for
race data, read by the coach's system context and its ``race_tools`` and
written by plan commits/replans (``endury.planning.race_book``) and the
athlete or coach directly (``upsert_race``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: A race's standing relative to the season's A-goal.
RacePriority = Literal["A", "B", "C"]

#: A race's outcome. No hard delete — a race the athlete drops is
#: ``cancelled``, never removed.
RaceStatus = Literal["planned", "completed", "dns", "dnf", "cancelled"]


class AthleteRace(BaseModel):
    """One race the athlete is targeting or has run."""

    id: int
    name: str
    race_date: str
    goal_type: str | None = None
    priority: RacePriority = "A"
    distance_km: float | None = None
    climb_m: float | None = None
    status: RaceStatus = "planned"
    notes: str | None = None
    created_at: str = ""
    updated_at: str = ""
