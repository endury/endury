"""Tests for the brief sheet's history readings (``endury.planning.brief.history``)."""

from __future__ import annotations

from datetime import date

from endury.intervals.models import IntervalsActivity
from endury.planner.foundations.types import SportType
from endury.planning.brief.history import (
    climb_rate_m_per_h,
    hold_curve,
    power_curve,
    recent_sessions,
    recent_temp_c,
    routes_like,
    trail_estimate,
)

TODAY = date(2026, 10, 23)


def act(
    day: str,
    sport_type: str = "Run",
    *,
    minutes: float = 60,
    hr: int | None = None,
    np: int | None = None,
    km: float | None = None,
    climb: float | None = None,
    name: str | None = None,
    activity_id: str | None = None,
    summary: list[str] | None = None,
    weather: float | None = None,
    device_temp: float | None = None,
    load: float | None = None,
) -> IntervalsActivity:
    """Build one intervals.icu activity for the brief tests."""
    return IntervalsActivity(
        id=activity_id or f"{day}-{sport_type}-{minutes}",
        name=name,
        start_date_local=f"{day}T08:00:00",
        type=sport_type,
        moving_time=round(minutes * 60),
        average_heartrate=hr,
        icu_weighted_avg_watts=np,
        distance=km * 1000 if km is not None else None,
        total_elevation_gain=climb,
        interval_summary=summary,
        average_weather_temp=weather,
        average_temp=device_temp,
        icu_training_load=load,
    )


def test_hold_curve_keeps_one_row_per_winner_longest_first() -> None:
    rows = hold_curve(
        [
            act("2026-04-25", "TrailRun", minutes=288, hr=158, name="Coast"),
            act("2026-10-11", minutes=159, hr=160, name="Hillside"),
            act("2026-10-03", minutes=179, hr=145),
            # intervals.icu lap-marks a race ("21x 4m23s"): its average still
            # counts as held. A hike and a ride are not running efforts.
            act(
                "2026-09-27",
                minutes=93,
                hr=172,
                name="City",
                summary=["21x 4m23s 171bpm"],
            ),
            act("2026-10-19", "Hike", minutes=300, hr=170),
            act("2026-10-18", "Ride", minutes=300, hr=175),
        ],
        SportType.RUNNING,
    )
    assert rows == [
        {"duration_min": 288, "avg_hr": 158, "date": "2026-04-25", "name": "Coast"},
        {"duration_min": 159, "avg_hr": 160, "date": "2026-10-11", "name": "Hillside"},
        {"duration_min": 93, "avg_hr": 172, "date": "2026-09-27", "name": "City"},
    ]


def test_hold_curve_keeps_a_single_lap_and_prefers_the_newest_on_ties() -> None:
    rows = hold_curve(
        [
            act("2026-09-01", minutes=70, hr=150, summary=["1x 70m 150bpm"]),
            act("2026-10-01", minutes=65, hr=150),
        ],
        SportType.RUNNING,
    )
    assert [row["date"] for row in rows] == ["2026-10-01"]
    assert rows[0]["name"] == "Run"


def test_power_curve_reads_normalized_power_by_bucket() -> None:
    curve = power_curve(
        [
            act("2026-10-01", "Ride", minutes=130, np=240),
            act("2026-10-05", "Ride", minutes=190, np=220),
            act("2026-10-08", "VirtualRide", minutes=70, np=260),
            act("2026-10-09", "Run", minutes=200, np=300),
        ]
    )
    assert curve == {"np_60": 260, "np_120": 240, "np_180": 220}
    assert power_curve([]) == {"np_60": None, "np_120": None, "np_180": None}


def test_climb_rate_is_the_median_of_the_best_three() -> None:
    runs = [
        act("2026-04-25", minutes=288, climb=2500),  # 521 m/h
        act("2026-10-11", minutes=159, climb=1112),  # 420 m/h
        act("2026-10-03", minutes=180, climb=1129),  # 376 m/h
        act("2026-09-03", minutes=180, climb=600),  # 200 m/h
        act("2026-09-05", minutes=60, climb=900),  # too short
    ]
    assert climb_rate_m_per_h(runs) == 420
    assert climb_rate_m_per_h(runs[:2]) is None


