"""Pydantic model for athlete profile data."""

from pydantic import BaseModel


class AthleteProfile(BaseModel):
    """Represents a single athlete's coaching profile."""

    id: int = 1
    name: str | None = None
    experience_level: str | None = None  # "beginner"|"intermediate"|"advanced"|"elite"
    primary_sport: str | None = None  # e.g. "running", "cycling", "triathlon"
    communication_style: str | None = None  # free-text preference from athlete
    language: str = "en"  # ISO 639-1 language code
    preferred_run_surface: str | None = None  # road|trail|mixed
    open_water_access: bool | None = None
    race_location_access: bool | None = None
    last_session_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
