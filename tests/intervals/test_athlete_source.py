"""Tests for the intervals.icu -> planner athlete-state resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from endury.intervals.athlete_source import (
    DERIVED_CYCLING_LTHR,
    history_measured_sports,
    resolve_from_intervals,
)
from endury.intervals.fitness import activity_date, per_sport_ctl_atl
from endury.intervals.models import (
    AthleteProfile,
    IntervalsActivity,
    SportSettingsRow,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    GoalType,
    RaceGoal,
    SportType,
)
from endury.planner.pipeline import default_profile_for

#: A Wednesday: the current ISO week (Mon 2026-08-03) is incomplete.
TODAY = date(2026, 8, 5)

#: Monday six complete ISO weeks before TODAY's week.
WINDOW_START = date(2026, 6, 22)

MARATHON = [
    RaceGoal(
        goal_type=GoalType.RUNNING_MARATHON,
        race_date=date(2026, 11, 1),
        priority="A",
    )
]
IRONMAN = [
    RaceGoal(goal_type=GoalType.IRONMAN, race_date=date(2026, 11, 1), priority="A")
]

RUN_SETTINGS = SportSettingsRow(
    types=["Run", "VirtualRun"],
    ftp=None,
    lthr=170,
    max_hr=190,
    threshold_pace=10.0 / 3.0,
    pace_units="MINS_KM",
)
BIKE_SETTINGS = SportSettingsRow(
    types=["Ride", "VirtualRide"],
    ftp=250,
    lthr=None,
    max_hr=185,
    threshold_pace=None,
    pace_units=None,
)
SWIM_SETTINGS = SportSettingsRow(
    types=["Swim", "OpenWaterSwim"],
    ftp=None,
    lthr=None,
    max_hr=None,
    threshold_pace=2.0 / 3.0,
    pace_units="SECS_100M",
)
ALL_SETTINGS = [RUN_SETTINGS, BIKE_SETTINGS, SWIM_SETTINGS]


@dataclass(frozen=True)
class FakeAnswer:
    """Stand-in for a stored self-assessment row."""

    weekly_hours: float
    longest_session_min: int


class FakeClient:
    """Offline stand-in for ``IntervalsClient``'s read methods."""

    def __init__(
        self,
        activities: list[IntervalsActivity] | None = None,
        sport_settings: list[SportSettingsRow] | None = None,
    ) -> None:
        self.activities = activities or []
        self.sport_settings = sport_settings or []

    def get_athlete(self) -> AthleteProfile:
        return AthleteProfile(id="i1", sport_settings=self.sport_settings)

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        return [
            act
            for act in self.activities
            if (day := activity_date(act)) is not None and oldest <= day <= newest
        ]


def _act(
    day: date,
    sport_type: str = "Run",
    seconds: int = 3600,
    load: float = 60.0,
) -> IntervalsActivity:
    """Build a minimal intervals.icu activity fixture."""
    return IntervalsActivity(
        id=f"i{day.isoformat()}{sport_type}{seconds}",
        start_date_local=f"{day.isoformat()}T06:00:00",
        type=sport_type,
        moving_time=seconds,
        icu_training_load=load,
        distance=10000.0,
    )


def _weekly_runs(
    hours_per_week: list[float], sport_type: str = "Run"
) -> list[IntervalsActivity]:
    """Two equal sessions per complete ISO week, week 0 = ``WINDOW_START``."""
    acts = []
    for index, hours in enumerate(hours_per_week):
        if hours <= 0:
            continue
        monday = WINDOW_START + timedelta(weeks=index)
        seconds = int(hours * 3600 / 2)
        acts.append(_act(monday, sport_type, seconds))
        acts.append(_act(monday + timedelta(days=3), sport_type, seconds))
    return acts


