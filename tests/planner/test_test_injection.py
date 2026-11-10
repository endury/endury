"""Tests for endury/planner/sessions/test_injection.py."""

from __future__ import annotations

from datetime import date

import pytest

from endury.planner.foundations.types import (
    ActivityKey,
    CapacityProjection,
    GoalType,
    PhasePlan,
    PhaseType,
    PlacedSession,
    RaceGoal,
    SessionOrigin,
    SportType,
    TestProtocol,
    WeekMode,
    WeekSpec,
)
from endury.planner.sessions.test_injection import (
    inject_test_sessions,
    resolve_test_weeks,
)


def _week(week: int, phase: PhaseType, mode: WeekMode) -> WeekSpec:
    return WeekSpec(
        week=week,
        phase=phase,
        mode=mode,
        sport_split={
            SportType.RUNNING: 0.34,
            SportType.CYCLING: 0.33,
            SportType.SWIMMING: 0.33,
        },
        effective_goal=GoalType.IRONMAN,
        owning_goal=None,
    )


def _phase_plan(n_weeks: int = 16, recovery_every: int = 4) -> PhasePlan:
    """Build a synthetic 16-week plan with a recovery week every Nth.

    Weeks 0, 1, 2 load; week 3 recovery; weeks 4, 5, 6 load; week 7 recovery; …
    """
    weeks = []
    for i in range(n_weeks):
        mode = WeekMode.RECOVERY if (i + 1) % recovery_every == 0 else WeekMode.LOAD
        weeks.append(_week(i, PhaseType.BASE, mode))
    goal = RaceGoal(
        goal_type=GoalType.IRONMAN,
        race_date=date(2026, 6, 1),
        priority="A",
    )
    return PhasePlan(weeks=weeks, race_events=[], a_goal=goal)


def _anchor_session(
    week: int,
    sport: SportType,
    activity_key: ActivityKey,
    target_hours: float,
    slot_suffix: str = "",
) -> PlacedSession:
    return PlacedSession(
        slot_id=f"{sport.value}_{week}_{slot_suffix or activity_key.value}",
        activity_key=activity_key,
        sport=sport,
        origin=SessionOrigin.ANCHOR,
        week=week,
        day_of_week=None,
        target_hours=target_hours,
    )


ALL_SPORTS = (SportType.CYCLING, SportType.RUNNING, SportType.SWIMMING)


