"""Tests for enforced onboarding: history > self-report > default.

Covers ``resolve_athlete_state`` source precedence, the
``athlete_data_required`` refusal in ``run_generate``, and the
self-assessment repository round-trip.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from endury.database.sqlite.repositories.self_assessment import (
    SelfAssessmentRepository,
    SelfAssessmentRow,
)
from endury.intervals.models import AthleteProfile, IntervalsActivity, SportSettingsRow
from endury.planner.foundations.types import (
    ActivityFamily,
    GoalType,
    SportType,
)
from endury.planner.pipeline import default_profile_for
from endury.planning.athlete_state import (
    NOT_CONFIGURED_REASON,
    history_measured_sports,
    measured_sports_with_reason,
    resolve_athlete_state,
)
from endury.planning.generate_plan import run_generate
from endury.planning.wire.requests import GenerateRequest, RaceGoalModel
from endury.wiring.container import RepositoryContainer

_TODAY = date(2026, 7, 13)  # a Monday


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the app clock to ``_TODAY``.

    The fixtures date their activities relative to ``_TODAY``, but
    ``run_generate`` reads the clock itself. Without this the measurement
    window drifts away from the fixtures as real time passes and the
    gate tests start failing on a date change rather than a code change.
    """
    monkeypatch.setattr("endury.common.clock.today", lambda: _TODAY)


def _act(
    sport_type: str, days_ago: int, duration_seconds: float = 3600.0
) -> IntervalsActivity:
    day = _TODAY - timedelta(days=days_ago)
    return IntervalsActivity(
        id=f"i{days_ago}{sport_type}",
        start_date_local=day.isoformat(),
        type=sport_type,
        moving_time=int(duration_seconds),
        icu_training_load=50.0,
        distance=10_000.0,
    )


def _sport_settings(
    *,
    run_lthr: float | None = 165.0,
    run_pace_sec_per_km: float | None = 270.0,
    cycling_ftp: float | None = 250.0,
    swim_css_sec_per_100m: float | None = 110.0,
) -> list[SportSettingsRow]:
    """Build sport-settings rows reproducing the given threshold values."""
    rows: list[SportSettingsRow] = []
    if run_lthr or run_pace_sec_per_km:
        rows.append(
            SportSettingsRow(
                types=["Run"],
                lthr=int(run_lthr) if run_lthr else None,
                threshold_pace=(
                    1000.0 / run_pace_sec_per_km if run_pace_sec_per_km else None
                ),
            )
        )
    if cycling_ftp:
        rows.append(SportSettingsRow(types=["Ride"], ftp=int(cycling_ftp)))
    if swim_css_sec_per_100m:
        rows.append(
            SportSettingsRow(
                types=["Swim"], threshold_pace=100.0 / swim_css_sec_per_100m
            )
        )
    return rows


class _FakeIntervalsClient:
    """Offline stand-in for the ``IntervalsSource`` protocol."""

    def __init__(
        self,
        activities: list[IntervalsActivity] | None = None,
        sport_settings: list[SportSettingsRow] | None = None,
    ) -> None:
        self._activities = activities or []
        self._profile = AthleteProfile(sport_settings=sport_settings or [])

    def get_athlete(self) -> AthleteProfile:
        return self._profile

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        return self._activities


class _FakeSelfAssessment:
    def __init__(self, rows: dict[str, SelfAssessmentRow]) -> None:
        self._rows = rows

    def get_all(self) -> dict[str, SelfAssessmentRow]:
        return self._rows


def _repos(
    activities: list[IntervalsActivity],
    reported: dict[str, SelfAssessmentRow] | None = None,
    sport_settings: list[SportSettingsRow] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        intervals_client=_FakeIntervalsClient(activities, sport_settings),
        self_assessment=_FakeSelfAssessment(reported or {}),
    )


def _progress(event: object, message: object = None) -> None:
    _ = event, message


