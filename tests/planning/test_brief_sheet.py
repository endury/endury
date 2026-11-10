"""Tests for the brief sheet assembly (``endury.planning.brief.build_brief``)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.form_context import fetch_form
from endury.database.sqlite.repositories.write_models import SessionInsertRow
from endury.intervals.fitness import advance_training_load
from endury.intervals.models import AthleteProfile, SportSettingsRow, WellnessDay
from endury.planning.brief import build_brief
from endury.planning.brief.fuel import DASH
from endury.wiring.container import RepositoryContainer
from tests.coach.conftest import StubIntervalsClient
from tests.planning.test_brief_history import act

TODAY = date(2026, 10, 23)

SHEET_KEYS = {
    "subject",
    "purpose",
    "state",
    "hold",
    "fuel",
    "conditions",
    "routes",
    "estimate",
    "logistics",
    "rubric",
    "thin",
}


def _hr(low: int, high: int, zone: int) -> dict[str, Any]:
    return {
        "target_type": "heart_rate_zone",
        "zone": zone,
        "low_bpm": low,
        "high_bpm": high,
    }


def _step(kind: str, minutes: float, **targets: Any) -> dict[str, Any]:
    return {"type": kind, "duration_seconds": minutes * 60, **targets}


LONG_TRAIL = [
    _step("warmup", 10, target=_hr(120, 141, 1)),
    _step(
        "interval",
        160,
        target={"target_type": "rpe", "rpe": 4},
        secondary_target=_hr(141, 150, 2),
    ),
    _step("cooldown", 10, target=_hr(120, 141, 1)),
]
SHORT_INTERVALS = [
    _step("warmup", 10, target=_hr(120, 141, 1)),
    {
        "type": "repeat",
        "iterations": 5,
        "steps": [
            _step("interval", 3, target=_hr(160, 170, 4)),
            _step("recovery", 2, target=_hr(120, 141, 1)),
        ],
    },
    _step("cooldown", 15),
]
RIDE = [
    _step("warmup", 10),
    _step(
        "interval",
        130,
        target={"target_type": "power", "low_watts": 200, "high_watts": 230},
        secondary_target=_hr(125, 140, 2),
    ),
    _step("cooldown", 10),
]
SWIM = [
    {"type": "interval", "duration_seconds": 0, "duration_meters": 1500},
]


def _session(
    day: str,
    key: str,
    steps: list[dict[str, Any]],
    *,
    sport: str = "running",
    tss: float = 100.0,
    optional: bool = False,
) -> SessionInsertRow:
    workout = {
        "name": key,
        "steps": steps,
        "coaching_intent": "Long aerobic run on hilly ground.",
        "coaching_notes": ["Hike the steep pitches."],
        "why_details": ["The long run carries the build."],
    }
    return SessionInsertRow(
        slot_id=f"{day}-{key}",
        date=day,
        week_index=0,
        phase_type="build",
        sport=sport,
        activity_key=key,
        family="long",
        target_tss=tss,
        achieved_tss=tss,
        duration_minutes=None,
        workout_json=json.dumps(workout),
        optional=optional,
    )


def _repos(
    container: RepositoryContainer,
    sessions: list[SessionInsertRow],
    races: list[SimpleNamespace] | None = None,
) -> tuple[SimpleNamespace, dict[str, int]]:
    """Commit a plan with ``sessions`` and return repos plus slot -> id."""
    plan_id = container.plan.create(
        name="Trail 20K build",
        goal_type="trail_20k",
        goal_date="2026-11-15",
        plan_start="2026-09-01",
        solve_result_json="{}",
        solver_version="test",
        input_hash="h",
    )
    container.plan.bulk_insert_sessions(plan_id, sessions)
    ids = {row.slot_id: row.id for row in container.plan.get_sessions(plan_id)}
    race_repo = MagicMock()
    race_repo.list.return_value = races or []
    return SimpleNamespace(plan=container.plan, races=race_repo), ids


def _history() -> list[Any]:
    return [
        act(
            "2026-04-25",
            "TrailRun",
            minutes=288,
            hr=158,
            km=40,
            climb=2500,
            name="Coastal Trail Running",
            load=300,
        ),
        act(
            "2026-10-11",
            minutes=159,
            hr=160,
            km=24.1,
            climb=1112,
            name="Hillside Trail Running",
            weather=7,
            load=160,
        ),
        act(
            "2026-10-03",
            minutes=179,
            hr=145,
            km=26.6,
            climb=1129,
            name="Hillside Trail Running",
            load=170,
        ),
        act(
            "2026-10-20",
            minutes=60,
            hr=165,
            summary=["5x 4m 170bpm"],
            device_temp=17,
            load=80,
        ),
        act("2026-10-18", minutes=90, hr=127, km=15.4, climb=302, load=86),
    ]


def _wellness() -> list[WellnessDay]:
    rows = []
    for offset in range(20):
        day = TODAY - timedelta(days=offset)
        rows.append(
            WellnessDay(
                id=day.isoformat(),
                hrv=80 + (offset % 3),
                restingHR=40,
                sleepSecs=8 * 3600,
                weight=78 if offset == 5 else None,
            )
        )
    return rows


def test_a_half_filled_today_does_not_read_as_a_spike(
    container: RepositoryContainer,
) -> None:
    # The watch wrote today's resting heart rate hours before HRV and sleep.
    rows = [
        *(row for row in _wellness() if row.id != TODAY.isoformat()),
        WellnessDay(id=TODAY.isoformat(), restingHR=74),
    ]
    repos, ids = _repos(
        container, [_session("2026-10-25", "run_trail_long", LONG_TRAIL)]
    )
    client = StubIntervalsClient(activities=_history(), wellness=rows)
    with patch("endury.common.clock.today", return_value=TODAY):
        sheet = build_brief(
            repos=repos, client=client, session_id=ids["2026-10-25-run_trail_long"]
        )
    assert sheet["state"]["wellness"]["resting_hr"] == 40
    assert sheet["state"]["flags"] == []


def test_session_by_id_reads_one_row(container: RepositoryContainer) -> None:
    repos, ids = _repos(
        container, [_session("2026-10-25", "run_trail_long", LONG_TRAIL)]
    )
    row = repos.plan.get_session_by_id(ids["2026-10-25-run_trail_long"])
    assert row is not None
    assert row.activity_key == "run_trail_long"
    assert repos.plan.get_session_by_id(9999) is None


def test_long_trail_session(container: RepositoryContainer) -> None:
    repos, ids = _repos(
        container,
        [
            _session("2026-10-24", "run_easy", [_step("interval", 45)], tss=50),
            _session("2026-10-24", "bike_easy", [], sport="cycling", optional=True),
            _session("2026-10-25", "run_trail_long", LONG_TRAIL, tss=168),
        ],
    )
    client = StubIntervalsClient(activities=_history(), wellness=_wellness())
    sid = ids["2026-10-25-run_trail_long"]
    with patch("endury.common.clock.today", return_value=TODAY):
        sheet = build_brief(repos=repos, client=client, session_id=sid)

    assert set(sheet) == SHEET_KEYS
    assert sheet["subject"] == {
        "kind": "session",
        "handle": f"session:{sid}",
        "title": "Trail Long",
        "sport": "running",
        "date": "2026-10-25",
        "days_away": 2,
        "duration_min": 180,
        "distance_km": None,
        "climb_m": None,
        "priority": None,
        "load": 168,
        "optional": False,
    }
    assert sheet["purpose"] == {
        "intent": "Long aerobic run on hilly ground.",
        "why": ["The long run carries the build."],
        "cues": ["Hike the steep pitches."],
    }

    state = sheet["state"]
    form, _ = fetch_form(client, TODAY)  # the coach's own "Form today" reading
    assert state["ctl"] == round(form.ctl, 1)
    assert state["tsb"] == form.tsb
    assert state["tsb_read"] in {
        "Very fresh, more than a race needs",
        "Race fresh",
        "Fresh: ready for a key day",
        "Steady: carrying normal training",
        "Carrying a heavy load",
        "Deep in fatigue: go easy",
    }
    ctl, atl = advance_training_load(form.ctl, form.atl, 50.0)  # optional ride left out
    assert state["projected"] == {
        "date": "2026-10-25",
        "ctl": round(ctl, 1),
        "tsb": round(ctl - atl, 1),
    }
    assert state["wellness"] == {
        "hrv": 80,
        "hrv_baseline": 81,
        "resting_hr": 40,
        "sleep_h": 8.0,
    }
    assert [row["date"] for row in state["recent"]] == [
        "2026-10-20",
        "2026-10-18",
        "2026-10-11",
    ]

    hold = sheet["hold"]
    # The proven cap (158 bpm over three hours) sits above the card's band,
    # so the card's band stands for "then"; the hike line keeps the cap.
    assert hold["anchors"] == [
        {"label": "First hour", "hr_max": 150},
        {"label": "Then", "hr_min": 141, "hr_max": 150},
        {"label": "Hike anything that pushes you over", "hr_max": 160},
    ]
    assert hold["card"] == [
        "Warm-up 10 min — 120-141 bpm (HR Z1)",
        "Main 160 min — RPE 4, 141-150 bpm (HR Z2)",
        "Cool-down 10 min — 120-141 bpm (HR Z1)",
    ]
    # The hour-long interval run's average counts as held too.
    assert [row["avg_hr"] for row in hold["proven"]] == [158, 160, 165]
    assert hold["climb_rate_m_per_h"] == 420
    assert hold["power"] is None

    fuel = sheet["fuel"]
    assert fuel["carbs_g_per_h"] == [60, 80]
    assert fuel["carbs_total_g"] == [180, 240]
    assert f"78{DASH}156 g" in fuel["before"]
    assert fuel["carb_load"] is None
    assert sheet["conditions"]["recent_temp_c"] == [7, 17]
    assert sheet["conditions"]["kit"][0] == "Long sleeve or a shirt with arm warmers"
    assert [row["date"] for row in sheet["routes"]] == ["2026-10-11", "2026-10-03"]
    assert sheet["estimate"] is None
    assert sheet["logistics"] is None
    assert [item["metric"] for item in sheet["rubric"]] == [
        "hr_first_third_max",
        "carbs_g_per_h",
        "finish_feel",
    ]
    assert sheet["rubric"][0]["label"] == "First hour under 150 bpm"
    assert sheet["rubric"][1]["label"] == f"60{DASH}80 g of carbs an hour"
    assert sheet["thin"] == []


def test_short_interval_run_has_no_anchors(container: RepositoryContainer) -> None:
    repos, ids = _repos(
        container, [_session("2026-10-24", "run_vo2", SHORT_INTERVALS, tss=60)]
    )
    client = StubIntervalsClient(activities=_history())
    sheet = build_brief(
        repos=repos, client=client, session_id=ids["2026-10-24-run_vo2"], today=TODAY
    )
    assert sheet["subject"]["duration_min"] == 50
    # The card is the brief: no anchors, no hours held, no routes, no kit.
    assert sheet["hold"]["anchors"] == []
    assert sheet["hold"]["proven"] == []
    assert sheet["hold"]["climb_rate_m_per_h"] is None
    assert "5x Rep 3 min — 160-170 bpm (HR Z4)" in sheet["hold"]["card"]
    assert sheet["routes"] == []
    assert sheet["conditions"] is None
    assert sheet["fuel"]["carbs_g_per_h"] is None
    assert sheet["fuel"]["fluid_ml_per_h"] is None
    assert sheet["rubric"] == [
        {
            "label": "Last rep within 5 % of the first",
            "metric": "rep_fade_pct",
            "value": 5,
        }
    ]
    # No wellness rows: the block is empty, not invented.
    assert sheet["state"]["wellness"] is None


def test_long_intervals_are_briefed_by_their_card(
    container: RepositoryContainer,
) -> None:
    # Seen live: a 75-minute 2x20 sweet spot ride showed three- to
    # five-hour ride heart rates, routes of its length and kit.
    sweet_spot = [
        _step("warmup", 15),
        {
            "type": "repeat",
            "iterations": 2,
            "steps": [
                _step(
                    "interval",
                    20,
                    target={
                        "target_type": "power",
                        "low_watts": 271,
                        "high_watts": 287,
                    },
                ),
                _step("recovery", 5),
            ],
        },
        _step("cooldown", 10),
    ]
    repos, ids = _repos(
        container,
        [_session("2026-10-24", "bike_sweet_spot", sweet_spot, sport="cycling")],
    )
    rides = [
        act("2026-10-12", "Ride", minutes=130, np=240, hr=140, km=60, weather=12),
        act("2026-10-05", "Ride", minutes=310, np=220, hr=135, km=90),
        act("2026-10-08", "Ride", minutes=70, np=260, hr=150, km=31),
    ]
    client = StubIntervalsClient(activities=rides)
    sheet = build_brief(
        repos=repos,
        client=client,
        session_id=ids["2026-10-24-bike_sweet_spot"],
        today=TODAY,
    )
    assert sheet["subject"]["duration_min"] == 75
    hold = sheet["hold"]
    assert hold["anchors"] == []
    assert hold["proven"] == []
    assert hold["card"][1] == "2x Rep 20 min — 271-287 W"
    # The power curve stays: the band's floor against the best hour ridden.
    assert hold["power"] == {"np_60": 260, "np_120": 240, "np_180": 220, "ftp": None}
    assert sheet["routes"] == []
    assert sheet["conditions"] is None
    assert sheet["fuel"]["fluid_ml_per_h"] is None
    assert sheet["fuel"]["notes"] == [
        "Water is enough for this one; no carbs needed on the way."
    ]
    assert [item["metric"] for item in sheet["rubric"]] == ["rep_fade_pct"]
    # Nothing the gate dropped is excused in ``thin``; only the unloaded form.
    assert sheet["thin"] == [
        "No training load is recorded yet, so there is no form reading."
    ]


def test_ride_reads_power(container: RepositoryContainer) -> None:
    repos, ids = _repos(
        container,
        [_session("2026-10-24", "bike_endurance", RIDE, sport="cycling", tss=120)],
    )
    athlete = AthleteProfile(
        id="i1",
        sportSettings=[
            SportSettingsRow(types=["Hike"], ftp=999),
            SportSettingsRow(types=["Run"], lthr=170),
            SportSettingsRow(types=["Ride", "VirtualRide"], ftp=300),
        ],
    )
    rides = [
        act("2026-10-12", "Ride", minutes=130, np=240, hr=140, km=60, weather=12),
        act("2026-10-05", "Ride", minutes=190, np=220, hr=135, km=90),
        act("2026-10-08", "VirtualRide", minutes=70, np=260, hr=150),
    ]
    client = StubIntervalsClient(athlete=athlete, activities=rides)
    sheet = build_brief(
        repos=repos,
        client=client,
        session_id=ids["2026-10-24-bike_endurance"],
        today=TODAY,
    )
    hold = sheet["hold"]
    assert hold["power"] == {"np_60": 260, "np_120": 240, "np_180": 220, "ftp": 300}
    assert hold["anchors"] == [
        {"label": "First hour", "w_max": 209, "hr_max": 130},
        {"label": "Then", "w_min": 207, "w_max": 220, "hr_min": 129, "hr_max": 135},
    ]
    assert [row["date"] for row in sheet["routes"]] == ["2026-10-12"]
    assert sheet["conditions"]["kit"][0].startswith("Jersey with arm")
    assert [item["metric"] for item in sheet["rubric"]] == [
        "hr_first_third_max",
        "carbs_g_per_h",
        "finish_feel",
    ]


def test_swim_has_card_only(container: RepositoryContainer) -> None:
    repos, ids = _repos(
        container, [_session("2026-10-24", "swim_endurance", SWIM, sport="swimming")]
    )
    sheet = build_brief(
        repos=repos,
        client=StubIntervalsClient(),
        session_id=ids["2026-10-24-swim_endurance"],
        today=TODAY,
    )
    assert sheet["hold"]["anchors"] == []
    assert sheet["hold"]["card"] == ["Main 1500 m"]
    assert sheet["conditions"] is None
    assert sheet["routes"] == []


def test_long_run_with_only_short_history_says_so(
    container: RepositoryContainer,
) -> None:
    repos, ids = _repos(
        container, [_session("2026-10-24", "run_long", LONG_TRAIL[1:2], tss=150)]
    )
    client = StubIntervalsClient(activities=[act("2026-10-11", minutes=100, hr=150)])
    sheet = build_brief(
        repos=repos, client=client, session_id=ids["2026-10-24-run_long"], today=TODAY
    )
    assert sheet["hold"]["anchors"][0] == {"label": "First hour", "hr_max": 145}
    assert sheet["thin"][0] == (
        "No run of three hours or more in the last twelve months, so the hold "
        "line rests on shorter efforts."
    )


def test_race_with_an_estimate(container: RepositoryContainer) -> None:
    race = SimpleNamespace(
        name="Trail du Mont",
        race_date="2026-11-01",
        goal_type="trail_20k",
        priority="A",
        distance_km=25.0,
        climb_m=1200.0,
        status="planned",
        notes="Bib pickup Saturday 16:00.",
    )
    repos, _ = _repos(container, [], races=[race])
    client = StubIntervalsClient(activities=_history(), wellness=_wellness())
    sheet = build_brief(repos=repos, client=client, race_date="2026-11-01", today=TODAY)

    assert set(sheet) == SHEET_KEYS
    subject = sheet["subject"]
    assert subject["kind"] == "race"
    assert subject["handle"] == "race:2026-11-01"
    assert subject["priority"] == "A"
    assert subject["climb_m"] == 1200
    assert subject["duration_min"] == 174  # the estimate's midpoint
    assert sheet["purpose"]["intent"] == "Your A race; the plan tapers into it."
    assert sheet["estimate"]["range_min"] == [167, 181]
    assert sheet["hold"]["anchors"] == [
        {"label": "First third", "hr_max": 153},
        {"label": "Middle third", "hr_min": 154, "hr_max": 160},
        {"label": "Last third", "open": True},
    ]
    assert sheet["fuel"]["carb_load"] == {
        "g_per_kg": [7, 10],
        "g": [546, 780],
        "days": 1,
    }
    assert sheet["logistics"] == {"notes": "Bib pickup Saturday 16:00."}
    assert sheet["routes"] == []
    assert sheet["rubric"][0]["label"] == "First third under 153 bpm"
    assert sheet["rubric"][-1]["metric"] == "finish_feel"
    assert sheet["thin"] == []


def test_race_without_enough_history(container: RepositoryContainer) -> None:
    # No race row: the plan's own goal stands in for it.
    repos, _ = _repos(container, [])
    sheet = build_brief(
        repos=repos,
        client=StubIntervalsClient(),
        race_date="2026-11-15",
        today=TODAY,
    )
    assert sheet["subject"]["title"] == "Trail 20K build"
    assert sheet["subject"]["duration_min"] is None
    assert sheet["estimate"] is None
    # No range of its own: fuelled for a typical trail 20K, and said so.
    assert sheet["fuel"]["carbs_g_per_h"] == [30, 60]
    assert (
        "The fuel plan assumes about 2 hours 30 minutes for this race; your own "
        "time may differ." in sheet["thin"]
    )
    assert sheet["hold"]["anchors"] == []
    assert sheet["state"]["ctl"] is None
    assert sheet["state"]["projected"] is None
    assert sheet["conditions"] == {"recent_temp_c": None, "kit": []}
    assert sheet["thin"] == [
        "The race has no distance yet, so there is no finish-time range.",
        "The fuel plan assumes about 2 hours 30 minutes for this race; your own "
        "time may differ.",
        "No continuous run of an hour or more with heart rate in the last twelve "
        "months, so the brief has no hold line.",
        "Fewer than three long hilly runs in the last twelve months, so there is "
        "no climbing rate yet.",
        "No training load is recorded yet, so there is no form reading.",
        "No recent outdoor session carries a temperature, so there is no kit line yet.",
    ]


def test_race_with_too_few_hilly_runs(container: RepositoryContainer) -> None:
    race = SimpleNamespace(
        name="Road Half",
        race_date="2026-11-01",
        goal_type="running_half_marathon",
        priority="B",
        distance_km=21.1,
        climb_m=None,
        status="planned",
        notes=None,
    )
    repos, _ = _repos(container, [], races=[race])
    client = StubIntervalsClient(activities=_history()[1:2])
    sheet = build_brief(repos=repos, client=client, race_date="2026-11-01", today=TODAY)
    assert sheet["estimate"] is None
    assert sheet["thin"][0].startswith("Fewer than two hilly runs")
    assert sheet["purpose"]["intent"].startswith("A tune-up")


def test_triathlon_race_has_no_range(container: RepositoryContainer) -> None:
    race = SimpleNamespace(
        name="Roth",
        race_date="2026-11-01",
        goal_type="ironman",
        priority="C",
        distance_km=226.0,
        climb_m=None,
        status="planned",
        notes=None,
    )
    repos, _ = _repos(container, [], races=[race])
    sheet = build_brief(
        repos=repos, client=StubIntervalsClient(), race_date="2026-11-01", today=TODAY
    )
    assert sheet["subject"]["sport"] == "triathlon"
    assert sheet["hold"]["proven"] == []
    assert sheet["thin"][0].startswith("The finish-time range covers run and trail")
    # No range of its own: fuelled for a typical Ironman day, and said so.
    assert sheet["fuel"]["carbs_g_per_h"] == [60, 90]
    assert sheet["fuel"]["carb_load"]["g_per_kg"] == [7, 10]
    assert (
        "The fuel plan assumes about 12 hours 30 minutes for this race; your own "
        "time may differ." in sheet["thin"]
    )


def test_unknown_subjects_raise(container: RepositoryContainer) -> None:
    repos, _ = _repos(container, [])
    client = StubIntervalsClient()
    with pytest.raises(ValueError, match="No planned session with id 412"):
        build_brief(repos=repos, client=client, session_id=412, today=TODAY)
    with pytest.raises(ValueError, match="No race on 2026-10-24"):
        build_brief(repos=repos, client=client, race_date="2026-10-24", today=TODAY)
    with pytest.raises(ValueError, match="either a planned session or a race"):
        build_brief(repos=repos, client=client, today=TODAY)


def test_card_lines_read_a_short_rest_in_seconds() -> None:
    from endury.planning.brief.sheet import _card_lines

    steps = [
        {
            "type": "repeat",
            "iterations": 2,
            "steps": [
                _step("interval", 8, target={"target_type": "rpe", "rpe": 4}),
                _step("rest", 0.25),
            ],
        }
    ]
    assert _card_lines(steps) == ["2x Rep 8 min — RPE 4", "2x Rest 15 s"]
