"""Unit tests for run_generate edge branches not reachable via routes."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from endury.database.sqlite.repositories.self_assessment import SelfAssessmentRow
from endury.intervals.models import AthleteProfile, IntervalsActivity
from endury.planner.foundations.request import AthleteState, PlanRequest, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseType,
    PlanMaturity,
    RaceGoal,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.macro.macro_planner import QPInfeasibleError
from endury.planner.phase.phase_config import MAX_HORIZON_WEEKS
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import PlanResult, default_profile_for, generate_plan
from endury.planning import generate_plan as generate_plan_module
from endury.planning import materialize_plan as materialize_plan_module
from endury.planning.athlete_state import ResolvedAthleteState
from endury.planning.generate_plan import (
    KEY_SESSION_WARNING,
    RACE_REHEARSAL_WARNING,
    REHEARSAL_SKIPPED_WARNING,
    REHEARSAL_SKIPPED_WARNING_MULTISPORT,
    REHEARSAL_TOO_SOON_WARNING,
    build_request_payload,
    collect_plan_warnings,
    key_session_warning,
    race_rehearsal_warning,
    rehearsal_skipped_warning,
    run_generate,
)
from endury.planning.plan_maturity import TIER_NOTES
from endury.planning.wire.requests import GenerateRequest, RaceGoalModel

# The unconfirmed builds below stand for goals whose menus are reviewed.
pytestmark = pytest.mark.usefixtures("alpha_goals")

#: Self-reported volumes chosen to reproduce the goal-default SportStates
#: exactly, so these tests keep exercising the plans they always did while
#: satisfying the enforced-onboarding gate.
SELF_REPORT = {
    "running": SelfAssessmentRow(8.0, 60, "2026-01-01"),
    "cycling": SelfAssessmentRow(10.0, 90, "2026-01-01"),
    "swimming": SelfAssessmentRow(3.0, 30, "2026-01-01"),
}


class _FakeIntervalsClient:
    """Offline stand-in for the ``IntervalsSource`` protocol."""

    def __init__(self, activities: list[IntervalsActivity] | None = None) -> None:
        self._activities = activities or []
        self._profile = AthleteProfile(sport_settings=[])

    def get_athlete(self) -> AthleteProfile:
        return self._profile

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        return self._activities


def _repos() -> MagicMock:
    repos = MagicMock()
    repos.intervals_client = _FakeIntervalsClient()
    repos.self_assessment.get_all.return_value = dict(SELF_REPORT)
    return repos


def _progress(event: object, message: object = None) -> None:
    _ = event, message


def _patch_resolved(monkeypatch, athlete: AthleteState) -> None:
    """Force run_generate to see a fully history-measured athlete state."""
    resolved = ResolvedAthleteState(
        athlete=athlete,
        sources=dict.fromkeys(athlete.sports, "history"),
    )
    monkeypatch.setattr(
        generate_plan_module,
        "resolve_athlete_state",
        lambda *_args, **_kwargs: resolved,
    )


class TestInvalidRequests:
    def test_unknown_goal_type_is_reported(self) -> None:
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_1500m", race_date="2026-10-04")],
            training_start="2026-03-09",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert "running_1500m" in result.error_message

    def test_malformed_date_is_reported(self) -> None:
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_10k", race_date="2026-10-04")],
            training_start="not-a-date",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"

    def test_empty_races_rejected(self) -> None:
        request = GenerateRequest(races=[], training_start="2026-03-09")
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert "At least one race" in result.error_message


class TestDerivedAthleteState:
    def test_history_marks_state_as_derived(self, monkeypatch) -> None:
        from datetime import date as _date
        from datetime import timedelta

        import endury.planning.athlete_state as athlete_state_mod

        # Pin the resolver's clock to the fixture's era so the activities
        # land inside the median window (the evidence bar reads the last
        # six complete ISO weeks, not bare bucket truthiness).
        monkeypatch.setattr(athlete_state_mod.clock, "today", lambda: _date(2026, 3, 8))

        def _act(days_ago: int) -> IntervalsActivity:
            day = _date(2026, 3, 8) - timedelta(days=days_ago)
            return IntervalsActivity(
                id=str(days_ago),
                start_date_local=day.isoformat(),
                type="Run",
                moving_time=3600,
                icu_training_load=55.0,
                distance=10_000.0,
            )

        repos = _repos()
        repos.intervals_client = _FakeIntervalsClient([_act(i) for i in range(30)])
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_marathon", race_date="2026-10-04")],
            training_start="2026-03-09",
            accept_experimental=True,  # 30 weeks: beyond the checked horizons
        )
        result = run_generate(request, repos=repos, progress=_progress)
        assert result.status == "ok"
        assert result.athlete_state["derived_from_history"] is True
        assert "running" in result.athlete_state["sports"]
        # The brief names what the plan was actually built from.
        assert "Built at" in result.coach_brief


class TestMidWeekStart:
    """A plan starts this week's Monday; the elapsed days carry no sessions."""

    @staticmethod
    def _generate(monkeypatch, today: date) -> object:
        monkeypatch.setattr(materialize_plan_module.clock, "today", lambda: today)
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_marathon", race_date="2026-10-04")],
            training_start=(today - timedelta(days=today.weekday())).isoformat(),
            accept_experimental=True,  # 30 weeks: beyond the checked horizons
        )
        return run_generate(request, repos=_repos(), progress=_progress)

    def test_days_already_elapsed_are_not_planned(self, monkeypatch) -> None:
        thursday = date(2026, 3, 12)
        result = self._generate(monkeypatch, thursday)
        assert result.status == "ok"
        assert result.sessions
        assert min(s["date"] for s in result.sessions) >= thursday.isoformat()

    def test_a_monday_start_keeps_the_whole_first_week(self, monkeypatch) -> None:
        whole = self._generate(monkeypatch, date(2026, 3, 9))
        trimmed = self._generate(monkeypatch, date(2026, 3, 12))
        assert whole.status == "ok"
        # Planning on the Monday itself trims nothing, and the Thursday
        # start loses only the days before it — the rest is identical.
        assert min(s["date"] for s in whole.sessions) < "2026-03-12"
        assert [s["slot_id"] for s in whole.sessions if s["date"] >= "2026-03-12"] == [
            s["slot_id"] for s in trimmed.sessions
        ]

    def test_a_deliberately_historical_plan_is_left_whole(self, monkeypatch) -> None:
        """Replan replays and reports still get every session they asked for."""
        whole = self._generate(monkeypatch, date(2026, 3, 9))
        monkeypatch.setattr(
            materialize_plan_module.clock, "today", lambda: date(2026, 8, 11)
        )
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_marathon", race_date="2026-10-04")],
            training_start="2026-03-09",
            accept_experimental=True,
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert [s["slot_id"] for s in result.sessions] == [
            s["slot_id"] for s in whole.sessions
        ]


