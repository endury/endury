"""Tests for endury/planner/days/day_assigner.py."""

from __future__ import annotations

from datetime import date, timedelta

from endury.planner.days.day_assigner import assign_days
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    PlacedSession,
    RaceEvent,
    RaceGoal,
    SessionOrigin,
    SportType,
)

_START = date(2026, 6, 1)  # Monday


def _session(
    slot_id: str,
    key: ActivityKey,
    sport: SportType,
    week: int = 0,
    hours: float = 1.0,
) -> PlacedSession:
    return PlacedSession(
        slot_id=slot_id,
        activity_key=key,
        sport=sport,
        origin=SessionOrigin.ANCHOR,
        week=week,
        day_of_week=None,
        target_hours=hours,
    )


def _hinted(
    slot_id: str,
    day: int,
    week: int = 0,
    sport: SportType = SportType.RUNNING,
    key: ActivityKey = ActivityKey.RUN_EASY,
    origin: SessionOrigin = SessionOrigin.ANCHOR,
) -> PlacedSession:
    return PlacedSession(
        slot_id=slot_id,
        activity_key=key,
        sport=sport,
        origin=origin,
        week=week,
        day_of_week=day,
        target_hours=1.0,
    )


def _race(day_of_week: int, week: int = 0, priority: str = "A") -> RaceEvent:
    goal_type = GoalType.IRONMAN if priority == "A" else GoalType.RUNNING_MARATHON
    prefix = f"race:{priority.lower()}_{goal_type.value}"
    event_id = prefix if priority == "A" else f"{prefix}_w{week}"
    return RaceEvent(
        event_id=event_id,
        goal=RaceGoal(
            goal_type, _START + timedelta(weeks=week, days=day_of_week), priority
        ),
        week=week,
        day_of_week=day_of_week,
        duration_by_sport={SportType.RUNNING: 4.0},
    )


