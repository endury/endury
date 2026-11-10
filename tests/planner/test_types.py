"""Tests for endury/planner/foundations/types.py."""

from __future__ import annotations

from datetime import date

import pytest

from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityHardness,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    PlacedSession,
    PlanMaturity,
    RaceEvent,
    RaceGoal,
    SessionOrigin,
    SportType,
    TargetPreference,
    TIDFractions,
    WeekMode,
    WeekSpec,
)


class TestEnumCompleteness:
    """Verify member counts and value casing for all enums."""

    def test_sport_type_count(self) -> None:
        assert len(SportType) == 3

    def test_goal_type_count(self) -> None:
        assert len(GoalType) == 14

    def test_phase_type_count(self) -> None:
        assert len(PhaseType) == 5

    def test_plan_maturity_count(self) -> None:
        assert len(PlanMaturity) == 3

    def test_target_preference_count(self) -> None:
        assert len(TargetPreference) == 4

    def test_ambition_level_count(self) -> None:
        assert len(AmbitionLevel) == 3

    def test_week_mode_count(self) -> None:
        assert len(WeekMode) == 5

    def test_session_origin_count(self) -> None:
        # ANCHOR and TEST are the only origins.
        assert len(SessionOrigin) == 2

    def test_activity_family_count(self) -> None:
        assert len(ActivityFamily) == 7

    def test_activity_key_count(self) -> None:
        assert len(ActivityKey) == 61

    def test_all_enum_values_are_lowercase(self) -> None:
        all_enums = [
            SportType,
            GoalType,
            PhaseType,
            PlanMaturity,
            TargetPreference,
            AmbitionLevel,
            WeekMode,
            SessionOrigin,
            ActivityFamily,
            ActivityKey,
        ]
        for enum_cls in all_enums:
            for member in enum_cls:
                assert member.value == member.value.lower(), (
                    f"{enum_cls.__name__}.{member.name} value {member.value!r} is not lowercase"
                )

    def test_activity_key_run_prefix(self) -> None:
        # 28 workouts + 1 test protocol (run_threshold_test)
        run_keys = [k for k in ActivityKey if k.value.startswith("run_")]
        assert len(run_keys) == 29

    def test_activity_key_bike_prefix(self) -> None:
        # 17 workouts + 1 test protocol (bike_ftp_test)
        bike_keys = [k for k in ActivityKey if k.value.startswith("bike_")]
        assert len(bike_keys) == 19

    def test_activity_key_swim_prefix(self) -> None:
        # 12 workouts + 1 test protocol (swim_css_test)
        swim_keys = [k for k in ActivityKey if k.value.startswith("swim_")]
        assert len(swim_keys) == 13

    def test_activity_key_test_protocols(self) -> None:
        assert ActivityKey.RUN_THRESHOLD_TEST in ActivityKey
        assert ActivityKey.BIKE_FTP_TEST in ActivityKey
        assert ActivityKey.SWIM_CSS_TEST in ActivityKey


class TestTIDFractions:
    def test_construction(self) -> None:
        tid = TIDFractions(z_low=0.7, z_mid=0.2, z_high=0.1)
        assert tid.z_low == 0.7
        assert tid.z_mid == 0.2
        assert tid.z_high == 0.1

    def test_frozen(self) -> None:
        tid = TIDFractions(z_low=0.7, z_mid=0.2, z_high=0.1)
        with pytest.raises(AttributeError):
            tid.z_low = 0.5  # type: ignore[misc]


class TestActivityHardness:
    def test_construction(self) -> None:
        h = ActivityHardness(
            is_hard_overall=True,
            is_hard_physiologically=True,
            is_hard_mentally=False,
        )
        assert h.is_hard_overall is True
        assert h.is_hard_mentally is False

    def test_frozen(self) -> None:
        h = ActivityHardness(
            is_hard_overall=False,
            is_hard_physiologically=False,
            is_hard_mentally=False,
        )
        with pytest.raises(AttributeError):
            h.is_hard_overall = True  # type: ignore[misc]


class TestRaceGoal:
    def test_construction(self) -> None:
        goal = RaceGoal(
            goal_type=GoalType.RUNNING_MARATHON,
            race_date=date(2026, 10, 1),
            priority="A",
        )
        assert goal.goal_type == GoalType.RUNNING_MARATHON
        assert goal.priority == "A"

    def test_frozen(self) -> None:
        goal = RaceGoal(
            goal_type=GoalType.IRONMAN,
            race_date=date(2026, 8, 15),
            priority="B",
        )
        with pytest.raises(AttributeError):
            goal.priority = "A"  # type: ignore[misc]


class TestWeekSpec:
    def test_construction(self) -> None:
        spec = WeekSpec(
            week=0,
            phase=PhaseType.BASE,
            mode=WeekMode.LOAD,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.RUNNING_MARATHON,
            owning_goal=None,
        )
        assert spec.week == 0
        assert spec.mode == WeekMode.LOAD
        assert spec.owning_goal is None


class TestRaceEvent:
    def _goal(self) -> RaceGoal:
        return RaceGoal(
            goal_type=GoalType.IRONMAN,
            race_date=date(2026, 9, 20),
            priority="A",
        )

    def test_total_tss_single_sport(self) -> None:
        event = RaceEvent(
            event_id="race:a_goal",
            goal=self._goal(),
            week=12,
            day_of_week=6,
            duration_by_sport={SportType.RUNNING: 1.5},
        )
        assert event.total_hours == 1.5

    def test_total_tss_multi_sport(self) -> None:
        event = RaceEvent(
            event_id="race:a_goal",
            goal=self._goal(),
            week=12,
            day_of_week=6,
            duration_by_sport={
                SportType.SWIMMING: 0.8,
                SportType.CYCLING: 2.5,
                SportType.RUNNING: 1.0,
            },
        )
        assert event.total_hours == pytest.approx(4.3)

    def test_leg_id(self) -> None:
        event = RaceEvent(
            event_id="race:a_goal",
            goal=self._goal(),
            week=12,
            day_of_week=6,
            duration_by_sport={SportType.RUNNING: 1.0},
        )
        assert event.leg_id(SportType.RUNNING) == "race:a_goal:running"
        assert event.leg_id(SportType.CYCLING) == "race:a_goal:cycling"


class TestPlacedSession:
    def test_construction_with_no_day(self) -> None:
        session = PlacedSession(
            slot_id="slot_001",
            activity_key=ActivityKey.RUN_EASY,
            sport=SportType.RUNNING,
            origin=SessionOrigin.ANCHOR,
            week=2,
            day_of_week=None,
            target_hours=0.75,
        )
        assert session.day_of_week is None
        assert session.target_hours == 0.75

    def test_construction_with_day(self) -> None:
        session = PlacedSession(
            slot_id="slot_002",
            activity_key=ActivityKey.BIKE_LONG,
            sport=SportType.CYCLING,
            origin=SessionOrigin.ANCHOR,
            week=1,
            day_of_week=5,
            target_hours=2.0,
        )
        assert session.day_of_week == 5
