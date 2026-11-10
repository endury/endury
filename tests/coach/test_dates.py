"""Tests for endury.coach.dates: the day names the coach reads instead of computing."""

import json
from datetime import date

from endury.coach.dates import (
    build_day_table,
    day_label,
    label_dates,
    resolve_date_args,
)

SATURDAY = date(2026, 9, 26)


# day_label


def test_day_label_names_the_near_days_relative_to_today():
    assert day_label(date(2026, 9, 25), SATURDAY) == "Friday, yesterday"
    assert day_label(SATURDAY, SATURDAY) == "Saturday, today"
    assert day_label(date(2026, 9, 27), SATURDAY) == "Sunday, tomorrow"


def test_day_label_puts_days_outside_this_week_in_their_week():
    """ "Monday" said on a Saturday is next week's: the label says so."""
    assert day_label(date(2026, 9, 28), SATURDAY) == "Monday, in 2 days, next week"
    assert day_label(date(2026, 9, 24), SATURDAY) == "Thursday, 2 days ago"
    assert day_label(date(2026, 9, 20), SATURDAY) == "Sunday, 6 days ago, last week"


def test_day_label_far_days_carry_only_the_weekday():
    assert day_label(date(2026, 10, 20), SATURDAY) == "Tuesday"
    assert day_label(date(2026, 8, 1), SATURDAY) == "Saturday"


# build_day_table


def test_day_table_runs_from_last_week_to_a_week_ahead():
    table = build_day_table(SATURDAY)
    assert table.startswith("## Dates\n")
    assert "- Sunday 2026-09-20 — 6 days ago, last week" in table
    assert "- Friday 2026-09-25 — yesterday" in table
    assert "- Saturday 2026-09-26 — today" in table
    assert "- Sunday 2026-09-27 — tomorrow" in table
    assert "- Saturday 2026-10-03 — in 7 days, next week" in table
    assert "2026-09-19" not in table
    assert "2026-10-04" not in table


def test_day_table_tells_the_coach_to_read_not_compute():
    assert "never work a date out yourself" in build_day_table(SATURDAY)


def test_day_table_reads_a_weekday_on_both_sides_by_its_session():
    # Seen live: "Sunday's Long Trail Run" on a Monday was read as
    # yesterday's strides, though only the coming Sunday held a long run.
    assert "the one whose session they name" in build_day_table(SATURDAY)


# resolve_date_args


def test_resolve_date_args_turns_relative_words_into_iso_dates():
    args = {"date": "Tomorrow", "start_date": "today", "end_date": " yesterday "}
    assert resolve_date_args(args, SATURDAY) == {
        "date": "2026-09-27",
        "start_date": "2026-09-26",
        "end_date": "2026-09-25",
    }


def test_resolve_date_args_leaves_everything_else_alone():
    """A weekday name is ambiguous (last Thursday or next?), so it is not
    guessed: the tool's own validation refuses it and the model retries."""
    args = {
        "date": "2026-09-30",
        "through_date": "Thursday",
        "note": "tomorrow",
        "duration_minutes": 45,
        "race_date": None,
    }
    assert resolve_date_args(args, SATURDAY) == args


def test_resolve_date_args_does_not_mutate_its_input():
    args = {"date": "tomorrow"}
    resolve_date_args(args, SATURDAY)
    assert args == {"date": "tomorrow"}


# label_dates


def test_label_dates_adds_a_day_beside_every_iso_date():
    result = json.dumps(
        {
            "sessions": [{"date": "2026-09-27", "sport": "running"}],
            "missed_sessions": [{"date": "2026-09-24"}],
            "race_date": "2026-10-20",
        }
    )
    labelled = json.loads(label_dates(result, SATURDAY))
    assert labelled["sessions"][0] == {
        "date": "2026-09-27",
        "day": "Sunday, tomorrow",
        "sport": "running",
    }
    assert labelled["missed_sessions"][0]["day"] == "Thursday, 2 days ago"
    assert labelled["race_day"] == "Tuesday"


def test_label_dates_keeps_an_existing_day_key():
    result = json.dumps({"date": "2026-09-27", "day": "kept"})
    assert json.loads(label_dates(result, SATURDAY))["day"] == "kept"


def test_label_dates_passes_through_text_without_dates():
    for result in ("plain text", '{"status": "ok"}', "[1, 2]", '"2026-09-27"'):
        assert label_dates(result, SATURDAY) == result


def test_label_dates_ignores_timestamps_and_non_date_keys():
    result = json.dumps(
        {"start_date_local": "2026-09-27T07:00:00", "week_start": "2026-09-21"}
    )
    assert label_dates(result, SATURDAY) == result


def test_label_dates_keeps_non_ascii_text_readable():
    result = json.dumps({"date": "2026-09-27", "why": "easy — keep it easy"})
    assert "easy — keep it easy" in label_dates(result, SATURDAY)
