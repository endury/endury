"""The offer: which plans a new request may build, and what the guard refuses."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from endury.database.sqlite.repositories.self_assessment import SelfAssessmentRow
from endury.intervals.models import AthleteProfile, IntervalsActivity
from endury.planner.foundations.types import AmbitionLevel, GoalType
from endury.planning.generate_plan import run_generate
from endury.planning.get_active_plan import build_bootstrap_payload
from endury.planning.offered_plans import (
    OFFERED_TIERS,
    is_offered,
    not_offered_message,
    offered_names,
)
from endury.planning.wire.requests import GenerateRequest, RaceGoalModel

_START = date(2026, 1, 5)


class _FakeIntervalsClient:
    """Offline stand-in for the ``IntervalsSource`` protocol."""

    def get_athlete(self) -> AthleteProfile:
        return AthleteProfile(sport_settings=[])

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        return []


def _repos() -> MagicMock:
    repos = MagicMock()
    repos.intervals_client = _FakeIntervalsClient()
    repos.self_assessment.get_all.return_value = {
        "running": SelfAssessmentRow(8.0, 60, "2026-01-01"),
        "cycling": SelfAssessmentRow(10.0, 90, "2026-01-01"),
        "swimming": SelfAssessmentRow(3.0, 30, "2026-01-01"),
    }
    return repos


def _generate(goal: str, tier: str, weeks: int, *tune_ups: RaceGoalModel):
    race = (_START + timedelta(weeks=weeks, days=-1)).isoformat()
    request = GenerateRequest(
        races=[RaceGoalModel(goal_type=goal, race_date=race), *tune_ups],
        training_start=_START.isoformat(),
        ambition_level=tier,
        accept_experimental=True,
    )
    return run_generate(request, repos=_repos(), progress=lambda _event: None)


@pytest.mark.usefixtures("offered_as_shipped")
class TestTheOfferAsShipped:
    def test_the_menus_offer_the_running_trail_and_triathlon_plans(self) -> None:
        offered = {goal: tiers for goal, tiers in OFFERED_TIERS.items() if tiers}

        assert offered == {
            GoalType.RUNNING_HALF_MARATHON: {AmbitionLevel.MODERATE},
            GoalType.TRAIL_20K: {AmbitionLevel.BASIC},
            GoalType.HALF_IRONMAN: {AmbitionLevel.MODERATE},
            GoalType.IRONMAN: {AmbitionLevel.MODERATE},
            GoalType.TRAIL_MAINTENANCE: {AmbitionLevel.BASIC},
            GoalType.TRIATHLON_MAINTENANCE: {AmbitionLevel.MODERATE},
        }
        assert offered_names() == (
            "half marathon (moderate), Trail 20K (basic), half-distance triathlon "
            "(moderate), full-distance triathlon (moderate), trail maintenance "
            "(basic), triathlon maintenance (moderate)"
        )

    def test_a_goal_that_is_not_on_offer_is_refused(self) -> None:
        result = _generate("running_marathon", "moderate", 16)

        assert result.status == "error"
        assert result.error_code == "plan_not_offered"
        assert result.error_message == not_offered_message(
            GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE
        )
        assert result.error_message == (
            "Endury is not offering a moderate marathon plan at the moment. "
            "On offer now: half marathon (moderate), Trail 20K (basic), "
            "half-distance triathlon (moderate), full-distance triathlon (moderate), "
            "trail maintenance (basic), triathlon maintenance (moderate)."
        )

    def test_another_tier_of_an_offered_goal_is_refused(self) -> None:
        assert not is_offered(GoalType.TRAIL_20K, AmbitionLevel.MODERATE)

        assert _generate("trail_20k", "moderate", 16).error_code == "plan_not_offered"

    @pytest.mark.parametrize(
        ("goal", "tier", "weeks"),
        [
            ("running_half_marathon", "moderate", 12),
            ("trail_20k", "basic", 16),
            ("trail_maintenance", "basic", 8),
            ("half_ironman", "moderate", 20),
            ("ironman", "moderate", 24),
            ("triathlon_maintenance", "moderate", 12),
        ],
    )
    def test_an_offered_plan_builds(self, goal: str, tier: str, weeks: int) -> None:
        assert _generate(goal, tier, weeks).status == "ok"

    def test_a_tune_up_race_may_be_of_any_type(self) -> None:
        road_10k = RaceGoalModel(
            goal_type="running_10k",
            race_date=(_START + timedelta(weeks=9, days=-1)).isoformat(),
            priority="B",
        )

        assert _generate("trail_20k", "basic", 16, road_10k).status == "ok"

    def test_the_wizard_is_served_the_tiers_on_offer(self) -> None:
        rows = build_bootstrap_payload(repos=_repos())["goal_types"]

        assert {
            row["id"]: row["offered_tiers"] for row in rows if row["offered_tiers"]
        } == {
            "running_half_marathon": ["moderate"],
            "trail_20k": ["basic"],
            "half_ironman": ["moderate"],
            "ironman": ["moderate"],
            "trail_maintenance": ["basic"],
            "triathlon_maintenance": ["moderate"],
        }


def test_the_suite_runs_with_every_plan_on_offer() -> None:
    assert all(tiers == frozenset(AmbitionLevel) for tiers in OFFERED_TIERS.values())
