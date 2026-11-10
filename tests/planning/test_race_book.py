"""Tests for race_book.sync_plan_races — races land in the athlete_goals table."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from endury.database.sqlite.repositories.athlete_goals import AthleteGoalsRepository
from endury.database.sqlite.session import DatabaseSession
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.race_book import drop_block_ends, sync_plan_races
from endury.planning.replan_plan import run_replan
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    RaceGoalModel,
)
from tests.planning.test_replan import (
    _RACE,
    _START,
    _TODAY,
    _base_repos,
    _committed_plan,
    _progress,
    _repos_with,
)


class _FakeRaceRepo:
    """Minimal in-memory stand-in for AthleteGoalsRepository."""

    def __init__(self) -> None:
        self.rows: dict[int, SimpleNamespace] = {}
        self._next_id = 1

    def get_by_natural_key(
        self, race_date: str, goal_type: str | None
    ) -> SimpleNamespace | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.race_date == race_date and row.goal_type == goal_type
            ),
            None,
        )

    def upsert(
        self,
        *,
        race_id: int | None = None,
        name: str,
        race_date: str,
        goal_type: str | None = None,
        priority: str = "A",
        **_kwargs: Any,
    ) -> SimpleNamespace:
        if race_id is not None and race_id in self.rows:
            row = self.rows[race_id]
            row.name = name
            row.race_date = race_date
            row.goal_type = goal_type
            row.priority = priority
            return row
        row = SimpleNamespace(
            id=self._next_id,
            name=name,
            race_date=race_date,
            goal_type=goal_type,
            priority=priority,
            distance_km=None,
            climb_m=None,
            status="planned",
            notes=None,
        )
        self.rows[row.id] = row
        self._next_id += 1
        return row


class TestSyncPlanRaces:
    def test_creates_a_race_with_a_default_name(self) -> None:
        repos = SimpleNamespace(races=_FakeRaceRepo())
        sync_plan_races(
            repos,
            [
                {
                    "goal_type": "running_marathon",
                    "race_date": "2026-11-08",
                    "priority": "A",
                }
            ],
        )
        (race,) = repos.races.rows.values()
        assert race.name == "Marathon — 2026-11-08"
        assert race.priority == "A"

    def test_a_maintenance_block_is_not_a_race(self) -> None:
        # Its race_date only marks where the block ends.
        repos = SimpleNamespace(races=_FakeRaceRepo())
        sync_plan_races(
            repos,
            [{"goal_type": "triathlon_maintenance", "race_date": "2026-11-08"}],
        )
        assert repos.races.rows == {}

    def test_drop_block_ends_deletes_only_maintenance_rows(self) -> None:
        session = DatabaseSession(":memory:")
        session.init_schema()
        repo = AthleteGoalsRepository(session)
        real = repo.upsert(
            name="Roth", race_date="2026-07-05", goal_type="ironman", priority="A"
        )
        for goal_type in ("triathlon_maintenance", "running_maintenance"):
            repo.upsert(name="Block", race_date="2026-01-04", goal_type=goal_type)

        assert drop_block_ends(SimpleNamespace(races=repo)) == 2

        assert [race.id for race in repo.list(include_past_days=3650)] == [real.id]

    def test_default_name_without_a_goal_type(self) -> None:
        repos = SimpleNamespace(races=_FakeRaceRepo())
        sync_plan_races(repos, [{"goal_type": None, "race_date": "2026-11-08"}])
        (race,) = repos.races.rows.values()
        assert race.name == "Race — 2026-11-08"

    def test_does_not_overwrite_an_athlete_named_race(self) -> None:
        repos = SimpleNamespace(races=_FakeRaceRepo())
        repos.races.upsert(
            name="Berlin Marathon",
            race_date="2026-11-08",
            goal_type="running_marathon",
            priority="A",
        )
        sync_plan_races(
            repos,
            [
                {
                    "goal_type": "running_marathon",
                    "race_date": "2026-11-08",
                    "priority": "B",
                }
            ],
        )
        (race,) = repos.races.rows.values()
        assert race.name == "Berlin Marathon"
        # The plan's own priority for this race still flows through each sync.
        assert race.priority == "B"

    def test_keeps_the_athletes_own_fields_on_a_race_it_already_knows(self) -> None:
        """A sync writes the plan's priority — and nothing else it never knew."""
        session = DatabaseSession(":memory:")
        session.init_schema()
        repo = AthleteGoalsRepository(session)
        stored = repo.upsert(
            name="Berlin Marathon",
            race_date="2026-11-08",
            goal_type="running_marathon",
            priority="A",
            distance_km=42.2,
            climb_m=120.0,
            status="completed",
            notes="flat, PB attempt",
        )

        sync_plan_races(
            SimpleNamespace(races=repo),
            [
                {
                    "goal_type": "running_marathon",
                    "race_date": "2026-11-08",
                    "priority": "A",
                }
            ],
        )

        after = repo.get(stored.id)
        assert after is not None
        assert (after.distance_km, after.climb_m) == (42.2, 120.0)
        assert after.notes == "flat, PB attempt"
        assert after.status == "completed"

    def test_matches_only_on_the_same_natural_key(self) -> None:
        repos = SimpleNamespace(races=_FakeRaceRepo())
        sync_plan_races(
            repos, [{"goal_type": "running_marathon", "race_date": "2026-11-08"}]
        )
        sync_plan_races(repos, [{"goal_type": "trail_50k", "race_date": "2026-11-08"}])
        assert len(repos.races.rows) == 2


class TestCommitAndReplanSyncRaces:
    def test_commit_upserts_the_plans_races(self) -> None:
        repos = _base_repos()
        repos.races = _FakeRaceRepo()
        repos.plan.create.return_value = 1
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_marathon", race_date=_RACE)],
            training_start=_START,
            accept_experimental=True,  # beyond the checked horizons
        )
        result = run_generate(request, repos=repos, progress=_progress)
        assert result.status == "ok"

        commit = run_commit(
            CommitRequest(
                name="Marathon Block", generate_request=request, generate_result=result
            ),
            repos=repos,
            maybe_get_coach=lambda: None,
            client=None,
        )

        assert commit["status"] == "ok"
        (race,) = repos.races.rows.values()
        assert race.race_date == _RACE
        assert race.goal_type == "running_marathon"

    def test_replan_upserts_a_changed_race_set(self) -> None:
        # A past B race leaves the plan on replan — its sync must leave too.
        active, macro, solve = _committed_plan()
        solve["request"]["races"] = [
            *solve["request"]["races"],
            {"goal_type": "running_10k", "race_date": "2026-04-05", "priority": "B"},
        ]
        active.solve_result_json = json.dumps(solve)
        repos, _plan_repo = _repos_with(active, macro)
        repos.races = _FakeRaceRepo()

        run_replan(
            repos=repos, progress=_progress, maybe_get_coach=lambda: None, today=_TODAY
        )

        synced = {race.race_date for race in repos.races.rows.values()}
        assert _RACE in synced
        assert "2026-04-05" not in synced
