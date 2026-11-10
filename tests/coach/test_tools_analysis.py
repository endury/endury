"""The coach's analysis tool: the data it gathers, the run, the charts."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest

from endury.analysis.runner import run_local
from endury.coach.tools.analysis_tools import (
    CHART_MARKER,
    athlete_thresholds,
    build_payload,
    charts_for_model,
    history_row,
    make_analysis_tools,
    take_charts,
    with_chart_lines,
)
from endury.coach.tools.registry import execute_tool_async
from endury.common import clock
from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalsActivity,
    SportSettingsRow,
)
from tests.doubles import FakeIntervals

pytestmark = pytest.mark.coach

RUN = IntervalsActivity(
    id="i9",
    name="Tempo",
    start_date_local="2026-10-20T07:00:00",
    type="Run",
    moving_time=600,
    elapsed_time=620,
    distance=2400,
    average_speed=4.0,
    gap=4.0,
    average_heartrate=150,
    icu_hr_zone_times=[100, 400, 100, 0, 0],
)


def _fake() -> FakeIntervals:
    seconds = 600
    streams = {
        "time": list(range(seconds)),
        "distance": [4.0 * t for t in range(seconds)],
        "velocity_smooth": [4.0] * seconds,
        "heartrate": [140 + t / 60 for t in range(seconds)],
    }
    return FakeIntervals(
        athlete=AthleteProfile(
            id="i1",
            sportSettings=[
                SportSettingsRow(
                    types=["Run", "TrailRun"], lthr=168, max_hr=185, threshold_pace=4.0
                ),
                SportSettingsRow(types=["Ride"], lthr=156, ftp=300),
            ],
        ),
        activities=[RUN],
        streams={"i9": [ActivityStream(type=k, data=v) for k, v in streams.items()]},
    )


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "today", lambda: date(2026, 10, 25))


def test_history_row_carries_sport_speeds_and_the_easy_flag() -> None:
    row = history_row(RUN)
    assert row["sport"] == "run"
    assert row["date"] == "2026-10-20"
    assert row["speed_mps"] == 4.0
    assert row["km"] == 2.4
    assert row["easy"] is True  # 500 of 600 s in zones 1-2
    assert (
        history_row(RUN.model_copy(update={"icu_hr_zone_times": [100, 300, 200]}))[
            "easy"
        ]
        is False
    )
    assert history_row(IntervalsActivity(id="x", type="Ride"))["easy"] is None


def test_thresholds_are_read_per_sport() -> None:
    out = athlete_thresholds(_fake().athlete)
    assert out["run_lthr"] == 168
    assert out["run_threshold_pace"] == "4:10"
    assert out["ride_ftp"] == 300
    assert athlete_thresholds(None) == {}


async def test_a_script_reads_the_activity_and_charts_it() -> None:
    (tool,) = make_analysis_tools(_fake().client(), run_local)
    code = (
        "a = ana.activity('i9')\n"
        "print(ana.summary(a)['pace'], len(ana.history), ana.athlete['run_lthr'])\n"
        "ana.chart('line', a.t / 60, [a.hr], 'Heart rate', 'min', 'bpm')\n"
    )
    raw = await execute_tool_async(
        [tool], "run_analysis", {"code": code, "activity_ids": "i9"}
    )
    text, charts = take_charts(raw)
    result = json.loads(text)
    assert result["output"] == "4:10 1 168\n"
    assert result["error"] is None
    assert result["charts_attached"] == ["Heart rate (line, 1 series, 200 points)"]
    assert "ys" not in text
    assert charts[0]["unit"] == "bpm"
    assert len(charts[0]["x"]) == 200


async def test_a_failing_script_comes_back_to_fix() -> None:
    (tool,) = make_analysis_tools(_fake().client(), run_local)
    raw = await execute_tool_async(
        [tool], "run_analysis", {"code": "ana.activity('i9')", "activity_ids": ""}
    )
    assert "list it in activity_ids" in json.loads(raw)["error"]


async def test_too_many_activities_are_refused() -> None:
    (tool,) = make_analysis_tools(_fake().client(), run_local)
    ids = ",".join(f"i{n}" for n in range(13))
    raw = await execute_tool_async(
        [tool], "run_analysis", {"code": "", "activity_ids": ids}
    )
    assert json.loads(raw) == {"error": "At most 12 activities per script."}


async def test_a_sandbox_failure_is_a_tool_error() -> None:
    async def broken(payload: dict) -> dict:
        raise RuntimeError("sandbox gone")

    (tool,) = make_analysis_tools(_fake().client(), broken)
    raw = await execute_tool_async([tool], "run_analysis", {"code": "print(1)"})
    assert "error" in json.loads(raw)


def test_stored_charts_come_back_for_the_page_and_as_titles_for_the_model() -> None:
    chart = {"kind": "bar", "title": "Hours per sport", "x": ["a"], "ys": [[1]]}
    stored = with_chart_lines("Here you go.", [chart])
    assert stored.splitlines()[1].startswith(CHART_MARKER)
    assert (
        charts_for_model(stored)
        == "Here you go.\n[Chart shown under this reply: Hours per sport]"
    )
    assert with_chart_lines("Plain.", []) == "Plain."
    assert charts_for_model("Plain.") == "Plain."


def test_take_charts_leaves_other_results_alone() -> None:
    assert take_charts("not json") == ("not json", [])
    assert take_charts('{"output": "x"}') == ('{"output": "x"}', [])


def _gps_fake() -> FakeIntervals:
    """Two short activities, the second starting 100 m east of the first."""
    lat0, lng0 = 48.8, 9.8
    per_m_lng = 1 / (111_320 * math.cos(math.radians(lat0)))

    def track(east0: float) -> list[ActivityStream]:
        east = [east0 + 3.0 * t for t in range(60)]
        return [
            ActivityStream(type="time", data=list(range(60))),
            ActivityStream(
                type="latlng",
                data=[lat0] * 60,
                data2=[lng0 + e * per_m_lng for e in east],
            ),
        ]

    runs = [RUN.model_copy(update={"id": i, "route_id": 7}) for i in ("g1", "g2")]
    return FakeIntervals(
        activities=runs, streams={"g1": track(0.0), "g2": track(100.0)}
    )


def test_gps_reaches_the_sandbox_as_metres_from_a_shared_point() -> None:
    payload = build_payload(_gps_fake().client(), "", ["g1", "g2"])

    first, second = (payload["activities"][i]["streams"] for i in ("g1", "g2"))
    assert "lat" not in first
    assert "lng" not in first
    assert first["x"][0] == 0.0
    assert first["x"][10] == pytest.approx(30.0, abs=0.2)
    assert second["x"][0] == pytest.approx(100.0, abs=0.3)
    assert second["y"][0] == 0.0
    assert payload["activities"]["g1"]["meta"]["route_id"] == 7


def test_an_activity_without_gps_gets_no_positions() -> None:
    payload = build_payload(_fake().client(), "", ["i9"])
    assert "x" not in payload["activities"]["i9"]["streams"]
