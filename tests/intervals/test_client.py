"""Tests for endury.intervals.client — IntervalsClient over a mock transport."""

from __future__ import annotations

import base64
import json
from datetime import date
from unittest.mock import patch

import httpx
import pytest

from endury.intervals.client import (
    IntervalsApiError,
    IntervalsAuthError,
    IntervalsClient,
    _parse_retry_after,
)

_API_KEY = "secret-test-key"


def _decode_basic_auth(header: str) -> tuple[str, str]:
    scheme, _, encoded = header.partition(" ")
    assert scheme == "Basic"
    decoded = base64.b64decode(encoded).decode()
    username, _, password = decoded.partition(":")
    return username, password


def _client(handler, *, athlete_id: str = "0") -> IntervalsClient:
    transport = httpx.MockTransport(handler)
    return IntervalsClient(api_key=_API_KEY, athlete_id=athlete_id, transport=transport)


# Auth + transport shape


def test_sends_basic_auth_and_user_agent():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "42", "sportSettings": []})

    client = _client(handler)
    client.get_athlete()

    request = captured[0]
    username, password = _decode_basic_auth(request.headers["authorization"])
    assert username == "API_KEY"
    assert password == _API_KEY
    assert request.headers["user-agent"] == "endury/0.1 (+https://localhost)"


def test_an_access_token_goes_out_as_a_bearer_instead_of_the_key():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "42", "sportSettings": []})

    client = IntervalsClient(
        access_token="oauth-token", transport=httpx.MockTransport(handler)
    )
    client.get_athlete()

    assert captured[0].headers["authorization"] == "Bearer oauth-token"


def test_athlete_id_defaults_to_zero_and_is_overridable():
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    default_client = _client(handler)
    default_client.get_athlete()

    custom_client = _client(handler, athlete_id="77")
    custom_client.get_athlete()

    assert seen_paths == [
        "/api/v1/athlete/0",
        "/api/v1/athlete/77",
    ]


def test_close_and_context_manager():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    client.close()
    assert client._client.is_closed

    with _client(handler) as ctx_client:
        ctx_client.get_athlete()
    assert ctx_client._client.is_closed


# get_athlete


def test_get_athlete_parses_sport_settings():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "12345",
                "sportSettings": [
                    {
                        "types": ["Run", "TrailRun"],
                        "ftp": None,
                        "lthr": 165,
                        "max_hr": 190,
                        "threshold_pace": 3.5,
                        "pace_units": "MINS_KM",
                    },
                    {
                        "types": ["Ride"],
                        "ftp": 250,
                        "lthr": None,
                        "max_hr": 185,
                        "threshold_pace": None,
                        "pace_units": "NONE",
                    },
                ],
            },
        )

    client = _client(handler)
    profile = client.get_athlete()

    assert profile.id == "12345"
    assert len(profile.sport_settings) == 2
    run_row = profile.sport_settings[0]
    assert run_row.types == ["Run", "TrailRun"]
    assert run_row.lthr == 165
    assert run_row.threshold_pace == 3.5
    ride_row = profile.sport_settings[1]
    assert ride_row.ftp == 250
    assert ride_row.lthr is None


# list_activities


def test_list_activities_projects_fields_and_parses_rows():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "a1",
                    "start_date_local": "2026-08-01T07:00:00",
                    "type": "Run",
                    "moving_time": 3600,
                    "icu_training_load": 65,
                    "distance": 10000.0,
                    "paired_event_id": 999,
                    "compliance": 0.95,
                },
                {
                    # Sparse row: fields the API omitted because they were
                    # null under the `fields=` projection.
                    "id": "a2",
                    "start_date_local": "2026-08-02T07:00:00",
                    "type": "Swim",
                },
            ],
        )

    client = _client(handler)
    activities = client.list_activities(date(2026, 7, 1), date(2026, 8, 8))

    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/athlete/0/activities"
    assert request.url.params["oldest"] == "2026-07-01"
    assert request.url.params["newest"] == "2026-08-08"
    assert request.url.params["fields"] == (
        "id,name,start_date_local,type,source,device_name,moving_time,elapsed_time,icu_recording_time,"
        "icu_training_load,distance,paired_event_id,compliance,"
        "average_heartrate,max_heartrate,icu_average_watts,icu_weighted_avg_watts,"
        "average_speed,pace,gap,average_cadence,total_elevation_gain,"
        "icu_intensity,icu_variability_index,icu_efficiency_factor,decoupling,trimp,"
        "icu_hr_zone_times,icu_zone_times,pace_zone_times,"
        "icu_ftp,lthr,threshold_pace,interval_summary,feel,icu_rpe,"
        "average_temp,average_weather_temp,carbs_ingested,route_id"
    )

    assert len(activities) == 2
    first, second = activities
    assert first.id == "a1"
    assert first.moving_time == 3600
    assert first.paired_event_id == 999
    assert second.moving_time is None
    assert second.paired_event_id is None