class TestResolveTestWeeks:
    def test_explicit_weeks_verbatim(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(0, 7, 15))
        assert resolve_test_weeks(plan, protocol) == [0, 7, 15]

    def test_explicit_weeks_drops_out_of_range(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(0, 99, 15))
        assert resolve_test_weeks(plan, protocol) == [0, 15]

    def test_explicit_weeks_dedups_and_sorts(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(7, 0, 7, 15, 0))
        assert resolve_test_weeks(plan, protocol) == [0, 7, 15]

    def test_cadence_every_recovery_week(self) -> None:
        plan = _phase_plan(n_weeks=16)  # recoveries at 3, 7, 11, 15
        protocol = TestProtocol(sports=ALL_SPORTS, every_n_recovery_weeks=1)
        assert resolve_test_weeks(plan, protocol) == [3, 7, 11, 15]

    def test_cadence_every_second_recovery_week(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, every_n_recovery_weeks=2)
        # Every 2nd recovery: indices 1 and 3 in the recovery sequence = wk7, wk15
        assert resolve_test_weeks(plan, protocol) == [7, 15]

    def test_cadence_with_baseline_adds_week_zero(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(
            sports=ALL_SPORTS,
            every_n_recovery_weeks=2,
            include_baseline=True,
        )
        assert resolve_test_weeks(plan, protocol) == [0, 7, 15]

    def test_a_replan_counts_from_the_plans_own_start(self) -> None:
        # Trained weeks -4..-1 (recovery at -1), then weeks 0..11 (recoveries
        # 3, 7, 11): the 2nd and 4th recovery of the plan are weeks 3 and 11,
        # and week 0 is no baseline, the plan opened four weeks ago.
        plan = _phase_plan(n_weeks=12)
        prior = tuple(
            _week(i, PhaseType.BASE, WeekMode.RECOVERY if i == -1 else WeekMode.LOAD)
            for i in range(-4, 0)
        )
        replanned = PhasePlan(
            weeks=plan.weeks,
            race_events=[],
            a_goal=plan.a_goal,
            prior_weeks=prior,
        )
        protocol = TestProtocol(
            sports=ALL_SPORTS, every_n_recovery_weeks=2, include_baseline=True
        )
        assert resolve_test_weeks(replanned, protocol) == [3, 11]
        every = TestProtocol(sports=ALL_SPORTS, every_n_recovery_weeks=1)
        assert resolve_test_weeks(replanned, every) == [3, 7, 11]

    def test_no_schedule_returns_empty(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS)  # neither weeks nor cadence
        assert resolve_test_weeks(plan, protocol) == []

    def test_include_baseline_noop_when_week_zero_out_of_plan(self) -> None:
        # Degenerate: empty plan — include_baseline silently skips.
        goal = RaceGoal(
            goal_type=GoalType.IRONMAN,
            race_date=date(2026, 6, 1),
            priority="A",
        )
        plan = PhasePlan(weeks=[], race_events=[], a_goal=goal)
        protocol = TestProtocol(
            sports=ALL_SPORTS,
            every_n_recovery_weeks=1,
            include_baseline=True,
        )
        assert resolve_test_weeks(plan, protocol) == []


class TestInjectTestSessions:
    def test_none_protocol_returns_unchanged_copy(self) -> None:
        plan = _phase_plan(n_weeks=8)
        sessions = [
            _anchor_session(0, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 2.0),
        ]
        result = inject_test_sessions(plan, sessions, None)
        assert result == sessions
        assert result is not sessions  # new list

    def test_empty_sports_returns_unchanged_copy(self) -> None:
        plan = _phase_plan(n_weeks=8)
        sessions = [
            _anchor_session(0, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 2.0),
        ]
        protocol = TestProtocol(weeks=(0,))  # sports empty
        result = inject_test_sessions(plan, sessions, protocol)
        assert result == sessions

    def test_no_resolved_weeks_returns_unchanged_copy(self) -> None:
        plan = _phase_plan(n_weeks=8)
        sessions = [
            _anchor_session(0, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 2.0),
        ]
        # Out-of-range week is silently dropped, leaving no schedule.
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(99,))
        result = inject_test_sessions(plan, sessions, protocol)
        assert result == sessions

    def test_emits_one_test_per_sport_per_week(self) -> None:
        plan = _phase_plan(n_weeks=16)
        sessions = [
            _anchor_session(7, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 2.0),
            _anchor_session(7, SportType.RUNNING, ActivityKey.RUN_EASY, 0.75),
            _anchor_session(7, SportType.SWIMMING, ActivityKey.SWIM_ENDURANCE, 1.0),
        ]
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(7,))
        result = inject_test_sessions(plan, sessions, protocol)

        tests = [s for s in result if s.origin == SessionOrigin.TEST]
        assert len(tests) == 3
        assert {t.activity_key for t in tests} == {
            ActivityKey.BIKE_FTP_TEST,
            ActivityKey.RUN_THRESHOLD_TEST,
            ActivityKey.SWIM_CSS_TEST,
        }
        assert all(t.week == 7 for t in tests)

    def test_tests_have_locked_duration(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(7,))
        result = inject_test_sessions(plan, [], protocol)
        for t in result:
            assert t.origin == SessionOrigin.TEST
            assert t.min_hours == t.target_hours == t.max_hours
            assert t.target_hours > 0

    def test_default_days_applied(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(7,))
        result = inject_test_sessions(plan, [], protocol)
        by_sport = {t.sport: t.day_of_week for t in result}
        assert by_sport[SportType.CYCLING] == 1  # Tue
        assert by_sport[SportType.RUNNING] == 2  # Wed
        assert by_sport[SportType.SWIMMING] == 4  # Fri

    def test_custom_days_override_defaults(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(
            sports=ALL_SPORTS,
            weeks=(7,),
            day_by_sport=(
                (SportType.CYCLING, 3),
                (SportType.RUNNING, 0),
            ),
        )
        result = inject_test_sessions(plan, [], protocol)
        by_sport = {t.sport: t.day_of_week for t in result}
        assert by_sport[SportType.CYCLING] == 3  # override
        assert by_sport[SportType.RUNNING] == 0  # override
        assert by_sport[SportType.SWIMMING] == 4  # default unchanged

    def test_sports_subset(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(7,))
        result = inject_test_sessions(plan, [], protocol)
        assert len(result) == 1
        assert result[0].activity_key == ActivityKey.BIKE_FTP_TEST

    def test_displaces_shortest_same_sport_session(self) -> None:
        plan = _phase_plan(n_weeks=16)
        # Three cycling sessions at wk7 with varying durations.
        # The injector should drop the shortest (0.5h) and keep the longer ones.
        long_ride = _anchor_session(
            7, SportType.CYCLING, ActivityKey.BIKE_LONG, 3.0, "long"
        )
        medium_ride = _anchor_session(
            7, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 2.0, "med"
        )
        short_ride = _anchor_session(
            7, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 0.5, "short"
        )
        sessions = [long_ride, medium_ride, short_ride]

        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(7,))
        result = inject_test_sessions(plan, sessions, protocol)

        # Three cycling sessions remain: long + medium + test (short was dropped).
        cycling = [s for s in result if s.sport == SportType.CYCLING]
        assert len(cycling) == 3
        assert long_ride in cycling
        assert medium_ride in cycling
        assert short_ride not in cycling
        assert any(s.origin == SessionOrigin.TEST for s in cycling)

    def test_displaces_only_anchor_origin(self) -> None:
        plan = _phase_plan(n_weeks=16)
        # Pre-existing test session (simulating a prior test_placer run).
        existing_test = PlacedSession(
            slot_id="pre_existing_test",
            activity_key=ActivityKey.BIKE_FTP_TEST,
            sport=SportType.CYCLING,
            origin=SessionOrigin.TEST,
            week=7,
            day_of_week=1,
            target_hours=0.42,
            min_hours=0.42,
            max_hours=0.42,
        )
        short_anchor = _anchor_session(
            7, SportType.CYCLING, ActivityKey.BIKE_ENDURANCE, 0.3, "short"
        )
        sessions = [existing_test, short_anchor]

        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(7,))
        result = inject_test_sessions(plan, sessions, protocol)

        # The anchor (0.3h) must be dropped, not the existing test.
        assert existing_test in result
        assert short_anchor not in result

    def test_no_matching_sport_still_adds_test(self) -> None:
        plan = _phase_plan(n_weeks=16)
        # Week has only running sessions; cycling test has no displacement target.
        sessions = [
            _anchor_session(7, SportType.RUNNING, ActivityKey.RUN_EASY, 1.0),
        ]
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(7,))
        result = inject_test_sessions(plan, sessions, protocol)

        # Running session untouched; FTP test added.
        assert len(result) == 2
        assert any(s.activity_key == ActivityKey.BIKE_FTP_TEST for s in result)

    def test_stable_slot_ids(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=ALL_SPORTS, weeks=(7,))
        result = inject_test_sessions(plan, [], protocol)
        slot_ids = {s.slot_id for s in result}
        assert slot_ids == {
            "test_cycling_wk7",
            "test_running_wk7",
            "test_swimming_wk7",
        }


