"""Tests for the replan window (where a replan cuts the plan in two)."""

from __future__ import annotations

from datetime import date

import pytest

from endury.planning.replan_window import (
    ReplanWindow,
    next_monday,
    previous_race_events,
    replan_window,
)

_START = date(2026, 3, 9)  # a Monday


class TestNextMonday:
    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 5, 18), date(2026, 5, 25)),  # a Monday
            (date(2026, 5, 20), date(2026, 5, 25)),  # a Wednesday
            (date(2026, 5, 24), date(2026, 5, 25)),  # a Sunday
        ],
    )
    def test_is_always_strictly_next(self, today: date, expected: date) -> None:
        assert next_monday(today) == expected


class TestDefaultWindow:
    @pytest.mark.parametrize(
        "today",
        [date(2026, 5, 18), date(2026, 5, 20), date(2026, 5, 24)],
    )
    def test_cuts_at_next_monday(self, today: date) -> None:
        window = replan_window(today, _START)

        assert window == ReplanWindow(
            start=date(2026, 5, 25), cutoff=date(2026, 5, 25), prior=11
        )

    def test_before_the_plan_starts_regenerates_from_the_original_start(self) -> None:
        window = replan_window(date(2026, 2, 1), _START)

        assert window == ReplanWindow(start=_START, cutoff=_START, prior=0)


class TestResumeDate:
    def test_a_mid_week_resume_keeps_the_days_before_it(self) -> None:
        window = replan_window(date(2026, 5, 20), _START, date(2026, 5, 28))

        assert window == ReplanWindow(
            start=date(2026, 5, 25), cutoff=date(2026, 5, 28), prior=11
        )

    def test_a_resume_inside_the_current_week_cuts_there(self) -> None:
        window = replan_window(date(2026, 5, 20), _START, date(2026, 5, 23))

        assert window == ReplanWindow(
            start=date(2026, 5, 18), cutoff=date(2026, 5, 23), prior=10
        )

    def test_a_resume_in_the_first_plan_week_keeps_the_original_start(self) -> None:
        window = replan_window(date(2026, 3, 10), _START, date(2026, 3, 12))

        assert window == ReplanWindow(start=_START, cutoff=date(2026, 3, 12), prior=0)

    def test_a_resume_before_the_plan_starts_cannot_precede_it(self) -> None:
        window = replan_window(date(2026, 2, 1), _START, date(2026, 2, 5))

        assert window == ReplanWindow(start=_START, cutoff=_START, prior=0)

    @pytest.mark.parametrize(
        "resume", [date(2026, 5, 20), date(2026, 5, 19)]
    )  # today, yesterday
    def test_a_resume_not_after_today_is_refused(self, resume: date) -> None:
        with pytest.raises(ValueError, match="not rewritten backwards"):
            replan_window(date(2026, 5, 20), _START, resume)


_RACES = [
    {"goal_type": "running_marathon", "race_date": "2026-06-07", "priority": "A"},
    {"goal_type": "running_10k", "race_date": "2026-05-17", "priority": "C"},
    {"goal_type": "running_half_marathon", "race_date": "2026-04-12", "priority": "B"},
]


class TestPreviousRaceEvents:
    def test_a_tune_up_behind_the_cutoff_keeps_its_calendar_event(self) -> None:
        # The replan of 2026-05-20 cuts at the next Monday; the C race the
        # Sunday before it has been run and its event stays on the calendar.
        ids = [e["external_id"] for e in previous_race_events(7, _RACES, "2026-05-25")]

        assert ids == ["coachie:7:race:A:2026-06-07"]

    def test_a_race_on_or_after_the_cutoff_can_go_stale(self) -> None:
        ids = [e["external_id"] for e in previous_race_events(7, _RACES, "2026-04-12")]

        assert ids == [
            "coachie:7:race:A:2026-06-07",
            "coachie:7:race:C:2026-05-17",
            "coachie:7:race:B:2026-04-12",
        ]