# get_activity


def test_get_activity_requests_intervals_and_parses_reps_and_groups():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "i1",
                "type": "Run",
                "moving_time": 3110,
                "icu_hr_zone_times": [2275, 621, 139, 72, 0],
                "icu_zone_times": [{"id": "Z1", "secs": 60}, {"id": "SS", "secs": 5}],
                "interval_summary": ["4x 7m26s 139bpm"],
                "icu_intervals": [
                    {
                        "type": "WORK",
                        "group_id": "446s@139bpm80rpm",
                        "start_time": 0,
                        "elapsed_time": 416,
                        "distance": 1002.4,
                        "average_heartrate": 132,
                        "gap": 3.37,
                        "zone": 1,
                        "intensity": 78,
                        "unmodeled_field": "ignored",
                    }
                ],
                "icu_groups": [
                    {"count": 4, "elapsed_time": 446, "average_heartrate": 139}
                ],
            },
        )

    client = _client(handler)
    detail = client.get_activity("i1")

    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/activity/i1"
    assert request.url.params["intervals"] == "true"

    assert detail.id == "i1"
    assert detail.icu_hr_zone_times == [2275, 621, 139, 72, 0]
    assert [(z.id, z.secs) for z in detail.icu_zone_times or []] == [
        ("Z1", 60),
        ("SS", 5),
    ]
    assert detail.interval_summary == ["4x 7m26s 139bpm"]
    assert len(detail.icu_intervals) == 1
    rep = detail.icu_intervals[0]
    assert (rep.type, rep.elapsed_time, rep.average_heartrate, rep.zone) == (
        "WORK",
        416,
        132,
        1,
    )
    assert detail.icu_groups[0].count == 4


def test_get_activity_treats_null_interval_lists_as_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "i2", "icu_intervals": None, "icu_groups": None}
        )

    detail = _client(handler).get_activity("i2")
    assert detail.icu_intervals == []
    assert detail.icu_groups == []


# get_activity_streams


def test_get_activity_streams_requests_types_and_parses_rows():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "type": "watts",
                    "data": [100.0, 105.0, None],
                    "valueType": "watts",
                },
                {"type": "heartrate", "data": [140, 142, 144]},
            ],
        )

    client = _client(handler)
    streams = client.get_activity_streams("i1", ["watts", "heartrate"])

    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/activity/i1/streams.json"
    assert request.url.params["types"] == "watts,heartrate"

    assert [s.type for s in streams] == ["watts", "heartrate"]
    assert streams[0].data == [100.0, 105.0, None]
    assert streams[1].data == [140, 142, 144]


def test_get_activity_streams_tolerates_null_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"type": "heartrate", "data": None}])

    streams = _client(handler).get_activity_streams("i1", ["heartrate"])
    assert streams[0].data == []


# pair_activity


def test_pair_activity_puts_paired_event_id_and_parses_activity():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"id": "i123", "type": "Run", "paired_event_id": 42},
        )

    client = _client(handler)
    activity = client.pair_activity("i123", 42)

    request = captured[0]
    assert request.method == "PUT"
    assert request.url.path == "/api/v1/activity/i123"
    assert json.loads(request.content) == {"paired_event_id": 42}

    assert activity.id == "i123"
    assert activity.paired_event_id == 42


# update_activity_verdict


def test_update_activity_verdict_puts_only_the_given_fields():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"id": "i123", "type": "Run", "feel": 2, "icu_rpe": 7}
        )

    client = _client(handler)
    both = client.update_activity_verdict("i123", feel=2, icu_rpe=7)
    client.update_activity_verdict("i123", icu_rpe=7)

    assert captured[0].method == "PUT"
    assert captured[0].url.path == "/api/v1/activity/i123"
    assert json.loads(captured[0].content) == {"feel": 2, "icu_rpe": 7}
    assert json.loads(captured[1].content) == {"icu_rpe": 7}
    assert (both.feel, both.icu_rpe) == (2, 7)


