"""Tests for endury.coach.card_summary — the Apply card's athlete-facing line."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from endury.coach.card_summary import CARD_LINES, preview_summary
from endury.coach.proposals import is_gated
from endury.coach.tools import make_tools


def test_every_gated_tool_has_a_card_line():
    tools = make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
        repos=MagicMock(),
        intervals_client=MagicMock(),
    )
    gated = {tool.name for tool in tools if is_gated(tool)}
    assert gated == set(CARD_LINES)


# ---------------------------------------------------------------------------
# pause_training
# ---------------------------------------------------------------------------


def test_pause_names_the_days_the_count_and_the_return():
    line = preview_summary(
        "pause_training",
        {
            "status": "preview",
            "window": {"from": "2026-09-24", "through": "2026-09-27"},
            "resume_date": "2026-09-28",
            "sessions": [{"session_id": n} for n in range(5)],
        },
    )
    assert line == (
        "Pause training from Thursday 24 September to Sunday 27 September "
        "(5 sessions skipped) and resume on Monday 28 September."
    )


def test_a_one_day_pause_with_one_session_reads_singular():
    line = preview_summary(
        "pause_training",
        {
            "status": "preview",
            "window": {"from": "2026-09-24", "through": "2026-09-24"},
            "resume_date": "2026-09-25",
            "sessions": [{"session_id": 1}],
        },
    )
    assert line == (
        "Pause training on Thursday 24 September (1 session skipped) and "
        "resume on Friday 25 September."
    )


def test_a_booking_only_pause_names_the_bookings_it_removes():
    line = preview_summary(
        "pause_training",
        {
            "status": "preview",
            "window": {"from": "2026-09-24", "through": "2026-09-27"},
            "sessions": [],
            "ad_hoc": {
                "status": "preview",
                "workouts": [
                    {"date": "2026-09-25", "name": "Easy Shakeout"},
                    {"date": "2026-09-26", "name": ""},
                ],
            },
        },
    )
    assert line == (
        "Pause training from Thursday 24 September to Sunday 27 September and "
        "take the Easy Shakeout booked for Friday 25 September and the workout "
        "booked for Saturday 26 September off the calendar."
    )


def test_a_plan_less_pause_reads_its_bookings_flat():
    line = preview_summary(
        "pause_training",
        {
            "status": "preview",
            "no_plan": True,
            "window": {"from": "2026-09-24", "through": "2026-09-27"},
            "workouts": [{"date": "2026-09-25", "name": "Easy Shakeout"}],
        },
    )
    assert "take the Easy Shakeout booked for Friday 25 September off" in line


def test_an_empty_window_with_a_plan_marks_the_days_unavailable():
    line = preview_summary(
        "pause_training",
        {
            "status": "empty",
            "window": {"from": "2026-09-24", "through": "2026-09-27"},
            "message": "Nothing between 2026-09-24 and 2026-09-27 is pending.",
        },
    )
    assert line == (
        "Mark yourself unavailable from Thursday 24 September to Sunday "
        "27 September. Nothing is planned then, so no sessions change."
    )


# ---------------------------------------------------------------------------
# swap_planned_session / adjust_current_week
# ---------------------------------------------------------------------------


def _swap(date="2026-05-21"):
    return {
        "status": "preview",
        "date": date,
        "current": {"activity_key": "run_easy", "date": "2026-05-21"},
        "replacement": {
            "workout_name": "Threshold Run",
            "duration_minutes": 70.0,
            "achieved_tss": 88.0,
        },
        "week": {"before_tss": 400.0, "after_tss": 440.0, "tss_delta_percent": 10.0},
    }


def test_swap_names_both_workouts_and_the_weeks_load():
    assert preview_summary("swap_planned_session", _swap()) == (
        "Swap the Easy Run on Thursday 21 May for a 70-minute Threshold Run. "
        "The week's load goes from 400 to 440 load."
    )


def test_swap_with_a_new_date_says_where_it_moves():
    line = preview_summary("swap_planned_session", _swap(date="2026-05-23"))
    assert line.startswith(
        "Swap the Easy Run on Thursday 21 May for a 70-minute Threshold Run, "
        "moved to Saturday 23 May."
    )


def test_adjust_says_the_cut_and_the_weeks_minutes():
    line = preview_summary(
        "adjust_current_week",
        {
            "status": "preview",
            "reduce_percent": 33,
            "sessions": [{"session_id": 1}, {"session_id": 2}],
            "totals": {"before_minutes": 300.0, "after_minutes": 200.0},
        },
    )
    assert line == (
        "Shorten this week's 2 remaining sessions by 33%. The week goes from "
        "300 to 200 minutes."
    )


def test_adjust_with_zero_restores_the_planned_length():
    line = preview_summary(
        "adjust_current_week",
        {
            "status": "preview",
            "reduce_percent": 0,
            "sessions": [{"session_id": 1}],
            "totals": {"before_minutes": 200.0, "after_minutes": 300.0},
        },
    )
    assert line == (
        "Restore this week's 1 remaining session to full length. The week goes "
        "from 200 to 300 minutes."
    )


# ---------------------------------------------------------------------------
# set_plan_directives / replan_active_plan
# ---------------------------------------------------------------------------


def test_directives_read_as_what_is_in_force():
    line = preview_summary(
        "set_plan_directives",
        {
            "status": "preview",
            "directives": {
                "before": {},
                "after": {
                    "resume_date": "2026-10-12",
                    "unavailable": [
                        ["2026-10-01", "2026-10-05"],
                        ["2026-10-20", "2026-10-20"],
                    ],
                    "suppress_tests_until": "2026-10-18",
                    "weekly_hours_cap": 8.0,
                },
            },
            "removes": [
                {"date": "2026-10-02"},
                {"date": "2026-10-03"},
                {"date": "2026-10-05"},
            ],
            "edited_workouts": [{"external_id": "coachie:7:w03"}],
        },
    )
    assert line == (
        "Replan from next Monday with a return to training on Monday 12 October, "
        "time away from Thursday 1 October to Monday 5 October and on Tuesday "
        "20 October, no fitness tests before Sunday 18 October and a cap of 8 "
        "hours a week. This drops 3 upcoming sessions. It overwrites 1 workout "
        "you edited on intervals.icu."
    )


def test_cleared_directives_say_so():
    line = preview_summary(
        "set_plan_directives",
        {"status": "preview", "directives": {"before": {}, "after": {}}, "removes": []},
    )
    assert line == "Replan from next Monday with the earlier constraints cleared."


def test_replan_is_plain_and_counts_edited_workouts():
    assert preview_summary(
        "replan_active_plan", {"status": "preview", "edited_workouts": []}
    ) == ("Replan everything from next Monday onward using your latest training data.")
    line = preview_summary(
        "replan_active_plan",
        {
            "status": "preview",
            "edited_workouts": [{"external_id": "a"}, {"external_id": "b"}],
        },
    )
    assert line.endswith(" It overwrites 2 workouts you edited on intervals.icu.")


# ---------------------------------------------------------------------------
# unschedule_workout / add_race_to_plan / create_plan / schedule_workout_override
# ---------------------------------------------------------------------------


def test_unschedule_names_every_booking():
    line = preview_summary(
        "unschedule_workout",
        {
            "status": "preview",
            "workouts": [
                {"date": "2026-06-01", "name": "Easy Run"},
                {"date": "2026-06-03", "name": "Threshold Run"},
            ],
        },
    )
    assert line == (
        "Take the Easy Run booked for Monday 1 June and the Threshold Run "
        "booked for Wednesday 3 June off the calendar."
    )


@pytest.mark.parametrize(
    ("fits_as", "phrase"), [("B", "as a B race"), ("A", "as an A race")]
)
def test_add_race_names_the_race_and_its_priority(fits_as, phrase):
    line = preview_summary(
        "add_race_to_plan",
        {
            "status": "preview",
            "race_name": "City Half",
            "race_date": "2026-10-04",
            "fits_as": fits_as,
        },
    )
    assert line == (
        f"Add City Half on Sunday 4 October to the plan {phrase} and replan "
        "from next Monday."
    )


def _create(goal_type="running_marathon", goal="marathon", replaces=None):
    return {
        "status": "preview",
        "goal": goal,
        "races": [
            {"goal_type": goal_type, "race_date": "2026-11-08", "priority": "A"},
            {"goal_type": "running_10k", "race_date": "2026-09-13", "priority": "B"},
        ],
        "weeks": 25,
        "plan_start": "2026-05-18",
        "replaces": replaces,
    }


def test_create_plan_spans_start_to_race_day():
    assert preview_summary("create_plan", _create()) == (
        "Build a 25-week marathon plan from Monday 18 May to race day on "
        "Sunday 8 November."
    )


def test_create_plan_says_when_it_replaces_the_current_one():
    line = preview_summary("create_plan", _create(replaces={"plan_id": 3}))
    assert line.endswith(" It replaces your current plan.")


def test_a_maintenance_block_has_no_race_day():
    line = preview_summary(
        "create_plan", _create("running_maintenance", "running maintenance")
    )
    assert line == "Build a 25-week running maintenance block from Monday 18 May."


def test_override_names_the_workout_the_collision_and_the_week():
    line = preview_summary(
        "schedule_workout_override",
        {
            "status": "preview",
            "date": "2026-06-01",
            "workout": {"workout_name": "Threshold Run", "duration_minutes": 45.0},
            "plan_collision": {"session_id": 4, "activity_key": "run_easy"},
            "plan_row": {"session_id": None},
            "week": {"before_tss": 400.0, "after_tss": 462.0},
        },
    )
    assert line == (
        "Add a 45-minute Threshold Run on Monday 1 June alongside the Easy Run "
        "already planned that day. The week's load goes from 400 to 462 load."
    )


def test_a_standalone_override_is_one_sentence():
    line = preview_summary(
        "schedule_workout_override",
        {
            "status": "preview",
            "date": "2026-06-01",
            "workout": {"workout_name": "Long Run", "duration_minutes": 120.0},
            "plan_collision": None,
        },
    )
    assert line == "Add a 120-minute Long Run on Monday 1 June."


def test_a_skip_card_names_the_session_and_the_calendar():
    parsed = {
        "status": "preview",
        "session": {
            "session_id": 657,
            "date": "2026-10-24",
            "activity_key": "bike_long",
        },
    }
    line = preview_summary("skip_planned_session", parsed)
    assert line.startswith("Skip the ")
    assert "Saturday 24 October" in line
    assert line.endswith("and take it off your calendar.")


def test_two_skips_in_one_reply_are_two_cards():
    from endury.coach.proposals import card_key

    assert card_key("skip_planned_session", {"session_id": 657}) != card_key(
        "skip_planned_session", {"session_id": 659}
    )
