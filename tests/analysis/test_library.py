"""The ``ana`` library on synthetic runs, rides and swims."""

from __future__ import annotations

import math

import pytest

from endury.analysis import library as ana
from endury.analysis.charts import ChartError


def _run(seconds: int = 1800) -> dict:
    """A 1 Hz run: flat 10 min, a 10 % climb half walked, a flat finish."""
    time = list(range(seconds))
    speed, grade, cad, alt = [], [], [], []
    height = 100.0
    for t in time:
        if 600 <= t < 1200:
            walking = (t // 60) % 2 == 1
            v = 1.2 if walking else 2.5
            g = 10.0
            c = 55 if walking else 82
        else:
            v, g, c = 3.3, 0.0, 85
        height += v * g / 100
        speed.append(v)
        grade.append(g)
        cad.append(c)
        alt.append(round(height, 2))
    distance = [0.0]
    for v in speed[:-1]:
        distance.append(distance[-1] + v)
    return {
        "time": time,
        "distance": distance,
        "velocity_smooth": speed,
        "ga_velocity": [v * (1 + g / 25) for v, g in zip(speed, grade, strict=True)],
        "fixed_altitude": alt,
        "grade_smooth": grade,
        "cadence": cad,
        "heartrate": [140 + t / 120 for t in time],
        "watts": [250 + 2 * g for g in grade],
        "temp": [15.0] * seconds,
    }


def _ride(seconds: int = 3600) -> dict:
    """A 1 Hz ride with a 5-minute effort at 300 W in each half."""
    watts = [300.0 if 600 <= t % 1800 < 900 else 180.0 for t in range(seconds)]
    speed = [9.0] * seconds
    return {
        "time": list(range(seconds)),
        "distance": [9.0 * t for t in range(seconds)],
        "velocity_smooth": speed,
        "watts": watts,
        "heartrate": [130 + (w - 180) / 6 + t / 400 for t, w in enumerate(watts)],
        "cadence": [90] * seconds,
        "altitude": [200.0] * seconds,
    }


SWIM_LAPS = [
    {
        "type": "WORK",
        "start_time": 0,
        "moving_time": 110,
        "distance": 100.0,
        "average_speed": 100 / 110,
    },
    {
        "type": "RECOVERY",
        "start_time": 110,
        "moving_time": 20,
        "distance": None,
        "average_speed": None,
    },
    {
        "type": "WORK",
        "start_time": 130,
        "moving_time": 106,
        "distance": 100.0,
        "average_speed": 100 / 106,
    },
    {
        "type": "WORK",
        "start_time": 236,
        "moving_time": 50,
        "distance": 50.0,
        "average_speed": 1.0,
    },
]


def _payload() -> dict:
    meta = {
        "run": {
            "id": "r1",
            "date": "2026-10-24",
            "type": "Run",
            "sport": "run",
            "name": "Hill run",
            "km": 4.9,
            "climb_m": 60,
            "elapsed_min": 30,
        },
        "ride": {
            "id": "b1",
            "date": "2026-10-20",
            "type": "Ride",
            "sport": "ride",
            "name": "Efforts",
            "km": 32.4,
            "climb_m": 0,
            "elapsed_min": 60,
        },
        "swim": {
            "id": "s1",
            "date": "2026-10-21",
            "type": "Swim",
            "sport": "swim",
            "name": "Pool",
            "km": 0.25,
            "elapsed_min": 5,
        },
    }
    swim_streams = {
        "time": list(range(286)),
        "distance": [t * 0.9 for t in range(286)],
        "velocity_smooth": [0.9] * 286,
    }
    return {
        "history": [
            {**meta["run"], "moving_min": 30, "load": 40},
            {**meta["ride"], "moving_min": 60, "load": 60},
            {**meta["swim"], "moving_min": 5, "load": 5},
        ],
        "athlete": {"run_lthr": 168},
        "activities": {
            "r1": {"meta": meta["run"], "streams": _run(), "laps": []},
            "b1": {"meta": meta["ride"], "streams": _ride(), "laps": []},
            "s1": {"meta": meta["swim"], "streams": swim_streams, "laps": SWIM_LAPS},
        },
    }


@pytest.fixture(autouse=True)
def _loaded() -> None:
    ana.load(_payload())


def test_load_replaces_the_previous_run_and_clears_charts() -> None:
    ana.chart("line", [1, 2], [[3, 4]], "t")
    ana.load({"history": [], "athlete": {}, "activities": {}})
    assert ana.history == []
    assert ana.athlete == {}
    assert ana.CHARTS == []
    with pytest.raises(LookupError, match="activity_ids"):
        ana.activity("r1")


def test_summary_speaks_each_sports_units() -> None:
    run, ride, swim = (ana.summary(ana.activity(i)) for i in ("r1", "b1", "s1"))
    assert {"pace", "gap_pace", "walked_pct"} <= set(run)
    assert {"kmh", "np"} <= set(ride)
    assert "pace" not in ride
    assert "pace_100m" in swim
    assert "climb_m" not in swim


def test_walking_is_a_runs_low_cadence_only() -> None:
    a = ana.activity("r1")
    # Half of the ten climbing minutes, in a thirty-minute run.
    assert ana.summary(a)["walked_pct"] == pytest.approx(16.7, abs=0.2)
    assert not ana.activity("b1").walking.any()


def test_climbs_find_the_hill_with_its_gradient() -> None:
    (climb,) = ana.climbs(ana.activity("r1"))
    assert climb["grade_pct"] == pytest.approx(10, abs=0.5)
    assert climb["climb_m"] == pytest.approx(111, abs=3)


def test_splits_by_distance_and_by_time() -> None:
    a = ana.activity("r1")
    assert [r["from_km"] for r in ana.splits(a, every_km=2)] == [0.0, 2.0, 4.0]
    assert len(ana.splits(ana.activity("b1"), every_min=20)) == 3


def test_compare_flags_a_small_sample(capsys: pytest.CaptureFixture[str]) -> None:
    a = ana.activity("r1")
    rows = ana.compare(a, [ana.uphill(a, 5), ana.between(a, 0, 5)], ["climb", "start"])
    assert [r["part"] for r in rows] == ["climb", "start"]
    assert "small sample (under 10 minutes): start" in capsys.readouterr().out


def test_walk_vs_run_prints_the_power_trade_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = ana.walk_vs_run(ana.activity("r1"), min_grade=10)
    assert [r["how"] for r in rows] == ["walking", "running"]
    assert "trade-off: running climbed" in capsys.readouterr().out


def test_best_efforts_find_the_five_minute_effort() -> None:
    rows = {r["minutes"]: r for r in ana.best_efforts(ana.activity("b1"), (5, 20))}
    assert rows[5]["best_w"] == 300
    assert rows[20]["best_w"] < 300


def test_decoupling_reads_power_per_beat_on_a_bike() -> None:
    row = ana.decoupling(ana.activity("b1"))
    assert row["unit"] == "W per beat"
    assert row["change_pct"] < 0


def test_swim_laps_group_by_distance() -> None:
    a = ana.activity("s1")
    assert [r["type"] for r in ana.laps(a)] == ["work", "recovery", "work", "work"]
    sets = {r["distance_m"]: r for r in ana.lap_sets(a)}
    assert sets[100]["count"] == 2
    assert sets[100]["fastest"] == "1:46"
    assert sets[50]["count"] == 1


def test_trend_calls_noise_noise_and_a_climb_a_climb() -> None:
    dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
    flat = [1.0 + (0.05 if d % 2 else -0.05) for d in range(20)]
    assert ana.trend(dates, flat)["verdict"].startswith("no clear trend")
    rising = [1.0 + 0.02 * d for d in range(20)]
    assert ana.trend(dates, rising)["verdict"].startswith("rising (better)")
    assert ana.trend(dates[:3], rising[:3])["verdict"] == "too few points for a trend"


def test_trend_names_its_lowest_and_highest_session(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dates = ["2026-08-01", "2026-08-10", "2026-08-20", "2026-09-01"]
    row = ana.trend(dates, [1.3, 1.43, 1.13, 1.25], "m per beat")
    assert row["lowest"] == {"date": "2026-08-20", "value": 1.13}
    assert row["highest"] == {"date": "2026-08-10", "value": 1.43}
    assert "lowest 1.13 on 2026-08-20, highest 1.43 on 2026-08-10" in (
        capsys.readouterr().out
    )


def test_weekly_totals_by_sport() -> None:
    rows = ana.weekly("hours", weeks=1)
    assert rows == [
        {"week": "2026-10-19", "swim": 0.1, "ride": 1.0, "run": 0.5, "other": 0.0}
    ]


def test_overview_prints_every_sport(capsys: pytest.CaptureFixture[str]) -> None:
    for i in ("r1", "b1", "s1"):
        ana.overview(ana.activity(i))
    out = capsys.readouterr().out
    assert "climbs of 40 m or more" in out
    assert "best efforts" in out
    assert "work laps by distance" in out


def test_pace_and_seconds() -> None:
    assert ana.pace(1000 / 245) == "4:05"
    assert ana.pace(100 / 107, per=100) == "1:47"
    assert ana.pace(0) == "-"
    assert ana.seconds(4.0) == 250
    assert math.isnan(ana.seconds([0.0])[0])


def test_chart_keeps_three_and_refuses_a_fourth() -> None:
    for i in range(3):
        ana.chart("line", [1, 2], [[1, 2]], f"t{i}", unit="W")
    with pytest.raises(ChartError, match="at most 3"):
        ana.chart("line", [1, 2], [[1, 2]], "t3")
