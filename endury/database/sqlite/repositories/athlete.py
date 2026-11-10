"""Athlete profile repository for coach tools."""

import logging
from typing import Any

from endury.common import clock
from endury.database.sqlite.session import DatabaseSession
from endury.domain.models.athlete import AthleteProfile

logger = logging.getLogger(__name__)


class AthleteProfileRepository:
    """Repository for athlete profile data."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def get(self) -> AthleteProfile | None:
        """Get the athlete profile."""
        logger.debug("AthleteProfileRepository.get() - fetching profile")
        with self._session.cursor() as cursor:
            cursor.execute("SELECT * FROM athlete_profile WHERE id = 1")
            row = cursor.fetchone()

            if row:
                profile = AthleteProfile(
                    id=row["id"],
                    name=row["name"],
                    experience_level=row["experience_level"],
                    primary_sport=row["primary_sport"],
                    communication_style=row["communication_style"],
                    language=row["language"] or "en",
                    preferred_run_surface=row["preferred_run_surface"],
                    open_water_access=row["open_water_access"],
                    race_location_access=row["race_location_access"],
                    last_session_at=row["last_session_at"],
                    created_at=row["created_at"] or "",
                    updated_at=row["updated_at"] or "",
                )
                logger.debug(
                    "AthleteProfileRepository.get() - found profile: %s", profile
                )
                return profile

        logger.debug(
            "AthleteProfileRepository.get() - no profile found, returning None"
        )
        return None

    def update_last_session_at(self, timestamp: str) -> None:
        """Update the last_session_at timestamp for the athlete profile."""
        with self._session.cursor() as cursor:
            cursor.execute(
                "UPDATE athlete_profile SET last_session_at = ? WHERE id = 1",
                (timestamp,),
            )
        logger.debug(
            "AthleteProfileRepository.update_last_session_at() - updated to %s",
            timestamp,
        )

    def update(self, profile_data: dict[str, Any]) -> AthleteProfile | None:
        """Update the athlete profile."""
        logger.debug("AthleteProfileRepository.update() - input data: %s", profile_data)

        allowed_fields = {
            "name": str,
            "experience_level": str,
            "primary_sport": str,
            "communication_style": str,
            "language": str,
            "preferred_run_surface": str,
            "open_water_access": bool,
            "race_location_access": bool,
        }

        update_parts: list[str] = []
        params: list[str | int] = []

        for field, field_type in allowed_fields.items():
            if field in profile_data and profile_data[field] is not None:
                value = profile_data[field]
                if field_type is bool and isinstance(value, str):
                    value = value.strip().lower() in {"1", "true", "yes", "y"}
                else:
                    value = field_type(value)
                update_parts.append(f"{field} = ?")
                params.append(value)

        if not update_parts:
            logger.debug(
                "AthleteProfileRepository.update() "
                "- no valid fields, returning current profile"
            )
            return self.get()

        update_parts.append("updated_at = ?")
        params.append(clock.now().isoformat())
        params.append(1)

        query = f"UPDATE athlete_profile SET {', '.join(update_parts)} WHERE id = ?"  # noqa: S608
        logger.debug("AthleteProfileRepository.update() - executing query: %s", query)

        with self._session.cursor() as cursor:
            cursor.execute(query, params)

        result = self.get()
        logger.debug("AthleteProfileRepository.update() - result: %s", result)
        return result