# update_sport_settings


@pytest.mark.parametrize("recalc", [True, False])
def test_update_sport_settings_puts_the_fields_on_the_row(recalc):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": 7, "types": ["Ride"], "ftp": 260})

    row = _client(handler).update_sport_settings(
        7, {"ftp": 260}, recalc_hr_zones=recalc
    )

    request = captured[0]
    assert request.method == "PUT"
    assert request.url.path == "/api/v1/athlete/0/sport-settings/7"
    assert request.url.params["recalcHrZones"] == str(recalc).lower()
    assert json.loads(request.content) == {"ftp": 260}
    assert row.ftp == 260


# list_wellness


def test_list_wellness_parses_camelcase_aliases():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "2026-08-07",
                    "ctl": 55.2,
                    "atl": 60.1,
                    "sleepSecs": 27000,
                    "sleepScore": 82.0,
                    "hrv": 45.3,
                    "restingHR": 48,
                    "weight": 71.5,
                    "fatigue": 2,
                    "soreness": 1,
                }
            ],
        )

    client = _client(handler)
    rows = client.list_wellness(date(2026, 8, 1), date(2026, 8, 7))

    request = captured[0]
    assert request.url.path == "/api/v1/athlete/0/wellness"
    assert request.url.params["oldest"] == "2026-08-01"
    assert request.url.params["newest"] == "2026-08-07"

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "2026-08-07"
    assert row.sleep_secs == 27000
    assert row.sleep_score == 82.0
    assert row.resting_hr == 48
    assert row.hrv == 45.3


# list_events


def test_list_events_without_category_omits_param():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client(handler)
    client.list_events(date(2026, 8, 1), date(2026, 8, 7))

    request = captured[0]
    assert "category" not in request.url.params


def test_list_events_with_category_and_push_errors():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 555,
                    "external_id": "coachie:1:slot-1",
                    "start_date_local": "2026-08-10",
                    "type": "Run",
                    "name": "Tempo Run",
                    "description": "- Warmup 15m 55-70% LTHR",
                    "category": "WORKOUT",
                    "tags": ["coachie"],
                    "push_errors": [
                        {
                            "service": "garmin",
                            "message": "device offline",
                            "date": "2026-08-09T12:00:00Z",
                        }
                    ],
                }
            ],
        )

    client = _client(handler)
    events = client.list_events(date(2026, 8, 1), date(2026, 8, 14), category="WORKOUT")

    request = captured[0]
    assert request.url.params["category"] == "WORKOUT"

    assert len(events) == 1
    event = events[0]
    assert event.external_id == "coachie:1:slot-1"
    assert event.tags == ["coachie"]
    assert len(event.push_errors) == 1
    assert event.push_errors[0].service == "garmin"
    assert event.push_errors[0].message == "device offline"


# bulk_upsert_events / bulk_delete_events


def test_bulk_upsert_events_sends_upsert_query_and_body():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "external_id": "coachie:1:slot-1",
                    "start_date_local": "2026-08-10",
                    "type": "Run",
                    "name": "Tempo Run",
                    "description": "text",
                    "category": "WORKOUT",
                    "tags": ["coachie"],
                    "push_errors": [],
                }
            ],
        )

    events_payload = [
        {
            "external_id": "coachie:1:slot-1",
            "start_date_local": "2026-08-10",
            "type": "Run",
            "name": "Tempo Run",
            "description": "text",
            "category": "WORKOUT",
            "tags": ["coachie"],
        }
    ]

    client = _client(handler)
    result = client.bulk_upsert_events(events_payload)

    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/athlete/0/events/bulk"
    # All three query params are marked required by the OpenAPI spec.
    assert request.url.params["upsert"] == "true"
    assert request.url.params["upsertOnUid"] == "false"
    assert request.url.params["updatePlanApplied"] == "false"
    assert json.loads(request.content) == events_payload

    assert len(result) == 1
    assert result[0].external_id == "coachie:1:slot-1"


def test_bulk_delete_events_sends_external_ids_and_returns_count():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"eventsDeleted": 2})

    client = _client(handler)
    count = client.bulk_delete_events(["coachie:1:slot-1", "coachie:1:slot-2"])

    request = captured[0]
    assert request.method == "PUT"
    assert request.url.path == "/api/v1/athlete/0/events/bulk-delete"
    assert json.loads(request.content) == [
        {"external_id": "coachie:1:slot-1"},
        {"external_id": "coachie:1:slot-2"},
    ]
    assert count == 2


