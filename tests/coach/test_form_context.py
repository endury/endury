"""Tests for the coach's form reading (endury.coach.form_context)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from endury.coach.form_context import (
    CTL_WARMUP_DAYS,
    PMC_HISTORY_DAYS,
    build_form_line,
    fetch_form,
    form_as_of,
)
from tests.coach.conftest import StubIntervalsClient, make_activity

pytestmark = pytest.mark.coach

TODAY = date(2026, 10, 20)  # a Tuesday; the long run was Sunday
LONG_RUN = make_activity("2026-10-18", "Run", activity_id="r1", load=86.0)


def test_form_as_of_runs_the_ema_to_the_day_asked():
    """Two rest days after a long run: fatigue falls faster than fitness."""
    sunday = form_as_of([LONG_RUN], date(2026, 10, 18))
    tuesday = form_as_of([LONG_RUN], TODAY)
    assert set(sunday.by_sport) == {"running"}
    assert tuesday.atl < sunday.atl
    assert tuesday.tsb > sunday.tsb
    assert tuesday.tsb == round(tuesday.ctl - tuesday.atl, 1)


def test_form_as_of_ignores_activities_after_the_day():
    """Yesterday's form is read from the same list without today's session."""
    today_ride = make_activity("2026-10-20", "Ride", activity_id="b1", load=70.0)
    yesterday = TODAY - timedelta(days=1)
    with_ride = form_as_of([LONG_RUN, today_ride], yesterday)
    without = form_as_of([LONG_RUN], yesterday)
    assert (with_ride.ctl, with_ride.atl) == (without.ctl, without.atl)
    assert with_ride.by_sport["running"] == without.by_sport["running"]


def test_form_as_of_skips_a_sport_with_no_recorded_load():
    swim = make_activity("2026-10-19", "Swim", activity_id="s1", load=None)
    form = form_as_of([LONG_RUN, swim], TODAY)
    assert "swimming" not in form.by_sport
    assert form.ctl == form.by_sport["running"]["ctl"]


def test_fetch_form_widens_the_window_to_the_pmc_history_plus_warmup():
    client = StubIntervalsClient(activities=[LONG_RUN])
    form, activities = fetch_form(client, TODAY, weeks_back=1)
    oldest, newest = client.activity_windows[0]
    assert newest == TODAY
    assert (newest - oldest).days == PMC_HISTORY_DAYS + CTL_WARMUP_DAYS
    assert activities == [LONG_RUN]
    assert form == form_as_of([LONG_RUN], TODAY)


def test_build_form_line_dates_today_and_names_yesterday():
    """Seen live: the coach advised on Sunday's -14 two days later; the
    line is dated, and yesterday's figure beside it shows the move."""
    client = StubIntervalsClient(activities=[LONG_RUN])
    with patch("endury.common.clock.today", return_value=TODAY):
        line = build_form_line(client)
    today = form_as_of([LONG_RUN], TODAY)
    yesterday = form_as_of([LONG_RUN], TODAY - timedelta(days=1))
    assert line.startswith("As of Tuesday 2026-10-20, from completed sessions: ")
    assert f"form {today.tsb:+.1f} — " in line
    assert line.endswith(f"yesterday's form {yesterday.tsb:+.1f}.")
    assert today.tsb != yesterday.tsb


@pytest.mark.parametrize(
    "client",
    [
        None,
        StubIntervalsClient(),
        StubIntervalsClient(
            activities=[
                make_activity("2026-10-19", "Swim", activity_id="s1", load=None)
            ]
        ),
        StubIntervalsClient(activities_error=RuntimeError("network down")),
    ],
    ids=["no client", "no activities", "no measured load", "api down"],
)
def test_build_form_line_is_none_when_there_is_nothing_to_read(client):
    with patch("endury.common.clock.today", return_value=TODAY):
        assert build_form_line(client) is None
