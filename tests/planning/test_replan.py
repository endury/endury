"""Tests for the deterministic replan (run_replan)."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.database.sqlite.repositories.self_assessment import SelfAssessmentRow
from endury.intervals.models import AthleteProfile
from endury.planner.foundations.types import AmbitionLevel, GoalType
from endury.planner.macro.macro_planner import QPInfeasibleError
from endury.planning import adjust_week as adjust_week_mod
from endury.planning import generate_plan as generate_plan_module
from endury.planning import replan_plan as mod
from endury.planning import schedule_workout as schedule_workout_mod
from endury.planning.adjust_week import adjust_current_week
from endury.planning.commit_plan import (
    build_solve_result_payload,
    reschedule_session,
)
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    build_request_payload,
    run_generate,
)
from endury.planning.offered_plans import OFFERED_TIERS
from endury.planning.replan_plan import run_replan
from endury.planning.schedule_workout import schedule_workout
from endury.planning.wire.requests import (
    GenerateError,
    GenerateRequest,
    RaceGoalModel,
    ScheduleRequest,
)
from endury.planning.wire.sessions import build_commit_session_rows

_START = "2026-03-09"  # a Monday
_RACE = "2026-11-08"  # a Sunday
_TODAY = date(2026, 5, 20)  # a Wednesday, 10+ weeks in
_CUTOFF = "2026-05-25"  # next Monday
_PRIOR = 11


def _progress(event: object, message: object = None) -> None:
    _ = event, message


class _FakePlanRepo:
    def __init__(self, active: SimpleNamespace | None, macro: SimpleNamespace | None):
        self._active = active
        self._macro = macro
        self.deleted_from: str | None = None
        self.inserted_rows: list | None = None
        self.updated_solve_json: str | None = None
        self.revise_macro_kwargs: dict | None = None

    def get_active(self) -> SimpleNamespace | None:
        return self._active

    def get_sessions(self, plan_id: int) -> list[SimpleNamespace]:
        return []

    def get_macro_for_plan(self, plan: object) -> SimpleNamespace | None:
        return self._macro

    def delete_future_sessions(self, plan_id: int, from_date: str) -> int:
        self.deleted_from = from_date
        return 7

    def bulk_insert_sessions(self, plan_id: int, rows: list) -> None:
        self.inserted_rows = rows

    def update_solve_result_json(self, plan_id: int, solve_result_json: str) -> None:
        self.updated_solve_json = solve_result_json

    def revise_macro(self, macro_id: int, **kwargs: object) -> None:
        self.revise_macro_kwargs = {"macro_id": macro_id, **kwargs}


class _FakeIntervalsClient:
    """Offline stand-in for the ``IntervalsSource`` protocol (no history)."""

    def get_athlete(self) -> AthleteProfile:
        return AthleteProfile(sport_settings=[])

    def list_activities(self, oldest, newest) -> list:
        return []


def _base_repos() -> MagicMock:
    repos = MagicMock()
    repos.intervals_client = _FakeIntervalsClient()
    repos.self_assessment.get_all.return_value = {
        # Reproduces the goal-default running SportState exactly.
        "running": SelfAssessmentRow(8.0, 60, "2026-01-01"),
    }
    repos.session.transaction.return_value = nullcontext()
    return repos


def _request(
    race: str = _RACE, *, accept: bool = False, cap: float | None = None
) -> GenerateRequest:
    return GenerateRequest(
        races=[RaceGoalModel(goal_type="running_marathon", race_date=race)],
        training_start=_START,
        ambition_level="moderate",
        accept_short_horizon=accept,
        accept_experimental=True,  # 35 weeks: beyond the checked horizons
        weekly_hours_cap=cap,
    )


def _committed_plan(
    race: str = _RACE, *, accept: bool = False, cap: float | None = None
) -> tuple[SimpleNamespace, SimpleNamespace, dict]:
    """Generate the original plan and shape it as the persisted active plan."""
    request = _request(race, accept=accept, cap=cap)
    result = run_generate(request, repos=_base_repos(), progress=_progress)
    assert result.status == "ok"
    solve = build_solve_result_payload(result)
    solve["request"] = build_request_payload(request)
    active = SimpleNamespace(
        id=1,
        name="Marathon Block",
        goal_type="running_marathon",
        goal_date=race,
        plan_start=_START,
        solve_result_json=json.dumps(solve),
        solver_version=SOLVER_VERSION,
        athlete_state_json=json.dumps(result.athlete_state),
    )
    macro = SimpleNamespace(id=5, revision=1)
    return active, macro, solve


def _repos_with(active: SimpleNamespace | None, macro: SimpleNamespace | None):
    repos = _base_repos()
    plan_repo = _FakePlanRepo(active, macro)
    repos.plan = plan_repo
    return repos, plan_repo


class _StatefulPlanRepo(_FakePlanRepo):
    """Plan repo that keeps materialized session rows, like the real DB."""

    def __init__(self, active: SimpleNamespace, macro: SimpleNamespace | None):
        super().__init__(active, macro)
        solve = json.loads(active.solve_result_json)
        self.rows = [
            SimpleNamespace(
                id=index + 1,
                slot_id=session["slot_id"],
                date=session["date"],
                week_index=session["week_index"],
                phase_type=session.get("phase_type", "base"),
                matched_activity_id=None,
            )
            for index, session in enumerate(solve["sessions"])
        ]

    def get_sessions(self, plan_id: int) -> list[SimpleNamespace]:
        return sorted(self.rows, key=lambda row: (row.date, row.slot_id))

    def update_session_date(
        self,
        session_id: int,
        plan_id: int,
        new_date: str,
        *,
        week_index: int,
        phase_type: str | None = None,
    ) -> bool:
        row = next((r for r in self.rows if r.id == session_id), None)
        if row is None:
            return False
        row.date = new_date
        row.week_index = week_index
        if phase_type is not None:
            row.phase_type = phase_type
        return True

    def update_solve_result_json(self, plan_id: int, solve_result_json: str) -> None:
        super().update_solve_result_json(plan_id, solve_result_json)
        self._active.solve_result_json = solve_result_json

    def delete_future_sessions(self, plan_id: int, from_date: str) -> int:
        self.deleted_from = from_date
        keep = [
            r
            for r in self.rows
            if r.date < from_date or r.matched_activity_id is not None
        ]
        deleted = len(self.rows) - len(keep)
        self.rows = keep
        return deleted

    def bulk_insert_sessions(self, plan_id: int, rows: list) -> None:
        super().bulk_insert_sessions(plan_id, rows)
        next_id = max((r.id for r in self.rows), default=0) + 1
        for offset, row in enumerate(rows):
            self.rows.append(
                SimpleNamespace(
                    id=next_id + offset,
                    slot_id=row.slot_id,
                    date=row.date,
                    week_index=row.week_index,
                    matched_activity_id=None,
                )
            )


def _stateful_repos() -> tuple[MagicMock, _StatefulPlanRepo]:
    active, macro, _ = _committed_plan()
    repos = _base_repos()
    plan_repo = _StatefulPlanRepo(active, macro)
    repos.plan = plan_repo
    return repos, plan_repo


class TestRescheduleAcrossReplanCutoff:
    """A manual move must leave the DB rows and the solve payload coherent."""

    @staticmethod
    def _assert_coherent(plan_repo: _StatefulPlanRepo) -> None:
        merged = json.loads(plan_repo.updated_solve_json)
        payload = sorted((s["slot_id"], s["date"]) for s in merged["sessions"])
        rows = sorted((r.slot_id, r.date) for r in plan_repo.rows)
        assert len(rows) == len(set(rows)), "duplicate sessions materialized"
        assert rows == payload

    def test_move_from_current_week_into_the_future_stays_coherent(self) -> None:
        repos, plan_repo = _stateful_repos()
        moved = next(
            r
            for r in plan_repo.get_sessions(1)
            if "2026-05-20" <= r.date <= "2026-05-24"
        )
        assert (
            reschedule_session(
                moved.id,
                ScheduleRequest(date="2026-05-28"),
                get_repos=lambda: repos,
            )["status"]
            == "ok"
        )

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        self._assert_coherent(plan_repo)

    def test_move_from_the_future_into_the_current_week_stays_coherent(self) -> None:
        repos, plan_repo = _stateful_repos()
        moved = next(
            r
            for r in plan_repo.get_sessions(1)
            if "2026-05-25" <= r.date <= "2026-05-31"
        )
        assert (
            reschedule_session(
                moved.id,
                ScheduleRequest(date="2026-05-24"),
                get_repos=lambda: repos,
            )["status"]
            == "ok"
        )

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        self._assert_coherent(plan_repo)

    def test_move_into_a_day_that_has_passed_stays_coherent(self) -> None:
        """A session done on another day goes back to that day."""
        repos, plan_repo = _stateful_repos()
        moved = next(r for r in plan_repo.get_sessions(1) if r.date >= "2026-05-25")
        result = reschedule_session(
            moved.id,
            ScheduleRequest(date="2026-05-19"),
            get_repos=lambda: repos,
        )
        assert result["status"] == "ok"
        assert moved.date == "2026-05-19"

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        self._assert_coherent(plan_repo)


class TestReplanHappyPath:
    def test_replaces_future_and_keeps_past(self) -> None:
        active, macro, original_solve = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert out["status"] == "ok"
        assert out["action"] == "replanned"
        assert plan_repo.deleted_from == _CUTOFF
        assert plan_repo.inserted_rows, "expected regenerated session rows"
        for row in plan_repo.inserted_rows:
            assert row.slot_id.startswith("r2:")
            assert row.date >= _CUTOFF
            assert row.week_index >= _PRIOR

        merged = json.loads(plan_repo.updated_solve_json)
        dates = [s["date"] for s in merged["sessions"]]
        assert any(d < _CUTOFF for d in dates), "past sessions must be retained"
        assert merged["request"] == original_solve["request"]

    def test_phase_calendar_is_continuous_with_original(self) -> None:
        """Same request + defaults → the replanned suffix equals the original."""
        active, macro, original_solve = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        merged = json.loads(plan_repo.updated_solve_json)
        assert merged["phase_calendar"] == original_solve["phase_calendar"]

    def test_macro_revised_and_projections_cover_full_window(self) -> None:
        active, macro, original_solve = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        assert plan_repo.revise_macro_kwargs is not None
        assert plan_repo.revise_macro_kwargs["revision"] == 2
        merged = json.loads(plan_repo.updated_solve_json)
        assert merged["macro_plan"]["revision"] == 2
        assert len(merged["macro_plan"]["weeks"]) == len(
            original_solve["macro_plan"]["weeks"]
        )
        days = (date.fromisoformat(_RACE) - date.fromisoformat(_START)).days + 1
        assert len(merged["projections"]["aggregate_ctl"]) == days

    def test_matched_future_session_survives_in_the_solve_payload(self) -> None:
        """A moved-then-matched session past the cutoff stays in BOTH layers
        — the DB exempts matched rows, so the solve payload must too."""
        repos, plan_repo = _stateful_repos()
        moved = next(r for r in plan_repo.rows if r.date >= _CUTOFF)
        moved.matched_activity_id = "act-1"

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert any(r.slot_id == moved.slot_id for r in plan_repo.rows)
        merged = json.loads(plan_repo.updated_solve_json)
        assert any(s["slot_id"] == moved.slot_id for s in merged["sessions"]), (
            "matched future session vanished from the solve payload"
        )

    def test_brief_describes_the_full_plan_not_the_fragment(self) -> None:
        """The replanned brief keeps whole-plan horizon + week numbering."""
        active, macro, original_solve = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        assert plan_repo.revise_macro_kwargs is not None
        brief = plan_repo.revise_macro_kwargs["coaching_brief"]
        total_weeks = len(original_solve["phase_calendar"])
        assert brief.startswith(f"{total_weeks}-week marathon plan")
        assert f"Resumed mid-plan at week {_PRIOR + 1}." in brief
        # Week numbering is absolute: the taper lands at the plan's end,
        # never at "weeks 1-2" of a fragment-relative frame.
        assert "taper weeks 1-" not in brief


class TestReplanEdges:
    def test_replan_before_plan_start_regenerates_from_original_start(self) -> None:
        active, macro, _ = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=date(2026, 2, 1),  # weeks before the plan starts
        )
        assert out["status"] == "ok"
        assert plan_repo.deleted_from == _START
        assert min(r.week_index for r in plan_repo.inserted_rows) == 0

    def test_corrupt_athlete_state_json_falls_back_to_fresh_seed(self) -> None:
        active, macro, _ = _committed_plan()
        active.athlete_state_json = "{{not json"
        repos, plan_repo = _repos_with(active, macro)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        assert out["status"] == "ok"
        merged = json.loads(plan_repo.updated_solve_json)
        assert merged["projections"]["aggregate_ctl"]


class TestReplanFromAResumeDate:
    """A mid-week resume rewrites from that day, not from next Monday."""

    def test_regenerates_from_the_resume_date(self) -> None:
        repos, plan_repo = _stateful_repos()
        head = {r.slot_id for r in plan_repo.rows if _CUTOFF <= r.date < "2026-05-28"}
        assert head, "fixture must have sessions before the resume date"

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
            resume_date=date(2026, 5, 28),
        )

        assert out["status"] == "ok"
        assert plan_repo.deleted_from == "2026-05-28"
        assert plan_repo.inserted_rows, "expected regenerated session rows"
        for row in plan_repo.inserted_rows:
            assert row.slot_id.startswith("r2:")
            assert row.date >= "2026-05-28"
            assert row.week_index >= _PRIOR
        # The days the athlete is out keep their committed sessions.
        assert head == {
            r.slot_id for r in plan_repo.rows if _CUTOFF <= r.date < "2026-05-28"
        }

    def test_a_resume_inside_the_current_week_cuts_at_that_day(self) -> None:
        repos, plan_repo = _stateful_repos()

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
            resume_date=date(2026, 5, 23),
        )

        assert out["status"] == "ok"
        assert plan_repo.deleted_from == "2026-05-23"
        assert plan_repo.inserted_rows, "expected regenerated session rows"
        assert all(row.date >= "2026-05-23" for row in plan_repo.inserted_rows)
        assert min(row.week_index for row in plan_repo.inserted_rows) == _PRIOR - 1


class TestReplanKeepsThePlansOwnTimeline:
    """A replan resumes the plan's cadence and opening week, not its own start."""

    def test_a_trail_replan_two_weeks_in_neither_retests_nor_eases_the_long_run(
        self,
    ) -> None:
        start, race = "2026-03-09", "2026-07-26"  # 20 weeks, Monday to Sunday
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="trail_20k", race_date=race)],
            training_start=start,
            ambition_level="basic",
        )
        result = run_generate(request, repos=_base_repos(), progress=_progress)
        assert result.status == "ok"
        solve = build_solve_result_payload(result)
        solve["request"] = build_request_payload(request)
        active = SimpleNamespace(
            id=1,
            name="Trail Block",
            goal_type="trail_20k",
            goal_date=race,
            plan_start=start,
            solve_result_json=json.dumps(solve),
            solver_version=SOLVER_VERSION,
            athlete_state_json=json.dumps(result.athlete_state),
        )
        repos, plan_repo = _repos_with(active, SimpleNamespace(id=5, revision=1))

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=date(2026, 3, 18),  # Wednesday of week 1: the cut is week 2
        )

        assert out["status"] == "ok"
        rows = plan_repo.inserted_rows
        first_week = [r for r in rows if "2026-03-23" <= r.date <= "2026-03-29"]
        keys = {r.activity_key for r in first_week}
        assert "run_threshold_test" not in keys
        assert "run_trail_long" in keys
        recovery = {
            w["week_index"] for w in solve["macro_plan"]["weeks"] if w["is_recovery"]
        }
        tests = [r.week_index for r in rows if r.activity_key == "run_threshold_test"]
        assert len(tests) <= 1
        assert set(tests) <= recovery

    def test_a_replan_in_a_long_b_races_week_keeps_the_easier_week_after_it(
        self,
    ) -> None:
        start, b_race, a_race = "2026-03-09", "2026-05-31", "2026-08-23"
        request = GenerateRequest(
            races=[
                RaceGoalModel(goal_type="half_ironman", race_date=a_race),
                RaceGoalModel(
                    goal_type="running_marathon", race_date=b_race, priority="B"
                ),
            ],
            training_start=start,
            ambition_level="moderate",
        )
        assessed = {
            "running": SelfAssessmentRow(3.5, 80, "2026-01-01"),
            "cycling": SelfAssessmentRow(4.0, 120, "2026-01-01"),
            "swimming": SelfAssessmentRow(1.5, 40, "2026-01-01"),
        }
        repos = _base_repos()
        repos.self_assessment.get_all.return_value = assessed
        result = run_generate(request, repos=repos, progress=_progress)
        assert result.status == "ok"
        solve = build_solve_result_payload(result)
        solve["request"] = build_request_payload(request)
        active = SimpleNamespace(
            id=1,
            name="Half Block",
            goal_type="half_ironman",
            goal_date=a_race,
            plan_start=start,
            solve_result_json=json.dumps(solve),
            solver_version=SOLVER_VERSION,
            athlete_state_json=json.dumps(result.athlete_state),
        )
        repos, plan_repo = _repos_with(active, SimpleNamespace(id=5, revision=1))
        repos.self_assessment.get_all.return_value = assessed

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=date(2026, 5, 27),  # Wednesday of the race week: the cut is after it
        )

        assert out["status"] == "ok"
        week_after = ("2026-06-01", "2026-06-07")
        held = {
            (s["date"], s["activity_key"])
            for s in solve["sessions"]
            if week_after[0] <= s["date"] <= week_after[1]
        }
        replanned = {
            (r.date, r.activity_key)
            for r in plan_repo.inserted_rows
            if week_after[0] <= r.date <= week_after[1]
        }
        assert replanned == held


