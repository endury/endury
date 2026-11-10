"""The predict_race_time coach tool and its tool-summary line."""

import json
from unittest.mock import MagicMock

import pytest

from endury.coach.messages import build_tool_summary
from endury.coach.tools.load_tools import make_load_tools
from endury.intervals.models import IntervalRow
from tests.coach.conftest import (
    StubIntervalsClient,
    make_activity,
    make_activity_detail,
)


def _tool(client):
    """Return the predict_race_time tool bound to a stub client."""
    tools = {t.name: t for t in make_load_tools(MagicMock(), client)}
    return tools["predict_race_time"]


@pytest.fixture
def predict():
    """The tool over a client holding one 5 km run in 20:00."""
    return _tool(
        StubIntervalsClient(
            activities=[
                make_activity(
                    "2026-01-05",
                    activity_id="run5k",
                    moving_time=1200,
                    distance=5000.0,
                ),
                make_activity(
                    "2026-01-06",
                    "Ride",
                    activity_id="ride1",
                    moving_time=3600,
                    distance=30000.0,
                ),
                make_activity(
                    "2026-01-07",
                    "Hike",
                    activity_id="hike1",
                    moving_time=3600,
                    distance=8000.0,
                ),
            ]
        )
    )


def _by_distance(payload):
    return {row["distance"]: row for row in payload["predictions"]}


# By supplied values


def test_predicts_the_standard_distances_from_values(predict):
    payload = json.loads(
        predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
    )
    rows = _by_distance(payload)
    assert payload["basis"] == {
        "source": "values",
        "activity_id": None,
        "distance_m": 5000,
        "time": "20:00",
        "pace_per_km": "4:00/km",
    }
    assert payload["model"]["exponent"] == 1.06
    assert rows["5k"]["predicted_time"] == "20:00"
    assert rows["10k"]["predicted_time"] == "41:42"
    assert rows["half_marathon"]["predicted_time"] == "1:32:00"
    assert rows["marathon"]["predicted_time"] == "3:11:49"
    assert "target" not in payload


def test_confidence_degrades_with_the_extrapolation_reach(predict):
    rows = _by_distance(
        json.loads(
            predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
        )
    )
    assert rows["10k"]["extrapolation_ratio"] == 2.0
    assert rows["10k"]["confidence"].startswith("high")
    assert rows["marathon"]["extrapolation_ratio"] == 8.44
    assert rows["marathon"]["confidence"].startswith("low")


def test_explicit_target_distance_is_predicted_too(predict):
    payload = json.loads(
        predict.invoke(
            {
                "target_distance_m": 15000,
                "known_distance_m": 5000,
                "known_time_seconds": 1200,
            }
        )
    )
    target = payload["target"]
    assert target["distance"] == "target"
    assert target["distance_m"] == 15000
    assert target["predicted_time"] == "1:04:05"
    assert target["pace_per_km"] == "4:16/km"
    assert target["confidence"].startswith("moderate")


def test_caveats_name_the_running_only_limit(predict):
    payload = json.loads(
        predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
    )
    assert any("Running only" in caveat for caveat in payload["caveats"])
    assert any("equally trained" in caveat for caveat in payload["caveats"])


# By activity id


def test_reads_the_effort_from_a_named_activity(predict):
    payload = json.loads(predict.invoke({"activity_id": "run5k"}))
    assert payload["basis"]["source"] == "activity"
    assert payload["basis"]["activity_id"] == "run5k"
    assert payload["basis"]["distance_m"] == 5000
    assert _by_distance(payload)["10k"]["predicted_time"] == "41:42"


def test_refuses_a_non_running_activity(predict):
    payload = json.loads(predict.invoke({"activity_id": "ride1"}))
    assert "running-only" in payload["message"]
    assert "predictions" not in payload


def test_refuses_a_hike_activity(predict):
    payload = json.loads(predict.invoke({"activity_id": "hike1"}))
    assert "running-only" in payload["message"]
    assert "predictions" not in payload


def test_reports_an_activity_without_distance(predict):
    tool = _tool(
        StubIntervalsClient(
            activities=[
                make_activity("2026-01-05", activity_id="nodist", distance=None)
            ]
        )
    )
    payload = json.loads(tool.invoke({"activity_id": "nodist"}))
    assert "no distance or moving time" in payload["message"]


# Basis-quality guardrail


