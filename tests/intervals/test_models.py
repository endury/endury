"""Tests for endury.intervals.models — pydantic parsing and SPORT_TYPE_MAP."""

from __future__ import annotations

from endury.intervals.models import (
    SPORT_TYPE_MAP,
    ActivityStream,
    AthleteProfile,
    IntervalsActivity,
    IntervalsActivityDetail,
    IntervalsEvent,
    IntervalsPushError,
    SportSettingsRow,
    WellnessDay,
)
from endury.planner.foundations.types import SportType


def test_sport_settings_row_ignores_unknown_fields():
    row = SportSettingsRow.model_validate(
        {
            "types": ["Run"],
            "ftp": None,
            "lthr": 160,
            "max_hr": 188,
            "threshold_pace": 3.4,
            "pace_units": "MINS_KM",
            "warmup_time": 600,  # unmodeled — must be dropped
        }
    )
    assert row.types == ["Run"]
    assert row.lthr == 160
    assert not hasattr(row, "warmup_time")


def test_sport_settings_row_carries_the_athletes_zones():
    row = SportSettingsRow.model_validate(
        {
            "types": ["Swim"],
            "lthr": 168,
            "hr_zones": [141, 150, 158, 167, 185],
            "hr_zone_names": ["Recovery", "Aerobic", "Tempo", "Sub", "Super"],
            "pace_zones": [77.5, 87.7, 94.3, 100.0, 999.0],
            "pace_zone_names": ["Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5"],
        }
    )
    assert row.hr_zones == [141, 150, 158, 167, 185]
    assert row.hr_zone_names[0] == "Recovery"
    assert row.pace_zones[1] == 87.7
    assert row.power_zones is None


def test_sport_settings_row_defaults_when_absent():
    row = SportSettingsRow.model_validate({})
    assert row.types is None
    assert row.ftp is None
    assert row.pace_units is None


def test_athlete_profile_parses_camelcase_sport_settings():
    profile = AthleteProfile.model_validate(
        {
            "id": "999",
            "sportSettings": [
                {"types": ["Ride"], "ftp": 240},
            ],
            "email": "athlete@example.com",  # unmodeled — must be dropped
        }
    )
    assert profile.id == "999"
    assert len(profile.sport_settings) == 1
    assert profile.sport_settings[0].ftp == 240


def test_athlete_profile_populate_by_name_accepts_snake_case():
    profile = AthleteProfile.model_validate({"id": "1", "sport_settings": []})
    assert profile.sport_settings == []


def test_athlete_profile_sport_settings_defaults_to_none_when_absent():
    profile = AthleteProfile.model_validate({"id": "1"})
    assert profile.sport_settings is None


def test_intervals_activity_optional_safe_parsing():
    activity = IntervalsActivity.model_validate(
        {
            "id": "a1",
            "start_date_local": "2026-08-07T06:30:00",
            "type": "Run",
            "moving_time": 2400,
            "icu_training_load": 55,
            "distance": 8000.5,
            "paired_event_id": 111,
            "compliance": 1.02,
            "average_hr": 148,  # unmodeled — must be dropped
        }
    )
    assert activity.id == "a1"
    assert activity.moving_time == 2400
    assert activity.icu_training_load == 55
    assert activity.compliance == 1.02


def test_intervals_activity_all_fields_optional():
    activity = IntervalsActivity.model_validate({})
    assert activity.id is None
    assert activity.start_date_local is None
    assert activity.type is None
    assert activity.moving_time is None
    assert activity.icu_training_load is None
    assert activity.distance is None
    assert activity.paired_event_id is None
    assert activity.compliance is None


def test_wellness_day_aliases_and_defaults():
    row = WellnessDay.model_validate(
        {
            "id": "2026-08-07",
            "ctl": 50.0,
            "atl": 55.0,
            "sleepSecs": 25200,
            "sleepScore": 78.5,
            "hrv": 42.0,
            "restingHR": 47,
            "weight": 70.2,
            "fatigue": 3,
            "soreness": 2,
            "mood": 4,  # unmodeled — must be dropped
        }
    )
    assert row.sleep_secs == 25200
    assert row.sleep_score == 78.5
    assert row.resting_hr == 47

    empty = WellnessDay.model_validate({"id": "2026-08-06"})
    assert empty.ctl is None
    assert empty.sleep_secs is None
    assert empty.resting_hr is None


def test_wellness_day_populate_by_name_accepts_snake_case():
    row = WellnessDay.model_validate({"id": "2026-08-07", "sleep_secs": 100})
    assert row.sleep_secs == 100


def test_intervals_push_error_parsing():
    error = IntervalsPushError.model_validate(
        {
            "service": "garmin",
            "message": "device offline",
            "date": "2026-08-07T12:00:00Z",
        }
    )
    assert error.service == "garmin"
    assert error.message == "device offline"
    assert error.date == "2026-08-07T12:00:00Z"