class TestReplanErrors:
    def test_no_active_plan(self) -> None:
        repos, _ = _repos_with(None, None)
        with pytest.raises(ValueError, match="No active training plan"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=_TODAY,
            )

    def test_foreign_solver_version(self) -> None:
        active = SimpleNamespace(
            solver_version="planner_v6_full_horizon_v1", solve_result_json="{}"
        )
        repos, _ = _repos_with(active, None)
        with pytest.raises(ValueError, match="retired engine"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=_TODAY,
            )

    def test_missing_stored_request(self) -> None:
        active = SimpleNamespace(solver_version=SOLVER_VERSION, solve_result_json="{}")
        repos, _ = _repos_with(active, None)
        with pytest.raises(ValueError, match="no stored request"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=_TODAY,
            )

    def test_goal_race_passed(self) -> None:
        active, macro, _ = _committed_plan()
        repos, _ = _repos_with(active, macro)
        with pytest.raises(ValueError, match="race has passed"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 11, 9),
            )

    def test_race_week_is_not_claimed_passed(self) -> None:
        """Mid race week there is nothing to replan, but the race has NOT
        passed — the athlete must not be told to create a new plan."""
        active, macro, _ = _committed_plan()
        repos, _ = _repos_with(active, macro)
        with pytest.raises(ValueError, match=r"Race week.*2026-11-08"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 11, 4),
            )

    @staticmethod
    def _maintenance_active(block_end: str) -> SimpleNamespace:
        solve = {
            "request": {
                "races": [
                    {
                        "goal_type": "triathlon_maintenance",
                        "race_date": block_end,
                        "priority": "A",
                    }
                ],
                "training_start": "2026-07-06",
                "ambition_level": "moderate",
            },
            "sessions": [],
        }
        return SimpleNamespace(
            id=1,
            solver_version=SOLVER_VERSION,
            solve_result_json=json.dumps(solve),
        )

    def test_maintenance_final_week_is_not_claimed_ended(self) -> None:
        """3 days before the block end the block is still active."""
        repos, _ = _repos_with(self._maintenance_active("2026-08-30"), None)
        with pytest.raises(ValueError, match="ends 2026-08-30"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 8, 27),
            )

    def test_maintenance_block_actually_ended(self) -> None:
        repos, _ = _repos_with(self._maintenance_active("2026-08-30"), None)
        with pytest.raises(ValueError, match="has ended; start a new one"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 8, 31),
            )


