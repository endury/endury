"""Profile and memory tools: athlete profile and the athlete-memory document."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.common.exceptions import RepositoryError
from endury.common.memory_notes import MEMORY_DOCUMENT_RULES, undated_constraints
from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)

logger = logging.getLogger(__name__)


def make_profile_tools(
    athlete_repo: AthleteProfileRepository,
    memory_repo: AthleteMemoryRepository,
) -> list:
    """Create profile/memory tool list with injected repositories.

    Args:
        athlete_repo: Repository for the athlete profile.
        memory_repo: Repository for the athlete-memory document.
    """
    _athlete_repo = athlete_repo
    _memory_repo = memory_repo

    @coach_tool
    def get_athlete_profile() -> str:
        """Get the current athlete's profile information.

        Includes personal details (name, experience level, primary sport)
        and coach preferences (communication style).

        Returns:
            JSON string with athlete profile data.
        """
        try:
            profile = _athlete_repo.get()
            output: dict[str, Any] = profile.model_dump() if profile else {}
            if output:
                return json.dumps(output, indent=2)
            return json.dumps(
                {"message": "No athlete profile found. Please set up your profile."}
            )
        except Exception as e:
            return tool_error_response("retrieve athlete profile", e)

    @coach_tool
    def update_athlete_profile(
        name: str | None = None,
        experience_level: str | None = None,
        primary_sport: str | None = None,
        communication_style: str | None = None,
        preferred_run_surface: str | None = None,
        open_water_access: bool | None = None,
        race_location_access: bool | None = None,
    ) -> str:
        """Update athlete profile preferences and settings.

        Args:
            name: Athlete's name. Only set it when the athlete explicitly
                states their name; never infer it from a request, a reply
                chip, or any other message text.
            experience_level: Experience level
                (beginner, intermediate, advanced, elite). Only set it when
                the athlete states it in their own words; never infer it
                from how they train or write, and never save the level your
                system context tells you to pitch at — that is the app's
                default, not their answer.
            primary_sport: Primary sport
                (running, cycling, swimming, triathlon). Only set it when
                the athlete states it; never infer it from a plan, a
                request, or the sport of a session they asked about.
            communication_style: Preferred communication style
                (concise, detailed)
            preferred_run_surface: Preferred run surface (road, trail, mixed)
            open_water_access: Whether athlete has open-water swim access
            race_location_access: Whether athlete can train at race location

        Returns:
            JSON string with success status and updated profile.
        """
        try:
            profile_data: dict[str, object] = {}
            if name is not None:
                profile_data["name"] = name
            if experience_level is not None:
                profile_data["experience_level"] = experience_level
            if primary_sport is not None:
                profile_data["primary_sport"] = primary_sport
            if communication_style is not None:
                profile_data["communication_style"] = communication_style
            if preferred_run_surface is not None:
                profile_data["preferred_run_surface"] = preferred_run_surface
            if open_water_access is not None:
                profile_data["open_water_access"] = open_water_access
            if race_location_access is not None:
                profile_data["race_location_access"] = race_location_access

            if not profile_data:
                return json.dumps({"success": False, "message": "No fields to update"})

            updated_profile = _athlete_repo.update(profile_data)

            return json.dumps(
                {
                    "success": True,
                    "message": "Athlete profile updated successfully",
                    "profile": (
                        updated_profile.model_dump() if updated_profile else None
                    ),
                },
                indent=2,
            )
        except RepositoryError as e:
            return tool_error_response("update athlete profile", e)

    @coach_tool
    def update_memory(content: str) -> str:
        """Rewrite the athlete-memory document — the one thing you remember.

        The current document is always shown in your system context. This
        tool REPLACES it whole, so send the full new text, not a fragment:
        copy what still holds, then add, edit, or remove.

        Args:
            content: The complete new document.

        Returns:
            JSON string with success status and the stored length.
        """
        undated = undated_constraints(content)
        if undated:
            return json.dumps(
                {
                    "success": False,
                    "message": (
                        "Every Constraints bullet needs its review date, "
                        '"(review YYYY-MM-DD)". Add one to: '
                        + "; ".join(undated)
                        + ". Nothing was saved; write the document again."
                    ),
                }
            )
        try:
            changed = _memory_repo.set(content)
        except ValueError as e:
            return json.dumps({"success": False, "message": str(e)})
        except Exception as e:
            return tool_error_response("update athlete memory", e)
        return json.dumps(
            {
                "success": True,
                "characters": len(content),
                "message": (
                    "Athlete memory updated"
                    if changed
                    else "Athlete memory already reads exactly this — unchanged"
                ),
            }
        )

    # The model-facing description carries the document rules verbatim from
    # the single shared constant, so the "Tidy up" cleanup prompt and this
    # tool can never drift apart.
    head, args_marker, tail = update_memory.description.partition("\n\nArgs:")
    update_memory = dataclasses.replace(
        update_memory,
        description=f"{head}\n\n{MEMORY_DOCUMENT_RULES}{args_marker}{tail}",
    )

    return [
        get_athlete_profile,
        update_athlete_profile,
        update_memory,
    ]