def _capacity(weekly_ceiling: dict[int, float]) -> CapacityProjection:
    return CapacityProjection(weekly_ceiling=weekly_ceiling, weekly_ceiling_by_sport={})


class TestInjectTestSessionsWithCapacity:
    """``capacity``-driven deferral of an infeasible week's battery."""

    def test_none_capacity_disables_deferral(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(3,))
        result = inject_test_sessions(plan, [], protocol, capacity=None)
        assert {t.week for t in result} == {3}

    def test_fits_at_original_week_stays_put(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(7,))
        capacity = _capacity({7: 1.0})
        result = inject_test_sessions(plan, [], protocol, capacity=capacity)
        assert {t.week for t in result} == {7}

    def test_infeasible_week_defers_to_next_week_with_room(self) -> None:
        plan = _phase_plan(n_weeks=16)  # week 3 RECOVERY, week 4 LOAD
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(3,))
        capacity = _capacity({3: 0.5, 4: 1.0})
        result = inject_test_sessions(plan, [], protocol, capacity=capacity)
        assert {t.week for t in result} == {4}

    def test_defer_skips_week_reserved_by_another_battery(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(3, 4))
        # Week 3 is infeasible and would otherwise defer to week 4, but
        # week 4 already has its own battery — week 3 must skip past it.
        capacity = _capacity({3: 0.5, 4: 1.0, 5: 1.0})
        result = inject_test_sessions(plan, [], protocol, capacity=capacity)
        assert {t.week for t in result} == {4, 5}

    def test_defer_skips_week_with_native_test_for_same_sport(self) -> None:
        plan = _phase_plan(n_weeks=16)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(3,))
        native_test = PlacedSession(
            slot_id="menu_test_cycling_wk4",
            activity_key=ActivityKey.BIKE_FTP_TEST,
            sport=SportType.CYCLING,
            origin=SessionOrigin.TEST,
            week=4,
            day_of_week=1,
            target_hours=0.5,
            min_hours=0.5,
            max_hours=0.5,
        )
        capacity = _capacity({3: 0.5, 4: 1.0, 5: 1.0})
        result = inject_test_sessions(plan, [native_test], protocol, capacity=capacity)
        tests = [s for s in result if s.origin == SessionOrigin.TEST]
        assert {t.week for t in tests} == {4, 5}

    def test_defer_never_lands_in_taper_race_or_race_recovery(self) -> None:
        weeks = [
            _week(0, PhaseType.BASE, WeekMode.LOAD),
            _week(1, PhaseType.TAPER, WeekMode.TAPER),
            _week(2, PhaseType.TAPER, WeekMode.RACE),
            _week(3, PhaseType.BASE, WeekMode.LOAD),
        ]
        goal = RaceGoal(
            goal_type=GoalType.IRONMAN, race_date=date(2026, 6, 1), priority="A"
        )
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=goal)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(0,))
        # Weeks 1 and 2 have room, but must be skipped for their mode;
        # only week 3 (LOAD) is a valid deferral target.
        capacity = _capacity({0: 0.5, 1: 1.0, 2: 1.0, 3: 1.0})
        result = inject_test_sessions(plan, [], protocol, capacity=capacity)
        assert {t.week for t in result} == {3}

    def test_no_feasible_later_week_keeps_original_slot(self) -> None:
        plan = _phase_plan(n_weeks=8)
        protocol = TestProtocol(sports=(SportType.CYCLING,), weeks=(3,))
        capacity = _capacity(dict.fromkeys(range(8), 0.1))
        result = inject_test_sessions(plan, [], protocol, capacity=capacity)
        assert {t.week for t in result} == {3}