class TestReplanCarriesTheWeeklyHoursCap:
    """The stored request block is the only place the cap survives."""

    @staticmethod
    def _spy_caps(monkeypatch: pytest.MonkeyPatch) -> list[float | None]:
        caps: list[float | None] = []
        pipeline = generate_plan_module.generate_plan

        def _spy(request):
            caps.append(request.constraints.weekly_hours_cap)
            return pipeline(request)

        monkeypatch.setattr(generate_plan_module, "generate_plan", _spy)
        return caps

    def test_the_cap_replays_on_replan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        active, macro, _ = _committed_plan(cap=3.0)
        repos, plan_repo = _repos_with(active, macro)
        caps = self._spy_caps(monkeypatch)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert out["status"] == "ok"
        assert caps == [3.0]
        merged = json.loads(plan_repo.updated_solve_json)
        assert merged["request"]["weekly_hours_cap"] == 3.0

    def test_a_plan_stored_before_the_field_existed_replans_uncapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        active, macro, solve = _committed_plan()
        assert "weekly_hours_cap" not in solve["request"]
        repos, _ = _repos_with(active, macro)
        caps = self._spy_caps(monkeypatch)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert out["status"] == "ok"
        assert caps == [None]


class TestReplanQpFallback:
    def test_retries_fresh_when_truncated_request_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        active, macro, _ = _committed_plan()
        repos, plan_repo = _repos_with(active, macro)
        fresh_result = run_generate(
            _request(),
            repos=_base_repos(),
            progress=_progress,
            prior_training_weeks=0,
            training_start_override=date.fromisoformat(_CUTOFF),
        )
        assert fresh_result.status == "ok"

        priors: list[int] = []

        def _fake_generate(
            request,
            *,
            repos,
            progress,
            prior_training_weeks=0,
            training_start_override=None,
            enforce_athlete_data=True,
            enforce_offered=True,
        ):
            assert enforce_athlete_data is False, (
                "a legacy plan must never hard-fail its replan on onboarding"
            )
            assert enforce_offered is False, "a stored plan replans off the offer"
            priors.append(prior_training_weeks)
            if prior_training_weeks > 0:
                return GenerateError(error_message="macro QP infeasible")
            return fresh_result

        monkeypatch.setattr(mod, "run_generate", _fake_generate)
        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )
        assert priors == [_PRIOR, 0]
        assert out["status"] == "ok"
        # Fresh fallback restarts week numbering at the cutoff.
        assert min(r.week_index for r in plan_repo.inserted_rows) == 0

    def test_raises_when_both_attempts_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        active, macro, _ = _committed_plan()
        repos, _ = _repos_with(active, macro)
        monkeypatch.setattr(
            mod,
            "run_generate",
            lambda *a, **k: GenerateError(error_message="still infeasible"),
        )
        with pytest.raises(ValueError, match="still infeasible"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=_TODAY,
            )


