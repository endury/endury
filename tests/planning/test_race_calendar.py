"""Tests for race_calendar: a race from the race table on the intervals.icu calendar."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from endury.domain.models.race import AthleteRace
from endury.planning.race_calendar import (
    race_calendar_id,
    reconcile_race_events,
    sync_race_event,
)

_TODAY = date(2026, 9, 26)


def _race(**overrides) -> AthleteRace:
    fields = {
        "id": 4,
        "name": "Munich Half",
        "race_date": "2026-09-27",
        "goal_type": "running_half_marathon",
        "priority": "B",
    }
    fields.update(overrides)
    return AthleteRace(**fields)


def _client(existing=()) -> MagicMock:
    client = MagicMock()
    client.list_events.return_value = list(existing)
    return client


def _sync(race, client):
    return sync_race_event(race, client=client, today=_TODAY)


def test_a_planned_race_goes_on_the_calendar_as_a_race_event():
    client = _client()
    result = _sync(_race(notes="Target 1:45"), client)

    assert result["status"] == "ok"
    client.list_events.assert_called_once_with(
        date(2026, 9, 27), date(2026, 9, 27), category="RACE_A,RACE_B,RACE_C"
    )
    client.bulk_upsert_events.assert_called_once_with(
        [
            {
                "category": "RACE_B",
                "external_id": "coachie:race:4",
                "start_date_local": "2026-09-27T00:00:00",
                "type": "Run",
                "name": "Munich Half (B-Race)",
                "description": "Target 1:45",
                "tags": ["endury"],
            }
        ]
    )


def test_a_race_without_a_running_goal_is_an_other_event():
    client = _client()
    _sync(_race(goal_type=None), client)
    event = client.bulk_upsert_events.call_args.args[0][0]
    assert event["type"] == "Other"
    assert "description" not in event


def test_a_trail_race_is_a_run_event():
    client = _client()
    _sync(_race(goal_type="trail_50k"), client)
    assert client.bulk_upsert_events.call_args.args[0][0]["type"] == "Run"


def test_a_race_today_still_goes_on():
    assert _sync(_race(race_date="2026-09-26"), _client())["status"] == "ok"


def test_a_past_race_is_left_alone():
    client = _client()
    result = _sync(_race(race_date="2026-09-20"), client)
    assert result["status"] == "unchanged"
    client.bulk_upsert_events.assert_not_called()
    client.bulk_delete_events.assert_not_called()


def test_a_completed_race_is_left_alone():
    client = _client()
    assert _sync(_race(status="completed"), client)["status"] == "unchanged"
    client.bulk_upsert_events.assert_not_called()


def test_a_day_that_already_has_a_race_gets_no_second_one():
    """The plan's own race event is kept, never doubled; a race of ours moved
    onto that day comes off its old day."""
    plan_race = SimpleNamespace(external_id="coachie:7:race:A:2026-09-27", name="Half")
    client = _client([plan_race])
    result = _sync(_race(), client)

    assert result["status"] == "already_on_calendar"
    assert "Half" in result["message"]
    client.bulk_upsert_events.assert_not_called()
    client.bulk_delete_events.assert_called_once_with(["coachie:race:4"])


def test_our_own_event_on_that_day_is_updated_in_place():
    ours = SimpleNamespace(external_id="coachie:race:4", name="Munich Half (B-Race)")
    client = _client([ours])
    assert _sync(_race(), client)["status"] == "ok"
    client.bulk_upsert_events.assert_called_once()


def test_a_cancelled_race_comes_off_the_calendar():
    client = _client()
    result = _sync(_race(status="cancelled"), client)
    assert result["status"] == "removed"
    client.bulk_delete_events.assert_called_once_with(["coachie:race:4"])


def test_without_intervals_the_race_stays_in_endury():
    result = _sync(_race(), None)
    assert result["status"] == "skipped"
    assert "saved in Endury" in result["message"]


def test_a_failed_push_is_reported_not_raised():
    client = _client()
    client.bulk_upsert_events.side_effect = RuntimeError("503")
    result = _sync(_race(), client)
    assert result["status"] == "error"
    assert "503" in result["message"]


def test_race_calendar_id_is_keyed_by_the_race():
    assert race_calendar_id(12) == "coachie:race:12"


# reconcile_race_events — a boot puts a planned race the calendar lost back on


def _reconcile(races, client):
    return reconcile_race_events(races, client=client, today=_TODAY)


def test_a_planned_race_with_no_event_on_its_day_is_put_back():
    """Live 2026-10-20: the A race four days out had no calendar event while
    both 2027 races had theirs, so race day showed a long ride instead."""
    client = _client()
    results = _reconcile([_race(race_date="2026-10-24", priority="A")], client)

    assert [r["status"] for r in results] == ["ok"]
    client.list_events.assert_called_with(
        date(2026, 10, 24), date(2026, 10, 24), category="RACE_A,RACE_B,RACE_C"
    )
    client.bulk_upsert_events.assert_called_once()
    assert (
        client.bulk_upsert_events.call_args.args[0][0]["external_id"]
        == "coachie:race:4"
    )


def test_a_day_that_holds_any_race_event_is_left_alone():
    ours = SimpleNamespace(
        external_id="coachie:race:4", start_date_local="2026-10-24T00:00:00"
    )
    plan_race = SimpleNamespace(
        external_id="coachie:7:race:A:2027-01-24",
        start_date_local="2027-01-24T00:00:00",
    )
    client = _client()
    client.list_events.side_effect = lambda oldest, newest, category=None: [
        event
        for event in (ours, plan_race)
        if oldest <= date.fromisoformat(event.start_date_local[:10]) <= newest
    ]
    results = _reconcile(
        [
            _race(race_date="2026-10-24"),
            _race(id=5, name="Half", race_date="2027-01-24"),
            _race(id=6, name="Full", race_date="2027-08-21"),
        ],
        client,
    )

    assert [r["status"] for r in results] == ["ok"]
    assert client.list_events.call_args_list[0].args == (
        date(2026, 10, 24),
        date(2027, 8, 21),
    )
    assert (
        client.bulk_upsert_events.call_args.args[0][0]["external_id"]
        == "coachie:race:6"
    )


def test_past_and_cancelled_races_are_not_due():
    client = _client()
    assert (
        _reconcile(
            [_race(race_date="2026-09-25"), _race(id=5, status="cancelled")], client
        )
        == []
    )
    client.list_events.assert_not_called()


def test_without_intervals_nothing_is_reconciled():
    assert _reconcile([_race()], None) == []


def test_an_unreadable_calendar_is_logged_not_raised():
    client = _client()
    client.list_events.side_effect = RuntimeError("503")
    assert _reconcile([_race()], client) == []
    client.bulk_upsert_events.assert_not_called()
