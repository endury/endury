"""Repository for the athlete's self-reported training volume.

The onboarding fallback for plan generation: when intervals.icu history is too
thin to measure a goal sport, the wizard's questionnaire answers are
stored here and seed the planner instead of the built-in goal defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from endury.common import clock
from endury.database.sqlite.session import DatabaseSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelfAssessmentRow:
    """One stored self-assessment answer for a single sport."""

    weekly_hours: float
    longest_session_min: int
    updated_at: str


class SelfAssessmentRepository:
    """CRUD for ``athlete_self_assessment`` (one row per sport)."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def get_all(self) -> dict[str, SelfAssessmentRow]:
        """Return every stored answer keyed by sport value.

        Returns:
            Mapping of sport (``running``/``cycling``/``swimming``) to its
            stored answer; empty when the athlete never answered.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT sport, weekly_hours, longest_session_min, updated_at "
                "FROM athlete_self_assessment WHERE athlete_id = 1"
            )
            rows = cursor.fetchall()
        return {
            row["sport"]: SelfAssessmentRow(
                weekly_hours=float(row["weekly_hours"]),
                longest_session_min=int(row["longest_session_min"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        }

    def upsert_many(self, entries: dict[str, tuple[float, int]]) -> None:
        """Insert or replace the answers for the given sports.

        Args:
            entries: Sport value -> ``(weekly_hours, longest_session_min)``.
                Sports absent from the mapping keep their stored answer.
        """
        now = clock.now().isoformat()
        with self._session.cursor() as cursor:
            for sport, (weekly_hours, longest_session_min) in entries.items():
                cursor.execute(
                    """
                    INSERT INTO athlete_self_assessment
                        (athlete_id, sport, weekly_hours,
                         longest_session_min, updated_at)
                    VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(athlete_id, sport) DO UPDATE SET
                        weekly_hours = excluded.weekly_hours,
                        longest_session_min = excluded.longest_session_min,
                        updated_at = excluded.updated_at
                    """,
                    (sport, float(weekly_hours), int(longest_session_min), now),
                )
        logger.debug(
            "SelfAssessmentRepository.upsert_many() - stored %d sports",
            len(entries),
        )