class TestReplanShortHorizonPlan:
    """A plan that joined mid-preparation must resume at the same week."""

    RACE = "2026-05-03"  # 8 weeks out; a marathon prep wants 12
    DEFICIT = 4
    TODAY = date(2026, 3, 25)  # a Wednesday in week 2
    ELAPSED = 3  # weeks from the plan start to next Monday

    @staticmethod
    def _spy_priors(monkeypatch: pytest.MonkeyPatch, fail_first: bool = False):
        """Record the trained and joined weeks each pipeline run truncates."""
        priors: list[tuple[int, int]] = []
        pipeline = generate_plan_module.generate_plan

        def _spy(request):
            priors.append((request.prior_training_weeks, request.horizon_join_weeks))
            if fail_first and len(priors) == 1:
                raise QPInfeasibleError("Session-menu QP infeasible (status=...).")
            return pipeline(request)

        monkeypatch.setattr(generate_plan_module, "generate_plan", _spy)
        return priors

    def _replan(self, repos) -> dict:
        return run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=self.TODAY,
        )

    def test_prior_accumulates_the_elapsed_weeks_and_the_join_deficit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        active, macro, _ = _committed_plan(self.RACE, accept=True)
        repos, plan_repo = _repos_with(active, macro)
        priors = self._spy_priors(monkeypatch)

        out = self._replan(repos)

        assert out["status"] == "ok"
        assert priors == [(self.ELAPSED, self.DEFICIT)]
        # The merge is in the plan's own numbering, not the virtual one.
        assert min(r.week_index for r in plan_repo.inserted_rows) == self.ELAPSED
        merged = json.loads(plan_repo.updated_solve_json)
        assert len(merged["phase_calendar"]) == 8
        # The short-notice note belongs to the generate that offered the
        # join; the athlete is already in the plan, not deciding to join it.
        assert "Short-notice" not in out["plan_result"]["coach_brief"]

    def test_a_failed_short_horizon_replan_is_not_retried_fresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regenerating from the cutoff leaves 5 weeks — short of the 12 a
        fresh marathon prep needs, so "retry fresh" would hand the pipeline
        the very truncation that just failed."""
        active, macro, _ = _committed_plan(self.RACE, accept=True)
        repos, _ = _repos_with(active, macro)
        priors = self._spy_priors(monkeypatch, fail_first=True)

        with pytest.raises(ValueError, match="lower ambition level") as excinfo:
            self._replan(repos)

        assert priors == [(self.ELAPSED, self.DEFICIT)]
        assert "Short-notice" not in str(excinfo.value)

    def test_a_late_prep_replan_is_never_relabelled_short_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ordinary plan with 3 weeks left has no fresh plan to fall back
        to either — and must not be sold a mid-join it never asked for."""
        active, macro, _ = _committed_plan()  # the full 35-week block
        repos, plan_repo = _repos_with(active, macro)
        priors = self._spy_priors(monkeypatch, fail_first=True)

        with pytest.raises(ValueError, match="lower ambition level") as excinfo:
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 10, 14),  # week 31 of the prep
            )

        assert priors == [(32, 0)]
        assert "Short-notice" not in str(excinfo.value)
        assert plan_repo.updated_solve_json is None, "nothing was persisted"

    def test_a_replan_before_the_plan_starts_reproduces_the_join(self) -> None:
        """The one replan that may say "short-notice": nothing has elapsed,
        so it regenerates the very request the athlete accepted."""
        active, macro, _ = _committed_plan(self.RACE, accept=True)
        repos, _ = _repos_with(active, macro)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=date(2026, 3, 4),  # before the plan's own start
        )

        assert out["status"] == "ok"
        assert "Short-notice" in out["plan_result"]["coach_brief"]

    def test_a_replan_is_never_re_asked_to_accept_the_short_horizon(self) -> None:
        """The stored request carries no acceptance — the plan itself is it."""
        active, macro, solve = _committed_plan(self.RACE, accept=True)
        solve["request"]["accept_short_horizon"] = False
        active.solve_result_json = json.dumps(solve)
        repos, _ = _repos_with(active, macro)

        assert self._replan(repos)["status"] == "ok"


