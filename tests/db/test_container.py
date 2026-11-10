"""Tests for RepositoryContainer, the repository DI container."""

import pytest

from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)
from endury.database.sqlite.repositories.conversation_history import (
    ConversationHistoryRepository,
)
from endury.intervals.client import IntervalsClient
from endury.wiring.container import RepositoryContainer


def test_container_in_memory_creates_all_repos():
    """RepositoryContainer.in_memory() wires all repositories."""
    c = RepositoryContainer.in_memory()
    assert isinstance(c.athlete, AthleteProfileRepository)
    assert isinstance(c.memory, AthleteMemoryRepository)
    assert isinstance(c.history, ConversationHistoryRepository)


def test_container_shares_session():
    """All repos in the container use the same session."""
    c = RepositoryContainer.in_memory()
    assert c.athlete._session is c.memory._session
    assert c.plan._session is c.history._session


def test_container_athlete_profile_seeded():
    """The in-memory container's schema is initialised (athlete_profile row exists)."""
    c = RepositoryContainer.in_memory()
    profile = c.athlete.get()
    assert profile is not None
    assert profile.id == 1


class TestIntervalsClientLazyConstruction:
    def test_missing_key_raises_only_when_the_property_is_accessed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constructing the container never touches intervals.icu config."""
        # Blank, not deleted: get_coach_config calls load_dotenv(), which
        # would refill a deleted var from a developer's real .env.
        monkeypatch.setenv("INTERVALS_API_KEY", "")
        c = RepositoryContainer.in_memory()  # must not raise
        with pytest.raises(
            ValueError, match=r"intervals\.icu API key is not configured"
        ):
            _ = c.intervals_client

    def test_builds_a_client_once_a_key_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INTERVALS_API_KEY", "ic-test-key")
        c = RepositoryContainer.in_memory()
        assert isinstance(c.intervals_client, IntervalsClient)

    def test_client_is_memoized_across_accesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INTERVALS_API_KEY", "ic-test-key")
        c = RepositoryContainer.in_memory()
        assert c.intervals_client is c.intervals_client


def test_bind_intervals_client_replaces_the_lazy_client():
    """A bound client is what intervals_client serves, no key needed."""
    c = RepositoryContainer.in_memory()
    scripted = object()
    c.bind_intervals_client(scripted)  # type: ignore[arg-type]
    assert c.intervals_client is scripted
