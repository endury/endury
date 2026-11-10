"""Repository for the athlete's races — the single home for goal-race data.

Races are matched by id when one is given, else by their natural key
(``race_date``, ``goal_type``) — how a plan commit or replan
(``endury.planning.race_book``) recognizes a race it already knows about
without duplicating it. No hard delete of a race: a dropped race is upserted
with ``status="cancelled"`` instead. Only rows that were never races go
(:meth:`delete_goal_types`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from endury.common import clock
from endury.database.sqlite.session import DatabaseSession
from endury.domain.models.race import AthleteRace, RacePriority, RaceStatus

logger = logging.getLogger(__name__)


def _row_to_race(row: Any) -> AthleteRace:
    """Build an :class:`AthleteRace` from one ``athlete_goals`` row."""
    return AthleteRace(
        id=row["id"],
        name=row["name"],
        race_date=row["race_date"],
        goal_type=row["goal_type"],
        priority=row["priority"],
        distance_km=row["distance_km"],
        climb_m=row["climb_m"],
        status=row["status"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class AthleteGoalsRepository:
    """CRUD for ``athlete_goals`` (one row per race, no hard delete)."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def list(self, include_past_days: int = 30) -> list[AthleteRace]:
        """Return races on or after today minus ``include_past_days``.

        Args:
            include_past_days: How far back past races are still returned.

        Returns:
            Races ordered by ``race_date``, ascending.
        """
        cutoff = (clock.today() - timedelta(days=include_past_days)).isoformat()
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM athlete_goals WHERE athlete_id = 1 AND race_date >= ? "
                "ORDER BY race_date",
                (cutoff,),
            )
            rows = cursor.fetchall()
        return [_row_to_race(row) for row in rows]

    def get(self, race_id: int) -> AthleteRace | None:
        """Return one race by id, or ``None``.

        Args:
            race_id: The race's id.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM athlete_goals WHERE id = ? AND athlete_id = 1",
                (race_id,),
            )
            row = cursor.fetchone()
        return _row_to_race(row) if row is not None else None

    def get_by_natural_key(
        self, race_date: str, goal_type: str | None
    ) -> AthleteRace | None:
        """Return the race matching ``(race_date, goal_type)``, or ``None``.

        The commit-time upsert's own matching key: a plan's race and the
        table's race describe the same event when both agree.

        Args:
            race_date: ISO race date.
            goal_type: The race's ``GoalType`` value, or ``None``.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM athlete_goals WHERE athlete_id = 1 "
                "AND race_date = ? AND goal_type IS ?",
                (race_date, goal_type),
            )
            row = cursor.fetchone()
        return _row_to_race(row) if row is not None else None

    def get_by_date(self, race_date: str) -> AthleteRace | None:
        """Return the athlete's non-cancelled race on ``race_date``, or ``None``.

        Unlike :meth:`get_by_natural_key`, this ignores ``goal_type`` — it is
        how ``create_plan``'s trail-band backstop finds the race a requested
        goal_type is supposed to describe, whatever band it was itself
        stored under.

        Args:
            race_date: ISO race date.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM athlete_goals WHERE athlete_id = 1 "
                "AND race_date = ? AND status != 'cancelled' "
                "ORDER BY id LIMIT 1",
                (race_date,),
            )
            row = cursor.fetchone()
        return _row_to_race(row) if row is not None else None

    def upsert(
        self,
        *,
        race_id: int | None = None,
        name: str,
        race_date: str,
        goal_type: str | None = None,
        priority: RacePriority = "A",
        distance_km: float | None = None,
        climb_m: float | None = None,
        status: RaceStatus = "planned",
        notes: str | None = None,
    ) -> AthleteRace:
        """Insert or update a race: by id when given, else by natural key.

        Args:
            race_id: The row to update, or ``None`` to match by
                ``(race_date, goal_type)`` instead (inserting when nothing
                matches).
            name: The race's name.
            race_date: ISO race date.
            goal_type: The race's ``GoalType`` value, or ``None``.
            priority: "A", "B", or "C".
            distance_km: Race distance, if known.
            climb_m: Race climb, if known.
            status: "planned", "completed", "dns", "dnf", or "cancelled".
            notes: Free-text notes.

        Returns:
            The stored race.
        """
        existing = (
            self.get(race_id)
            if race_id is not None
            else self.get_by_natural_key(race_date, goal_type)
        )
        now = clock.now().isoformat()
        with self._session.cursor() as cursor:
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO athlete_goals
                        (athlete_id, name, race_date, goal_type, priority,
                         distance_km, climb_m, status, notes,
                         created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        race_date,
                        goal_type,
                        priority,
                        distance_km,
                        climb_m,
                        status,
                        notes,
                        now,
                        now,
                    ),
                )
                new_id = cursor.lastrowid
                created_at = now
            else:
                cursor.execute(
                    """
                    UPDATE athlete_goals SET
                        name = ?, race_date = ?, goal_type = ?, priority = ?,
                        distance_km = ?, climb_m = ?, status = ?, notes = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        race_date,
                        goal_type,
                        priority,
                        distance_km,
                        climb_m,
                        status,
                        notes,
                        now,
                        existing.id,
                    ),
                )
                new_id = existing.id
                created_at = existing.created_at
        assert new_id is not None
        return AthleteRace(
            id=new_id,
            name=name,
            race_date=race_date,
            goal_type=goal_type,
            priority=priority,
            distance_km=distance_km,
            climb_m=climb_m,
            status=status,
            notes=notes,
            created_at=created_at,
            updated_at=now,
        )

    def delete_goal_types(self, goal_types: Sequence[str]) -> int:
        """Delete every row of the given goal types, past ones included.

        Args:
            goal_types: The ``goal_type`` values whose rows go.

        Returns:
            How many rows were deleted.
        """
        deleted = 0
        with self._session.cursor() as cursor:
            for goal_type in goal_types:
                cursor.execute(
                    "DELETE FROM athlete_goals WHERE athlete_id = 1 AND goal_type = ?",
                    (goal_type,),
                )
                deleted += cursor.rowcount
        return deleted