class _AdjustablePlanRepo(_StatefulPlanRepo):
    """Stateful repo whose rows carry the full prescription columns."""

    def __init__(self, active: SimpleNamespace, macro: SimpleNamespace | None):
        super().__init__(active, macro)
        solve = json.loads(active.solve_result_json)
        by_slot = {s["slot_id"]: s for s in solve["sessions"]}
        inserts = {
            row.slot_id: row for row in build_commit_session_rows(solve["sessions"])
        }
        for row in self.rows:
            session, insert = by_slot[row.slot_id], inserts[row.slot_id]
            row.plan_id = active.id
            row.phase_type = session["phase_type"]
            row.sport = session["sport"]
            row.activity_key = session["activity_key"]
            row.family = session["family"]
            row.target_tss = session["target_tss"]
            row.achieved_tss = session["achieved_tss"]
            row.duration_minutes = session["duration_minutes"]
            row.duration_target_minutes = session["duration_target_minutes"]
            row.workout_json = insert.workout_json
            row.tid_fractions_json = insert.tid_fractions_json
            row.status = "pending"
            row.pushed_event_json = None

    def get_sessions_by_date_range(
        self, plan_id: int, start_date: str, end_date: str
    ) -> list[SimpleNamespace]:
        return [
            row
            for row in self.get_sessions(plan_id)
            if start_date <= str(row.date) <= end_date
        ]

    def update_session_prescription(
        self,
        session_id: int,
        plan_id: int,
        *,
        target_tss: float,
        achieved_tss: float,
        duration_minutes: float,
        workout_json: str,
        tid_fractions_json: str | None,
    ) -> bool:
        row = next((r for r in self.rows if r.id == session_id), None)
        if row is None:  # pragma: no cover - defensive
            return False
        row.target_tss = target_tss
        row.achieved_tss = achieved_tss
        row.duration_minutes = duration_minutes
        row.workout_json = workout_json
        row.tid_fractions_json = tid_fractions_json
        return True


