"""Tests for AthleteGoalsRepository — the athlete_goals table."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from endury.database.sqlite.repositories.athlete_goals import AthleteGoalsRepository
from endury.database.sqlite.session import DatabaseSession

_TODAY = "2026-09-19"


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> AthleteGoalsRepository:
    """A races repository over a fresh in-memory database, clock pinned."""
    monkeypatch.setattr("endury.common.clock.today", lambda: date.fromisoformat(_TODAY))
    monkeypatch.setattr(
        "endury.common.clock.now",
        lambda: datetime.fromisoformat(f"{_TODAY}T12:00:00"),
    )
    session = DatabaseSession(":memory:")
    session.init_schema()
    return AthleteGoalsRepository(session)


def test_upsert_creates_a_new_race(repo: AthleteGoalsRepository) -> None:
    race = repo.upsert(
        name="Berlin Marathon", race_date="2026-11-08", goal_type="running_marathon"
    )
    assert race.id > 0
    assert race.name == "Berlin Marathon"
    assert race.race_date == "2026-11-08"
    assert race.goal_type == "running_marathon"
    assert race.priority == "A"
    assert race.status == "planned"
    assert race.created_at.startswith(_TODAY)
    assert race.updated_at.startswith(_TODAY)


def test_upsert_by_id_updates_the_same_row(repo: AthleteGoalsRepository) -> None:
    created = repo.upsert(name="Berlin Marathon", race_date="2026-11-08")
    updated = repo.upsert(
        race_id=created.id, name="Berlin Marathon (renamed)", race_date="2026-11-08"
    )
    assert updated.id == created.id
    assert repo.get(created.id).name == "Berlin Marathon (renamed)"
    assert len(repo.list(include_past_days=0)) == 1


def test_upsert_by_natural_key_updates_instead_of_duplicating(
    repo: AthleteGoalsRepository,
) -> None:
    first = repo.upsert(
        name="Marathon — 2026-11-08",
        race_date="2026-11-08",
        goal_type="running_marathon",
    )
    second = repo.upsert(
        name="Berlin Marathon",
        race_date="2026-11-08",
        goal_type="running_marathon",
        priority="A",
    )
    assert second.id == first.id
    assert second.name == "Berlin Marathon"
    assert len(repo.list()) == 1


def test_upsert_by_natural_key_treats_a_different_goal_type_as_a_new_race(
    repo: AthleteGoalsRepository,
) -> None:
    repo.upsert(name="A", race_date="2026-11-08", goal_type="running_marathon")
    repo.upsert(name="B", race_date="2026-11-08", goal_type="trail_50k")
    assert len(repo.list()) == 2


def test_upsert_preserves_created_at_across_updates(
    repo: AthleteGoalsRepository,
) -> None:
    created = repo.upsert(name="A", race_date="2026-11-08")
    updated = repo.upsert(race_id=created.id, name="B", race_date="2026-11-08")
    assert updated.created_at == created.created_at


def test_get_returns_none_for_an_unknown_id(repo: AthleteGoalsRepository) -> None:
    assert repo.get(999) is None


def test_get_by_natural_key_returns_none_when_nothing_matches(
    repo: AthleteGoalsRepository,
) -> None:
    assert repo.get_by_natural_key("2026-11-08", "running_marathon") is None


def test_get_by_natural_key_matches_a_null_goal_type(
    repo: AthleteGoalsRepository,
) -> None:
    race = repo.upsert(name="Mystery race", race_date="2026-11-08", goal_type=None)
    found = repo.get_by_natural_key("2026-11-08", None)
    assert found is not None
    assert found.id == race.id


def test_get_by_date_returns_none_when_nothing_matches(
    repo: AthleteGoalsRepository,
) -> None:
    assert repo.get_by_date("2026-11-08") is None


def test_get_by_date_ignores_goal_type(repo: AthleteGoalsRepository) -> None:
    race = repo.upsert(
        name="Mystery race", race_date="2026-11-08", goal_type="trail_50k"
    )
    found = repo.get_by_date("2026-11-08")
    assert found is not None
    assert found.id == race.id


def test_get_by_date_excludes_a_cancelled_race(repo: AthleteGoalsRepository) -> None:
    race = repo.upsert(name="Dropped", race_date="2026-11-08")
    repo.upsert(
        race_id=race.id, name="Dropped", race_date="2026-11-08", status="cancelled"
    )
    assert repo.get_by_date("2026-11-08") is None


def test_list_orders_by_race_date(repo: AthleteGoalsRepository) -> None:
    repo.upsert(name="Later", race_date="2026-12-01")
    repo.upsert(name="Sooner", race_date="2026-10-01")
    races = repo.list()
    assert [race.name for race in races] == ["Sooner", "Later"]


def test_list_excludes_races_past_the_window(repo: AthleteGoalsRepository) -> None:
    repo.upsert(name="Long gone", race_date="2026-01-01")
    repo.upsert(name="Recent", race_date="2026-09-01")
    races = repo.list(include_past_days=30)
    assert [race.name for race in races] == ["Recent"]


def test_list_default_window_is_thirty_days(repo: AthleteGoalsRepository) -> None:
    repo.upsert(name="30 days ago", race_date="2026-08-20")
    repo.upsert(name="31 days ago", race_date="2026-08-19")
    races = repo.list()
    assert [race.name for race in races] == ["30 days ago"]


def test_races_are_scoped_to_athlete_id_one(repo: AthleteGoalsRepository) -> None:
    """Direct SQL rows for a different athlete_id never surface."""
    with repo._session.cursor() as cursor:
        cursor.execute(
            "INSERT INTO athlete_goals "
            "(athlete_id, name, race_date, priority, status, "
            "created_at, updated_at) "
            "VALUES (2, 'Someone else', '2026-11-08', 'A', 'planned', ?, ?)",
            (f"{_TODAY}T12:00:00", f"{_TODAY}T12:00:00"),
        )
    assert repo.list() == []