def test_basis_warning_flags_an_interval_shaped_activity():
    tool = _tool(
        StubIntervalsClient(
            details=[
                make_activity_detail(
                    "2026-01-05",
                    activity_id="workout1",
                    distance=12000.0,
                    moving_time=3300,
                    intervals=[
                        IntervalRow(type="WORK", moving_time=420),
                        IntervalRow(type="RECOVERY", moving_time=120),
                        IntervalRow(type="WORK", moving_time=420),
                        IntervalRow(type="RECOVERY", moving_time=120),
                    ],
                )
            ]
        )
    )
    payload = json.loads(tool.invoke({"activity_id": "workout1"}))
    assert "SLOW" in payload["basis_warning"]
    assert "race, time trial" in payload["basis_warning"]
    assert next(iter(payload)) == "basis_warning"


def test_basis_warning_absent_for_a_single_effort_activity():
    tool = _tool(
        StubIntervalsClient(
            details=[
                make_activity_detail(
                    "2026-01-05",
                    activity_id="tt1",
                    distance=10000.0,
                    moving_time=2400,
                    intervals=[IntervalRow(type="WORK", moving_time=2400)],
                )
            ]
        )
    )
    payload = json.loads(tool.invoke({"activity_id": "tt1"}))
    assert "basis_warning" not in payload


def test_basis_warning_absent_for_an_activity_with_no_interval_breakdown(predict):
    payload = json.loads(predict.invoke({"activity_id": "run5k"}))
    assert "basis_warning" not in payload


def test_basis_warning_absent_for_a_named_effort(predict):
    payload = json.loads(
        predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
    )
    assert "basis_warning" not in payload


# Error shapes


def test_asks_for_an_effort_when_nothing_is_supplied(predict):
    payload = json.loads(predict.invoke({"target_distance_m": 10000}))
    assert "known_distance_m" in payload["message"]


def test_negative_distance_relays_the_model_complaint(predict):
    payload = json.loads(
        predict.invoke({"known_distance_m": -5000, "known_time_seconds": 1200})
    )
    assert payload["message"] == "Distances must be positive (metres)."


def test_client_failure_returns_the_standard_error_shape():
    tool = _tool(StubIntervalsClient(detail_error=RuntimeError("boom")))
    payload = json.loads(tool.invoke({"activity_id": "run5k"}))
    assert payload["error"] == "Failed to predict race time."


# Tool summary


def _tool_message(content):
    return {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "predict_race_time",
        "content": content,
    }


def test_summary_lists_the_standard_predictions(predict):
    content = predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
    summary = build_tool_summary([_tool_message(content)])
    assert "from 5000 m in 20:00" in summary
    assert "10k 41:42 (high confidence)" in summary
    assert "marathon 3:11:49 (low confidence)" in summary


def test_summary_narrows_to_the_explicit_target(predict):
    content = predict.invoke(
        {
            "target_distance_m": 15000,
            "known_distance_m": 5000,
            "known_time_seconds": 1200,
        }
    )
    summary = build_tool_summary([_tool_message(content)])
    assert "target 1:04:05 (moderate confidence)" in summary
    assert "marathon" not in summary


def test_summary_relays_a_refusal_message(predict):
    content = predict.invoke({"activity_id": "ride1"})
    summary = build_tool_summary([_tool_message(content)])
    assert "predict_race_time: Activity ride1 is not a run" in summary


def test_summary_falls_back_for_a_non_dict_result():
    summary = build_tool_summary([_tool_message('"nope"')])
    assert "predict_race_time: result received." in summary


def test_basis_warning_flags_a_trail_run_basis():
    tool = _tool(
        StubIntervalsClient(
            activities=[
                make_activity(
                    "2026-01-05",
                    "TrailRun",
                    activity_id="trail1",
                    moving_time=3600,
                    distance=8000.0,
                )
            ]
        )
    )
    payload = json.loads(tool.invoke({"activity_id": "trail1"}))
    assert "trail run" in payload["basis_warning"]
    assert "SLOW" in payload["basis_warning"]
    assert next(iter(payload)) == "basis_warning"


def test_caveats_name_the_flat_road_limit(predict):
    payload = json.loads(
        predict.invoke({"known_distance_m": 5000, "known_time_seconds": 1200})
    )
    assert any("trail or mountain race" in c for c in payload["caveats"])
