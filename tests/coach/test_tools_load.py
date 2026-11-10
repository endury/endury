"""Tests for training-load analytics coach tools (load_tools.py).

Fitness/fatigue/form come from intervals.icu activity load and daily
wellness rows; these tests drive the tools with a stub client plus a
MagicMock plan repository.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.form_context import CTL_WARMUP_DAYS, PMC_HISTORY_DAYS, tsb_zone_read
from endury.coach.tools.load_tools import (
    _session_is_hard,
    _weekly_load,
    make_load_tools,
)
from tests.coach.conftest import StubIntervalsClient, make_activity, make_wellness

pytestmark = pytest.mark.coach

TODAY = date(2026, 1, 8)  # a Thursday


# Helpers


def _get_tool(tools_list, name):
    return next(t for t in tools_list if t.name == name)


def _plan_repo(*, active_plan=None, sessions=None):
    repo = MagicMock()
    repo.get_active.return_value = active_plan
    repo.get_sessions_by_date_range.return_value = sessions or []
    return repo


def _session_row(**overrides):
    base = {
        "sport": "running",
        "family": "easy",
        "activity_key": "run_easy",
        "target_tss": 50.0,
        "duration_minutes": 60.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _daily_runs(days: int, load: float = 60.0, end: date = TODAY):
    """One run per day for the last ``days`` days, ending on ``end``."""
    return [
        make_activity(
            (end - timedelta(days=offset)).isoformat(),
            activity_id=f"a{offset}",
            load=load,
        )
        for offset in range(days)
    ]


def _call(tools, name, **kwargs):
    with patch("endury.common.clock.today", return_value=TODAY):
        return json.loads(_get_tool(tools, name).invoke(kwargs))


# make_load_tools wiring


def test_make_load_tools_returns_four_tools():
    tools = make_load_tools(_plan_repo(), StubIntervalsClient())
    assert [t.name for t in tools] == [
        "get_training_load_status",
        "recommend_todays_session",
        "get_wellness_trend",
        "predict_race_time",
    ]


# get_wellness_trend


def test_wellness_trend_labels_each_metric_over_the_window():
    # 21 days ending TODAY: a steady fortnight, then a suppressed last week
    # (HRV down, resting HR up); sleep unchanged throughout.
    rows = [
        make_wellness(
            (TODAY - timedelta(days=20 - i)).isoformat(),
            sleep_secs=7 * 3600,
            hrv=60.0 if i < 14 else 48.0,
            resting_hr=50 if i < 14 else 56,
        )
        for i in range(21)
    ]
    tools = make_load_tools(_plan_repo(), StubIntervalsClient(wellness=rows))
    payload = _call(tools, "get_wellness_trend", days=21)
    assert payload["start_date"] == "2025-12-19"
    assert payload["end_date"] == "2026-01-08"
    metrics = payload["metrics"]
    assert metrics["hrv"]["trend"] == "declining"
    assert metrics["hrv"]["days"] == 21
    assert metrics["hrv"]["latest"] == 48.0
    assert metrics["hrv"]["baseline_avg"] == 60.0
    assert metrics["hrv"]["recent_avg"] == 48.0
    assert metrics["resting_hr"]["trend"] == "rising"
    assert metrics["sleep_hours"] == {
        "days": 21,
        "latest": 7.0,
        "baseline_avg": 7.0,
        "baseline_sd": 0.0,
        "recent_avg": 7.0,
        "trend": "stable",
    }
    # Metrics with no data at all are omitted, not reported as zero.
    assert "weight_kg" not in metrics
    assert "sleep_score" not in metrics
    assert "fatigue signature" in payload["note"]
    assert "0.75 baseline SD" in payload["note"]


def test_wellness_trend_draws_hrv_and_resting_hr_when_asked():
    rows = [
        make_wellness(
            (TODAY - timedelta(days=13 - i)).isoformat(),
            hrv=60.0 + i,
            resting_hr=50 if i == 0 else None,
        )
        for i in range(14)
    ]
    tools = make_load_tools(_plan_repo(), StubIntervalsClient(wellness=rows))

    payload = _call(tools, "get_wellness_trend", chart=True)

    # Resting HR has one day only: no line to draw.
    (hrv,) = payload["charts"]
    assert (hrv["kind"], hrv["title"], hrv["unit"]) == ("line", "HRV", "ms")
    assert hrv["x"][-1] == "2026-01-08"
    assert hrv["ys"] == [[60.0 + i for i in range(14)]]
    assert _call(tools, "get_wellness_trend")["charts"] == []


def test_wellness_trend_with_too_few_points_says_so():
    rows = [make_wellness("2026-01-08", hrv=60.0)]
    tools = make_load_tools(_plan_repo(), StubIntervalsClient(wellness=rows))
    payload = _call(tools, "get_wellness_trend", days=1)  # clamps to 14
    assert payload["start_date"] == "2025-12-26"
    assert payload["metrics"]["hrv"] == {
        "days": 1,
        "latest": 60.0,
        "trend": "insufficient data",
    }


def test_wellness_trend_with_an_unreachable_api_answers_empty():
    client = StubIntervalsClient(wellness_error=RuntimeError("boom"))
    tools = make_load_tools(_plan_repo(), client)
    # fetch_wellness swallows the read error into "no rows"; the tool still
    # answers, with an empty metric map rather than a failure.
    payload = _call(tools, "get_wellness_trend")
    assert payload["metrics"] == {}


def test_wellness_trend_reports_unexpected_errors():
    tools = make_load_tools(_plan_repo(), StubIntervalsClient())
    with patch(
        "endury.coach.tools.load_tools.wellness_trends", side_effect=RuntimeError("x")
    ):
        payload = _call(tools, "get_wellness_trend")
    assert "error" in payload


# get_training_load_status


def test_training_load_status_reports_per_sport_and_combined_form():
    client = StubIntervalsClient(
        activities=[
            *_daily_runs(60, load=60.0),
            make_activity("2026-01-06", "Ride", activity_id="r1", load=100.0),
        ]
    )
    tools = make_load_tools(_plan_repo(), client)
    payload = _call(tools, "get_training_load_status", weeks_back=4)

    assert payload["as_of"] == "2026-01-08"
    assert set(payload["by_sport"]) == {"running", "cycling"}
    assert payload["by_sport"]["running"]["ctl"] > 0
    current = payload["current"]
    assert current["tsb"] == round(current["ctl"] - current["atl"], 1)
    assert current["tsb_zone"]
    assert payload["acwr"]["value"] is not None
    assert len(payload["weekly_summary"]) == 4


def test_training_load_status_draws_fitness_fatigue_and_form_when_asked():
    client = StubIntervalsClient(activities=_daily_runs(60, load=60.0))
    tools = make_load_tools(_plan_repo(), client)

    payload = _call(tools, "get_training_load_status", weeks_back=2, chart=True)

    (chart,) = payload["charts"]
    assert (chart["kind"], chart["unit"]) == ("line", "load")
    assert chart["labels"] == ["Fitness", "Fatigue", "Form"]
    assert len(chart["x"]) == 14
    assert chart["x"][-1] == "2026-01-08"
    # The chart ends on the figures the athlete is told.
    current = payload["current"]
    assert [series[-1] for series in chart["ys"]] == [
        current["ctl"],
        current["atl"],
        current["tsb"],
    ]
    assert _call(tools, "get_training_load_status")["charts"] == []


def test_training_load_status_skips_a_sport_with_no_recorded_load():
    """A strapless sport has no CTL to report; the others still count."""
    client = StubIntervalsClient(
        activities=[
            *_daily_runs(60, load=60.0),
            make_activity("2026-01-06", "Swim", activity_id="s1", load=None),
        ]
    )
    tools = make_load_tools(_plan_repo(), client)
    payload = _call(tools, "get_training_load_status", weeks_back=4)

    assert set(payload["by_sport"]) == {"running"}


def test_training_load_status_widens_the_fetch_to_the_pmc_window():
    client = StubIntervalsClient()
    tools = make_load_tools(_plan_repo(), client)
    _call(tools, "get_training_load_status", weeks_back=4)
    oldest, newest = client.activity_windows[0]
    assert newest == TODAY
    assert (newest - oldest).days == PMC_HISTORY_DAYS + CTL_WARMUP_DAYS


def test_training_load_status_without_history_reports_no_acwr_or_ramp():
    tools = make_load_tools(_plan_repo(), StubIntervalsClient())
    payload = _call(tools, "get_training_load_status")
    assert payload["current"]["ctl"] == 0.0
    assert payload["by_sport"] == {}
    assert payload["acwr"]["value"] is None
    assert "insufficient training history" in payload["acwr"]["note"]
    assert payload["load_ramp_pct"]["value"] is None
    assert "Not enough weekly history" in payload["load_ramp_pct"]["note"]


def test_training_load_status_computes_the_week_over_week_ramp():
    client = StubIntervalsClient(
        activities=[
            make_activity("2025-12-30", activity_id="a1", load=100.0),
            make_activity("2026-01-06", activity_id="a2", load=150.0),
        ]
    )
    tools = make_load_tools(_plan_repo(), client)
    payload = _call(tools, "get_training_load_status", weeks_back=2)
    assert payload["load_ramp_pct"]["value"] == 50.0


def test_training_load_status_reports_api_errors():
    client = StubIntervalsClient(activities_error=RuntimeError("boom"))
    tools = make_load_tools(_plan_repo(), client)
    assert "error" in _call(tools, "get_training_load_status")


# recommend_todays_session


def test_recommend_without_active_plan_rests():
    tools = make_load_tools(_plan_repo(), StubIntervalsClient())
    payload = _call(tools, "recommend_todays_session")
    assert payload["plan_status"] == "no_active_plan"
    assert payload["verdict"] == "rest"
    assert "no active plan" in payload["reasoning"]


def test_recommend_on_a_rest_day_rests():
    tools = make_load_tools(
        _plan_repo(active_plan=SimpleNamespace(id=1)), StubIntervalsClient()
    )
    payload = _call(tools, "recommend_todays_session", date="2026-01-08")
    assert payload["plan_status"] == "rest_day"
    assert payload["verdict"] == "rest"


def test_recommend_keeps_an_easy_session_when_signals_are_clean():
    client = StubIntervalsClient(
        wellness=[
            make_wellness("2026-01-07", sleep_secs=28800, hrv=60.0, resting_hr=50),
            make_wellness("2026-01-08", sleep_secs=28800, hrv=61.0, resting_hr=50),
        ]
    )
    tools = make_load_tools(
        _plan_repo(active_plan=SimpleNamespace(id=1), sessions=[_session_row()]),
        client,
    )
    payload = _call(tools, "recommend_todays_session")
    assert payload["verdict"] == "keep"
    assert payload["signals"]["wellness_data_available"] is True
    assert "no concerning fatigue or recovery signals" in payload["reasoning"]
    # Reads five weeks so the recovery flags have a personal baseline to
    # judge today against; only the last seven days drive the flags.
    assert client.wellness_windows == [(date(2025, 12, 5), TODAY)]


def test_recommend_ignores_a_bad_night_inside_the_athletes_own_hrv_spread():
    # 28 baseline days swinging 72/48, then a 52 that is 13% below the last
    # six days — enough for the absolute threshold, routine for this athlete.
    hrvs = [72.0, 48.0] * 14 + [60.0] * 6 + [52.0]
    client = StubIntervalsClient(
        wellness=[
            make_wellness(
                (TODAY - timedelta(days=34 - i)).isoformat(),
                sleep_secs=28800,
                hrv=hrv,
                resting_hr=50,
            )
            for i, hrv in enumerate(hrvs)
        ]
    )
    tools = make_load_tools(
        _plan_repo(
            active_plan=SimpleNamespace(id=1),
            sessions=[_session_row(family="threshold", activity_key="run_threshold")],
        ),
        client,
    )
    payload = _call(tools, "recommend_todays_session")
    assert payload["signals"]["recovery_flags"] == []
    assert payload["verdict"] == "keep"


def test_recommend_notes_missing_wellness_data():
    tools = make_load_tools(
        _plan_repo(active_plan=SimpleNamespace(id=1), sessions=[_session_row()]),
        StubIntervalsClient(),
    )
    payload = _call(tools, "recommend_todays_session")
    assert payload["signals"]["wellness_data_available"] is False
    assert any("no recent wellness data" in line for line in payload["reasoning"])


def test_recommend_rests_a_hard_session_when_overreaching():
    # 60 consecutive days of heavy load drive TSB well below -30.
    client = StubIntervalsClient(activities=_daily_runs(60, load=250.0))
    tools = make_load_tools(
        _plan_repo(
            active_plan=SimpleNamespace(id=1),
            sessions=[_session_row(family="interval", activity_key="run_vo2")],
        ),
        client,
    )
    payload = _call(tools, "recommend_todays_session")
    assert payload["signals"]["tsb"] < -30
    assert payload["verdict"] == "rest"
    assert any("deep fatigue" in line for line in payload["reasoning"])
    assert "the session is a hard/quality session" in payload["reasoning"]


def test_recommend_for_another_day_never_calls_it_today():
    """Asked about tomorrow, the reasoning must not say "today's session"."""
    client = StubIntervalsClient(activities=_daily_runs(60, load=250.0))
    tools = make_load_tools(
        _plan_repo(
            active_plan=SimpleNamespace(id=1),
            sessions=[_session_row(family="interval", activity_key="run_vo2")],
        ),
        client,
    )
    payload = _call(tools, "recommend_todays_session", date="2026-08-20")
    assert payload["date"] == "2026-08-20"
    assert not any("today" in line for line in payload["reasoning"])


def test_recommend_swaps_to_easy_when_overreaching_without_a_hard_session():
    client = StubIntervalsClient(activities=_daily_runs(60, load=250.0))
    tools = make_load_tools(
        _plan_repo(active_plan=SimpleNamespace(id=1), sessions=[_session_row()]),
        client,
    )
    assert _call(tools, "recommend_todays_session")["verdict"] == "swap_to_easy"


def test_recommend_reduces_intensity_on_productive_overload_plus_hard_session():
    client = StubIntervalsClient(activities=_daily_runs(60, load=90.0))
    tools = make_load_tools(
        _plan_repo(
            active_plan=SimpleNamespace(id=1),
            sessions=[_session_row(family="easy", activity_key="run_brick_long")],
        ),
        client,
    )
    payload = _call(tools, "recommend_todays_session")
    assert -30 <= payload["signals"]["tsb"] <= -10
    assert payload["verdict"] == "reduce_intensity"
    assert any("productive overload" in line for line in payload["reasoning"])


def test_recommend_rests_when_two_wellness_flags_and_a_hard_session():
    client = StubIntervalsClient(
        wellness=[
            make_wellness("2026-01-06", sleep_secs=18000, hrv=70.0, resting_hr=48),
            make_wellness("2026-01-07", sleep_secs=18000, hrv=70.0, resting_hr=48),
            make_wellness("2026-01-08", sleep_secs=18000, hrv=40.0, resting_hr=60),
        ]
    )
    tools = make_load_tools(
        _plan_repo(
            active_plan=SimpleNamespace(id=1),
            sessions=[_session_row(family="threshold", activity_key="run_threshold")],
        ),
        client,
    )
    payload = _call(tools, "recommend_todays_session")
    assert len(payload["signals"]["recovery_flags"]) >= 2
    assert payload["verdict"] == "rest"


def test_recommend_note_does_not_steer_to_replanning_for_this_week():
    # Replanning only regenerates from next Monday, so it can never act on
    # today's recommendation — set_session_status is the tool that can.
    tools = make_load_tools(
        _plan_repo(active_plan=SimpleNamespace(id=1), sessions=[_session_row()]),
        StubIntervalsClient(),
    )
    note = _call(tools, "recommend_todays_session")["note"]
    assert "use replanning" not in note
    assert "skip_planned_session" in note


def test_recommend_reports_errors():
    plan_repo = MagicMock()
    plan_repo.get_active.side_effect = RuntimeError("db gone")
    tools = make_load_tools(plan_repo, StubIntervalsClient())
    assert "error" in _call(tools, "recommend_todays_session")


def test_recommend_rejects_a_malformed_date():
    tools = make_load_tools(_plan_repo(), StubIntervalsClient())
    assert "error" in _call(tools, "recommend_todays_session", date="08-01-2026")


# Pure helpers


@pytest.mark.parametrize(
    ("tsb", "fragment"),
    [
        (30.0, "very fresh"),
        (20.0, "race-ready"),
        (8.0, "fresh"),
        (0.0, "maintenance"),
        (-20.0, "productive overload"),
        (-40.0, "deep fatigue"),
    ],
)
def test_tsb_zone_read_bands(tsb, fragment):
    assert fragment in tsb_zone_read(tsb)


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ({"family": "interval", "activity_key": "run_vo2"}, True),
        ({"family": "easy", "activity_key": "run_brick_long"}, True),
        ({"family": "easy", "activity_key": "run_easy"}, False),
        ({"family": None, "activity_key": None}, False),
    ],
)
def test_session_is_hard(session, expected):
    assert _session_is_hard(session) is expected


def test_weekly_load_ignores_out_of_window_and_undated_activities():
    activities = [
        make_activity("2025-11-01", activity_id="old", load=999.0),
        make_activity("2026-01-06", activity_id="in", load=40.0),
        make_activity("2026-01-06", activity_id="undated").model_copy(
            update={"start_date_local": None}
        ),
    ]
    weeks = _weekly_load(activities, TODAY, 2)
    assert weeks == [
        {"week_start": "2025-12-29", "training_load": 0.0},
        {"week_start": "2026-01-05", "training_load": 40.0},
    ]