class TestMeasuredHistory:
    """The measured path: hours median, longest session, CTL/ATL, thresholds."""

    @pytest.fixture
    def resolved(self):
        acts = _weekly_runs([3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        client = FakeClient(acts, ALL_SETTINGS)
        return resolve_from_intervals(client, TODAY, MARATHON), acts

    def test_source_is_history(self, resolved) -> None:
        state, _ = resolved
        assert state.sources == {SportType.RUNNING: "history"}

    def test_hours_are_the_median_of_six_complete_weeks(self, resolved) -> None:
        state, _ = resolved
        assert state.athlete.sports[SportType.RUNNING].recent_weekly_hours == 5.5

    def test_longest_session_uses_the_42_day_window(self, resolved) -> None:
        state, _ = resolved
        assert state.athlete.sports[SportType.RUNNING].longest_recent_min == {
            ActivityFamily.LONG: 240
        }

    def test_ctl_atl_come_from_the_weekly_bridge(self, resolved) -> None:
        state, acts = resolved
        expected = per_sport_ctl_atl(acts, TODAY)[SportType.RUNNING]
        sport_state = state.athlete.sports[SportType.RUNNING]
        assert (sport_state.ctl_0, sport_state.atl_0) == expected
        assert sport_state.ctl_0 is not None
        assert sport_state.ctl_0 > 0.0

    def test_run_threshold_pace_is_seconds_per_km(self, resolved) -> None:
        state, _ = resolved
        run = state.athlete.sports[SportType.RUNNING]
        assert run.threshold_pace_sec_per_km == pytest.approx(300.0)
        assert run.ftp_watts is None

    def test_only_goal_sports_are_kept(self) -> None:
        acts = _weekly_runs([3.0, 4.0, 5.0, 6.0, 7.0, 8.0]) + _weekly_runs(
            [2.0, 2.0, 2.0, 2.0, 2.0, 2.0], "Ride"
        )
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert set(state.athlete.sports) == {SportType.RUNNING}


class TestMeasurementGates:
    """The global activity-count gate and the per-sport median gate."""

    def test_fewer_than_five_activities_measures_nothing(self) -> None:
        acts = _weekly_runs([2.0, 0, 2.0, 0, 0, 0])  # 4 activities
        assert len(acts) == 4
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert state.sources == {SportType.RUNNING: "default"}
        assert (
            state.athlete.sports[SportType.RUNNING]
            == default_profile_for(GoalType.RUNNING_MARATHON).sports[SportType.RUNNING]
        )

    def test_two_trained_weeks_of_six_measure_nothing(self) -> None:
        acts = _weekly_runs([3.0, 3.0, 0, 0, 0, 0]) + _weekly_runs([1.0], "Swim")
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert state.sources == {SportType.RUNNING: "default"}

    def test_three_trained_weeks_of_six_measure(self) -> None:
        acts = _weekly_runs([0, 0, 0, 6.0, 6.0, 6.0])
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert state.sources == {SportType.RUNNING: "history"}

    def test_current_incomplete_week_alone_measures_nothing(self) -> None:
        acts = [
            _act(date(2026, 8, 3) + timedelta(days=index % 3), seconds=3600 + index)
            for index in range(5)
        ]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert state.sources == {SportType.RUNNING: "default"}

    def test_activities_outside_the_lookback_are_not_fetched(self) -> None:
        acts = [_act(TODAY - timedelta(days=200 + i)) for i in range(6)]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        assert state.sources == {SportType.RUNNING: "default"}


class TestThinMeasuredHistory:
    """Floors and fallbacks that keep a sparse history solvable."""

    def test_hours_are_floored_at_half_the_goal_default(self) -> None:
        acts = _weekly_runs([0, 0.5, 0.5, 0.5, 0.5, 0])
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        run = state.athlete.sports[SportType.RUNNING]
        default_hours = (
            default_profile_for(GoalType.RUNNING_MARATHON)
            .sports[SportType.RUNNING]
            .recent_weekly_hours
        )
        assert state.sources[SportType.RUNNING] == "history"
        assert run.recent_weekly_hours == 0.5 * default_hours
        assert run.recent_weekly_hours > 0.5  # above the measured median
        assert run.longest_recent_min == {ActivityFamily.LONG: 15}

    def test_sub_minute_sessions_fall_back_to_the_default_longest(self) -> None:
        acts = _weekly_runs([0, 0, 0, 0.01, 0.01, 0.01])
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        run = state.athlete.sports[SportType.RUNNING]
        assert state.sources[SportType.RUNNING] == "history"
        assert run.longest_recent_min == {ActivityFamily.LONG: 60}

    def test_measured_hours_without_any_recorded_load_leave_ctl_unseeded(self) -> None:
        """A strapless athlete: hours are real, load is absent, CTL derives."""
        acts = [
            act.model_copy(update={"icu_training_load": None})
            for act in _weekly_runs([4.0] * 6)
        ]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)
        run = state.athlete.sports[SportType.RUNNING]

        assert state.sources[SportType.RUNNING] == "history"
        assert run.recent_weekly_hours == 4.0
        assert run.ctl_0 is None
        assert run.atl_0 is None


class TestVolumeSeedDuration:
    """Volume is measured from the timer, not from moving time."""

    def test_recording_time_wins_over_moving_time(self) -> None:
        acts = [
            act.model_copy(update={"icu_recording_time": act.moving_time * 2})
            for act in _weekly_runs([4.0] * 6)
        ]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)

        assert state.athlete.sports[SportType.RUNNING].recent_weekly_hours == 8.0

    def test_moving_time_is_the_fallback_when_the_timer_is_absent(self) -> None:
        acts = _weekly_runs([4.0] * 6)
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)

        assert state.athlete.sports[SportType.RUNNING].recent_weekly_hours == 4.0


