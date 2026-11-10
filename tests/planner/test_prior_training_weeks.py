"""Tests for prior_training_weeks (replanning) and the Monday-start guard.

The core contract: a plan with ``prior_training_weeks=k`` starting k weeks
into a prep must equal the suffix of the equivalent full-horizon plan —
``plan_phases(prior=k).weeks == plan_phases(prior=0).weeks[k:]`` after
re-indexing. The ``prior=0`` path must be byte-identical to the pre-replan
behavior (guarded globally by the score_quality matrix baseline).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from endury.planner import PlanRequest, generate_plan
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhaseRequest,
    RaceGoal,
)
from endury.planner.phase.phase_planner import (
    plan_phases,
    validate_phase_request,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES

_MONDAY = date(2026, 1, 5)  # a Monday


def _a_goal(
    goal_type: GoalType,
    weeks_from_start: int,
    start: date = _MONDAY,
) -> RaceGoal:
    return RaceGoal(
        goal_type=goal_type,
        race_date=start + timedelta(weeks=weeks_from_start),
        priority="A",
    )


def _tune_up(goal_type: GoalType, priority: str) -> RaceGoal:
    """A tune-up on the Sunday of week 11, the last week a week-12 replan trained."""
    return RaceGoal(
        goal_type=goal_type,
        race_date=_MONDAY + timedelta(weeks=12, days=-1),
        priority=priority,
    )


class TestSuffixProperty:
    """plan_phases(prior=k) == re-indexed suffix of the full-horizon plan."""

    @pytest.mark.parametrize(
        ("goal_type", "horizon"),
        [
            (GoalType.RUNNING_MARATHON, 24),
            (GoalType.IRONMAN, 32),
            (GoalType.RUNNING_5K, 16),
        ],
    )
    @pytest.mark.parametrize("prior", [1, 4, 9])
    def test_weeks_match_full_horizon_suffix(
        self, goal_type: GoalType, horizon: int, prior: int
    ) -> None:
        a = _a_goal(goal_type, horizon)
        full = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        truncated = plan_phases(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=prior),
                prior_training_weeks=prior,
            )
        )
        expected = [replace(w, week=w.week - prior) for w in full.weeks[prior:]]
        assert truncated.weeks == expected

    def test_the_trained_weeks_are_kept_as_negative_prior_weeks(self) -> None:
        """Two weeks trained: weeks -2 and -1 are the full plan's weeks 0 and 1."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        full = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        truncated = plan_phases(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=2),
                prior_training_weeks=2,
            )
        )
        assert [w.week for w in truncated.prior_weeks] == [-2, -1]
        assert truncated.prior_weeks == tuple(
            replace(w, week=w.week - 2) for w in full.weeks[:2]
        )
        assert truncated.weeks == [replace(w, week=w.week - 2) for w in full.weeks[2:]]

    def test_joined_weeks_are_dropped_and_only_trained_weeks_are_kept(self) -> None:
        """Three joined weeks before two trained ones: only the trained pair stays."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        full = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        truncated = plan_phases(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=5),
                prior_training_weeks=2,
                horizon_join_weeks=3,
            )
        )
        assert truncated.prior_weeks == tuple(
            replace(w, week=w.week - 5) for w in full.weeks[3:5]
        )
        assert truncated.weeks[0] == replace(full.weeks[5], week=0)

    def test_a_short_horizon_join_alone_keeps_no_prior_weeks(self) -> None:
        """Nothing trained: the joined weeks are dropped and none are kept."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        full = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        joined = plan_phases(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=3),
                horizon_join_weeks=3,
            )
        )
        assert joined.prior_weeks == ()
        assert joined.weeks == [replace(w, week=w.week - 3) for w in full.weeks[3:]]

    def test_race_events_reindexed(self) -> None:
        prior = 6
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b = RaceGoal(
            goal_type=GoalType.RUNNING_10K,
            race_date=_MONDAY + timedelta(weeks=12),
            priority="B",
        )
        full = plan_phases(PhaseRequest(goals=[a, b], training_start=_MONDAY))
        truncated = plan_phases(
            PhaseRequest(
                goals=[a, b],
                training_start=_MONDAY + timedelta(weeks=prior),
                prior_training_weeks=prior,
            )
        )
        expected = [replace(e, week=e.week - prior) for e in full.race_events]
        assert truncated.race_events == expected

    @pytest.mark.parametrize("prior", [12, 13, 15])
    def test_a_passed_tune_up_keeps_its_weeks_on_the_timeline(self, prior: int) -> None:
        """A B race behind the replan's start keeps its race and easier weeks."""
        a = _a_goal(GoalType.HALF_IRONMAN, 24)
        b = _tune_up(GoalType.RUNNING_MARATHON, "B")
        full = plan_phases(PhaseRequest(goals=[a, b], training_start=_MONDAY))
        truncated = plan_phases(
            PhaseRequest(
                goals=[a, b],
                training_start=_MONDAY + timedelta(weeks=prior),
                prior_training_weeks=prior,
            )
        )
        assert truncated.weeks == [
            replace(w, week=w.week - prior) for w in full.weeks[prior:]
        ]
        assert truncated.race_events == [
            replace(e, week=e.week - prior) for e in full.race_events if e.week >= prior
        ]
        assert truncated.prior_weeks[11 - prior].owning_goal == b

    def test_mid_prep_replan_resumes_in_build_not_base(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        full = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        # Replan from the first week the full plan has left BASE: the
        # truncated plan must resume there, not restart the base phase.
        prior = next(
            i for i, w in enumerate(full.weeks) if w.phase != full.weeks[0].phase
        )
        truncated = plan_phases(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=prior),
                prior_training_weeks=prior,
            )
        )
        assert truncated.weeks[0].phase == full.weeks[prior].phase
        assert truncated.weeks[0].phase != full.weeks[0].phase

    def test_prior_zero_is_default_path(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        implicit = plan_phases(PhaseRequest(goals=[a], training_start=_MONDAY))
        explicit = plan_phases(
            PhaseRequest(goals=[a], training_start=_MONDAY, prior_training_weeks=0)
        )
        assert implicit.weeks == explicit.weeks
        assert implicit.race_events == explicit.race_events


class TestValidation:
    def test_negative_prior_rejected(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        result = validate_phase_request(
            PhaseRequest(goals=[a], training_start=_MONDAY, prior_training_weeks=-1)
        )
        assert not result.valid
        assert "prior_training_weeks" in result.errors[0]

    def test_min_horizon_checked_against_virtual_horizon(self) -> None:
        """A short remaining horizon is valid when prior weeks fill the gap."""
        min_weeks = GOAL_PROFILES[GoalType.RUNNING_MARATHON].min_horizon_weeks
        remaining = 6
        assert remaining < min_weeks  # the fresh-plan path must reject this
        a = _a_goal(GoalType.RUNNING_MARATHON, remaining)
        fresh = validate_phase_request(PhaseRequest(goals=[a], training_start=_MONDAY))
        assert not fresh.valid

        replan = validate_phase_request(
            PhaseRequest(
                goals=[a],
                training_start=_MONDAY,
                prior_training_weeks=min_weeks - remaining,
            )
        )
        assert replan.valid, replan.errors


class TestGeneratePlan:
    def test_prior_threads_through_pipeline(self) -> None:
        prior = 8
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        full = generate_plan(PlanRequest(goals=[a], training_start=_MONDAY))
        replanned = generate_plan(
            PlanRequest(
                goals=[a],
                training_start=_MONDAY + timedelta(weeks=prior),
                prior_training_weeks=prior,
            )
        )
        assert len(replanned.phase_plan.weeks) == len(full.phase_plan.weeks) - prior
        assert replanned.sessions, "replanned pipeline produced no sessions"
        # Assignments live in the actual (post-replan) calendar window.
        first = min(x.date for x in replanned.assignments)
        assert first >= _MONDAY + timedelta(weeks=prior)

    @pytest.mark.parametrize(
        ("goal_type", "priority"),
        [(GoalType.RUNNING_MARATHON, "B"), (GoalType.RUNNING_10K, "C")],
    )
    def test_the_week_after_a_passed_tune_up_keeps_its_sessions(
        self, goal_type: GoalType, priority: str
    ) -> None:
        """The easier week, and no hard session in the days after the race."""
        a = _a_goal(GoalType.HALF_IRONMAN, 24)
        goals = [a, _tune_up(goal_type, priority)]
        moderate = AmbitionLevel.MODERATE  # the tier on offer
        full = generate_plan(
            PlanRequest(goals=goals, training_start=_MONDAY, ambition=moderate)
        )
        replanned = generate_plan(
            PlanRequest(
                goals=goals,
                training_start=_MONDAY + timedelta(weeks=12),
                ambition=moderate,
                prior_training_weeks=12,
            )
        )

        def week(result: object, index: int) -> list[str]:
            return sorted(s.activity_key for s in result.sessions if s.week == index)

        assert week(replanned, 0) == week(full, 12)

    def test_non_monday_start_rejected(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        with pytest.raises(ValueError, match="Monday"):
            generate_plan(
                PlanRequest(goals=[a], training_start=_MONDAY + timedelta(days=1))
            )