def test_routes_like_matches_length_climb_and_window() -> None:
    activities = [
        act("2026-10-11", minutes=159, km=24.1, climb=1112, name="A"),
        act("2026-10-03", minutes=179, km=26.6, climb=1129, name="B"),
        act("2026-10-02", minutes=170, km=30.0, climb=100, name="Flat"),
        act("2026-10-01", "VirtualRun", minutes=170, name="Treadmill"),
        act("2026-03-01", minutes=170, name="Old"),
        act("2026-10-05", minutes=60, name="Short"),
    ]
    rows = routes_like(activities, SportType.RUNNING, 180, today=TODAY)
    assert [row["name"] for row in rows] == ["A", "B", "Flat"]
    assert rows[0] == {
        "date": "2026-10-11",
        "name": "A",
        "distance_km": 24.1,
        "climb_m": 1112,
        "duration_min": 159,
    }
    hilly = routes_like(activities, SportType.RUNNING, 180, today=TODAY, climb_m=1100)
    assert [row["name"] for row in hilly] == ["A", "B"]
    assert (
        routes_like(activities, SportType.RUNNING, 180, today=TODAY, limit=1)[0]["name"]
        == "A"
    )


def test_trail_estimate_ranges_over_the_basis_paces() -> None:
    basis = [
        act("2026-10-11", minutes=159, km=24.1, climb=1112),
        act("2026-10-03", minutes=179, km=26.6, climb=1129),
    ]
    estimate = trail_estimate(basis, 25.0, 1200, today=TODAY)
    assert estimate is not None
    # 37 flat-equivalent km at 4.51 and 4.72 min per km.
    assert estimate["range_min"] == [167, 175]
    assert "2 fastest long hilly runs" in estimate["method"]
    assert "wider" not in estimate["method"]
    assert [row["date"] for row in estimate["basis"]] == ["2026-10-11", "2026-10-03"]


def test_trail_estimate_rests_on_the_fastest_three_not_the_latest() -> None:
    runs = [
        act("2026-10-18", minutes=90, km=15.4, climb=302),  # an easy 4.9 min/km
        act("2026-10-11", minutes=159, km=24.1, climb=1112),
        act("2026-10-03", minutes=179, km=26.6, climb=1129),
        act("2026-09-20", minutes=150, km=24.0, climb=1000),  # 4.41 min/km
    ]
    estimate = trail_estimate(runs, 25.0, 1200, today=TODAY)
    assert estimate is not None
    assert [row["date"] for row in estimate["basis"]] == [
        "2026-09-20",
        "2026-10-11",
        "2026-10-03",
    ]
    assert "3 fastest" in estimate["method"]


def test_trail_estimate_widens_for_a_longer_race_and_needs_two_runs() -> None:
    basis = [
        act("2026-10-11", minutes=159, km=24.1, climb=1112),
        act("2026-10-03", minutes=179, km=26.6, climb=1129),
        act("2026-09-20", minutes=120, km=18.0, climb=200),  # too flat
        act("2026-05-01", minutes=200, km=30.0, climb=1500),  # too old
    ]
    estimate = trail_estimate(basis, 50.0, 2500, today=TODAY)
    assert estimate is not None
    assert estimate["range_min"] == [339, 372]  # 75 km; slow end x 1.05
    assert "wider" in estimate["method"]
    assert trail_estimate(basis[:1], 25.0, 1200, today=TODAY) is None


def test_recent_temperature_from_outdoor_sessions() -> None:
    activities = [
        act("2026-10-11", weather=7.4),
        act("2026-10-20", "Ride", device_temp=16.6),
        act("2026-10-21", "VirtualRide", device_temp=25),
        act("2026-10-22", "Swim", device_temp=28),
        act("2026-09-01", weather=30),
    ]
    assert recent_temp_c(activities, today=TODAY) == [7, 17]
    assert recent_temp_c(activities[2:], today=TODAY) is None


def test_recent_sessions_newest_first_of_one_sport() -> None:
    activities = [
        act("2026-10-18", minutes=90, hr=127, km=15.4, climb=302, load=86, name="A"),
        act("2026-10-20", minutes=45, name="Short"),
        act("2026-10-21", "Ride", minutes=120, name="Ride"),
        act("2026-10-10", minutes=70, name="B"),
    ]
    rows = recent_sessions(activities, SportType.RUNNING)
    assert [row["name"] for row in rows] == ["A", "B"]
    assert rows[0] == {
        "date": "2026-10-18",
        "name": "A",
        "distance_km": 15.4,
        "climb_m": 302,
        "duration_min": 90,
        "avg_hr": 127,
        "load": 86,
    }
    assert rows[1]["load"] is None
    assert len(recent_sessions(activities, None)) == 3