class TestAdjustAcrossReplanCutoff:
    """A trimmed current week must survive the next replan verbatim."""

    def _snapshot(self, plan_repo, ids: set[int]) -> dict:
        return {
            row.id: (row.date, row.duration_minutes, row.target_tss, row.workout_json)
            for row in plan_repo.rows
            if row.id in ids
        }

    def test_a_trimmed_current_week_survives_the_replan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The fixture plan is built without live thresholds; keep the rebuild
        # on the same footing so the resolver gate lets it through.
        monkeypatch.setattr(
            adjust_week_mod, "get_planner_load_context", lambda _repos: None
        )
        active, macro, _ = _committed_plan()
        repos = _base_repos()
        plan_repo = _AdjustablePlanRepo(active, macro)
        repos.plan = plan_repo

        adjusted = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert adjusted["status"] == "ok"
        trimmed = {
            entry["session_id"]: entry["after_minutes"]
            for entry in adjusted["sessions"]
        }
        assert trimmed
        before = self._snapshot(plan_repo, set(trimmed))

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        # delete_future_sessions starts at next Monday, so the trimmed rows
        # are never reached.
        assert plan_repo.deleted_from == _CUTOFF
        assert self._snapshot(plan_repo, set(trimmed)) == before

        merged = json.loads(plan_repo.updated_solve_json)
        by_slot = {s["slot_id"]: s for s in merged["sessions"]}
        for row in plan_repo.rows:
            if row.id in trimmed:
                assert by_slot[row.slot_id]["duration_minutes"] == trimmed[row.id]

        # The regenerated suffix is prescribed in full — the trim is this
        # week's, not the plan's.
        regenerated = [s for s in merged["sessions"] if s["slot_id"].startswith("r2:")]
        assert regenerated
        assert not any("Trimmed to" in s["coaching_intent"] for s in regenerated)