class TestQPInfeasibleMapping:
    def test_macro_qp_infeasibility_gets_a_friendly_message(self, monkeypatch) -> None:
        """A QPInfeasibleError from the pipeline is never shown raw."""

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise QPInfeasibleError(
                "Session-menu QP infeasible (status='infeasible'). Check "
                "capacity ceilings, session bounds, and freshness targets."
            )

        monkeypatch.setattr(generate_plan_module, "generate_plan", _raise)
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_marathon", race_date="2026-10-04")],
            training_start="2026-03-09",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert result.error_message == generate_plan_module.QP_INFEASIBLE_MESSAGE
        assert "Session-menu QP infeasible" not in result.error_message


class TestValidationMessageIsUserFacing:
    def test_horizon_rejection_drops_the_internal_request_class_name(self) -> None:
        """The wizard shows this verbatim — "Invalid PhaseRequest" is our jargon."""
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="ironman", race_date="2027-11-07")],
            training_start="2026-03-09",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert "PhaseRequest" not in result.error_message
        assert result.error_message.startswith("This plan would run")
        assert f"at most {MAX_HORIZON_WEEKS}" in result.error_message

    def test_two_tune_up_refusals_read_as_sentences(self) -> None:
        """The race week the athlete counts, what fits, and nothing of ours."""
        request = GenerateRequest(
            races=[
                RaceGoalModel(goal_type="running_marathon", race_date="2026-07-26"),
                RaceGoalModel(
                    goal_type="running_10k", race_date="2026-07-12", priority="B"
                ),
                RaceGoalModel(
                    goal_type="running_5k", race_date="2026-03-15", priority="C"
                ),
            ],
            training_start="2026-03-09",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert (
            "Your tune-up race on Jul 12, 2026 falls in week 18" in result.error_message
        )
        assert (
            "Your tune-up race on Mar 15, 2026 falls in week 1" in result.error_message
        )
        assert "; " not in result.error_message
        assert "B-goal" not in result.error_message


class TestRaceRehearsalWarning:
    """WARN, don't refuse, when capacity can't reach race-rehearsal volume."""

    def test_below_threshold_warns(self) -> None:
        summary = [{"total_duration_minutes": 11.0 * 60.0}]
        assert (
            race_rehearsal_warning(GoalType.IRONMAN, summary, plan_weeks=20)
            == RACE_REHEARSAL_WARNING
        )

    def test_at_threshold_does_not_warn(self) -> None:
        summary = [{"total_duration_minutes": 12.0 * 60.0}]
        assert race_rehearsal_warning(GoalType.IRONMAN, summary, plan_weeks=20) is None

    def test_half_ironman_below_threshold_warns(self) -> None:
        summary = [{"total_duration_minutes": 8.5 * 60.0}]
        assert (
            race_rehearsal_warning(GoalType.HALF_IRONMAN, summary, plan_weeks=20)
            == RACE_REHEARSAL_WARNING
        )

    def test_half_ironman_at_threshold_does_not_warn(self) -> None:
        summary = [{"total_duration_minutes": 9.0 * 60.0}]
        assert (
            race_rehearsal_warning(GoalType.HALF_IRONMAN, summary, plan_weeks=20)
            is None
        )

    def test_short_course_goal_never_warns(self) -> None:
        summary = [{"total_duration_minutes": 1.0 * 60.0}]
        assert (
            race_rehearsal_warning(GoalType.RUNNING_5K, summary, plan_weeks=12) is None
        )
        assert (
            key_session_warning(
                GoalType.RUNNING_5K, AmbitionLevel.BASIC, summary, 12, recent_hours=1.0
            )
            is None
        )

    def test_at_max_horizon_recommends_trail_maintenance_first(self) -> None:
        """trail_20k at its 20-wk max: 'longer horizon' is not actionable."""
        summary = [{"total_duration_minutes": 1.7 * 60.0}]
        warning = race_rehearsal_warning(GoalType.TRAIL_20K, summary, plan_weeks=20)
        assert warning is not None
        assert "trail maintenance" in warning
        assert "longer horizon can't close the gap" in warning

    def test_at_max_horizon_recommends_triathlon_maintenance_first(self) -> None:
        summary = [{"total_duration_minutes": 5.0 * 60.0}]
        warning = race_rehearsal_warning(GoalType.IRONMAN, summary, plan_weeks=36)
        assert warning is not None
        assert "triathlon maintenance" in warning

    def test_below_max_horizon_keeps_the_longer_horizon_advice(self) -> None:
        summary = [{"total_duration_minutes": 1.7 * 60.0}]
        assert (
            race_rehearsal_warning(GoalType.TRAIL_20K, summary, plan_weeks=12)
            == RACE_REHEARSAL_WARNING
        )


class TestKeySessionWarning:
    """Road goals warn when capacity can't hold the peak week's key sessions."""

    @pytest.mark.parametrize(
        ("goal", "ambition", "threshold"),
        [
            (GoalType.RUNNING_10K, AmbitionLevel.BASIC, 2.6),
            (GoalType.RUNNING_10K, AmbitionLevel.MODERATE, 2.9),
            (GoalType.RUNNING_10K, AmbitionLevel.COMPETITIVE, 3.2),
            (GoalType.RUNNING_HALF_MARATHON, AmbitionLevel.BASIC, 3.2),
            (GoalType.RUNNING_HALF_MARATHON, AmbitionLevel.MODERATE, 3.8),
            (GoalType.RUNNING_HALF_MARATHON, AmbitionLevel.COMPETITIVE, 4.2),
            (GoalType.RUNNING_MARATHON, AmbitionLevel.BASIC, 4.1),
            (GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE, 5.9),
            (GoalType.RUNNING_MARATHON, AmbitionLevel.COMPETITIVE, 6.9),
        ],
    )
    def test_warns_below_the_tier_threshold_only(
        self, goal: GoalType, ambition: AmbitionLevel, threshold: float
    ) -> None:
        below = [{"total_duration_minutes": (threshold - 0.1) * 60.0}]
        at = [{"total_duration_minutes": threshold * 60.0}]
        warn = key_session_warning(goal, ambition, below, 12, recent_hours=1.0)
        assert warn == KEY_SESSION_WARNING
        assert key_session_warning(goal, ambition, at, 12, recent_hours=1.0) is None

    def test_a_peak_short_only_on_horizon_stays_silent(self) -> None:
        """The warning blames recent volume; a short replan's peak is not that."""
        summary = [{"total_duration_minutes": 5.8 * 60.0}]
        assert (
            key_session_warning(
                GoalType.RUNNING_MARATHON,
                AmbitionLevel.MODERATE,
                summary,
                8,
                recent_hours=5.9,
            )
            is None
        )

    def test_at_max_horizon_recommends_running_maintenance_first(self) -> None:
        """HM at its 24-wk max: 'longer horizon' is not actionable."""
        summary = [{"total_duration_minutes": 2.0 * 60.0}]
        warning = key_session_warning(
            GoalType.RUNNING_HALF_MARATHON,
            AmbitionLevel.BASIC,
            summary,
            24,
            recent_hours=1.0,
        )
        assert warning is not None
        assert "running maintenance" in warning
        assert "longer horizon can't close the gap" in warning

    def test_long_course_goals_keep_the_rehearsal_warning_only(self) -> None:
        summary = [{"total_duration_minutes": 1.0 * 60.0}]
        assert (
            key_session_warning(
                GoalType.IRONMAN, AmbitionLevel.BASIC, summary, 20, recent_hours=1.0
            )
            is None
        )

    def test_low_volume_half_marathon_request_warns_and_generates(
        self, monkeypatch
    ) -> None:
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={SportType.RUNNING: SportState(recent_weekly_hours=1.5)}
            ),
        )
        request = GenerateRequest(
            races=[
                RaceGoalModel(goal_type="running_half_marathon", race_date="2026-05-31")
            ],
            training_start="2026-03-09",
            ambition_level="basic",
            accept_experimental=True,  # 1.5 h: below the checked volume
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert result.warning == KEY_SESSION_WARNING
        assert KEY_SESSION_WARNING in result.coach_brief

    def test_sufficient_volume_half_marathon_request_has_no_warning(
        self, monkeypatch
    ) -> None:
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={SportType.RUNNING: SportState(recent_weekly_hours=4.0)}
            ),
        )
        request = GenerateRequest(
            races=[
                RaceGoalModel(goal_type="running_half_marathon", race_date="2026-05-31")
            ],
            training_start="2026-03-09",
            ambition_level="basic",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert result.warning is None

    def test_low_volume_ironman_request_warns_and_generates(self, monkeypatch) -> None:
        """A low-capacity Ironman request still generates (WARN, don't refuse)."""
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={
                    SportType.RUNNING: SportState(recent_weekly_hours=1.83),
                    SportType.CYCLING: SportState(recent_weekly_hours=2.56),
                    SportType.SWIMMING: SportState(recent_weekly_hours=1.1),
                }
            ),
        )
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="ironman", race_date="2026-08-09")],
            training_start="2026-03-09",
            ambition_level="basic",
            accept_experimental=True,  # 5.5 h: below the checked volume
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        # Its big weekend is more than a quarter past the long ride before it,
        # so the week stays routine and the plan says so (R-SESS-023).
        assert result.warning == REHEARSAL_SKIPPED_WARNING_MULTISPORT
        assert REHEARSAL_SKIPPED_WARNING_MULTISPORT in result.coach_brief

    def test_long_horizon_5k_warns_and_generates(self) -> None:
        """R-PHASE-017: the planner's soft warning reaches the wire payload."""
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="running_5k", race_date="2026-12-06")],
            training_start="2026-03-09",
            accept_experimental=True,
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert "maintenance block" in result.warning
        assert "maintenance block" in result.coach_brief

    def test_sufficient_volume_ironman_request_has_no_warning(
        self, monkeypatch
    ) -> None:
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={
                    SportType.RUNNING: SportState(recent_weekly_hours=2.5),
                    SportType.CYCLING: SportState(recent_weekly_hours=3.5),
                    SportType.SWIMMING: SportState(recent_weekly_hours=1.5),
                }
            ),
        )
        request = GenerateRequest(
            races=[RaceGoalModel(goal_type="ironman", race_date="2026-08-09")],
            training_start="2026-03-09",
            ambition_level="basic",
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert result.warning is None
        assert RACE_REHEARSAL_WARNING not in result.coach_brief


class TestRehearsalSkippedWarning:
    """R-SESS-023: report a race-sim fallback rather than guess from hours."""

    #: The real athlete this warning was written for: a competitive trail
    #: 50K, joining a full 12-week preparation at week 8, sized from 3.53
    #: h/week of recent running (tests/planner/test_menu_contract_survives_
    #: sizing.py's fixture, the case the race-sim gate (R-SESS-033) was
    #: measured against).
    @staticmethod
    def _trail_50k_athlete(recent_hours: float = 3.53):
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=recent_hours,
                    longest_recent_min={ActivityFamily.LONG: 230},
                    threshold_pace_sec_per_km=250,
                )
            }
        )
        return generate_plan(
            PlanRequest(
                goals=[RaceGoal(GoalType.TRAIL_50K, date(2026, 10, 24), "A")],
                training_start=date(2026, 9, 21),
                ambition=AmbitionLevel.COMPETITIVE,
                athlete=athlete,
                prior_training_weeks=7,
            )
        )

    def test_the_athletes_plan_is_warned(self) -> None:
        result = self._trail_50k_athlete()
        warning = rehearsal_skipped_warning(
            result, GoalType.TRAIL_50K, AmbitionLevel.COMPETITIVE
        )
        assert warning == REHEARSAL_SKIPPED_WARNING

    def test_a_long_run_still_growing_is_named_as_the_cause(self) -> None:
        """The default competitive 50K at 12 weeks has the hours, not the long run.

        Its long run grows from 60 min by about 10 % a week and is near
        two hours at the peak: the four-hour rehearsal waits, and the
        athlete is told why, without blaming a weekly volume that is fine.
        """
        goal, ambition = GoalType.TRAIL_50K, AmbitionLevel.COMPETITIVE
        start = date(2026, 9, 7)
        result = generate_plan(
            PlanRequest(
                goals=[RaceGoal(goal, start + timedelta(weeks=12, days=-1), "A")],
                training_start=start,
                ambition=ambition,
                athlete=default_profile_for(goal, ambition),
            )
        )
        assert not any(s.slot_name == "t50_peak_sun_race_sim" for s in result.sessions)
        warning = rehearsal_skipped_warning(result, goal, ambition)
        assert warning == REHEARSAL_TOO_SOON_WARNING
        assert "volume" not in warning

    @pytest.mark.parametrize(
        ("ambition", "warning"),
        [
            (AmbitionLevel.BASIC, None),
            (AmbitionLevel.MODERATE, REHEARSAL_TOO_SOON_WARNING),
            (AmbitionLevel.COMPETITIVE, REHEARSAL_TOO_SOON_WARNING),
        ],
    )
    def test_default_profile_trail_50k_at_16_weeks(
        self, ambition: AmbitionLevel, warning: str | None
    ) -> None:
        """A rehearsal waits until the long run is within a quarter of it.

        From 60 min the long run reaches about 150 min (moderate,
        competitive) by the rehearsal week: the 240 and 250-min rehearsals
        are more than a quarter past it, so each plan keeps its routine long
        run and says why (R-SESS-023; none jumps 135 -> 180 or to
        240). Basic's 180-min rehearsal fits: at 3:1 with
        no peak shed its long run reaches within a quarter of it.
        """
        start = date(2026, 9, 7)
        result = generate_plan(
            PlanRequest(
                goals=[
                    RaceGoal(
                        GoalType.TRAIL_50K, start + timedelta(weeks=16, days=-1), "A"
                    )
                ],
                training_start=start,
                ambition=ambition,
                athlete=default_profile_for(GoalType.TRAIL_50K, ambition),
            )
        )
        assert rehearsal_skipped_warning(result, GoalType.TRAIL_50K, ambition) == (
            warning
        )

    def test_a_goal_whose_menu_authors_no_rehearsal_never_warns(self) -> None:
        goal, ambition = GoalType.RUNNING_MARATHON, AmbitionLevel.COMPETITIVE
        start = date(2026, 9, 7)
        result = generate_plan(
            PlanRequest(
                goals=[RaceGoal(goal, start + timedelta(weeks=18, days=-1), "A")],
                training_start=start,
                ambition=ambition,
                athlete=default_profile_for(goal, ambition),
            )
        )
        assert rehearsal_skipped_warning(result, goal, ambition) is None

    def test_does_not_double_up_with_the_hours_based_warning(self) -> None:
        """Both mechanisms can fire on the same low-volume plan; only one shows."""
        goal, ambition = GoalType.TRAIL_20K, AmbitionLevel.COMPETITIVE
        start = date(2026, 9, 7)
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=1.5,
                    longest_recent_min={ActivityFamily.LONG: 30},
                    threshold_pace_sec_per_km=250,
                )
            }
        )
        horizon = GOAL_PROFILES[goal].min_horizon_weeks
        result = generate_plan(
            PlanRequest(
                goals=[RaceGoal(goal, start + timedelta(weeks=horizon, days=-1), "A")],
                training_start=start,
                ambition=ambition,
                athlete=athlete,
            )
        )
        # Below TRAIL_20K's 4.4 h hours-based threshold, so on its own
        # race_rehearsal_warning would also fire for this plan.
        weekly_summary = [{"total_duration_minutes": 3.0 * 60.0}]
        assert (
            rehearsal_skipped_warning(result, goal, ambition)
            == REHEARSAL_SKIPPED_WARNING
        )
        assert (
            race_rehearsal_warning(goal, weekly_summary, len(result.phase_plan.weeks))
            == RACE_REHEARSAL_WARNING
        )

        warning = collect_plan_warnings(result, goal, weekly_summary, ambition, athlete)
        assert REHEARSAL_SKIPPED_WARNING in warning
        assert RACE_REHEARSAL_WARNING not in warning

    def test_a_multisport_rehearsal_is_not_worded_as_running(self) -> None:
        """Ironman's BASIC race-sim week also rehearses the long ride, not
        just the long run: "running volume"/"long run" would be wrong for
        it. A hand-built PlanResult with no sessions stands in for a plan
        whose PEAK LOAD week fell back — only the real menu's own
        ``race_sim`` specs (read from ``get_session_menu``) drive the
        wording check.
        """
        goal, ambition = GoalType.IRONMAN, AmbitionLevel.BASIC
        a_goal = RaceGoal(goal, date(2026, 11, 1), "A")
        week = WeekSpec(
            week=0,
            phase=PhaseType.PEAK,
            mode=WeekMode.LOAD,
            sport_split={
                SportType.RUNNING: 0.4,
                SportType.CYCLING: 0.4,
                SportType.SWIMMING: 0.2,
            },
            effective_goal=goal,
            owning_goal=None,
        )
        result = PlanResult(
            phase_plan=PhasePlan(weeks=[week], race_events=[], a_goal=a_goal),
            sessions=[],
            assignments=[],
        )
        warning = rehearsal_skipped_warning(result, goal, ambition)
        assert warning == REHEARSAL_SKIPPED_WARNING_MULTISPORT
        assert "running" not in warning.lower()


