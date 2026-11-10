"""Tests for the threshold tool backed by intervals.icu sport settings."""

import json

import pytest

from endury.coach.tools.zone_tools import make_zone_tools
from endury.intervals.models import AthleteProfile, SportSettingsRow
from tests.coach.conftest import StubIntervalsClient

pytestmark = pytest.mark.coach


def _tool(client):
    return make_zone_tools(client)[0]


def _profile(*rows):
    return AthleteProfile(id="i1", sportSettings=list(rows))


def test_make_zone_tools_reads_first_then_sets_and_estimates():
    tools = make_zone_tools(StubIntervalsClient())
    assert [t.name for t in tools] == [
        "get_athlete_thresholds",
        "set_athlete_thresholds",
        "estimate_threshold_from_race",
    ]


def test_reports_every_sport_with_settings(stub_intervals):
    result = json.loads(_tool(stub_intervals).invoke({}))
    by_sport = {entry["sport"]: entry for entry in result}
    assert by_sport["running"]["lthr_bpm"] == 165
    assert by_sport["running"]["max_hr_bpm"] == 190
    # 3.5 m/s -> 1000/3.5 = 285.7 sec/km
    assert by_sport["running"]["threshold_pace_sec_per_km"] == 285.7
    assert by_sport["cycling"]["ftp_watts"] == 260
    assert "threshold_pace_sec_per_km" not in by_sport["cycling"]


def test_swim_pace_converts_to_css_per_100m():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Swim"], threshold_pace=1.25))
    )
    result = json.loads(_tool(client).invoke({}))
    assert result[0]["sport"] == "swimming"
    assert result[0]["css_sec_per_100m"] == 80.0


def test_sport_filter_selects_one_sport(stub_intervals):
    result = json.loads(_tool(stub_intervals).invoke({"sport": "Cycling"}))
    assert [entry["sport"] for entry in result] == ["cycling"]


def test_unknown_sport_filter_reports_a_message(stub_intervals):
    result = json.loads(_tool(stub_intervals).invoke({"sport": "swimming"}))
    assert "No intervals.icu sport settings for 'swimming'." in result["message"]


def test_no_settings_reports_a_message():
    client = StubIntervalsClient(athlete=_profile())
    result = json.loads(_tool(client).invoke({}))
    assert result["message"].startswith("No sport settings configured")


def test_unmapped_activity_types_are_ignored():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Yoga"], lthr=140))
    )
    result = json.loads(_tool(client).invoke({}))
    assert result["message"].startswith("No sport settings configured")


def test_row_without_types_is_ignored():
    client = StubIntervalsClient(athlete=_profile(SportSettingsRow(ftp=200)))
    result = json.loads(_tool(client).invoke({}))
    assert result["message"].startswith("No sport settings configured")


def test_first_row_wins_for_a_sport():
    client = StubIntervalsClient(
        athlete=_profile(
            SportSettingsRow(types=["Run"], lthr=160),
            SportSettingsRow(types=["TrailRun"], lthr=170),
        )
    )
    result = json.loads(_tool(client).invoke({}))
    assert [entry["lthr_bpm"] for entry in result] == [160]


def test_hike_row_before_run_row_does_not_claim_running():
    client = StubIntervalsClient(
        athlete=_profile(
            SportSettingsRow(types=["Hike"], lthr=140),
            SportSettingsRow(types=["Run"], lthr=170),
        )
    )
    result = json.loads(_tool(client).invoke({}))
    by_sport = {entry["sport"]: entry for entry in result}
    assert by_sport["running"]["lthr_bpm"] == 170


def test_missing_sport_settings_key_is_tolerated():
    client = StubIntervalsClient(athlete=AthleteProfile(id="i1"))
    result = json.loads(_tool(client).invoke({}))
    assert result["message"].startswith("No sport settings configured")


def test_api_errors_are_reported():
    client = StubIntervalsClient(athlete_error=RuntimeError("401"))
    result = json.loads(_tool(client).invoke({}))
    assert "error" in result


# Zone band tables alongside raw thresholds


def test_running_row_reports_heart_rate_and_pace_bands():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Run"], lthr=170, threshold_pace=3.97))
    )
    result = json.loads(_tool(client).invoke({}))
    zones = result[0]["zones"]
    assert set(zones) == {"heart_rate", "pace"}
    assert zones["heart_rate"]["units"] == "bpm"
    assert len(zones["heart_rate"]["bands"]) == 5
    assert zones["pace"]["units"] == "min/km"
    assert len(zones["pace"]["bands"]) == 7
    # 170 bpm, band 4 (Threshold, 95-100% LTHR) = 161.5-170.
    z4 = next(b for b in zones["heart_rate"]["bands"] if b["zone"] == 4)
    assert (z4["low"], z4["high"]) == (162, 170)


def test_cycling_row_with_ftp_only_reports_power_and_no_heart_rate():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Ride"], ftp=280))
    )
    result = json.loads(_tool(client).invoke({}))
    zones = result[0]["zones"]
    assert "power" in zones
    assert "heart_rate" not in zones
    assert zones["power"]["units"] == "watts"
    assert len(zones["power"]["bands"]) == 7


def test_swimming_row_reports_swim_pace_bands():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Swim"], threshold_pace=1.25))
    )
    result = json.loads(_tool(client).invoke({}))
    zones = result[0]["zones"]
    assert set(zones) == {"swim_pace"}
    assert zones["swim_pace"]["units"] == "sec/100m"
    assert len(zones["swim_pace"]["bands"]) == 6


def test_row_without_thresholds_omits_zones_key():
    client = StubIntervalsClient(
        athlete=_profile(SportSettingsRow(types=["Run"], max_hr=190))
    )
    result = json.loads(_tool(client).invoke({}))
    assert "zones" not in result[0]


def test_reports_the_athletes_own_zones_with_their_names():
    client = StubIntervalsClient(
        athlete=_profile(
            SportSettingsRow(
                types=["Swim"],
                lthr=168,
                threshold_pace=1.25,
                hr_zones=[141, 150, 158, 167, 185],
                hr_zone_names=["Recovery", "Aerobic", "Tempo", "Sub", "Super"],
                pace_zones=[77.5, 87.7, 94.3, 100, 999],
                pace_zone_names=["Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5"],
            )
        )
    )
    zones = json.loads(_tool(client).invoke({}))[0]["zones"]

    assert zones["heart_rate"]["units"] == "bpm"
    assert zones["heart_rate"]["bands"][1] == {
        "zone": 2,
        "name": "Aerobic",
        "low": 141,
        "high": 150,
    }
    # CSS 80 s/100 m: zone 1 is anything slower than 103.2 s, zone 3 91.2-84.8 s.
    assert zones["swim_pace"]["units"] == "sec/100m"
    assert zones["swim_pace"]["bands"][0] == {
        "zone": 1,
        "name": "Zone 1",
        "low": 103.2,
        "high": None,
    }
    assert zones["swim_pace"]["bands"][2]["high"] == 91.2