class TestAssignDays:
    """The menu-authored-day placement contract."""

    def test_empty_sessions_returns_empty_list(self) -> None:
        assert assign_days([], _START) == []

    def test_uses_session_day_when_set(self) -> None:
        result = assign_days([_hinted("run_tue", 1)], _START)
        assert len(result) == 1
        # Tuesday of week 0
        assert result[0].date == _START + timedelta(days=1)

    def test_falls_back_to_monday_when_day_missing(self) -> None:
        """Defensive fallback for hand-built sessions without a day.

        Menu-origin sessions always carry a day (Slot.day is mandatory).
        """
        sess = _session("run_unhinted", ActivityKey.RUN_EASY, SportType.RUNNING)
        result = assign_days([sess], _START)
        assert len(result) == 1
        assert result[0].date == _START  # Monday

    def test_one_assignment_per_session(self) -> None:
        """Totality: without races, every session yields one assignment."""
        sessions = [_hinted(f"s{i}", day=i % 7, week=i // 7) for i in range(10)]
        result = assign_days(sessions, _START)
        assert len(result) == len(sessions)
        assert {a.slot_id for a in result} == {s.slot_id for s in sessions}

    def test_multi_week_sessions_land_in_correct_weeks(self) -> None:
        sessions = [_hinted(f"wk{w}", day=2, week=w) for w in range(3)]
        result = assign_days(sessions, _START)
        by_slot = {a.slot_id: a.date for a in result}
        for w in range(3):
            assert by_slot[f"wk{w}"] == _START + timedelta(weeks=w, days=2)

    def test_carries_activity_key_and_hours_unchanged(self) -> None:
        sess = _hinted("bike", 5, sport=SportType.CYCLING, key=ActivityKey.BIKE_LONG)
        result = assign_days([sess], _START)
        assert result[0].activity_key == ActivityKey.BIKE_LONG
        assert result[0].target_hours == 1.0

    def test_sorts_by_date_then_slot(self) -> None:
        sessions = [
            _hinted("b_sat", 5, sport=SportType.CYCLING, key=ActivityKey.BIKE_LONG),
            _hinted("a_mon", 0),
        ]
        result = assign_days(sessions, _START)
        assert [a.slot_id for a in result] == ["a_mon", "b_sat"]


class TestRaceDayEviction:
    """Race-day handling of the assigner (deterministic eviction)."""

    def test_saturday_a_race_moves_shakeout_earlier(self) -> None:
        """A Sat shakeout collides with a Sat race and lands on Fri
        (cross-sport: the Fri swim does not block a run)."""
        sessions = [
            _hinted("tue_run", 1),
            _hinted(
                "fri_swim",
                4,
                sport=SportType.SWIMMING,
                key=ActivityKey.SWIM_ENDURANCE,
            ),
            _hinted("sat_shakeout", 5),
        ]
        result = assign_days(sessions, _START, [_race(5)])
        by_slot = {a.slot_id: a.date for a in result}
        assert by_slot["sat_shakeout"] == _START + timedelta(days=4)
        assert by_slot["tue_run"] == _START + timedelta(days=1)
        assert by_slot["fri_swim"] == _START + timedelta(days=4)

    def test_sunday_b_race_moves_maintenance_bike(self) -> None:
        """A Sunday B-race evicts the Sun maintenance bike to Saturday."""
        sessions = [
            _hinted(
                "sun_bike",
                6,
                week=8,
                sport=SportType.CYCLING,
                key=ActivityKey.BIKE_ENDURANCE,
            ),
        ]
        result = assign_days(sessions, _START, [_race(6, week=8, priority="B")])
        assert len(result) == 1
        assert result[0].date == _START + timedelta(weeks=8, days=5)

    def test_midweek_c_race_cascades_past_same_sport_day(self) -> None:
        """A Wed collider skips the run-occupied Tue and lands on Mon."""
        sessions = [
            _hinted("tue_run", 1, week=6),
            _hinted("wed_run", 2, week=6),
        ]
        result = assign_days(sessions, _START, [_race(2, week=6, priority="C")])
        by_slot = {a.slot_id: a.date for a in result}
        assert by_slot["wed_run"] == _START + timedelta(weeks=6, days=0)

    def test_two_same_sport_colliders_cascade_to_distinct_days(self) -> None:
        """Evicted sessions register occupancy: the second same-sport
        collider lands one day earlier than the first (R-DAY-006)."""
        sessions = [
            _hinted("wed_run_a", 2),
            _hinted("wed_run_b", 2),
        ]
        result = assign_days(sessions, _START, [_race(2)])
        by_slot = {a.slot_id: a.date for a in result}
        assert by_slot["wed_run_a"] == _START + timedelta(days=1)  # Tue
        assert by_slot["wed_run_b"] == _START + timedelta(days=0)  # Mon

    def test_eviction_respects_day_capacity(self, caplog) -> None:
        """An eviction never stacks a third session onto a day.

        The Ironman race-week shape with a Tuesday race: Mon bike is
        authored, the Tue run and Tue swim collide. The run lands on
        Monday (2 sessions); the swim finds Monday at capacity and,
        with no earlier day, is dropped rather than stacked.
        """
        sessions = [
            _hinted(
                "mon_bike", 0, sport=SportType.CYCLING, key=ActivityKey.BIKE_ENDURANCE
            ),
            _hinted("tue_run", 1),
            _hinted(
                "tue_swim", 1, sport=SportType.SWIMMING, key=ActivityKey.SWIM_RECOVERY
            ),
        ]
        with caplog.at_level("WARNING"):
            result = assign_days(sessions, _START, [_race(1)])
        by_slot = {a.slot_id: a.date for a in result}
        assert by_slot["mon_bike"] == _START
        assert by_slot["tue_run"] == _START
        assert "tue_swim" not in by_slot
        assert "dropping race-day session tue_swim" in caplog.text
        per_day: dict = {}
        for a in result:
            per_day[a.date] = per_day.get(a.date, 0) + 1
        assert max(per_day.values()) <= 2

    def test_post_a_race_sessions_dropped(self, caplog) -> None:
        """A Monday A-race drops the rest of the race week."""
        sessions = [
            _hinted("tue_run", 1),
            _hinted("wed_run", 2),
        ]
        with caplog.at_level("WARNING"):
            result = assign_days(sessions, _START, [_race(0)])
        assert result == []
        assert "dropping post-race session tue_run" in caplog.text
        assert "dropping post-race session wed_run" in caplog.text

    def test_test_origin_pinned_on_race_day(self) -> None:
        """An explicitly scheduled TEST session stays, even on race day
        and even after the A-race."""
        sessions = [
            _hinted(
                "race_day_test",
                5,
                key=ActivityKey.RUN_THRESHOLD_TEST,
                origin=SessionOrigin.TEST,
            ),
            _hinted(
                "post_race_test",
                6,
                key=ActivityKey.RUN_THRESHOLD_TEST,
                origin=SessionOrigin.TEST,
            ),
        ]
        result = assign_days(sessions, _START, [_race(5)])
        by_slot = {a.slot_id: a.date for a in result}
        assert by_slot["race_day_test"] == _START + timedelta(days=5)
        assert by_slot["post_race_test"] == _START + timedelta(days=6)

    def test_drop_when_no_earlier_free_day(self, caplog) -> None:
        """A Monday-race collider has no earlier day and is dropped."""
        sessions = [_hinted("mon_run", 0)]
        with caplog.at_level("WARNING"):
            result = assign_days(sessions, _START, [_race(0)])
        assert result == []
        assert "dropping race-day session mon_run" in caplog.text

    def test_b_race_week_keeps_post_race_sessions(self) -> None:
        """Training after a midweek B-race is legitimate mid-season work."""
        sessions = [_hinted("thu_run", 3, week=8)]
        result = assign_days(sessions, _START, [_race(2, week=8, priority="B")])
        assert len(result) == 1
        assert result[0].date == _START + timedelta(weeks=8, days=3)