class TestShortHorizonJoin:
    """A race inside the minimum horizon joins a full prep mid-preparation."""

    #: 8 weeks of marathon horizon against a 12-week minimum: deficit 4.
    START = "2026-03-09"  # a Monday
    RACE = "2026-05-03"  # the Sunday of week 7
    MIN_WEEKS = 12
    ACTUAL_WEEKS = 8
    DEFICIT = 4

    @staticmethod
    def _request(
        race: str, *, accept: bool = False, goal: str = "running_marathon"
    ) -> GenerateRequest:
        return GenerateRequest(
            races=[RaceGoalModel(goal_type=goal, race_date=race)],
            training_start=TestShortHorizonJoin.START,
            accept_short_horizon=accept,
            # A join is Experimental; that confirmation is tested on its own.
            accept_experimental=True,
        )

    @staticmethod
    def _spy_priors(monkeypatch) -> list[tuple[int, int]]:
        """Record the trained and joined weeks the pipeline actually runs on."""
        priors: list[tuple[int, int]] = []
        pipeline = generate_plan_module.generate_plan

        def _spy(request):
            priors.append((request.prior_training_weeks, request.horizon_join_weeks))
            return pipeline(request)

        monkeypatch.setattr(generate_plan_module, "generate_plan", _spy)
        return priors

    def _run(self, race: str, **kwargs) -> object:
        return run_generate(
            self._request(race, **kwargs), repos=_repos(), progress=_progress
        )

    def test_a_short_horizon_is_refused_until_it_is_accepted(self) -> None:
        result = self._run(self.RACE)
        assert result.status == "error"
        assert result.error_code == "horizon_too_short"
        assert result.min_weeks == self.MIN_WEEKS
        assert result.actual_weeks == self.ACTUAL_WEEKS
        assert result.deficit_weeks == self.DEFICIT
        assert "marathon" in result.error_message
        assert "12 weeks" in result.error_message
        assert "8 weeks away" in result.error_message

    def test_a_single_week_horizon_reads_as_one_week(self) -> None:
        """The refusal is a sentence, not a template — 1 is not "1 weeks"."""
        result = self._run("2026-03-15")
        assert "only 1 week to race day" in result.error_message

    def test_a_race_before_the_training_start_is_the_planners_own_refusal(
        self,
    ) -> None:
        """Not a horizon to top up — "your race is -5 weeks away" is nonsense."""
        result = self._run("2026-02-01", accept=True)
        assert result.status == "error"
        assert result.error_code is None
        assert "is not after this plan's start" in result.error_message

    def test_a_request_without_an_a_race_is_refused_for_that(self) -> None:
        """Which race is too soon? The goal set has to be fixed first."""
        request = GenerateRequest(
            races=[
                RaceGoalModel(
                    goal_type="running_marathon", race_date=self.RACE, priority="B"
                )
            ],
            training_start=self.START,
        )
        result = run_generate(request, repos=_repos(), progress=_progress)
        assert result.status == "error"
        assert result.error_code is None
        assert "this one has none" in result.error_message

    def test_accepting_joins_the_full_preparation_part_way_through(
        self, monkeypatch
    ) -> None:
        priors = self._spy_priors(monkeypatch)
        result = self._run(self.RACE, accept=True)
        assert result.status == "ok"
        # The pipeline builds the full 12 weeks and truncates the deficit.
        assert priors == [(0, self.DEFICIT)]
        assert len(result.phase_calendar) == self.ACTUAL_WEEKS
        assert result.phase_calendar[0] != "base", "the base weeks are skipped"
        assert "Short-notice" in result.warning
        assert result.warning in result.coach_brief

    def test_the_warning_names_the_week_the_plan_joins_at(self, monkeypatch) -> None:
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={SportType.RUNNING: SportState(recent_weekly_hours=3.5)}
            ),
        )
        result = self._run(self.RACE, accept=True)
        assert "joins a 12-week preparation at week 5" in result.warning
        # At or above the volume the preparation assumes (3.0 h for a
        # moderate marathon), the join is a fact, not a risk.
        assert "should be manageable" in result.warning

    def test_a_detrained_athlete_is_warned_off_the_join(self, monkeypatch) -> None:
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={SportType.RUNNING: SportState(recent_weekly_hours=1.5)}
            ),
        )
        result = self._run(self.RACE, accept=True)
        assert result.status == "ok"
        assert "not recommended" in result.warning
        assert "should be manageable" not in result.warning

    def test_a_horizon_under_the_join_floor_is_refused_outright(self) -> None:
        """Terminal, so it carries no error_code: there is nothing to accept."""
        result = self._run("2026-03-22", accept=True)  # 2 weeks out
        assert result.status == "error"
        assert result.error_code is None
        assert "only 2 weeks to race day" in result.error_message
        assert "at least 5 weeks" in result.error_message

    def test_the_shortest_joinable_horizon_still_builds(self) -> None:
        """At the floor exactly (marathon taper 3 + 2) the join is offered."""
        result = self._run("2026-04-12", accept=True)  # 5 weeks out
        assert result.status == "ok"
        assert len(result.phase_calendar) == 5
        # The floor is what keeps a join from opening in its own taper.
        assert result.phase_calendar[0] != "taper"
        assert "Short-notice" in result.warning

    def test_a_maintenance_block_never_reaches_the_gate(self) -> None:
        """Raceless blocks prepare for nothing — they cannot fall short."""
        result = self._run("2026-03-22", goal="running_maintenance")
        assert result.status == "error"
        assert result.error_code is None
        assert result.error_message.startswith(
            "A maintenance block takes at least 4 weeks and this one has 2"
        )

    def test_a_caller_with_its_own_prior_is_never_asked_to_confirm(
        self, monkeypatch
    ) -> None:
        """Replanning tops the deficit up instead of refusing (no ack there)."""
        priors = self._spy_priors(monkeypatch)
        result = run_generate(
            self._request(self.RACE),
            repos=_repos(),
            progress=_progress,
            prior_training_weeks=1,
        )
        assert result.status == "ok"
        # One week already truncated plus the three still missing: the
        # pipeline sees the same virtual 12-week horizon either way.
        assert priors == [(1, self.DEFICIT - 1)]
        assert result.warning is None, "a resumed plan is not short notice"

    def test_the_acceptance_round_trips_into_the_persisted_request(self) -> None:
        default = build_request_payload(self._request(self.RACE))
        assert default["accept_short_horizon"] is False
        payload = build_request_payload(self._request(self.RACE, accept=True))
        assert payload["accept_short_horizon"] is True


