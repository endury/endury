"""Tests for AthleteMemoryRepository — the single memory document."""

from __future__ import annotations

import pytest

from endury.database.sqlite.repositories.athlete_memory import (
    MEMORY_CHAR_CAP,
    AthleteMemoryRepository,
)
from endury.database.sqlite.session import DatabaseSession


@pytest.fixture
def repo() -> AthleteMemoryRepository:
    """A memory repository over a fresh in-memory database."""
    session = DatabaseSession(":memory:")
    session.init_schema()
    return AthleteMemoryRepository(session)


def test_a_fresh_database_holds_an_empty_document(repo):
    memory = repo.get()
    assert memory.content == ""
    assert memory.updated_at is not None


def test_set_replaces_the_document_and_stamps_it(repo):
    repo.set("## Goals & races\n- 2026-10-04 Berlin Marathon")

    memory = repo.get()
    assert memory.content == "## Goals & races\n- 2026-10-04 Berlin Marathon"
    assert memory.updated_at.startswith("2026-")


def test_a_write_snapshots_the_version_it_replaces(repo):
    repo.set("first")
    repo.set("second")
    repo.set("third")

    assert repo.get().content == "third"
    assert [v.content for v in repo.history(10)] == ["second", "first"]


def test_rewriting_the_same_text_writes_nothing_and_snapshots_nothing(repo):
    """A no-op rewrite must not push a real version out of the history."""
    repo.set("first")
    assert repo.set("second") is True
    stamped = repo.get().updated_at

    assert repo.set("second") is False
    assert repo.get().content == "second"
    assert repo.get().updated_at == stamped
    assert [v.content for v in repo.history(10)] == ["first"]


def test_history_respects_its_limit(repo):
    repo.set("first")
    repo.set("second")
    repo.set("third")

    assert [v.content for v in repo.history(1)] == ["second"]


def test_the_first_write_snapshots_nothing(repo):
    """An empty starting document is not worth a history row."""
    repo.set("first")
    assert repo.history(10) == []


def test_a_wiped_row_reads_as_nothing_remembered_and_can_be_rewritten(repo):
    """A deleted row reads as nothing remembered; the app must survive until reboot."""
    with repo._session.cursor() as cursor:
        cursor.execute("DELETE FROM athlete_memory")

    assert repo.get().content == ""
    repo.set("## Notes\n- starting over")
    assert repo.get().content == "## Notes\n- starting over"
    assert repo.history(10) == []


def test_a_document_over_the_cap_is_refused(repo):
    with pytest.raises(ValueError, match="Condense it"):
        repo.set("x" * (MEMORY_CHAR_CAP + 1))

    assert repo.get().content == ""


def test_a_document_at_the_cap_is_accepted(repo):
    repo.set("x" * MEMORY_CHAR_CAP)
    assert len(repo.get().content) == MEMORY_CHAR_CAP
