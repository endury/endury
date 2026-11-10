"""Tests for maintenance-goal behavior across the use-case layer.

Covers the deterministic coach brief, session materialization, and the
replan-after-block-end rejection for raceless maintenance plans.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.database.sqlite.repositories.self_assessment import SelfAssessmentRow
from endury.intervals.models import AthleteProfile
from endury.planner import PlanRequest, generate_plan
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    RaceGoal,
)
from endury.planner.phase.phase_planner import plan_phases
from endury.planning.coach_brief import build_coach_brief
from endury.planning.commit_plan import build_solve_result_payload
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    run_generate,
)
from endury.planning.materialize_plan import materialize_sessions
from endury.planning.replan_plan import run_replan
from endury.planning.wire.requests import GenerateRequest, RaceGoalModel

_MONDAY = date(2026, 1, 5)


def _progress(*_args: object, **_kwargs: object) -> None:
    pass


def _maintenance_plan() -> PhasePlan:
    goal = RaceGoal(
        goal_type=GoalType.RUNNING_MAINTENANCE,
        race_date=_MONDAY + timedelta(weeks=8, days=-1),
        priority="A",
    )
    return plan_phases(PhaseRequest(goals=[goal], training_start=_MONDAY))


def _race_plan() -> PhasePlan:
    goal = RaceGoal(
        goal_type=GoalType.RUNNING_MARATHON,
        race_date=_MONDAY + timedelta(weeks=16),
        priority="A",
    )
    return plan_phases(PhaseRequest(goals=[goal], training_start=_MONDAY))


class TestMaintenanceCoachBrief:
    def test_maintenance_brief_mentions_holding_fitness_not_racing(self) -> None:
        plan = _maintenance_plan()
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.MODERATE)
        assert "holding fitness through" in brief
        assert "racing " not in brief

    def test_race_plan_brief_still_says_racing(self) -> None:
        plan = _race_plan()
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.MODERATE)
        assert "racing " in brief


def _maintenance_result():
    goal = RaceGoal(
        goal_type=GoalType.RUNNING_MAINTENANCE,
        race_date=_MONDAY + timedelta(weeks=8, days=-1),
        priority="A",
    )
    request = PlanRequest(
        goals=[goal], training_start=_MONDAY, ambition=AmbitionLevel.MODERATE
    )
    return generate_plan(request)


class TestMaintenanceMaterialize:
    def test_all_sessions_are_maintain_phase_without_phase_boilerplate(self) -> None:
        result = _maintenance_result()
        payloads = materialize_sessions(result, None)
        assert payloads
        assert all(p["phase_type"] == "maintain" for p in payloads)
        assert all("Maintenance block" not in p["coaching_intent"] for p in payloads)


def _maintenance_committed_active_plan() -> SimpleNamespace:
    """Generate + shape a running_maintenance plan as a persisted active row."""
    request = GenerateRequest(
        races=[RaceGoalModel(goal_type="running_maintenance", race_date="2026-02-01")],
        training_start="2026-01-05",
        ambition_level="moderate",
        accept_experimental=True,  # the maintenance menus are not reviewed yet
    )
    repos = MagicMock()
    repos.intervals_client.get_athlete.return_value = AthleteProfile(sport_settings=[])
    repos.intervals_client.list_activities.return_value = []
    # Reproduces the goal-default running SportState exactly.
    repos.self_assessment.get_all.return_value = {
        "running": SelfAssessmentRow(8.0, 60, "2026-01-01")
    }
    result = run_generate(request, repos=repos, progress=_progress)
    assert result.status == "ok"

    solve = build_solve_result_payload(result)
    solve["request"] = {
        "races": [
            {
                "goal_type": "running_maintenance",
                "race_date": "2026-02-01",
                "priority": "A",
            }
        ],
        "training_start": "2026-01-05",
        "ambition_level": "moderate",
    }
    return SimpleNamespace(
        solver_version=SOLVER_VERSION,
        solve_result_json=json.dumps(solve),
    )


class TestMaintenanceReplanBlockEnded:
    def test_replan_after_block_end_raises(self) -> None:
        active = _maintenance_committed_active_plan()
        repos = MagicMock()
        repos.plan.get_active.return_value = active

        with pytest.raises(ValueError, match="maintenance block has ended"):
            run_replan(
                repos=repos,
                progress=_progress,
                maybe_get_coach=lambda: None,
                today=date(2026, 2, 2),
            )
