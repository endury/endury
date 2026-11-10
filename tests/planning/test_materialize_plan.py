"""Tests for materialize_sessions (PlanResult → API session payloads)."""

from __future__ import annotations

from datetime import date, timedelta

from endury.planner import PlanRequest, generate_plan
from endury.planner.activity import get_activity
from endury.planner.foundations.request import SchedulePreferences
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    RaceGoal,
    SessionOrigin,
    SportType,
    TestProtocol,
    WeekMode,
)
from endury.planning.materialize_plan import materialize_sessions

_MONDAY = date(2026, 1, 5)

_REQUIRED_KEYS = {
    "slot_id",
    "date",
    "week_index",
    "phase_type",
    "sport",
    "activity_key",
    "family",
    "is_recovery_week",
    "coaching_intent",
    "coaching_notes",
    "why_details",
    "target_tss",
    "achieved_tss",
    "duration_minutes",
    "distance_meters",
    "hardness",
    "workout",
    "duration_target_minutes",
    "tid_fractions",
}


def _ironman_result():
    goals = [
        RaceGoal(
            goal_type=GoalType.IRONMAN,
            race_date=_MONDAY + timedelta(weeks=28, days=-1),
            priority="A",
        )
    ]
    request = PlanRequest(
        goals=goals,
        training_start=_MONDAY,
        schedule=SchedulePreferences(
            test_protocol=TestProtocol(
                sports=(SportType.RUNNING, SportType.CYCLING, SportType.SWIMMING),
                weeks=(0,),
            ),
        ),
    )
    return generate_plan(request)


class TestMaterializeSessions:
    def test_ironman_plan_materializes_every_assignment(self) -> None:
        result = _ironman_result()
        payloads = materialize_sessions(result, None)
        assert len(payloads) == len(result.assignments)
        dates = [p["date"] for p in payloads]
        assert dates == sorted(dates)
        for payload in payloads:
            assert payload.keys() >= _REQUIRED_KEYS
            assert payload["duration_minutes"] > 0
            assert payload["target_tss"] > 0
            assert payload["achieved_tss"] > 0
            assert payload["workout"]["steps"], payload["slot_id"]
            tid = payload["tid_fractions"]
            assert tid is not None
            assert abs(tid["z_low"] + tid["z_mid"] + tid["z_high"] - 1.0) < 0.01

    def test_target_tss_respects_activity_bounds(self) -> None:
        result = _ironman_result()
        by_slot = {s.slot_id: s for s in result.sessions}
        for payload in materialize_sessions(result, None):
            placed = by_slot[payload["slot_id"]]
            activity = get_activity(placed.activity_key)
            assert activity.tss_min <= payload["target_tss"] <= activity.tss_max

    def test_recovery_week_flag_follows_week_mode(self) -> None:
        result = _ironman_result()
        recovery_weeks = {
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.RECOVERY
        }
        assert recovery_weeks, "expected recovery weeks in a 28-week plan"
        payloads = materialize_sessions(result, None)
        flagged = {p["week_index"] for p in payloads if p["is_recovery_week"]}
        assert flagged == {
            p["week_index"] for p in payloads if p["week_index"] in recovery_weeks
        }

    def test_test_sessions_get_test_intent(self) -> None:
        result = _ironman_result()
        test_slots = {
            s.slot_id for s in result.sessions if s.origin is SessionOrigin.TEST
        }
        assert test_slots, "expected an injected week-0 fitness test"
        payloads = {p["slot_id"]: p for p in materialize_sessions(result, None)}
        assigned_test_slots = test_slots & payloads.keys()
        assert assigned_test_slots, "test sessions should be day-assigned"
        for slot_id in assigned_test_slots:
            assert "time trial" in payloads[slot_id]["coaching_intent"].lower()
        regular = next(p for sid, p in payloads.items() if sid not in test_slots)
        assert "time trial" not in regular["coaching_intent"].lower()

    def test_every_session_carries_a_why_and_the_long_rides_carry_cues(self) -> None:
        payloads = materialize_sessions(_ironman_result(), None)

        for payload in payloads:
            why = payload["why_details"]
            assert 2 <= len(why) <= 3, payload["slot_id"]
            assert all(isinstance(paragraph, str) and paragraph for paragraph in why)
            assert why[-1][0].isupper()
            assert why[-1].endswith(".")

        long_rides = [
            p
            for p in payloads
            if p["sport"] == "cycling" and p["duration_minutes"] >= 180.0
        ]
        assert long_rides, "an ironman plan must contain 3h+ rides"
        assert all(
            any("Fueling" in note for note in p["coaching_notes"]) for p in long_rides
        )

    def test_swim_sessions_report_distance(self) -> None:
        result = _ironman_result()
        payloads = materialize_sessions(result, None)
        swim = [p for p in payloads if p["sport"] == "swimming"]
        assert swim, "ironman plan must contain swim sessions"
        assert any(p["distance_meters"] > 0 for p in swim)