class TestRecentWeeklyElevation:
    """The climb reported beside the measured weekly hours."""

    def test_median_of_the_same_six_complete_weeks(self) -> None:
        acts = [
            act.model_copy(update={"total_elevation_gain": 150.0})
            for act in _weekly_runs([3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        ]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)

        # Two sessions a week, 150 m each.
        assert state.recent_weekly_elevation_m == {SportType.RUNNING: 300}

    def test_sessions_without_elevation_count_as_zero_metres(self) -> None:
        first_hilly_week = WINDOW_START + timedelta(weeks=3)
        acts = [
            act.model_copy(update={"total_elevation_gain": 150.0})
            if activity_date(act) >= first_hilly_week
            else act
            for act in _weekly_runs([4.0] * 6)
        ]
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)

        # Weekly totals [0, 0, 0, 300, 300, 300] -> median 150.
        assert state.recent_weekly_elevation_m == {SportType.RUNNING: 150}

    def test_history_that_records_no_elevation_reads_unmeasured(self) -> None:
        acts = _weekly_runs([4.0] * 6)
        state = resolve_from_intervals(FakeClient(acts, ALL_SETTINGS), TODAY, MARATHON)

        assert state.recent_weekly_elevation_m == {SportType.RUNNING: None}

    def test_unmeasured_sports_get_no_entry_at_all(self) -> None:
        state = resolve_from_intervals(
            FakeClient([], ALL_SETTINGS),
            TODAY,
            MARATHON,
            {"running": FakeAnswer(weekly_hours=2.5, longest_session_min=45)},
        )

        assert state.recent_weekly_elevation_m == {}