def test_intervals_event_parses_nested_push_errors_and_defaults():
    event = IntervalsEvent.model_validate(
        {
            "id": 42,
            "external_id": "coachie:1:slot-1",
            "start_date_local": "2026-08-10",
            "type": "Run",
            "name": "Tempo Run",
            "description": "- 20m 85-95%",
            "category": "WORKOUT",
            "tags": ["coachie"],
            "push_errors": [
                {"service": "strava", "message": "rate limited", "date": None}
            ],
        }
    )
    assert event.id == 42
    assert event.tags == ["coachie"]
    assert len(event.push_errors) == 1
    assert event.push_errors[0].service == "strava"

    bare = IntervalsEvent.model_validate({})
    assert bare.id is None
    assert bare.tags == []
    assert bare.push_errors == []


def test_null_list_fields_are_read_as_empty_lists():
    # Live contract: intervals.icu sends `null`, not `[]`, for an event with
    # no tags or push errors -- a strict list[...] field rejects that.
    event = IntervalsEvent.model_validate({"id": 1, "tags": None, "push_errors": None})
    assert event.tags == []
    assert event.push_errors == []


class TestUploadDestinations:
    def test_only_enabled_platforms_are_reported(self):
        profile = AthleteProfile.model_validate(
            {
                "id": "i1",
                "icu_garmin_upload_workouts": True,
                "zwift_upload_workouts": True,
                "wahoo_upload_workouts": False,
                "suunto_upload_workouts": None,
            }
        )
        assert profile.upload_destinations() == ["Garmin", "Zwift"]

    def test_no_enabled_platform_reports_nothing(self):
        # A real state, not an error: the plan syncs to intervals.icu and
        # still never reaches a watch or trainer.
        assert AthleteProfile.model_validate({"id": "i1"}).upload_destinations() == []


def test_sport_type_map_covers_documented_mappings():
    assert SPORT_TYPE_MAP["Run"] == SportType.RUNNING
    assert SPORT_TYPE_MAP["TrailRun"] == SportType.RUNNING
    assert SPORT_TYPE_MAP["VirtualRun"] == SportType.RUNNING
    assert SPORT_TYPE_MAP["Hike"] == SportType.RUNNING
    assert SPORT_TYPE_MAP["Walk"] == SportType.RUNNING
    assert SPORT_TYPE_MAP["Ride"] == SportType.CYCLING
    assert SPORT_TYPE_MAP["VirtualRide"] == SportType.CYCLING
    assert SPORT_TYPE_MAP["GravelRide"] == SportType.CYCLING
    assert SPORT_TYPE_MAP["MountainBikeRide"] == SportType.CYCLING
    assert SPORT_TYPE_MAP["Swim"] == SportType.SWIMMING
    assert SPORT_TYPE_MAP["OpenWaterSwim"] == SportType.SWIMMING


def test_sport_type_map_returns_none_for_unmapped_type():
    assert SPORT_TYPE_MAP.get("WeightTraining") is None
    assert SPORT_TYPE_MAP.get("Yoga") is None


def test_activity_detail_parses_trace_fields():
    detail = IntervalsActivityDetail.model_validate(
        {
            "id": "i1",
            "type": "Run",
            "icu_hr_zones": [141, 150, 158, 167, 185],
            "icu_power_zones": [55, 75, 90, 105, 120, 150, 999],
            "pace_zones": [80.0, 90.0, 95.0, 100.0, 999.0],
            "use_gap_zone_times": True,
            "pool_length": 25.0,
        }
    )
    assert detail.icu_hr_zones == [141, 150, 158, 167, 185]
    assert detail.icu_power_zones == [55, 75, 90, 105, 120, 150, 999]
    assert detail.pace_zones == [80.0, 90.0, 95.0, 100.0, 999.0]
    assert detail.use_gap_zone_times is True
    assert detail.pool_length == 25.0


def test_activity_detail_trace_fields_default_to_none():
    detail = IntervalsActivityDetail.model_validate({"id": "i2"})
    assert detail.icu_hr_zones is None
    assert detail.icu_power_zones is None
    assert detail.pace_zones is None
    assert detail.use_gap_zone_times is None
    assert detail.pool_length is None


def test_activity_stream_parses_type_and_data():
    stream = ActivityStream.model_validate(
        {"type": "watts", "data": [100.0, None, 105.0], "anomalies": []}
    )
    assert stream.type == "watts"
    assert stream.data == [100.0, None, 105.0]


def test_activity_stream_coerces_null_data_to_empty_list():
    stream = ActivityStream.model_validate({"type": "heartrate", "data": None})
    assert stream.data == []