class TestWeeklyHoursCap:
    """The athlete-authored volume cap, from wire field to pipeline input."""

    START = "2026-03-09"  # a Monday
    RACE = "2026-06-07"  # a Sunday, 13 weeks out

    @staticmethod
    def _request(cap: float | None = None) -> GenerateRequest:
        return GenerateRequest(
            races=[
                RaceGoalModel(
                    goal_type="running_marathon", race_date=TestWeeklyHoursCap.RACE
                )
            ],
            training_start=TestWeeklyHoursCap.START,
            weekly_hours_cap=cap,
        )

    @staticmethod
    def _spy_caps(monkeypatch) -> list[float | None]:
        """Record the cap each pipeline run is handed."""
        caps: list[float | None] = []
        pipeline = generate_plan_module.generate_plan

        def _spy(request):
            caps.append(request.constraints.weekly_hours_cap)
            return pipeline(request)

        monkeypatch.setattr(generate_plan_module, "generate_plan", _spy)
        return caps

    def test_the_wire_default_is_no_cap(self) -> None:
        assert self._request().weekly_hours_cap is None

    @pytest.mark.parametrize("cap", [0, -1.0])
    def test_a_non_positive_cap_is_refused(self, cap: float) -> None:
        with pytest.raises(ValidationError):
            self._request(cap)

    def test_an_uncapped_request_persists_exactly_what_it_always_did(self) -> None:
        """Absent means absent — an uncapped plan's input hash must not move."""
        assert build_request_payload(self._request()) == {
            "races": [
                {
                    "goal_type": "running_marathon",
                    "race_date": self.RACE,
                    "priority": "A",
                }
            ],
            "training_start": self.START,
            "ambition_level": "moderate",
            "accept_short_horizon": False,
        }

    def test_a_capped_request_persists_the_number(self) -> None:
        assert build_request_payload(self._request(4.5))["weekly_hours_cap"] == 4.5

    def test_the_cap_reaches_the_pipeline(self, monkeypatch) -> None:
        caps = self._spy_caps(monkeypatch)
        result = run_generate(self._request(3.0), repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert caps == [3.0]

    def test_an_uncapped_request_reaches_the_pipeline_uncapped(
        self, monkeypatch
    ) -> None:
        caps = self._spy_caps(monkeypatch)
        result = run_generate(self._request(), repos=_repos(), progress=_progress)
        assert result.status == "ok"
        assert caps == [None]

    def test_the_cap_is_part_of_the_plans_identity(self) -> None:
        uncapped = run_generate(self._request(), repos=_repos(), progress=_progress)
        capped = run_generate(self._request(3.0), repos=_repos(), progress=_progress)
        assert uncapped.status == "ok"
        assert capped.status == "ok"
        assert uncapped.input_hash != capped.input_hash


class TestExperimentalPlanGate:
    """An Experimental plan is built only once the athlete has confirmed it."""

    MONDAY = "2026-09-21"

    @staticmethod
    def _request(goal: str, race: str, ambition: str = "moderate", **flags):
        return GenerateRequest(
            races=[RaceGoalModel(goal_type=goal, race_date=race)],
            training_start=TestExperimentalPlanGate.MONDAY,
            ambition_level=ambition,
            **flags,
        )

    @staticmethod
    def _run(request: GenerateRequest):
        return run_generate(request, repos=_repos(), progress=_progress)

    def test_an_alpha_marathon_builds_without_confirmation(self, monkeypatch) -> None:
        """A default competitive marathon at 16 weeks is what the scorecard checks."""
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={SportType.RUNNING: SportState(recent_weekly_hours=3.5)}
            ),
        )
        race = (date(2026, 9, 21) + timedelta(weeks=16, days=-1)).isoformat()
        result = self._run(self._request("running_marathon", race, "competitive"))
        assert result.status == "ok"
        assert result.maturity == "alpha"
        assert result.maturity_reasons == []
        assert result.maturity_note == TIER_NOTES[PlanMaturity.ALPHA]

    def test_an_experimental_goal_is_refused_until_confirmed(self) -> None:
        race = "2026-12-13"  # 12 weeks
        refused = self._run(self._request("running_5k", race))
        assert refused.status == "error"
        assert refused.error_code == "experimental_plan"
        assert refused.reasons == ["goal_experimental"]
        assert refused.error_message == (
            "This plan type is built from training research, and its sessions "
            "have not yet been reviewed one by one. You can "
            "still build it. Confirm to go ahead."
        )

        built = self._run(self._request("running_5k", race, accept_experimental=True))
        assert built.status == "ok"
        assert built.maturity == "experimental"
        assert built.maturity_reasons == ["goal_experimental"]
        assert built.maturity_note == (
            "This plan type is built from training research, and its sessions "
            "have not yet been reviewed one by one."
        )
        # The note is the preview's own caveat, never part of the stored plan.
        assert built.maturity_note not in built.coach_brief

    def test_a_short_notice_join_is_asked_about_its_horizon_first(
        self, monkeypatch
    ) -> None:
        """The competitive trail 50K five weeks out, at 3.53 h a week."""
        _patch_resolved(
            monkeypatch,
            AthleteState(
                sports={
                    SportType.RUNNING: SportState(
                        recent_weekly_hours=3.53,
                        longest_recent_min={ActivityFamily.LONG: 230},
                    )
                }
            ),
        )
        race = "2026-10-24"
        first = self._run(self._request("trail_50k", race, "competitive"))
        assert first.error_code == "horizon_too_short"

        second = self._run(
            self._request("trail_50k", race, "competitive", accept_short_horizon=True)
        )
        assert second.error_code == "experimental_plan"
        assert second.reasons == ["short_notice_join", "recent_volume_below_range"]
        assert "joins one part-way through" in second.error_message
        assert second.error_message.endswith("Confirm to go ahead.")

        built = self._run(
            self._request(
                "trail_50k",
                race,
                "competitive",
                accept_short_horizon=True,
                accept_experimental=True,
            )
        )
        assert built.status == "ok"
        assert built.maturity == "experimental"
        assert built.maturity_note is not None
        assert "Confirm to go ahead" not in built.maturity_note

    def test_a_marathon_beyond_the_checked_horizons_needs_confirmation(self) -> None:
        """29 weeks sits above the checked range's 28-week ceiling."""
        race = (date(2026, 9, 21) + timedelta(weeks=29, days=-1)).isoformat()
        refused = self._run(self._request("running_marathon", race))
        assert refused.error_code == "experimental_plan"
        assert refused.reasons == ["horizon_out_of_range"]
        assert refused.error_message.startswith("At 29 weeks this plan")

    def test_the_confirmation_is_not_stored_with_the_plan(self) -> None:
        request = self._request("running_10k", "2026-12-13", accept_experimental=True)
        assert "accept_experimental" not in build_request_payload(request)

    def test_a_replan_never_asks_again(self) -> None:
        """The committed plan was confirmed when it was built."""
        payload = build_request_payload(self._request("running_10k", "2026-12-13"))
        replay = generate_plan_module.request_from_payload(
            payload, [RaceGoalModel(goal_type="running_10k", race_date="2026-12-13")]
        )
        assert replay.accept_experimental is True
