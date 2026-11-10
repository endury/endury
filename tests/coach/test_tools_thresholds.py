"""Tests for setting thresholds from the chat and the race-time estimate."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from endury.coach.card_summary import preview_summary
from endury.coach.proposals import apply_proposal, proposal_from_result, run_tool
from endury.coach.tools.zone_tools import make_zone_tools
from endury.intervals.models import AthleteProfile, SportSettingsRow
from tests.doubles import FakeIntervals

pytestmark = pytest.mark.coach


def _fake() -> FakeIntervals:
    return FakeIntervals(
        athlete=AthleteProfile(
            id="i1",
            sportSettings=[
                # 1000 / 3.3 = 303 sec/km -> 5:03/km
                SportSettingsRow(
                    id=1, types=["Run", "TrailRun"], lthr=165, threshold_pace=3.3
                ),
                SportSettingsRow(id=2, types=["Ride"], ftp=250, lthr=160),
            ],
        )
    )


def _tool(client, name):
    return next(t for t in make_zone_tools(client) if t.name == name)


def _set(fake, **args):
    return json.loads(_tool(fake.client(), "set_athlete_thresholds").invoke(args))


def _row(fake, row_id):
    return next(r for r in fake.athlete.sport_settings or [] if r.id == row_id)


# set_athlete_thresholds


def test_preview_shows_current_and_proposed_and_writes_nothing():
    fake = _fake()

    result = _set(fake, sport="running", threshold_pace_min_km="4:52", lthr_bpm=172)

    assert result["status"] == "preview"
    assert result["confirm_required"] is True
    assert result["changes"] == [
        {"label": "threshold pace", "current": "5:03/km", "proposed": "4:52/km"},
        {"label": "threshold heart rate", "current": "165 bpm", "proposed": "172 bpm"},
    ]
    assert _row(fake, 1).lthr == 165
    assert not any(method == "PUT" for method, _path in fake.calls)


def test_confirm_writes_to_intervals_and_says_what_changed():
    fake = _fake()

    result = _set(
        fake, sport="running", threshold_pace_min_km="4:52", lthr_bpm=172, confirm=True
    )

    assert result["status"] == "ok"
    assert result["updated_types"] == ["Run", "TrailRun"]
    assert result["changes"][1]["proposed"] == "172 bpm"
    assert _row(fake, 1).lthr == 172
    assert round(1000.0 / (_row(fake, 1).threshold_pace or 1.0)) == 292
    assert _row(fake, 2).lthr == 160


def test_the_card_line_names_each_value_in_whole_units():
    fake = _fake()
    preview = _set(fake, sport="running", threshold_pace_min_km="4:52", lthr_bpm=172)

    assert preview_summary("set_athlete_thresholds", preview) == (
        "Set your running threshold pace to 4:52/km and threshold heart rate to "
        "172 bpm on intervals.icu."
    )


def test_the_model_only_previews_and_the_card_applies_the_write():
    fake = _fake()
    tools = make_zone_tools(fake.client())
    repos = MagicMock()
    repos.plan.get_active.return_value = None
    args = {"sport": "cycling", "ftp_watts": 262, "confirm": True}

    result = run_tool(tools, "set_athlete_thresholds", args)
    proposal = proposal_from_result(
        tools, repos, "set_athlete_thresholds", args, result
    )

    assert _row(fake, 2).ftp == 250
    assert proposal is not None
    assert proposal.preview_summary == "Set your cycling FTP to 262 W on intervals.icu."
    outcome = apply_proposal(tools, repos, proposal)
    assert outcome.status == "applied"
    assert outcome.plan_changed is True
    assert _row(fake, 2).ftp == 262


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            {"sport": "running", "lthr_bpm": 250},
            "lthr_bpm must be between 80 bpm and 230 bpm.",
        ),
        (
            {"sport": "running", "threshold_pace_min_km": "1:30"},
            "threshold_pace_min_km must be between 2:00/km and 15:00/km.",
        ),
        (
            {"sport": "swimming", "css_min_100m": "6:00"},
            "css_min_100m must be between 0:50/100 m and 5:00/100 m.",
        ),
        ({"sport": "running", "ftp_watts": 250}, "running has no ftp_watts."),
        ({"sport": "running"}, "Pass at least one threshold to set."),
        (
            {"sport": "running", "threshold_pace_min_km": "4.52"},
            "'4.52' is not a time; write it as M:SS or H:MM:SS.",
        ),
    ],
)
def test_out_of_bounds_values_are_refused_before_any_read(args, message):
    fake = _fake()

    result = _set(fake, **args)

    assert result == {"status": "error", "message": message}
    assert fake.calls == []


def test_a_sport_without_settings_is_refused_in_the_preview():
    result = _set(_fake(), sport="swimming", css_min_100m="1:35")

    assert result["status"] == "error"
    assert "no swimming sport settings" in result["message"]


def test_a_rejected_key_reports_an_error():
    fake = _fake()
    fake.fail_next(401)

    result = _set(fake, sport="cycling", ftp_watts=262)

    assert result["error"] == "Failed to set the athlete's thresholds."


# estimate_threshold_from_race


def _estimate(distance_km, finish_time):
    tool = _tool(FakeIntervals().client(), "estimate_threshold_from_race")
    return json.loads(
        tool.invoke({"distance_km": distance_km, "finish_time": finish_time})
    )


def test_a_10k_in_50_minutes_predicts_the_hour_race_pace():
    # 10 km x (60 / 50)^(1 / 1.06) = 11.877 km in an hour -> 3600 / 11.877
    # = 303.1 s/km.
    result = _estimate(10.0, "50:00")

    assert result["status"] == "ok"
    assert result["one_hour_distance_km"] == 11.88
    assert result["threshold_pace_min_km"] == "5:03"
    assert result["race_pace_min_km"] == "5:00"
    assert "set_athlete_thresholds" in result["next_step"]
    assert '"5:03"' in result["next_step"]


def test_a_half_marathon_time_with_hours_parses():
    # 21.0975 km x (3600 / 6300)^(1 / 1.06) = 12.444 km -> 289.3 s/km.
    result = _estimate(21.0975, "1:45:00")

    assert result["threshold_pace_min_km"] == "4:49"


@pytest.mark.parametrize(
    ("distance_km", "finish_time", "message"),
    [
        (2.0, "7:00", "Use a race of 3 km or longer"),
        (5.0, "9:30", "between 10 minutes and 6 hours"),
        (100.0, "6:30:00", "between 10 minutes and 6 hours"),
        (10.0, "50:75", "minutes and seconds stay below 60"),
    ],
)
def test_out_of_range_races_are_refused(distance_km, finish_time, message):
    result = _estimate(distance_km, finish_time)

    assert result["status"] == "error"
    assert message in result["message"]