class TestCoachExtrasAcrossReplanCutoff:
    """Extras are plan rows: kept before the cutoff, regenerated away after."""

    @staticmethod
    def _extra(repos: MagicMock, day: str) -> str:
        scheduled = schedule_workout(
            repos=repos,
            duration_minutes=30,
            date_str=day,
            sport="running",
            intent="easy",
            client=None,
            today=_TODAY,
        )
        assert scheduled["status"] == "ok", scheduled["message"]
        return str(scheduled["plan_row"]["slot_id"])

    def test_this_weeks_extra_survives_and_a_later_one_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            schedule_workout_mod, "get_planner_load_context", lambda _repos: None
        )
        active, macro, _ = _committed_plan()
        repos = _base_repos()
        plan_repo = _AdjustablePlanRepo(active, macro)
        repos.plan = plan_repo
        kept = self._extra(repos, "2026-05-22")  # before the cutoff
        dropped = self._extra(repos, "2026-06-02")  # after it

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        rows = {row.slot_id for row in plan_repo.rows}
        payload = {
            session["slot_id"]
            for session in json.loads(plan_repo.updated_solve_json)["sessions"]
        }
        assert kept in rows
        assert kept in payload
        assert dropped not in rows
        assert dropped not in payload


@pytest.mark.usefixtures("offered_as_shipped")
class TestReplanAPlanNoLongerOnOffer:
    def test_a_stored_plan_replans_though_new_ones_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with monkeypatch.context() as offer:
            offer.setitem(
                OFFERED_TIERS, GoalType.RUNNING_MARATHON, frozenset(AmbitionLevel)
            )
            active, macro, _ = _committed_plan()
        refused = run_generate(_request(), repos=_base_repos(), progress=_progress)
        assert refused.error_code == "plan_not_offered"
        repos, _plan_repo = _repos_with(active, macro)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert out["status"] == "ok"
        assert out["action"] == "replanned"
