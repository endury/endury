"""FakeIntervals: the in-memory intervals.icu the store tests (and the E2E) run against."""

from __future__ import annotations

from datetime import date

import pytest

from devtools.frontend.fakes import MAX_DESCRIPTION_CHARS
from endury.intervals.client import IntervalsApiError
from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalRow,
    IntervalsActivity,
    IntervalsActivityDetail,
    SportSettingsRow,
    WellnessDay,
)
from tests.doubles import FakeIntervals


def _note(external_id: str, day: str, **fields) -> dict:
    return {
        "external_id": external_id,
        "category": "NOTE",
        "name": external_id,
        "start_date_local": f"{day}T00:00:00",
        **fields,
    }


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals(
        athlete=AthleteProfile(
            id="i1", sportSettings=[SportSettingsRow(types=["Run"], lthr=165)]
        ),
        activities=[
            IntervalsActivity(
                id="a1", start_date_local="2026-10-01T07:00:00", type="Run"
            ),
            IntervalsActivity(
                id="a2", start_date_local="2026-10-05T07:00:00", type="Ride"
            ),
        ],
        details={
            "a1": IntervalsActivityDetail(
                id="a1", type="Run", icu_intervals=[IntervalRow(type="WORK")]
            )
        },
        streams={
            "a1": [
                ActivityStream(type="heartrate", data=[120.0, 130.0]),
                ActivityStream(type="watts", data=[200.0, None]),
            ]
        },
        wellness=[WellnessDay(id="2026-10-01", hrv=60.0), WellnessDay(id="2026-10-09")],
    )


def test_reads_serve_the_scripted_account(fake):
    client = fake.client()

    assert client.get_athlete().sport_settings[0].lthr == 165
    listed = client.list_activities(date(2026, 10, 1), date(2026, 10, 4))
    assert [a.id for a in listed] == ["a1"]
    assert client.get_activity("a1").icu_intervals[0].type == "WORK"
    assert client.get_activity("a2").type == "Ride"
    assert [s.type for s in client.get_activity_streams("a1", ["watts"])] == ["watts"]
    assert client.get_activity_streams("a2", ["watts"]) == []
    wellness = client.list_wellness(date(2026, 10, 1), date(2026, 10, 2))
    assert [(w.id, w.hrv) for w in wellness] == [("2026-10-01", 60.0)]


def test_unknown_activities_and_routes_are_404(fake):
    client = fake.client()

    for call in (
        lambda: client.get_activity("nope"),
        lambda: client.pair_activity("nope", 1),
    ):
        with pytest.raises(IntervalsApiError) as err:
            call()
        assert err.value.status_code == 404
    assert fake.handle("GET", "/activity/a1/laps", {}, None).status == 404
    assert fake.handle("GET", "/athlete/0/gear", {}, None).status == 404
    assert fake.handle("GET", "/weather", {}, None).status == 404


def test_pairing_updates_the_activity(fake):
    paired = fake.client().pair_activity("a2", 1234)

    assert paired.paired_event_id == 1234
    assert fake.activities[1].paired_event_id == 1234


def test_upsert_matches_on_external_id_and_keeps_the_server_id(fake):
    client = fake.client()

    [created] = client.bulk_upsert_events(
        [_note("coachie:store", "2099-12-31", description="v1", hide_from_athlete=True)]
    )
    [updated] = client.bulk_upsert_events(
        [_note("coachie:store", "2099-12-31", description="v2")]
    )

    assert updated.id == created.id
    assert updated.description == "v2"
    assert list(fake.events) == ["coachie:store"]
    assert fake.event("coachie:store")["hide_from_athlete"] is True


def test_list_events_filters_by_window_and_category(fake):
    client = fake.client()
    client.bulk_upsert_events(
        [
            _note("n1", "2030-12-31"),
            _note("w1", "2030-12-31", category="WORKOUT"),
            _note("n2", "2099-12-31"),
        ]
    )

    day = date(2030, 12, 31)
    assert [e.external_id for e in client.list_events(day, day)] == ["n1", "w1"]
    assert [e.external_id for e in client.list_events(day, day, "NOTE")] == ["n1"]
    assert [
        e.external_id
        for e in client.list_events(day, date(2099, 12, 31), "NOTE,WORKOUT")
    ] == ["n1", "w1", "n2"]


@pytest.mark.parametrize(
    ("event", "status"),
    [
        ({"category": "NOTE", "start_date_local": "2026-10-01T00:00:00"}, 422),
        (_note("n1", "2026-10-01") | {"start_date_local": "2026-10-01"}, 422),
        (_note("n1", "2026-10-01", description="x" * (MAX_DESCRIPTION_CHARS + 1)), 413),
    ],
    ids=["no-external-id", "bare-date", "description-too-long"],
)
def test_a_malformed_upsert_writes_nothing(fake, event, status):
    with pytest.raises(IntervalsApiError) as err:
        fake.client().bulk_upsert_events([_note("ok", "2026-10-02"), event])

    assert err.value.status_code == status
    assert fake.events == {}


def test_a_description_at_the_limit_round_trips(fake):
    text = "x" * MAX_DESCRIPTION_CHARS
    fake.client().bulk_upsert_events([_note("big", "2026-10-02", description=text)])

    [event] = fake.client().list_events(date(2026, 10, 2), date(2026, 10, 2))
    assert event.description == text


def test_non_array_bodies_are_refused(fake):
    assert fake.handle("POST", "/api/v1/athlete/0/events/bulk", {}, {}).status == 422
    assert fake.handle("PUT", "/athlete/0/events/bulk-delete", {}, None).status == 422


def test_bulk_delete_counts_only_existing_events(fake):
    client = fake.client()
    client.bulk_upsert_events([_note("n1", "2026-10-01"), _note("n2", "2026-10-01")])

    assert client.bulk_delete_events(["n1", "missing"]) == 1
    assert list(fake.events) == ["n2"]


def test_fail_next_fails_one_matching_request(fake):
    client = fake.client()
    fake.fail_next(400, method="POST")

    assert client.get_athlete().id == "i1"
    with pytest.raises(IntervalsApiError) as err:
        client.bulk_upsert_events([_note("n1", "2026-10-01")])
    assert err.value.status_code == 400
    assert client.bulk_upsert_events([_note("n1", "2026-10-01")])

    fake.fail_next(403)
    with pytest.raises(IntervalsApiError):
        client.get_athlete()
    assert ("GET", "/athlete/0") in fake.calls