class TestIntegrationWithPipeline:
    def test_scenario_with_protocol_produces_tests(self) -> None:
        """End-to-end smoke: a test protocol flows through generate_plan."""
        from endury.planner import (
            PlanRequest,
            SchedulePreferences,
            generate_plan,
        )

        goals = [
            RaceGoal(
                goal_type=GoalType.IRONMAN,
                race_date=date(2026, 6, 1) + _days(weeks=36),
                priority="A",
            )
        ]
        protocol = TestProtocol(
            sports=ALL_SPORTS,
            weeks=(0, 7, 15, 23, 31),
        )
        result = generate_plan(
            PlanRequest(
                goals=goals,
                training_start=date(2026, 6, 1),
                schedule=SchedulePreferences(test_protocol=protocol),
            )
        )
        sessions = result.sessions
        tests = [s for s in sessions if s.origin == SessionOrigin.TEST]
        # Protocol injects tests at 5 explicit weeks (3 sports each = 15),
        # plus recovery-week menus include test specs — total exceeds 15.
        assert len(tests) >= 15
        assert sum(1 for t in tests if t.sport == SportType.CYCLING) >= 5
        assert sum(1 for t in tests if t.sport == SportType.RUNNING) >= 5
        assert sum(1 for t in tests if t.sport == SportType.SWIMMING) >= 5

    def test_scenario_without_protocol_has_menu_tests(self) -> None:
        """Without explicit protocol, recovery-week menus still emit tests."""
        from endury.planner import PlanRequest, generate_plan

        goals = [
            RaceGoal(
                goal_type=GoalType.IRONMAN,
                race_date=date(2026, 6, 1) + _days(weeks=36),
                priority="A",
            )
        ]
        result = generate_plan(
            PlanRequest(goals=goals, training_start=date(2026, 6, 1))
        )
        sessions = result.sessions
        tests = [s for s in sessions if s.origin == SessionOrigin.TEST]
        assert len(tests) > 0, "Recovery menus should include test sessions"


def _days(*, weeks: int) -> object:
    """Return a timedelta of N weeks (small helper to keep tests terse)."""
    from datetime import timedelta

    return timedelta(weeks=weeks)


@pytest.fixture(autouse=True)
def _ensure_registry() -> None:
    """Ensure the activity registry is loaded for integration tests."""
    from endury.planner.activity.registry import ensure_registry_loaded

    ensure_registry_loaded()


def test_test_hours_match_protocol_cards() -> None:
    """Booked test hours must track the registry's fixed-protocol cards.

    R-SESS-030: the locked QP bounds equal the workout the athlete
    executes (swim at the default CSS, matching the generator's floor
    probe). A protocol edit that changes a card duration must show up
    here as a deliberate `_TEST_HOURS` update.
    """
    from endury.planner.activity import ensure_registry_loaded, get_activity
    from endury.planner.sessions.test_injection import (
        _TEST_ACTIVITY_KEY,
        _TEST_HOURS,
    )

    ensure_registry_loaded()
    for sport, key in _TEST_ACTIVITY_KEY.items():
        activity = get_activity(key)
        card_hours = activity.duration_minutes_exact(activity.tss_min) / 60.0
        assert _TEST_HOURS[sport] == pytest.approx(card_hours, abs=0.01), (
            f"{key.value} card is {card_hours:.3f}h but _TEST_HOURS books "
            f"{_TEST_HOURS[sport]:.3f}h"
        )
