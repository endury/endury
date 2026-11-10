"""Tests for endury.intervals.push."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from endury.intervals.push import (
    EventDiff,
    event_start,
    plan_to_events,
    readback_diff,
    session_event_plan_id,
    stale_external_ids,
)
from endury.intervals.workout_dsl import SportThresholds
from endury.planner.foundations.types import (
    GoalType,
    RaceGoal,
    SportType,
    is_maintenance_goal,
)
from endury.planning.wire.types import (
    SerializedSessionPayload,
    WorkoutStepPayload,
    WorkoutTargetPayload,
)


def _step(
    *,
    step_type: str = "interval",
    duration_seconds: float = 600.0,
    duration_meters: float = 0.0,
    target: WorkoutTargetPayload | None = None,
    description: str = "",
    steps: list[WorkoutStepPayload] | None = None,
    iterations: int | None = None,
) -> WorkoutStepPayload:
    """Build a minimal WorkoutStepPayload dict."""
    payload: WorkoutStepPayload = {
        "type": step_type,
        "description": description,
        "duration_seconds": duration_seconds,
        "duration_meters": duration_meters,
    }
    if target is not None:
        payload["target"] = target
    if steps is not None:
        payload["steps"] = steps
    if iterations is not None:
        payload["iterations"] = iterations
    return payload


def _session(
    *,
    slot_id: str = "w01-run-easy",
    session_date: str = "2026-08-10",
    sport: str = "running",
    activity_key: str = "run_easy",
    family: str = "easy",
    coaching_intent: str = "Keep it conversational.",
    steps: list[WorkoutStepPayload] | None = None,
    workout_name: str = "Easy Run",
) -> SerializedSessionPayload:
    """Build a minimal SerializedSessionPayload dict."""
    return {
        "slot_id": slot_id,
        "date": session_date,
        "week_index": 1,
        "phase_type": "base",
        "sport": sport,
        "activity_key": activity_key,
        "family": family,
        "is_recovery_week": False,
        "coaching_intent": coaching_intent,
        "target_tss": 50.0,
        "achieved_tss": 50.0,
        "duration_minutes": 45.0,
        "distance_meters": 8000.0,
        "hardness": {
            "is_hard_overall": False,
            "is_hard_physiologically": False,
            "is_hard_mentally": False,
        },
        "workout": {
            "name": workout_name,
            "steps": steps
            if steps is not None
            else [
                _step(
                    step_type="interval",
                    duration_seconds=2700.0,
                    target={
                        "target_type": "heart_rate",
                        "low_bpm": 130,
                        "high_bpm": 145,
                    },
                )
            ],
        },
        "duration_target_minutes": 45.0,
        "tid_fractions": None,
    }


_THRESHOLDS: dict[SportType, SportThresholds] = {
    SportType.RUNNING: SportThresholds(lthr_bpm=170, threshold_pace_sec_per_km=270.0),
    SportType.CYCLING: SportThresholds(ftp_watts=250),
    SportType.SWIMMING: SportThresholds(css_sec_per_100m=95.0),
}


class _FakeEvent:
    """Minimal stand-in for IntervalsEvent, duck-typed for readback_diff."""

    def __init__(
        self,
        external_id: str,
        start_date_local: str,
        name: str,
        description: str,
    ) -> None:
        self.external_id = external_id
        self.start_date_local = start_date_local
        self.name = name
        self.description = description


class _FakeClient:
    """Minimal stand-in for IntervalsClient, duck-typed for readback_diff."""

    def __init__(self, events: list[_FakeEvent]) -> None:
        self._events = events

    def list_events(self, oldest: date, newest: date) -> list[_FakeEvent]:
        del oldest, newest
        return self._events


# plan_to_events — WORKOUT events


def test_every_pushed_event_carries_a_midnight_timestamp() -> None:
    # Live contract: intervals.icu parses start_date_local as a LocalDateTime
    # and rejects a bare "2026-08-10" with 422 Invalid start date.
    assert event_start(date(2026, 8, 10)) == "2026-08-10T00:00:00"
    assert event_start("2026-08-10") == "2026-08-10T00:00:00"

    events = plan_to_events(
        plan_id=1,
        sessions=[_session()],
        goals=[RaceGoal(GoalType.RUNNING_MARATHON, date(2026, 9, 1), "A")],
        thresholds_by_sport=_THRESHOLDS,
    )
    assert len(events) == 2  # the session plus its race
    for event in events:
        assert event["start_date_local"].endswith("T00:00:00")


def test_session_event_plan_id_reads_only_a_session_event() -> None:
    """The plan a session event belongs to; nothing for any other Endury id."""
    assert session_event_plan_id("coachie:6:w0_mon") == 6
    assert session_event_plan_id("coachie:adhoc:2026-02-12:ab12cd34") is None
    assert session_event_plan_id("coachie:store") is None
    assert session_event_plan_id("") is None


def test_session_event_shape_and_external_id() -> None:
    sessions = [_session()]
    events = plan_to_events(
        plan_id=7, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert len(events) == 1
    event = events[0]
    assert event["category"] == "WORKOUT"
    assert event["external_id"] == "coachie:7:w01-run-easy"
    assert event["start_date_local"] == "2026-08-10T00:00:00"
    assert event["type"] == "Run"
    assert event["tags"] == ["endury"]
    assert "moving_time" not in event
    assert "load" not in event


def test_session_event_name_uses_the_workout_display_name() -> None:
    # The catalog display name (session["workout"]["name"]) is the seam the
    # app's UI already renders — the pushed event must show the same title
    # rather than a title-cased activity_key derivation.
    sessions = [
        _session(
            activity_key="run_trail_long",
            family="long",
            workout_name="Long Trail Run",
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert events[0]["name"] == "Long Trail Run"


def test_session_event_name_falls_back_to_derived_title_when_workout_name_missing() -> (
    None
):
    # Older rows may carry a blank workout name; fall back to deriving one
    # from the activity_key so the event never ships with an empty title.
    sessions = [_session(activity_key="run_easy", family="easy", workout_name="")]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert events[0]["name"] == "Easy"


def test_session_event_name_falls_back_to_family_when_activity_key_has_no_prefix() -> (
    None
):
    sessions = [_session(activity_key="", family="threshold", workout_name="")]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert events[0]["name"] == "Threshold"


@pytest.mark.parametrize(
    ("sport", "intervals_type"),
    [("running", "Run"), ("cycling", "Ride"), ("swimming", "Swim")],
)
def test_session_event_type_from_sport(sport: str, intervals_type: str) -> None:
    sessions = [
        _session(
            sport=sport,
            activity_key=f"{'run' if sport == 'running' else 'bike' if sport == 'cycling' else 'swim'}_easy",
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert events[0]["type"] == intervals_type


def test_session_event_description_is_intent_blank_line_then_dsl() -> None:
    sessions = [
        _session(
            coaching_intent="Build aerobic base.",
            steps=[
                _step(
                    step_type="interval",
                    duration_seconds=1800.0,
                    target={
                        "target_type": "heart_rate",
                        "low_bpm": 130,
                        "high_bpm": 145,
                    },
                )
            ],
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    description = events[0]["description"]
    assert description.startswith("Build aerobic base.\n\n")
    dsl_text = description.split("\n\n", 1)[1]
    assert dsl_text == "- 30m 76-85% LTHR"


def test_session_event_description_carries_the_notes_and_the_why() -> None:
    session = _session(coaching_intent="Build aerobic base.")
    session["coaching_notes"] = [
        "<b>Fueling:</b> 80-100 g carbs / h from minute 30.",
        "<b>Vert:</b> aim for ~300-450 m of climbing.",
    ]
    session["why_details"] = ["Easy running builds the engine."]
    events = plan_to_events(
        plan_id=1, sessions=[session], goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert events[0]["description"] == (
        "Build aerobic base.\n\n"
        "**Fueling:** 80-100 g carbs / h from minute 30.\n"
        "**Vert:** aim for ~300-450 m of climbing.\n\n"
        "Easy running builds the engine.\n\n"
        "- 45m 76-85% LTHR"
    )


def test_only_a_fitness_test_carries_its_planned_load() -> None:
    # A test's main effort is RPE-only, which intervals.icu cannot price.
    test = _session(activity_key="run_threshold_test", family="test")
    test["achieved_tss"] = 64.6
    events = plan_to_events(
        plan_id=1,
        sessions=[test, _session()],
        goals=[],
        thresholds_by_sport=_THRESHOLDS,
    )

    assert events[0]["icu_training_load"] == 65
    assert "icu_training_load" not in events[1]


def test_session_event_description_has_no_leading_blank_when_intent_empty() -> None:
    sessions = [_session(coaching_intent="  ")]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    assert not events[0]["description"].startswith("\n")


def test_session_event_dsl_renders_repeat_block() -> None:
    sessions = [
        _session(
            sport="cycling",
            activity_key="bike_threshold",
            family="threshold",
            steps=[
                _step(
                    step_type="repeat",
                    duration_seconds=0.0,
                    iterations=3,
                    steps=[
                        _step(
                            step_type="interval",
                            duration_seconds=300.0,
                            target={
                                "target_type": "power",
                                "low_watts": 225,
                                "high_watts": 250,
                            },
                        )
                    ],
                )
            ],
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    dsl_text = events[0]["description"].split("\n\n", 1)[1]
    assert dsl_text == "3x\n- 5m 90-100%"


def test_session_event_distance_step_converts_to_dsl_meters() -> None:
    sessions = [
        _session(
            sport="swimming",
            activity_key="swim_css",
            family="threshold",
            steps=[
                _step(step_type="interval", duration_seconds=0.0, duration_meters=400.0)
            ],
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=[], thresholds_by_sport=_THRESHOLDS
    )

    dsl_text = events[0]["description"].split("\n\n", 1)[1]
    assert dsl_text == "- 400mtr"


# plan_to_events — RACE events


def test_race_events_built_from_goals_in_order() -> None:
    goals = [
        RaceGoal(goal_type=GoalType.IRONMAN, race_date=date(2026, 9, 1), priority="A"),
        RaceGoal(
            goal_type=GoalType.RUNNING_10K, race_date=date(2026, 7, 1), priority="B"
        ),
    ]
    events = plan_to_events(
        plan_id=42, sessions=[], goals=goals, thresholds_by_sport=_THRESHOLDS
    )

    assert len(events) == 2
    a_event, b_event = events
    assert a_event["category"] == "RACE_A"
    assert a_event["external_id"] == "coachie:42:race:A:2026-09-01"
    assert a_event["start_date_local"] == "2026-09-01T00:00:00"
    assert "Ironman" in a_event["name"]
    assert a_event["tags"] == ["endury"]
    # Multi-sport race: no combined type exists, but RACE_ events reject a
    # missing type outright (422), so it takes the neutral "Other".
    assert a_event["type"] == "Other"

    assert b_event["category"] == "RACE_B"
    assert b_event["external_id"] == "coachie:42:race:B:2026-07-01"
    assert b_event["start_date_local"] == "2026-07-01T00:00:00"
    assert b_event["type"] == "Run"


def test_every_race_event_names_a_type() -> None:
    """intervals.icu rejects a RACE_ event with no ``type``.

    ``422 type is required for category RACE_A`` fails the *whole* bulk
    push, so one untyped race silently strands the entire plan. Live API:
    ``Swim``/``Ride``/``Run``/``Other``/``Workout`` are accepted,
    ``Triathlon``/``MultiSport`` are ``422 Invalid type``.
    """
    racing_goals = [goal for goal in GoalType if not is_maintenance_goal(goal)]
    for goal_type in racing_goals:
        goal = RaceGoal(goal_type=goal_type, race_date=date(2026, 9, 1), priority="A")
        (event,) = plan_to_events(
            plan_id=1, sessions=[], goals=[goal], thresholds_by_sport=_THRESHOLDS
        )
        assert event["type"] in {"Run", "Ride", "Swim", "Other", "Workout"}, (
            f"{goal_type.value} pushes type={event.get('type')!r}"
        )


@pytest.mark.parametrize(
    "goal_type",
    [GoalType.TRIATHLON_MAINTENANCE, GoalType.RUNNING_MAINTENANCE],
)
def test_maintenance_block_pushes_no_race_event(goal_type: GoalType) -> None:
    """A maintenance goal's date is the block end, not a race.

    Its ``race_date`` is synthesized from the chosen block length, so a
    race event would be doubly wrong: it puts a fictitious A-Race on the
    athlete's calendar, and intervals.icu rejects the whole push with
    ``422 type is required for category RACE_A`` because a multi-sport
    goal names no activity type.
    """
    goals = [RaceGoal(goal_type=goal_type, race_date=date(2026, 9, 20), priority="A")]
    events = plan_to_events(
        plan_id=7, sessions=[], goals=goals, thresholds_by_sport=_THRESHOLDS
    )

    assert events == []


def test_race_external_ids_survive_a_dropped_same_priority_race() -> None:
    """Two B races: dropping the first must not remap the second onto it."""
    both = [
        RaceGoal(
            goal_type=GoalType.RUNNING_10K, race_date=date(2026, 6, 1), priority="B"
        ),
        RaceGoal(
            goal_type=GoalType.RUNNING_10K, race_date=date(2026, 7, 1), priority="B"
        ),
    ]
    all_ids = [
        event["external_id"]
        for event in plan_to_events(7, [], both, thresholds_by_sport=_THRESHOLDS)
    ]
    survivor_ids = [
        event["external_id"]
        for event in plan_to_events(7, [], both[1:], thresholds_by_sport=_THRESHOLDS)
    ]

    assert len(set(all_ids)) == 2
    assert survivor_ids == [all_ids[1]]


def test_plan_to_events_combines_sessions_and_races() -> None:
    sessions = [_session()]
    goals = [
        RaceGoal(
            goal_type=GoalType.RUNNING_MARATHON,
            race_date=date(2026, 10, 1),
            priority="A",
        )
    ]
    events = plan_to_events(
        plan_id=1, sessions=sessions, goals=goals, thresholds_by_sport=_THRESHOLDS
    )

    assert len(events) == 2
    assert events[0]["category"] == "WORKOUT"
    assert events[1]["category"] == "RACE_A"


# readback_diff


def test_readback_diff_detects_moved_event() -> None:
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "d",
        }
    ]
    remote = [_FakeEvent("coachie:1:w01", "2026-08-11", "Easy", "d")]
    client = _FakeClient(remote)

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert diffs == [
        EventDiff(
            external_id="coachie:1:w01",
            date="2026-08-10",
            name="Easy",
            changed_fields=("start_date_local",),
        )
    ]


def test_readback_diff_ignores_the_apis_datetime_serialization() -> None:
    """We push a bare date; intervals.icu reads it back as a LocalDateTime."""
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "d",
        }
    ]
    client = _FakeClient(
        [_FakeEvent("coachie:1:w01", "2026-08-10T00:00:00", "Easy", "d")]
    )

    assert readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31))) == []


def test_readback_diff_still_compares_unparsable_dates_literally() -> None:
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "d",
        }
    ]
    client = _FakeClient([_FakeEvent("coachie:1:w01", "whenever", "Easy", "d")])

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert diffs[0].changed_fields == ("start_date_local",)


def test_readback_diff_detects_edited_description() -> None:
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "original",
        }
    ]
    remote = [_FakeEvent("coachie:1:w01", "2026-08-10", "Easy", "athlete edited this")]
    client = _FakeClient(remote)

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert len(diffs) == 1
    assert diffs[0].changed_fields == ("description",)


def test_readback_diff_no_diff_when_unchanged() -> None:
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "same",
        }
    ]
    remote = [_FakeEvent("coachie:1:w01", "2026-08-10", "Easy", "same")]
    client = _FakeClient(remote)

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert diffs == []


def test_readback_diff_skips_events_with_no_remote_match() -> None:
    pushed = [
        {
            "external_id": "coachie:1:w01",
            "start_date_local": "2026-08-10",
            "name": "Easy",
            "description": "same",
        }
    ]
    client = _FakeClient([])

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert diffs == []


def test_readback_diff_detects_name_change_and_ignores_missing_external_id() -> None:
    pushed = [
        {"start_date_local": "2026-08-10", "name": "No id", "description": "x"},
        {
            "external_id": "coachie:1:w02",
            "start_date_local": "2026-08-12",
            "name": "Old Name",
            "description": "same",
        },
    ]
    remote = [_FakeEvent("coachie:1:w02", "2026-08-12", "New Name", "same")]
    client = _FakeClient(remote)

    diffs = readback_diff(client, 1, pushed, (date(2026, 8, 1), date(2026, 8, 31)))

    assert len(diffs) == 1
    assert diffs[0].external_id == "coachie:1:w02"
    assert diffs[0].changed_fields == ("name",)


# stale_external_ids


def test_stale_external_ids_returns_removed_sessions_only() -> None:
    old_sessions = [
        {"external_id": "coachie:1:w01"},
        {"external_id": "coachie:1:w02"},
        {"external_id": "coachie:1:race:0"},
    ]
    new_sessions = [
        {"external_id": "coachie:1:w01"},
        {"external_id": "coachie:1:race:0"},
    ]

    assert stale_external_ids(old_sessions, new_sessions) == ["coachie:1:w02"]


def test_stale_external_ids_empty_when_nothing_removed() -> None:
    sessions = [{"external_id": "coachie:1:w01"}]

    assert stale_external_ids(sessions, sessions) == []


def test_stale_external_ids_ignores_entries_without_external_id() -> None:
    old_sessions = [{"external_id": "coachie:1:w01"}, {"name": "no id"}]
    new_sessions: list[dict[str, Any]] = []

    assert stale_external_ids(old_sessions, new_sessions) == ["coachie:1:w01"]
