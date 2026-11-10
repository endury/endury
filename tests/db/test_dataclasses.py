"""Tests for the new Pydantic dataclass models and updated repositories."""

from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)
from endury.database.sqlite.repositories.conversation_history import (
    ConversationHistoryRepository,
)
from endury.domain.models.athlete import AthleteProfile
from endury.domain.models.memory import (
    AthleteMemory,
    ConversationHistoryEntry,
)

# AthleteProfile model


def test_athlete_profile_defaults():
    p = AthleteProfile()
    assert p.id == 1
    assert p.language == "en"
    assert p.name is None
    assert p.experience_level is None


def test_athlete_profile_model_copy():
    p = AthleteProfile(name="Alex", language="en")
    p2 = p.model_copy(update={"language": "de"})
    assert p.language == "en"
    assert p2.language == "de"
    assert p2.name == "Alex"


# AthleteMemory / ConversationHistoryEntry models


def test_athlete_memory_fields():
    m = AthleteMemory(content="## Notes\n- test", updated_at="2024-01-01")
    assert m.content == "## Notes\n- test"
    assert m.updated_at == "2024-01-01"


def test_conversation_history_entry_fields():
    h = ConversationHistoryEntry(
        id=10, role="user", content="Hello!", created_at="2024-01-02"
    )
    assert h.role == "user"
    assert h.content == "Hello!"


# AthleteProfileRepository — get() returns AthleteProfile | None


def test_athlete_repo_get_returns_profile(session):
    repo = AthleteProfileRepository(session)
    profile = repo.get()
    assert isinstance(profile, AthleteProfile)
    assert profile.id == 1  # seeded by init_schema


def test_athlete_repo_update_returns_profile(session):
    repo = AthleteProfileRepository(session)
    result = repo.update({"name": "Sam", "primary_sport": "running"})
    assert isinstance(result, AthleteProfile)
    assert result.name == "Sam"
    assert result.primary_sport == "running"


def test_athlete_repo_get_after_update(session):
    repo = AthleteProfileRepository(session)
    repo.update({"name": "Jordan", "experience_level": "advanced"})
    profile = repo.get()
    assert profile is not None
    assert profile.name == "Jordan"
    assert profile.experience_level == "advanced"


# AthleteMemoryRepository — get() returns AthleteMemory


def test_memory_repo_get_returns_pydantic(session):
    repo = AthleteMemoryRepository(session)
    repo.set("## Preferences\n- Morning runs")
    memory = repo.get()
    assert isinstance(memory, AthleteMemory)
    assert memory.content == "## Preferences\n- Morning runs"


# ConversationHistoryRepository — get_by_date_range() returns Pydantic


def test_history_repo_returns_pydantic(session):
    repo = ConversationHistoryRepository(session)
    repo.add("user", "Hello coach")
    entries = repo.get_by_date_range("2020-01-01", "2030-12-31")
    assert len(entries) == 1
    assert isinstance(entries[0], ConversationHistoryEntry)
    assert entries[0].role == "user"
    assert entries[0].content == "Hello coach"


def test_history_repo_get_recent_orders_and_limits(session):
    repo = ConversationHistoryRepository(session)
    repo.add("user", "first")
    repo.add("assistant", "second")
    repo.add("user", "third")

    recent = repo.get_recent(2)
    assert [e.content for e in recent] == ["second", "third"]


def test_history_repo_get_recent_zero_limit_returns_empty(session):
    repo = ConversationHistoryRepository(session)
    repo.add("user", "hello")
    assert repo.get_recent(0) == []
