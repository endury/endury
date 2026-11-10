"""Tests for add_race_to_plan (dry-run fit + confirm-gated replan)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from endury.planner.foundations.types import AmbitionLevel, GoalType
from endury.planning import add_race as mod
from endury.planning.add_race import add_race_to_plan
from endury.planning.commit_plan import build_solve_result_payload
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    build_request_payload,
    run_generate,
)
from endury.planning.offered_plans import OFFERED_TIERS
from endury.planning.wire.requests import GenerateError, GenerateRequest, RaceGoalModel
from tests.planning.test_replan import (
    _START,
    _TODAY,
    _base_repos,
    _committed_plan,
    _progress,
    _repos_with,
)

#: Week 16 of the committed 35-week marathon plan — a real, valid B/C slot,
#: verified against the real pipeline (not assumed).
_TUNE_UP_DATE = "2026-07-05"


def _race(
    race_id: int = 1,
    *,
    name: str = "City 10K",
    race_date: str = _TUNE_UP_DATE,
    goal_type: str | None = "running_10k",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=race_id, name=name, race_date=race_date, goal_type=goal_type
    )


class _RaceRepo:
    """Minimal ``repos.races`` stand-in: one race, looked up by id."""

    def __init__(self, race: SimpleNamespace | None) -> None:
        self._race = race

    def get(self, race_id: int) -> SimpleNamespace | None:
        if self._race is not None and self._race.id == race_id:
            return self._race
        return None


def _repos(race: SimpleNamespace | None = None):
    active, macro, _ = _committed_plan()
    repos, plan_repo = _repos_with(active, macro)
    repos.races = _RaceRepo(race if race is not None else _race())
    return repos, plan_repo


class TestValidation:
    def test_refuses_an_unknown_priority(self) -> None:
        repos, _ = _repos()
        result = add_race_to_plan(repos=repos, race_id=1, priority="D", today=_TODAY)
        assert result["status"] == "error"

    def test_refuses_an_unknown_race_id(self) -> None:
        repos, _ = _repos(None)
        result = add_race_to_plan(repos=repos, race_id=999, today=_TODAY)
        assert result["status"] == "error"
        assert "999" in result["message"]

    def test_refuses_a_race_with_no_goal_type(self) -> None:
        repos, _ = _repos(_race(goal_type=None))
        result = add_race_to_plan(repos=repos, race_id=1, today=_TODAY)
        assert result["status"] == "error"
        assert "upsert_race" in result["message"]

    def test_refuses_without_an_active_plan(self) -> None:
        repos, _ = _repos_with(None, None)
        repos.races = _RaceRepo(_race())
        result = add_race_to_plan(repos=repos, race_id=1, today=_TODAY)
        assert result["status"] == "error"


class TestCurrentWeekGuard:
    def test_race_inside_the_current_week_is_refused(self) -> None:
        # _TODAY is 2026-05-20 (Wed); next Monday is 2026-05-25.
        repos, _ = _repos(_race(race_date="2026-05-22"))
        result = add_race_to_plan(repos=repos, race_id=1, today=_TODAY)
        assert result["status"] == "error"
        assert "current week" in result["message"]

    def test_a_race_already_behind_us_is_refused(self) -> None:
        repos, _ = _repos(_race(race_date="2026-05-01"))
        result = add_race_to_plan(repos=repos, race_id=1, today=_TODAY)
        assert result["status"] == "error"


class TestFit:
    def test_b_race_that_fits_previews_as_b(self) -> None:
        repos, plan_repo = _repos()
        result = add_race_to_plan(repos=repos, race_id=1, priority="B", today=_TODAY)
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["fits_as"] == "B"
        assert result["weeks"] > 0
        assert any(
            race["race_date"] == _TUNE_UP_DATE and race["priority"] == "B"
            for race in result["races"]
        )
        assert plan_repo.updated_solve_json is None, "the dry run wrote nothing"

    def test_b_race_that_does_not_fit_falls_back_to_c(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_run_generate = mod.run_generate

        def _fake_generate(request, **kwargs):
            if request.races[-1].priority == "B":
                return GenerateError(error_message="too close to another goal")
            return original_run_generate(request, **kwargs)

        monkeypatch.setattr(mod, "run_generate", _fake_generate)
        repos, _ = _repos()

        result = add_race_to_plan(repos=repos, race_id=1, priority="B", today=_TODAY)

        assert result["status"] == "preview"
        assert result["fits_as"] == "C"

    def test_race_that_fits_nowhere_relays_the_planner_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _always_fails(request, **_kwargs):
            return GenerateError(
                error_message="Your tune-up races fall in the same week."
            )

        monkeypatch.setattr(mod, "run_generate", _always_fails)
        repos, _ = _repos()

        result = add_race_to_plan(repos=repos, race_id=1, priority="B", today=_TODAY)

        assert result["status"] == "preview"
        assert result["confirm_required"] is False
        assert result["fits_as"] is None
        assert result["reason"] == "Your tune-up races fall in the same week."
        assert "Your tune-up races fall in the same week." in result["message"]

    def test_a_requested_priority_of_c_never_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def _always_fails(request, **_kwargs):
            calls.append(request.races[-1].priority)
            return GenerateError(error_message="nope")

        monkeypatch.setattr(mod, "run_generate", _always_fails)
        repos, _ = _repos()

        add_race_to_plan(repos=repos, race_id=1, priority="C", today=_TODAY)

        assert calls == ["C"]


class TestConfirm:
    def test_confirm_persists_and_replans(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repos, plan_repo = _repos()
        fake_result = {
            "status": "ok",
            "action": "replanned",
            "reasoning": "Regenerated the rest of the plan around the new race.",
            "push": {"status": "skipped", "message": "no client"},
        }
        monkeypatch.setattr(mod, "run_replan", lambda **_kwargs: fake_result)

        result = add_race_to_plan(
            repos=repos, race_id=1, priority="B", confirm=True, today=_TODAY
        )

        assert result["status"] == "ok"
        assert result["fits_as"] == "B"
        assert result["reasoning"] == fake_result["reasoning"]
        assert result["push"] == fake_result["push"]
        stored = json.loads(plan_repo.updated_solve_json)
        assert any(
            race["race_date"] == _TUNE_UP_DATE and race["priority"] == "B"
            for race in stored["request"]["races"]
        )

    def test_a_refused_replan_puts_the_stored_request_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repos, plan_repo = _repos()
        original_races = json.loads(plan_repo._active.solve_result_json)["request"][
            "races"
        ]

        def _refuse(**_kwargs):
            raise ValueError("Race week — nothing left to replan.")

        monkeypatch.setattr(mod, "run_replan", _refuse)

        result = add_race_to_plan(
            repos=repos, race_id=1, priority="B", confirm=True, today=_TODAY
        )

        assert result["status"] == "error"
        stored = json.loads(plan_repo.updated_solve_json)
        assert stored["request"]["races"] == original_races


@pytest.mark.usefixtures("offered_as_shipped")
def _maintenance_block() -> tuple[SimpleNamespace, SimpleNamespace]:
    """A 16-week running maintenance block, committed, half-way through."""
    request = GenerateRequest(
        races=[RaceGoalModel(goal_type="running_maintenance", race_date="2026-06-28")],
        training_start=_START,
        ambition_level="basic",
        accept_experimental=True,
    )
    result = run_generate(
        request, repos=_base_repos(), progress=_progress, enforce_offered=False
    )
    assert result.status == "ok", result
    solve = build_solve_result_payload(result)
    solve["request"] = build_request_payload(request)
    active = SimpleNamespace(
        id=1,
        name="Running maintenance",
        goal_type="running_maintenance",
        goal_date="2026-06-28",
        plan_start=_START,
        solve_result_json=json.dumps(solve),
        solver_version=SOLVER_VERSION,
        athlete_state_json=json.dumps(result.athlete_state),
    )
    return active, SimpleNamespace(id=5, revision=1)


def test_a_tune_up_fits_a_maintenance_block_as_b() -> None:
    """Seen live: an A race four days into a maintenance block had no
    planner path, so the coach hand-wrote a taper the calendar never saw.
    A block now takes a tune-up in any week after the current one."""
    active, macro = _maintenance_block()
    repos, plan_repo = _repos_with(active, macro)
    repos.races = _RaceRepo(_race(race_date="2026-06-07"))  # week 13's Sunday

    result = add_race_to_plan(repos=repos, race_id=1, priority="B", today=_TODAY)

    assert result["status"] == "preview", result
    assert result["fits_as"] == "B"
    assert plan_repo.updated_solve_json is None


def test_a_race_joins_a_stored_plan_that_is_no_longer_on_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as offer:
        offer.setitem(
            OFFERED_TIERS, GoalType.RUNNING_MARATHON, frozenset(AmbitionLevel)
        )
        repos, _plan_repo = _repos()

    result = add_race_to_plan(repos=repos, race_id=1, priority="B", today=_TODAY)

    assert result["status"] == "preview"
    assert result["fits_as"] == "B"