def _small_runner_result():
    """0.9 h a week of running: too little for every session of the menus."""
    from endury.planner.foundations.request import AthleteState, SportState

    runner = AthleteState(sports={SportType.RUNNING: SportState(0.9)})
    return generate_plan(
        PlanRequest(
            goals=[
                RaceGoal(
                    GoalType.RUNNING_HALF_MARATHON,
                    date(2026, 6, 1) + timedelta(weeks=12, days=-1),
                    "A",
                )
            ],
            training_start=date(2026, 6, 1),
            athlete=runner,
        )
    )


def test_a_recurring_session_type_rotates_its_sets() -> None:
    """The second swim of a type gets the next set, not the first one again."""
    payloads = materialize_sessions(_ironman_result(), None)

    def repeat_block(payload):
        repeat = next(s for s in payload["workout"]["steps"] if s["type"] == "repeat")
        work = repeat["steps"][0]["duration"]
        return repeat["iterations"], work.get("value")

    for key in ("swim_css", "swim_threshold"):
        sessions = [p for p in payloads if p["activity_key"] == key]
        assert len(sessions) >= 2, key
        assert repeat_block(sessions[0]) != repeat_block(sessions[1]), key


def test_a_30_15_ride_keeps_its_15_second_recoveries() -> None:
    """A short step rounds to 5 s on the wire, so 30/15 is not pushed as 30/20."""
    from endury.planning.wire.workout_steps import _serialize_workout_step

    activity = get_activity(ActivityKey.BIKE_VO2MAX_SHORT)
    repeat = next(
        s for s in activity.build(activity.tss_max).steps if s.type == "repeat"
    )
    rep = _serialize_workout_step(repeat)["steps"]
    assert [step["duration_seconds"] for step in rep] == [30.0, 15.0]


def test_an_optional_session_says_so_in_its_title_and_why() -> None:
    """R-SESS-037: the card and the pushed event name an optional session."""
    from endury.planning.materialize_plan import OPTIONAL_TITLE_PREFIX

    result = _small_runner_result()
    optional = {s.slot_id: s for s in result.sessions if s.optional_reason}
    assert optional
    payloads = materialize_sessions(result, None)
    for p in payloads:
        is_optional = p["slot_id"] in optional
        assert p["workout"]["name"].startswith(OPTIONAL_TITLE_PREFIX) == is_optional
        if is_optional:
            assert p["why_details"][0] == optional[p["slot_id"]].optional_reason


def test_an_optional_session_is_stored_as_one_and_left_out_of_the_totals() -> None:
    """R-SESS-037: the flag survives the commit; totals and misses skip it."""
    from endury.planning.wire.sessions import (
        build_commit_session_rows,
        serialize_active_plan,
    )
    from endury.planning.wire.weekly import build_weekly_summary
    from endury.wiring.container import RepositoryContainer

    result = _small_runner_result()
    payloads = materialize_sessions(result, None)
    optional = {p["slot_id"] for p in payloads if p["optional"]}
    assert optional == {s.slot_id for s in result.sessions if s.optional_reason}

    repos = RepositoryContainer.in_memory()
    plan_id = repos.plan.create(
        name="Half",
        goal_type="running_half_marathon",
        goal_date="2026-08-23",
        plan_start="2026-06-01",
        solve_result_json="{}",
        solver_version="test",
        input_hash="x",
    )
    repos.plan.bulk_insert_sessions(plan_id, build_commit_session_rows(payloads))
    rows = repos.plan.get_sessions(plan_id)
    assert {r.slot_id for r in rows if r.optional} == optional

    # Every session lies in the past: the booked ones are missed, the
    # optional ones are not, and neither counts as a workout owed.
    plan = repos.plan.get_by_id(plan_id)
    compliance = serialize_active_plan(plan, rows, today=date(2027, 1, 1))["compliance"]
    assert compliance["total_workouts"] == len(rows) - len(optional)
    assert compliance["missed"] == len(rows) - len(optional)

    summary = build_weekly_summary(payloads, [], date(2026, 6, 1))
    assert sum(w["session_count"] for w in summary) == len(rows) - len(optional)
