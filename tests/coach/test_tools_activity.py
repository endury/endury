"""Tests for the intervals.icu-backed activity coach tools."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from endury.coach.tools.activity_tools import delivered_view, make_activity_tools
from endury.intervals.models import IntervalRow, ZoneTime
from tests.coach.conftest import (
    StubIntervalsClient,
    make_activity,
    make_activity_detail,
)

pytestmark = pytest.mark.coach

TODAY = date(2026, 1, 8)  # a Thursday


def _tool(client, name):
    with patch("endury.common.clock.today", return_value=TODAY):
        tools = make_activity_tools(client)
    return next(t for t in tools if t.name == name)


def _call(client, name, **kwargs):
    tool = _tool(client, name)
    with patch("endury.common.clock.today", return_value=TODAY):
        return json.loads(tool.invoke(kwargs))


def test_make_activity_tools_exposes_three_tools():
    tools = make_activity_tools(StubIntervalsClient())
    assert [t.name for t in tools] == [
        "get_recent_activities",
        "get_activity_detail",
        "get_weekly_training_summary",
    ]


# get_recent_activities


def test_recent_activities_projects_rows_newest_first():
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-01-05", activity_id="a1"),
            make_activity("2026-01-07", "Swim", activity_id="a2", distance=2000.0),
        ]
    )
    payload = _call(client, "get_recent_activities", days=14)
    assert payload["start_date"] == "2025-12-26"
    assert payload["end_date"] == "2026-01-08"
    assert payload["count"] == 2
    assert [a["activity_id"] for a in payload["activities"]] == ["a2", "a1"]
    swim = payload["activities"][0]
    assert swim["sport"] == "swimming"
    assert swim["start_time"] == "07:00"
    assert swim["duration_minutes"] == 60.0
    assert swim["distance_km"] == 2.0
    # Lean row: HR/IF ride along when present, nothing else from the detail.
    assert swim["avg_hr"] is None
    assert swim["intensity_factor"] is None
    assert "intervals" not in swim


def test_recent_activities_order_same_day_by_start_time():
    morning = make_activity("2026-01-06", "TrailRun", activity_id="run")
    evening = make_activity("2026-01-06", "Ride", activity_id="ride").model_copy(
        update={"start_date_local": "2026-01-06T20:06:26"}
    )
    client = StubIntervalsClient(activities=[morning, evening])
    payload = _call(client, "get_recent_activities", days=14)
    assert [(a["activity_id"], a["start_time"]) for a in payload["activities"]] == [
        ("ride", "20:06"),
        ("run", "07:00"),
    ]


def test_recent_activities_rows_carry_name_hr_and_intensity():
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-01-05", activity_id="a1").model_copy(
                update={
                    "name": "Threshold run",
                    "average_heartrate": 158,
                    "icu_intensity": 91.4,
                }
            )
        ]
    )
    row = _call(client, "get_recent_activities")["activities"][0]
    assert row["name"] == "Threshold run"
    assert row["avg_hr"] == 158
    assert row["intensity_factor"] == 0.91


def test_recent_activities_rows_carry_elevation_gain():
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-01-05", activity_id="hilly").model_copy(
                update={"total_elevation_gain": 612.4}
            ),
            make_activity("2026-01-06", "Swim", activity_id="pool"),
        ]
    )
    rows = {
        row["activity_id"]: row
        for row in _call(client, "get_recent_activities")["activities"]
    }
    assert rows["hilly"]["elevation_gain_m"] == 612
    # Nothing recorded it: null, not a fabricated flat 0.
    assert rows["pool"]["elevation_gain_m"] is None


def test_recent_activities_keeps_unmapped_type_and_missing_distance():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-06", "Yoga", distance=None)]
    )
    row = _call(client, "get_recent_activities")["activities"][0]
    assert row["sport"] == "Yoga"
    assert row["distance_km"] is None


def test_recent_activities_caps_the_listing():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-05", activity_id=f"a{i}") for i in range(45)]
    )
    payload = _call(client, "get_recent_activities")
    assert payload["count"] == 45
    assert len(payload["activities"]) == 40
    # A cut listing says so: the oldest listed day is not where the data ends.
    assert payload["older_not_listed"] == 5
    assert "ana.history" in payload["note"]


def test_recent_activities_uncut_listing_carries_no_note():
    client = StubIntervalsClient(activities=[make_activity("2026-01-05")])
    payload = _call(client, "get_recent_activities")
    assert "older_not_listed" not in payload
    assert "note" not in payload


def test_recent_activities_rows_carry_pace_or_speed():
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-01-05", activity_id="run").model_copy(
                update={"average_speed": 2.5, "gap": 2.6}
            ),
            make_activity("2026-01-06", "Ride", activity_id="ride").model_copy(
                update={"average_speed": 8.0}
            ),
        ]
    )
    rows = {
        row["activity_id"]: row
        for row in _call(client, "get_recent_activities")["activities"]
    }
    assert rows["run"]["pace_min_km"] == "6:40"
    assert rows["run"]["gap_min_km"] == "6:25"
    assert rows["ride"]["speed_kmh"] == 28.8


def test_recent_activities_reports_api_errors():
    client = StubIntervalsClient(activities_error=RuntimeError("boom"))
    payload = _call(client, "get_recent_activities")
    assert "error" in payload


# get_activity_detail


def _rep(**fields):
    return IntervalRow(**fields)


def _threshold_run_detail():
    """A 3x1k threshold run, auto-detected by intervals.icu (no labels)."""
    return make_activity_detail(
        "2026-01-06",
        activity_id="run1",
        name="Threshold Tuesday",
        source="GARMIN_CONNECT",
        device_name="Garmin Forerunner 965",
        moving_time=2400,
        elapsed_time=2460,
        distance=8000.0,
        total_elevation_gain=42.4,
        icu_training_load=71.0,
        trimp=88.26,
        icu_intensity=91.0,
        average_heartrate=155,
        max_heartrate=176,
        lthr=168,
        pace=3.333,  # 5:00/km
        gap=3.4,
        average_cadence=84.6,
        decoupling=3.24,
        icu_efficiency_factor=None,
        icu_hr_zone_times=[600, 600, 300, 780, 120],
        pace_zone_times=[900, 300, 240, 900, 60],
        threshold_pace=4.0,  # 4:10/km
        interval_summary=["3x 3m52s 172bpm"],
        feel=2,
        icu_rpe=7,
        intervals=[
            _rep(
                type="WORK",
                group_id="g1",
                start_time=0,
                elapsed_time=232,
                distance=1000.0,
                average_heartrate=168,
                max_heartrate=174,
                average_speed=4.31,
                gap=4.35,
                average_cadence=88.2,
                zone=4,
                intensity=104,
            ),
            _rep(
                type="RECOVERY",
                start_time=232,
                elapsed_time=120,
                distance=300.0,
                average_heartrate=140,
                average_speed=2.5,
                zone=1,
                intensity=60,
            ),
        ],
        groups=[
            _rep(
                count=3,
                elapsed_time=232,
                distance=1000.0,
                average_heartrate=172,
                max_heartrate=176,
                average_speed=4.31,
                gap=4.35,
                zone=4,
            )
        ],
    )


def test_activity_detail_by_id_projects_overview_groups_and_reps():
    client = StubIntervalsClient(details=[_threshold_run_detail()])
    view = _call(client, "get_activity_detail", activity_id="run1")

    assert client.detail_requests == ["run1"]
    assert view["activity_id"] == "run1"
    assert view["date"] == "2026-01-06"
    assert view["start_time"] == "07:00"
    assert view["sport"] == "running"
    assert view["name"] == "Threshold Tuesday"
    assert view["source"] == "GARMIN_CONNECT"
    assert view["device_name"] == "Garmin Forerunner 965"
    assert view["duration_minutes"] == 40.0
    assert view["elapsed_minutes"] == 41.0
    assert view["distance_km"] == 8.0
    assert view["elevation_gain_m"] == 42
    assert view["training_load"] == 71.0
    assert view["trimp"] == 88.3
    assert view["intensity_factor"] == 0.91
    assert view["avg_hr"] == 155
    assert view["lthr"] == 168
    assert view["pace_min_km"] == "5:00"
    assert view["gap_min_km"] == "4:54"
    assert view["threshold_pace_min_km"] == "4:10"
    assert view["avg_cadence"] == 85
    assert view["decoupling_pct"] == 3.2
    assert view["hr_zone_minutes"] == [10.0, 10.0, 5.0, 13.0, 2.0]
    assert view["pace_zone_minutes"] == [15.0, 5.0, 4.0, 15.0, 1.0]
    assert view["interval_summary"] == ["3x 3m52s 172bpm"]
    assert (view["feel"], view["feel_word"], view["rpe"]) == (2, "Good", 7)
    # Absent metrics are dropped, never rendered as zero/null.
    assert "efficiency_factor" not in view
    assert "normalized_power" not in view
    assert "power_zone_minutes" not in view

    assert view["groups"] == [
        {
            "count": 3,
            "duration": "3:52",
            "distance_km": 1.0,
            "avg_hr": 172,
            "max_hr": 176,
            "zone": 4,
            "pace_min_km": "3:52",
            "gap_min_km": "3:50",
        }
    ]
    work, recovery = view["intervals"]
    assert work["n"] == 1
    assert work["type"] == "WORK"
    assert work["start"] == "0:00"
    assert work["duration"] == "3:52"
    assert work["avg_hr"] == 168
    assert work["cadence"] == 88
    assert work["zone"] == 4
    assert work["intensity_factor"] == 1.04
    assert work["pace_min_km"] == "3:52"
    assert recovery["type"] == "RECOVERY"
    assert recovery["start"] == "3:52"
    assert recovery["pace_min_km"] == "6:40"
    assert "gap_min_km" not in recovery
    assert view["note"].startswith("Intervals were auto-detected")


def test_activity_detail_renders_ride_power_and_swim_pace():
    ride = make_activity_detail(
        "2026-01-04",
        "Ride",
        activity_id="ride1",
        moving_time=5400,
        average_speed=8.5,
        icu_average_watts=200,
        icu_weighted_avg_watts=231,
        icu_ftp=310,
        icu_variability_index=1.155,
        icu_efficiency_factor=1.51,
        icu_zone_times=[
            ZoneTime(id="Z1", secs=1800),
            ZoneTime(id="Z2", secs=3000),
            ZoneTime(id="SS", secs=600),
        ],
        intervals=[
            _rep(
                label="Interval 1",
                type="WORK",
                start_time=600,
                elapsed_time=1200,
                average_watts=280,
                weighted_average_watts=285,
                average_speed=9.0,
            )
        ],
    )
    swim = make_activity_detail(
        "2026-01-04",
        "Swim",
        activity_id="swim1",
        pace=0.9,
        threshold_pace=0.9346,
    )
    client = StubIntervalsClient(details=[ride, swim])

    view = _call(client, "get_activity_detail", activity_id="ride1")
    assert view["speed_kmh"] == 30.6
    assert view["avg_watts"] == 200
    assert view["normalized_power"] == 231
    assert view["ftp"] == 310
    assert view["variability_index"] == 1.16
    assert view["efficiency_factor"] == 1.51
    assert view["power_zone_minutes"] == {"Z1": 30.0, "Z2": 50.0, "SS": 10.0}
    assert "threshold_pace_min_km" not in view
    rep = view["intervals"][0]
    assert rep["label"] == "Interval 1"
    assert rep["start"] == "10:00"
    assert rep["duration"] == "20:00"
    assert rep["avg_watts"] == 280
    assert rep["normalized_power"] == 285
    assert rep["speed_kmh"] == 32.4
    assert "note" not in view  # athlete-labelled laps

    view = _call(client, "get_activity_detail", activity_id="swim1")
    # A raw 111 was quoted to the athlete as "1:11/100m".
    assert view["pace_min_100m"] == "1:51"
    assert view["css_min_100m"] == "1:47"
    assert view["groups"] == []
    assert view["intervals"] == []


def _continuous_run(paces_s_per_km, hrs):
    """A run of 1 km auto-laps, one per pace, like a race."""
    start = 0
    reps = []
    for pace, hr in zip(paces_s_per_km, hrs, strict=True):
        reps.append(
            _rep(
                type="WORK",
                start_time=start,
                elapsed_time=pace,
                distance=1000.0,
                average_heartrate=hr,
                average_speed=1000.0 / pace,
            )
        )
        start += pace
    return make_activity_detail(
        "2026-09-27", activity_id="race", elapsed_time=start, intervals=reps
    )


def test_a_continuous_run_states_its_split_so_the_coach_does_not_judge_it():
    # The replayed debrief called a 4:13 -> 4:47/km fade "negative-to-even".
    detail = _continuous_run([250, 250, 280, 280], [160, 164, 170, 176])
    view = _call(
        StubIntervalsClient(details=[detail]), "get_activity_detail", activity_id="race"
    )
    assert view["halves"] == {
        "first": {"pace_min_km": "4:10", "avg_hr": 162},
        "second": {"pace_min_km": "4:40", "avg_hr": 173},
        "split": "positive",
        "second_half_slower_pct": 12.0,
    }
    assert view["elapsed_time"] == "17:40"


def test_an_even_split_is_called_even():
    detail = _continuous_run([250, 252, 251, 250], [160, 162, 163, 164])
    view = _call(
        StubIntervalsClient(details=[detail]), "get_activity_detail", activity_id="race"
    )
    assert view["halves"]["split"] == "even"


def test_a_negative_split_is_called_negative():
    detail = _continuous_run([260, 260, 245, 245], [160, 162, 168, 172])
    view = _call(
        StubIntervalsClient(details=[detail]), "get_activity_detail", activity_id="race"
    )
    assert view["halves"]["split"] == "negative"
    assert view["halves"]["second_half_slower_pct"] == -5.8


def test_an_interval_session_gets_no_halves():
    # Recoveries make up a real share of the time: halves would mix reps and jogs.
    view = _call(
        StubIntervalsClient(details=[_threshold_run_detail()]),
        "get_activity_detail",
        activity_id="run1",
    )
    assert "halves" not in view


def test_activity_detail_caps_reps_but_keeps_groups():
    reps = [
        _rep(type="WORK", start_time=60 * i, elapsed_time=60, average_heartrate=150)
        for i in range(35)
    ]
    detail = make_activity_detail(
        "2026-01-06",
        activity_id="many",
        intervals=reps,
        groups=[_rep(count=35, elapsed_time=60, average_heartrate=150)],
    )
    view = _call(
        StubIntervalsClient(details=[detail]), "get_activity_detail", activity_id="many"
    )
    assert len(view["intervals"]) == 30
    assert view["intervals_omitted"] == 5
    assert view["groups"][0]["count"] == 35
    assert view["intervals"][-1]["start"] == "29:00"


def test_activity_detail_renders_long_reps_with_hours():
    detail = make_activity_detail(
        "2026-01-06",
        "Ride",
        activity_id="long",
        intervals=[_rep(type="WORK", start_time=3661, elapsed_time=5400)],
    )
    view = _call(
        StubIntervalsClient(details=[detail]), "get_activity_detail", activity_id="long"
    )
    assert view["intervals"][0]["start"] == "1:01:01"
    assert view["intervals"][0]["duration"] == "1:30:00"


def test_activity_detail_by_date_details_each_activity_that_day():
    listed = [
        make_activity("2026-01-06", "Swim", activity_id="s1"),
        make_activity("2026-01-06", "Ride", activity_id="r1"),
        make_activity("2026-01-07", activity_id="other"),
    ]
    client = StubIntervalsClient(
        activities=listed,
        details=[make_activity_detail("2026-01-06", "Swim", activity_id="s1")],
    )
    payload = _call(client, "get_activity_detail", date="2026-01-06")
    assert payload["date"] == "2026-01-06"
    assert [v["activity_id"] for v in payload["activities"]] == ["s1", "r1"]
    assert client.detail_requests == ["s1", "r1"]
    assert "activities_omitted" not in payload


def test_activity_detail_by_date_caps_at_three_and_reports_the_rest():
    listed = [make_activity("2026-01-06", activity_id=f"a{i}") for i in range(5)]
    payload = _call(
        StubIntervalsClient(activities=listed), "get_activity_detail", date="2026-01-06"
    )
    assert len(payload["activities"]) == 3
    assert payload["activities_omitted"] == 2


def test_activity_detail_by_date_with_nothing_logged():
    payload = _call(StubIntervalsClient(), "get_activity_detail", date="2026-01-06")
    assert payload == {"date": "2026-01-06", "activities": []}


def test_activity_detail_needs_an_id_or_a_date():
    payload = _call(StubIntervalsClient(), "get_activity_detail")
    assert "activity_id or a date" in payload["message"]


def test_activity_detail_rejects_a_bad_date():
    payload = _call(StubIntervalsClient(), "get_activity_detail", date="06.01.2026")
    assert "error" in payload


def test_activity_detail_reports_api_errors():
    client = StubIntervalsClient(detail_error=RuntimeError("boom"))
    payload = _call(client, "get_activity_detail", activity_id="x")
    assert "error" in payload


# get_weekly_training_summary


def test_weekly_summary_buckets_by_monday_and_sport():
    client = StubIntervalsClient(
        activities=[
            # Week of 2025-12-29
            make_activity("2025-12-30", activity_id="a1", load=50.0),
            # Current week (Monday 2026-01-05)
            make_activity("2026-01-05", activity_id="a2", load=60.0),
            make_activity("2026-01-07", "Ride", activity_id="a3", load=90.0),
        ]
    )
    payload = _call(client, "get_weekly_training_summary", weeks=2)
    weeks = payload["weeks"]
    assert [w["week_start"] for w in weeks] == ["2025-12-29", "2026-01-05"]
    assert weeks[0]["activities"] == 1
    assert weeks[1]["training_load"] == 150.0
    assert weeks[1]["hours"] == 2.0
    assert weeks[1]["by_sport"]["cycling"] == {
        "activities": 1,
        "hours": 1.0,
        "training_load": 90.0,
        "elevation_gain_m": None,
    }
    # No zone data on these fixtures: buckets stay zero and say so.
    assert weeks[1]["intensity_minutes"] == {"easy": 0.0, "moderate": 0.0, "hard": 0.0}
    assert weeks[1]["activities_with_zone_data"] == 0
    assert [s["activity_id"] for s in weeks[1]["key_sessions"]] == ["a3", "a2"]
    assert "zone" in payload["note"]


def test_weekly_summary_draws_hours_by_sport_when_asked():
    client = StubIntervalsClient(
        activities=[
            make_activity("2025-12-30", activity_id="a1"),
            make_activity("2026-01-05", activity_id="a2"),
            make_activity("2026-01-06", "Ride", activity_id="a3"),
            make_activity("2026-01-07", "Swim", activity_id="a4"),
            make_activity("2026-01-07", "Rowing", activity_id="a5"),
        ]
    )

    payload = _call(client, "get_weekly_training_summary", weeks=2, chart=True)

    (chart,) = payload["charts"]
    assert (chart["kind"], chart["unit"], chart["stacked"]) == ("bar", "h", True)
    assert chart["x"] == ["2025-12-29", "2026-01-05"]
    # Three series at most: the two biggest sports, the rest as Other.
    assert chart["labels"] == ["Running", "Cycling", "Other"]
    assert chart["ys"] == [[1.0, 1.0], [0.0, 1.0], [0.0, 2.0]]
    assert _call(client, "get_weekly_training_summary", weeks=2)["charts"] == []


def test_weekly_summary_sums_elevation_overall_and_per_sport():
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-01-05", activity_id="hills").model_copy(
                update={"total_elevation_gain": 420.0}
            ),
            make_activity("2026-01-06", activity_id="rollers").model_copy(
                update={"total_elevation_gain": 30.4}
            ),
            # Turbo session with no elevation: counts as zero for the week
            # total, but its own sport recorded nothing at all.
            make_activity("2026-01-07", "Ride", activity_id="turbo"),
        ]
    )
    week = _call(client, "get_weekly_training_summary", weeks=1)["weeks"][0]
    assert week["elevation_gain_m"] == 450
    assert week["by_sport"]["running"]["elevation_gain_m"] == 450
    assert week["by_sport"]["cycling"]["elevation_gain_m"] is None
    assert "elevation_gain_m" in _call(client, "get_weekly_training_summary")["note"]


def test_weekly_summary_elevation_is_null_when_nothing_measured_it():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-05", activity_id="a1")]
    )
    week = _call(client, "get_weekly_training_summary", weeks=1)["weeks"][0]
    assert week["elevation_gain_m"] is None
    assert week["by_sport"]["running"]["elevation_gain_m"] is None


def test_weekly_summary_buckets_intensity_and_ranks_key_sessions():
    run = make_activity("2026-01-05", activity_id="run", load=60.0).model_copy(
        update={
            "name": "Threshold",
            "icu_intensity": 91.0,
            "interval_summary": ["3x 3m52s 172bpm"],
            # HR zones win over pace zones when both exist.
            "icu_hr_zone_times": [600, 600, 300, 780, 120],
            "pace_zone_times": [3000, 0, 0, 0, 0],
        }
    )
    swim = make_activity(
        "2026-01-06", "Swim", activity_id="swim", load=40.0
    ).model_copy(update={"pace_zone_times": [300, 300, 600, 300, 60, 0, 0]})
    ride = make_activity(
        "2026-01-07", "Ride", activity_id="ride", load=90.0
    ).model_copy(
        update={
            "icu_zone_times": [
                ZoneTime(id="Z1", secs=1200),
                ZoneTime(id="Z2", secs=1200),
                ZoneTime(id="Z3", secs=600),
                ZoneTime(id="Z4", secs=300),
                ZoneTime(id="SS", secs=900),  # overlay, must not double count
            ]
        }
    )
    yoga = make_activity("2026-01-07", "Yoga", activity_id="yoga", load=10.0)
    client = StubIntervalsClient(activities=[run, swim, ride, yoga])
    week = _call(client, "get_weekly_training_summary", weeks=1)["weeks"][0]

    assert week["activities_with_zone_data"] == 3
    # easy = Z1+Z2, moderate = Z3, hard = Z4+ (minutes)
    assert week["intensity_minutes"] == {
        "easy": round((1200 + 600 + 2400) / 60, 1),
        "moderate": round((300 + 600 + 600) / 60, 1),
        "hard": round((900 + 360 + 300) / 60, 1),
    }
    assert [s["activity_id"] for s in week["key_sessions"]] == ["ride", "run", "swim"]
    top = week["key_sessions"][1]
    assert top == {
        "activity_id": "run",
        "date": "2026-01-05",
        "sport": "running",
        "name": "Threshold",
        "training_load": 60.0,
        "intensity_factor": 0.91,
        "interval_summary": ["3x 3m52s 172bpm"],
    }


# delivered_view (shared with plan_tools)


def test_delivered_view_projects_session_shape_per_sport():
    run = make_activity("2026-01-06", activity_id="r").model_copy(
        update={
            "name": "Threshold",
            "compliance": 96.0,
            "average_heartrate": 155,
            "max_heartrate": 176,
            "icu_intensity": 91.0,
            "pace": 3.333,
            "gap": 3.4,
            "decoupling": 3.24,
            "interval_summary": ["3x 3m52s 172bpm"],
            "source": "GARMIN_CONNECT",
            "feel": 4,
            "icu_rpe": 8,
        }
    )
    assert delivered_view(run) == {
        "activity_id": "r",
        "name": "Threshold",
        "source": "GARMIN_CONNECT",
        "training_load": 60.0,
        "duration_minutes": 60.0,
        "distance_km": 10.0,
        "compliance_pct": 96.0,
        "avg_hr": 155,
        "max_hr": 176,
        "intensity_factor": 0.91,
        "decoupling_pct": 3.2,
        "interval_summary": ["3x 3m52s 172bpm"],
        "pace_min_km": "5:00",
        "gap_min_km": "4:54",
        "feel": 4,
        "feel_word": "Poor",
        "rpe": 8,
    }
    ride = make_activity(
        "2026-01-06", "Ride", activity_id="b", distance=None
    ).model_copy(
        update={"icu_weighted_avg_watts": 231, "average_speed": 8.5, "pace": 9.0}
    )
    view = delivered_view(ride)
    assert view["normalized_power"] == 231
    assert view["speed_kmh"] == 30.6  # average_speed, not moving pace, for rides
    assert "distance_km" not in view
    assert "compliance_pct" not in view
    # An unanswered watch prompt leaves no verdict keys at all.
    assert not {"feel", "feel_word", "rpe"} & view.keys()


def test_weekly_summary_ignores_activities_outside_the_window():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-05", activity_id="a1")]
    )
    # weeks=0 clamps to a single (current) week; nothing may land elsewhere.
    payload = _call(client, "get_weekly_training_summary", weeks=0)
    assert len(payload["weeks"]) == 1
    assert payload["weeks"][0]["activities"] == 1


def test_weekly_summary_skips_undated_activities():
    client = StubIntervalsClient()
    client.activities = [make_activity("2026-01-05")]
    client.list_activities = lambda oldest, newest: [  # type: ignore[method-assign]
        make_activity("2026-01-05"),
        make_activity("2026-01-05").model_copy(update={"start_date_local": None}),
    ]
    payload = _call(client, "get_weekly_training_summary", weeks=1)
    assert payload["weeks"][0]["activities"] == 1


def test_weekly_summary_ignores_activities_past_the_last_bucket():
    """A future-dated activity must not spill into (or past) the last week."""
    client = StubIntervalsClient()
    client.list_activities = lambda oldest, newest: [  # type: ignore[method-assign]
        make_activity("2026-01-05", activity_id="in"),
        make_activity("2026-01-20", activity_id="future"),
    ]
    payload = _call(client, "get_weekly_training_summary", weeks=1)
    assert payload["weeks"][0]["activities"] == 1


def test_weekly_summary_reports_api_errors():
    client = StubIntervalsClient(activities_error=RuntimeError("boom"))
    payload = _call(client, "get_weekly_training_summary")
    assert "error" in payload