def test_bulk_delete_events_defaults_missing_count_to_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _client(handler)
    assert client.bulk_delete_events(["coachie:1:slot-1"]) == 0


# Errors + 429 retry


def test_401_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    client = _client(handler)
    with pytest.raises(IntervalsAuthError) as exc_info:
        client.get_athlete()
    assert exc_info.value.status_code == 401


def test_403_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = _client(handler)
    with pytest.raises(IntervalsAuthError):
        client.get_athlete()


def test_500_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server exploded")

    client = _client(handler)
    with (
        patch("endury.intervals.client.time.sleep"),
        pytest.raises(IntervalsApiError) as exc_info,
    ):
        client.get_athlete()
    assert exc_info.value.status_code == 500
    assert "server exploded" in str(exc_info.value)


def test_429_retries_once_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep") as mock_sleep:
        profile = client.get_athlete()

    assert calls["count"] == 2
    assert profile.id == "1"
    mock_sleep.assert_called_once_with(2.0)


def test_429_without_retry_after_header_uses_default_backoff():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep") as mock_sleep:
        client.get_athlete()

    mock_sleep.assert_called_once_with(1.0)


def test_429_with_non_numeric_retry_after_uses_default_backoff():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "soon"})
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep") as mock_sleep:
        client.get_athlete()

    mock_sleep.assert_called_once_with(1.0)


def test_429_twice_raises_api_error_after_single_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    client = _client(handler)
    with (
        patch("endury.intervals.client.time.sleep") as mock_sleep,
        pytest.raises(IntervalsApiError) as exc_info,
    ):
        client.get_athlete()

    mock_sleep.assert_called_once_with(0.0)
    assert exc_info.value.status_code == 429


# Transient retry: connect errors + 5xx


def test_connect_error_then_success_retries_transparently():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep") as mock_sleep:
        profile = client.get_athlete()

    assert calls["count"] == 2
    assert profile.id == "1"
    mock_sleep.assert_called_once_with(0.25)


def test_read_timeout_then_success_retries_transparently():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep"):
        client.get_athlete()

    assert calls["count"] == 2


def test_500_then_success_retries_transparently():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="server exploded")
        return httpx.Response(200, json={"id": "1", "sportSettings": []})

    client = _client(handler)
    with patch("endury.intervals.client.time.sleep") as mock_sleep:
        profile = client.get_athlete()

    assert calls["count"] == 2
    assert profile.id == "1"
    mock_sleep.assert_called_once_with(0.25)


def test_connect_error_exhausts_retries_and_raises_same_typed_error():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    with (
        patch("endury.intervals.client.time.sleep") as mock_sleep,
        pytest.raises(httpx.ConnectError),
    ):
        client.get_athlete()

    # One initial attempt plus _TRANSIENT_MAX_RETRIES retries.
    assert calls["count"] == 3
    assert mock_sleep.call_args_list == [((0.25,),), ((0.5,),)]


def test_500_exhausts_retries_and_raises_same_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server exploded")

    client = _client(handler)
    with (
        patch("endury.intervals.client.time.sleep") as mock_sleep,
        pytest.raises(IntervalsApiError) as exc_info,
    ):
        client.get_athlete()

    assert exc_info.value.status_code == 500
    assert "server exploded" in str(exc_info.value)
    assert mock_sleep.call_args_list == [((0.25,),), ((0.5,),)]


def test_400_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, text="bad request")

    client = _client(handler)
    with (
        patch("endury.intervals.client.time.sleep") as mock_sleep,
        pytest.raises(IntervalsApiError) as exc_info,
    ):
        client.get_athlete()

    assert calls["count"] == 1
    assert exc_info.value.status_code == 400
    mock_sleep.assert_not_called()


# _parse_retry_after (direct unit coverage of the fallback branches)


def test_parse_retry_after_missing_header():
    assert _parse_retry_after(None) == 1.0


def test_parse_retry_after_numeric_header():
    assert _parse_retry_after("3.5") == 3.5


def test_parse_retry_after_negative_header_clamped_to_zero():
    assert _parse_retry_after("-5") == 0.0


def test_parse_retry_after_is_capped():
    """The 15-min bucket can ask for 600 s; no request may block that long."""
    assert _parse_retry_after("600") == 30.0


def test_parse_retry_after_non_numeric_header_falls_back():
    assert _parse_retry_after("not-a-number") == 1.0
