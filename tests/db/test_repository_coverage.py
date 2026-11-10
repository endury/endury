"""Tests to fill coverage gaps in database repositories."""

import pytest

from endury.database.sqlite.session import DatabaseSession


@pytest.fixture
def session():
    s = DatabaseSession(":memory:")
    s.init_schema()
    return s


@pytest.fixture
def athlete_repo(session):
    from endury.database.sqlite.repositories.athlete import AthleteProfileRepository

    return AthleteProfileRepository(session)


# AthleteProfileRepository


def test_athlete_get_existing_profile(athlete_repo):
    athlete_repo.update({"name": "Alice", "primary_sport": "triathlon"})
    profile = athlete_repo.get()
    assert profile is not None
    assert profile.name == "Alice"
    assert profile.primary_sport == "triathlon"


def test_athlete_update_returns_profile(athlete_repo):
    profile = athlete_repo.update({"name": "Bob", "experience_level": "advanced"})
    assert profile is not None
    assert profile.name == "Bob"
    assert profile.experience_level == "advanced"


def test_athlete_update_partial_fields(athlete_repo):
    athlete_repo.update({"name": "Alice"})
    athlete_repo.update({"experience_level": "intermediate"})
    profile = athlete_repo.get()
    assert profile.name == "Alice"
    assert profile.experience_level == "intermediate"


def test_athlete_get_no_profile_returns_none(athlete_repo, session):
    """Cover athlete.py lines 44-47: get() returns None when no row exists."""
    with session.cursor() as cur:
        cur.execute("DELETE FROM athlete_profile")
    result = athlete_repo.get()
    assert result is None


def test_athlete_update_no_valid_fields_returns_current_profile(athlete_repo):
    """Cover athlete.py lines 84-88: update() with no valid fields returns current."""
    from endury.domain.models.athlete import AthleteProfile

    result = athlete_repo.update({})
    # No valid fields → falls through to return self.get()
    assert isinstance(result, AthleteProfile)


def test_athlete_update_last_session_at(athlete_repo):
    """Cover athlete.py lines 51-56: update_last_session_at() persists timestamp."""
    timestamp = "2026-01-15T10:30:00"
    athlete_repo.update_last_session_at(timestamp)
    profile = athlete_repo.get()
    assert profile is not None
    assert profile.last_session_at == timestamp


def test_athlete_update_preference_fields(athlete_repo):
    """Preference fields are persisted and returned on profile reads."""
    athlete_repo.update(
        {
            "preferred_run_surface": "trail",
            "open_water_access": True,
            "race_location_access": False,
        }
    )
    profile = athlete_repo.get()
    assert profile is not None
    assert profile.preferred_run_surface == "trail"
    assert profile.open_water_access is True
    assert profile.race_location_access is False


def test_athlete_update_bool_string_coercion(athlete_repo):
    """String values for bool fields are normalized during update."""
    profile = athlete_repo.update(
        {
            "open_water_access": "yes",
            "race_location_access": "0",
        }
    )
    assert profile is not None
    assert profile.open_water_access is True
    assert profile.race_location_access is False