class TestSourcePrecedence:
    def test_history_wins_over_self_report(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        reported = {"running": SelfAssessmentRow(1.0, 20, "2026-01-01")}
        resolved = resolve_athlete_state(
            _repos(acts, reported), GoalType.RUNNING_MARATHON, today=_TODAY
        )
        assert resolved.sources == {SportType.RUNNING: "history"}
        run = resolved.athlete.sports[SportType.RUNNING]
        assert run.ctl_0 is not None

    def test_self_report_wins_over_default(self) -> None:
        reported = {"running": SelfAssessmentRow(5.0, 95, "2026-01-01")}
        resolved = resolve_athlete_state(
            _repos([], reported), GoalType.RUNNING_MARATHON, today=_TODAY
        )
        assert resolved.sources == {SportType.RUNNING: "self_report"}
        run = resolved.athlete.sports[SportType.RUNNING]
        assert run.recent_weekly_hours == pytest.approx(5.0)
        assert run.longest_recent_min == {ActivityFamily.LONG: 95}
        assert run.ctl_0 is None
        assert run.atl_0 is None

    def test_default_when_neither(self) -> None:
        resolved = resolve_athlete_state(
            _repos([]), GoalType.RUNNING_MARATHON, today=_TODAY
        )
        assert resolved.sources == {SportType.RUNNING: "default"}
        assert (
            resolved.athlete.sports[SportType.RUNNING]
            == default_profile_for(GoalType.RUNNING_MARATHON).sports[SportType.RUNNING]
        )

    def test_partial_history_mixes_sources(self) -> None:
        """A runner asking for a triathlon: run measured, bike/swim reported."""
        acts = [_act("Run", i) for i in range(1, 20)]
        reported = {
            "cycling": SelfAssessmentRow(4.0, 120, "2026-01-01"),
            "swimming": SelfAssessmentRow(1.5, 45, "2026-01-01"),
        }
        resolved = resolve_athlete_state(
            _repos(acts, reported), GoalType.OLYMPIC_TRIATHLON, today=_TODAY
        )
        assert resolved.sources == {
            SportType.RUNNING: "history",
            SportType.CYCLING: "self_report",
            SportType.SWIMMING: "self_report",
        }
        assert resolved.athlete.sports[SportType.CYCLING].longest_recent_min == {
            ActivityFamily.LONG: 120
        }
        assert resolved.athlete.sports[SportType.SWIMMING].longest_recent_min == {
            ActivityFamily.EASY: 45
        }

    def test_thresholds_reach_self_reported_sports(self) -> None:
        repos = _repos(
            [],
            {
                "running": SelfAssessmentRow(5.0, 90, "2026-01-01"),
                "cycling": SelfAssessmentRow(6.0, 150, "2026-01-01"),
                "swimming": SelfAssessmentRow(2.0, 45, "2026-01-01"),
            },
            sport_settings=_sport_settings(),
        )
        resolved = resolve_athlete_state(repos, GoalType.IRONMAN, today=_TODAY)
        sports = resolved.athlete.sports
        assert sports[SportType.RUNNING].threshold_pace_sec_per_km == pytest.approx(
            4.5 * 60.0
        )
        assert sports[SportType.CYCLING].ftp_watts == pytest.approx(250.0)
        assert sports[SportType.SWIMMING].css_sec_per_100m == pytest.approx(110.0)


class TestSelfReportFloorAndFallback:
    def test_reported_hours_are_used_verbatim_never_floored(self) -> None:
        """A low answer is the athlete's own statement — no default floor.

        The 0.5x floor exists for gappy *measured* history; applying it to
        an explicit self-report would silently rebuild the default profile
        the gate refuses.
        """
        reported = {"running": SelfAssessmentRow(0.5, 60, "2026-01-01")}
        resolved = resolve_athlete_state(
            _repos([], reported), GoalType.RUNNING_MARATHON, today=_TODAY
        )
        assert resolved.athlete.sports[
            SportType.RUNNING
        ].recent_weekly_hours == pytest.approx(0.5)

    def test_zero_longest_session_falls_back_to_the_default(self) -> None:
        reported = {"running": SelfAssessmentRow(6.0, 0, "2026-01-01")}
        resolved = resolve_athlete_state(
            _repos([], reported), GoalType.RUNNING_MARATHON, today=_TODAY
        )
        default_state = default_profile_for(GoalType.RUNNING_MARATHON).sports[
            SportType.RUNNING
        ]
        assert (
            resolved.athlete.sports[SportType.RUNNING].longest_recent_min
            == default_state.longest_recent_min
        )


class TestHistoryMeasuredSports:
    def test_empty_when_the_five_activity_gate_fails(self) -> None:
        acts = [_act("Run", i) for i in range(1, 4)]
        assert history_measured_sports(_repos(acts), today=_TODAY) == set()

    def test_lists_every_bucketed_sport_not_just_goal_sports(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        acts += [_act("Ride", i) for i in range(1, 20)]
        assert history_measured_sports(_repos(acts), today=_TODAY) == {
            SportType.RUNNING,
            SportType.CYCLING,
        }

    def test_defaults_today_to_the_clock(self, monkeypatch) -> None:
        import endury.planning.athlete_state as mod

        monkeypatch.setattr(mod.clock, "today", lambda: _TODAY)
        acts = [_act("Run", i) for i in range(1, 20)]
        assert history_measured_sports(_repos(acts)) == {SportType.RUNNING}

    def test_one_stray_activity_does_not_measure_a_sport(self) -> None:
        """>= 5 activities overall, but a single old ride is not evidence.

        A sport counts as measured only when its own bucket clears the
        activity-count floor — one stray old activity must not.
        """
        acts = [_act("Run", i) for i in range(1, 20)]
        acts.append(_act("Ride", 100))
        assert history_measured_sports(_repos(acts), today=_TODAY) == {
            SportType.RUNNING
        }

    def test_current_week_only_activities_measure_nothing(self) -> None:
        """Six activities inside the incomplete ISO week: median reads 0."""
        acts = [_act("Run", 0) for _ in range(6)]
        assert history_measured_sports(_repos(acts), today=_TODAY) == set()


class _NoKeyRepos:
    """A container whose ``intervals_client`` raises, as it does with no key."""

    def __init__(self, reported: dict[str, SelfAssessmentRow] | None = None) -> None:
        self.self_assessment = _FakeSelfAssessment(reported or {})

    @property
    def intervals_client(self) -> object:
        raise ValueError("intervals.icu API key is not configured")


class _BrokenIntervalsClient(_FakeIntervalsClient):
    """A configured client whose reads fail (intervals.icu outage)."""

    def get_athlete(self) -> AthleteProfile:
        raise RuntimeError("intervals.icu is down")

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        raise RuntimeError("intervals.icu is down")


class TestIntervalsUnavailable:
    """No key / outage degrades to the fallback chain; it never raises."""

    def test_no_key_reports_no_measured_sports_with_a_reason(self) -> None:
        sports, reason = measured_sports_with_reason(_NoKeyRepos(), today=_TODAY)

        assert sports == set()
        assert reason == NOT_CONFIGURED_REASON

    def test_an_outage_reports_no_measured_sports_with_a_reason(self) -> None:
        repos = SimpleNamespace(
            intervals_client=_BrokenIntervalsClient(),
            self_assessment=_FakeSelfAssessment({}),
        )

        sports, reason = measured_sports_with_reason(repos, today=_TODAY)

        assert sports == set()
        assert "intervals.icu is down" in str(reason)

    def test_no_key_still_resolves_from_the_self_report(self) -> None:
        reported = {"running": SelfAssessmentRow(5.0, 95, "2026-01-01")}
        resolved = resolve_athlete_state(
            _NoKeyRepos(reported), GoalType.RUNNING_MARATHON, today=_TODAY
        )

        assert resolved.sources == {SportType.RUNNING: "self_report"}
        assert resolved.thresholds == {}

    def test_an_outage_still_resolves_from_the_self_report(self) -> None:
        repos = SimpleNamespace(
            intervals_client=_BrokenIntervalsClient(),
            self_assessment=_FakeSelfAssessment(
                {"running": SelfAssessmentRow(5.0, 95, "2026-01-01")}
            ),
        )

        resolved = resolve_athlete_state(repos, GoalType.RUNNING_MARATHON, today=_TODAY)

        assert resolved.sources == {SportType.RUNNING: "self_report"}


class TestEvidenceBarInResolve:
    def test_stray_activity_sport_falls_through_to_self_report(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        acts.append(_act("Ride", 100))
        reported = {
            "cycling": SelfAssessmentRow(2.0, 60, "2026-01-01"),
            "swimming": SelfAssessmentRow(1.0, 30, "2026-01-01"),
        }
        resolved = resolve_athlete_state(
            _repos(acts, reported), GoalType.OLYMPIC_TRIATHLON, today=_TODAY
        )
        assert resolved.sources[SportType.CYCLING] == "self_report"
        assert resolved.athlete.sports[
            SportType.CYCLING
        ].recent_weekly_hours == pytest.approx(2.0)

    def test_stray_activity_sport_without_answers_reads_default(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        acts.append(_act("Ride", 100))
        resolved = resolve_athlete_state(
            _repos(acts), GoalType.OLYMPIC_TRIATHLON, today=_TODAY
        )
        assert resolved.sources[SportType.CYCLING] == "default"


def _request(goal: str = "running_marathon") -> GenerateRequest:
    return GenerateRequest(
        races=[RaceGoalModel(goal_type=goal, race_date="2026-10-04")],
        training_start="2026-03-09",
        ambition_level="moderate",
        accept_experimental=True,  # 30 weeks: beyond the checked horizons
    )


class TestEnforcedGate:
    def test_empty_db_refuses_and_names_every_goal_sport(self) -> None:
        result = run_generate(_request("ironman"), repos=_repos([]), progress=_progress)
        assert result.status == "error"
        assert result.error_code == "athlete_data_required"
        assert set(result.missing_sports) == {"running", "cycling", "swimming"}
        assert "running" in result.error_message
        assert "and swimming" in result.error_message

    def test_partial_history_names_only_the_unknown_sports(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        result = run_generate(
            _request("olympic_triathlon"), repos=_repos(acts), progress=_progress
        )
        assert result.status == "error"
        assert result.missing_sports == ["cycling", "swimming"]

    def test_single_missing_sport_message_has_no_conjunction(self) -> None:
        result = run_generate(_request(), repos=_repos([]), progress=_progress)
        assert result.missing_sports == ["running"]
        assert "your running training" in result.error_message

    def test_self_assessment_unblocks_and_sizes_the_plan(self) -> None:
        reported = {"running": SelfAssessmentRow(4.0, 100, "2026-01-01")}
        result = run_generate(
            _request(), repos=_repos([], reported), progress=_progress
        )
        assert result.status == "ok"
        assert result.athlete_state["derived_from_history"] is False
        assert result.athlete_state["sources"] == {"running": "self_report"}
        assert result.athlete_state["sports"]["running"][
            "recent_weekly_hours"
        ] == pytest.approx(4.0)

    def test_mixed_sources_are_reported_in_the_payload(self) -> None:
        acts = [_act("Run", i) for i in range(1, 20)]
        reported = {
            "cycling": SelfAssessmentRow(6.0, 180, "2026-01-01"),
            "swimming": SelfAssessmentRow(2.0, 60, "2026-01-01"),
        }
        result = run_generate(
            _request("olympic_triathlon"),
            repos=_repos(acts, reported),
            progress=_progress,
        )
        assert result.status == "ok"
        assert result.athlete_state["sources"] == {
            "running": "history",
            "cycling": "self_report",
            "swimming": "self_report",
        }
        assert result.athlete_state["derived_from_history"] is True

    def test_enforcement_off_falls_back_to_the_goal_defaults(self) -> None:
        result = run_generate(
            _request(),
            repos=_repos([]),
            progress=_progress,
            enforce_athlete_data=False,
        )
        assert result.status == "ok"
        default_hours = (
            default_profile_for(GoalType.RUNNING_MARATHON)
            .sports[SportType.RUNNING]
            .recent_weekly_hours
        )
        assert result.athlete_state["sources"] == {"running": "default"}
        assert result.athlete_state["sports"]["running"][
            "recent_weekly_hours"
        ] == pytest.approx(default_hours)


class TestSelfAssessmentRepository:
    def test_upsert_then_read_back(self) -> None:
        repos = RepositoryContainer.in_memory()
        repos.self_assessment.upsert_many({"running": (5.0, 90), "cycling": (7.5, 180)})
        stored = repos.self_assessment.get_all()
        assert set(stored) == {"running", "cycling"}
        assert stored["running"].weekly_hours == pytest.approx(5.0)
        assert stored["cycling"].longest_session_min == 180
        assert stored["running"].updated_at

    def test_upsert_replaces_a_stored_answer(self) -> None:
        repos = RepositoryContainer.in_memory()
        repos.self_assessment.upsert_many({"running": (5.0, 90)})
        repos.self_assessment.upsert_many({"running": (9.0, 150)})
        stored = repos.self_assessment.get_all()
        assert stored["running"] == SelfAssessmentRow(
            9.0, 150, stored["running"].updated_at
        )

    def test_empty_database_reads_back_empty(self) -> None:
        repos = RepositoryContainer.in_memory()
        assert repos.self_assessment.get_all() == {}

    def test_repository_is_wired_into_the_container(self) -> None:
        repos = RepositoryContainer.in_memory()
        assert isinstance(repos.self_assessment, SelfAssessmentRepository)