class TestFallbackChain:
    """history > self_report > default."""

    def test_self_report_is_used_verbatim(self) -> None:
        acts = _weekly_runs([2.0, 2.0, 2.0, 2.0, 2.0, 2.0], "Ride")
        state = resolve_from_intervals(
            FakeClient(acts, ALL_SETTINGS),
            TODAY,
            MARATHON,
            {"running": FakeAnswer(weekly_hours=2.5, longest_session_min=45)},
        )
        run = state.athlete.sports[SportType.RUNNING]
        assert state.sources == {SportType.RUNNING: "self_report"}
        assert run.recent_weekly_hours == 2.5  # no 0.5x floor on a reported answer
        assert run.longest_recent_min == {ActivityFamily.LONG: 45}
        assert run.ctl_0 is None
        assert run.atl_0 is None
        assert run.threshold_pace_sec_per_km == pytest.approx(300.0)

    def test_self_report_without_a_longest_session_uses_the_default(self) -> None:
        state = resolve_from_intervals(
            FakeClient([], ALL_SETTINGS),
            TODAY,
            MARATHON,
            {"running": FakeAnswer(weekly_hours=2.5, longest_session_min=0)},
        )
        assert state.athlete.sports[SportType.RUNNING].longest_recent_min == {
            ActivityFamily.LONG: 60
        }

    def test_history_beats_self_report(self) -> None:
        acts = _weekly_runs([3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        state = resolve_from_intervals(
            FakeClient(acts, ALL_SETTINGS),
            TODAY,
            MARATHON,
            {"running": FakeAnswer(weekly_hours=2.5, longest_session_min=45)},
        )
        assert state.sources == {SportType.RUNNING: "history"}

    def test_defaults_when_nothing_is_known(self) -> None:
        state = resolve_from_intervals(FakeClient([], ALL_SETTINGS), TODAY, IRONMAN)
        assert state.sources == {
            SportType.RUNNING: "default",
            SportType.CYCLING: "default",
            SportType.SWIMMING: "default",
        }
        assert (
            state.athlete.sports[SportType.SWIMMING]
            == default_profile_for(GoalType.IRONMAN).sports[SportType.SWIMMING]
        )


class TestThresholds:
    """Sport-settings rows -> per-sport thresholds."""

    @pytest.fixture
    def resolved(self):
        return resolve_from_intervals(FakeClient([], ALL_SETTINGS), TODAY, IRONMAN)

    def test_swim_css_is_seconds_per_100m(self, resolved) -> None:
        assert resolved.thresholds[
            SportType.SWIMMING
        ].css_sec_per_100m == pytest.approx(150.0)

    def test_cycling_ftp_is_read(self, resolved) -> None:
        assert resolved.thresholds[SportType.CYCLING].ftp_watts == 250.0

    def test_running_lthr_is_read(self, resolved) -> None:
        assert resolved.thresholds[SportType.RUNNING].lthr_bpm == 170.0

    def test_cycling_lthr_is_derived_and_flagged(self, resolved) -> None:
        assert resolved.thresholds[SportType.CYCLING].lthr_bpm == 162.0
        assert resolved.notes == {SportType.CYCLING: [DERIVED_CYCLING_LTHR]}

    def test_missing_swim_lthr_stays_none(self, resolved) -> None:
        assert resolved.thresholds[SportType.SWIMMING].lthr_bpm is None

    def test_first_matching_row_wins(self) -> None:
        second_run = SportSettingsRow(
            types=["Run"],
            ftp=None,
            lthr=999,
            max_hr=None,
            threshold_pace=None,
            pace_units=None,
        )
        state = resolve_from_intervals(
            FakeClient([], [RUN_SETTINGS, second_run]), TODAY, MARATHON
        )
        assert state.thresholds[SportType.RUNNING].lthr_bpm == 170.0

    def test_rows_without_types_are_skipped(self) -> None:
        empty = SportSettingsRow(
            types=[],
            ftp=300,
            lthr=None,
            max_hr=None,
            threshold_pace=None,
            pace_units=None,
        )
        state = resolve_from_intervals(FakeClient([], [empty]), TODAY, MARATHON)
        assert state.thresholds == {}

    def test_hike_row_before_run_row_does_not_claim_running(self) -> None:
        hike = SportSettingsRow(
            types=["Hike"],
            ftp=None,
            lthr=140,
            max_hr=None,
            threshold_pace=1.0,
            pace_units="MINS_KM",
        )
        state = resolve_from_intervals(
            FakeClient([], [hike, RUN_SETTINGS]), TODAY, MARATHON
        )
        assert state.thresholds[SportType.RUNNING].lthr_bpm == 170.0

    def test_no_sport_settings_leaves_state_thresholds_empty(self) -> None:
        acts = _weekly_runs([3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        state = resolve_from_intervals(FakeClient(acts, None), TODAY, MARATHON)
        run = state.athlete.sports[SportType.RUNNING]
        assert state.thresholds == {}
        assert run.threshold_pace_sec_per_km is None
        assert run.css_sec_per_100m is None
        assert run.ftp_watts is None


class TestGoalSelection:
    """The A-race picks the sport set."""

    def test_a_goal_wins_over_a_b_goal(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.RUNNING_10K,
                race_date=date(2026, 9, 1),
                priority="B",
            ),
            RaceGoal(
                goal_type=GoalType.IRONMAN,
                race_date=date(2026, 11, 1),
                priority="A",
            ),
        ]
        state = resolve_from_intervals(FakeClient([], ALL_SETTINGS), TODAY, goals)
        assert set(state.athlete.sports) == {
            SportType.RUNNING,
            SportType.CYCLING,
            SportType.SWIMMING,
        }

    def test_first_goal_is_used_when_none_is_flagged_a(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.IRONMAN,
                race_date=date(2026, 11, 1),
                priority="B",
            )
        ]
        state = resolve_from_intervals(FakeClient([], ALL_SETTINGS), TODAY, goals)
        assert len(state.athlete.sports) == 3


class TestHistoryMeasuredSports:
    """The wizard's "which sports can we measure" probe."""

    def test_reports_every_measurable_sport(self) -> None:
        acts = _weekly_runs([3.0, 3.0, 3.0, 3.0, 3.0, 3.0]) + _weekly_runs(
            [2.0, 2.0, 2.0, 2.0, 2.0, 2.0], "Ride"
        )
        client = FakeClient(acts, ALL_SETTINGS)
        assert history_measured_sports(client, TODAY) == {
            SportType.RUNNING,
            SportType.CYCLING,
        }

    def test_empty_when_the_activity_gate_fails(self) -> None:
        acts = _weekly_runs([2.0, 0, 2.0, 0, 0, 0])
        assert history_measured_sports(FakeClient(acts, ALL_SETTINGS), TODAY) == set()
