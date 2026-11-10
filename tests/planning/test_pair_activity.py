"""Tests for pairing a completed activity with a planned workout."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from endury.intervals.client import IntervalsApiError
from endury.intervals.models import IntervalsActivity, IntervalsEvent
from endury.planning.pair_activity import (
    PairingNotRecorded,
    PairTargetNotFound,
    name_mismatch,
    pair_activity,
    resolve_session_event_id,
)
from endury.planning.wire.activity_requests import PairByEventRequest


class _Client:
    """Stub intervals client: scripted list_events + pair_activity."""

    def __init__(
        self,
        *,
        events: list[IntervalsEvent] | None = None,
        paired_event_id: int | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._events = events or []
        self._paired_event_id = paired_event_id
        self._exc = exc
        self.pair_calls: list[tuple[str, int]] = []
        self.event_calls: list[tuple[object, object, str | None]] = []

    def list_events(
        self, oldest: object, newest: object, category: str | None = None
    ) -> list[IntervalsEvent]:
        self.event_calls.append((oldest, newest, category))
        return self._events

    def pair_activity(self, activity_id: str, event_id: int) -> IntervalsActivity:
        self.pair_calls.append((activity_id, event_id))
        if self._exc is not None:
            raise self._exc
        return IntervalsActivity(id=activity_id, paired_event_id=self._paired_event_id)


def _repos(
    active_plan: SimpleNamespace | None = None,
    sessions: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        plan=SimpleNamespace(
            get_active=lambda: active_plan,
            get_sessions=lambda plan_id: sessions or [],
        )
    )


def _row(session_id: int = 99, slot_id: str = "w0_wed_run") -> SimpleNamespace:
    return SimpleNamespace(id=session_id, slot_id=slot_id, date="2026-08-20")


def _no_repos() -> SimpleNamespace:
    raise AssertionError("an event-id target must not touch the repositories")


def test_pairs_an_event_id_target_without_reading_the_plan() -> None:
    client = _Client(paired_event_id=42)

    result = pair_activity(client, "i123", PairByEventRequest(event_id=42), _no_repos)

    assert result == {"status": "ok", "activity_id": "i123", "event_id": 42}
    assert client.pair_calls == [("i123", 42)]
    assert client.event_calls == []


def test_resolves_a_session_target_by_its_external_id() -> None:
    """Same day, same category: only (plan_id, slot_id) decides the event."""
    client = _Client(
        events=[
            IntervalsEvent(id=41, external_id="coachie:7:w0_wed_bike"),
            IntervalsEvent(id=42, external_id="coachie:7:w0_wed_run"),
        ],
        paired_event_id=42,
    )
    repos = _repos(SimpleNamespace(id=7), [_row()])

    assert resolve_session_event_id(repos, client, 99) == 42
    assert client.event_calls == [(date(2026, 8, 20), date(2026, 8, 20), "WORKOUT")]


@pytest.mark.parametrize(
    ("repos", "events", "message"),
    [
        (_repos(None), [], "no longer exists"),
        (_repos(SimpleNamespace(id=7), []), [], "no longer exists"),
        (_repos(SimpleNamespace(id=7), [_row()]), [], "nothing to pair with"),
        (
            _repos(SimpleNamespace(id=7), [_row()]),
            [IntervalsEvent(id=None, external_id="coachie:7:w0_wed_run")],
            "nothing to pair with",
        ),
    ],
)
def test_an_unresolvable_session_target_is_not_found(
    repos: SimpleNamespace, events: list[IntervalsEvent], message: str
) -> None:
    client = _Client(events=events)

    with pytest.raises(PairTargetNotFound, match=message):
        resolve_session_event_id(repos, client, 99)
    assert client.pair_calls == []


def test_a_pairing_intervals_icu_did_not_record_raises() -> None:
    client = _Client(paired_event_id=None)

    with pytest.raises(PairingNotRecorded, match="did not record the pairing"):
        pair_activity(client, "i123", PairByEventRequest(event_id=42), _no_repos)


def test_intervals_icu_errors_propagate_to_the_caller() -> None:
    client = _Client(exc=IntervalsApiError(404, "no such activity"))

    with pytest.raises(IntervalsApiError):
        pair_activity(client, "i123", PairByEventRequest(event_id=42), _no_repos)


def _detail(name: str) -> IntervalsActivity:
    return IntervalsActivity(
        id="i9", name=name, type="Run", start_date_local="2026-10-26T15:31:10"
    )


def _session(session_id: int, day: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        date=day,
        sport="running",
        activity_key="run_easy",
        status="pending",
        matched_activity_id=None,
        duration_minutes=40.0,
        optional=False,
        workout_json=None,
        pushed_event_json=json.dumps({"name": title}),
    )


class _DetailClient:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_activity(self, activity_id: str) -> IntervalsActivity:
        return _detail(self._name)


_WEEK = [
    _session(470, "2026-10-25", "Optional: Easy Run + Strides"),
    _session(477, "2026-11-01", "Long Trail Run"),
]


def test_a_link_against_the_activitys_own_name_is_refused() -> None:
    # Seen live: the run named after Sunday 1 November's Long Trail Run
    # was linked three times to Sunday 25 October's strides.
    repos = _repos(SimpleNamespace(id=7), _WEEK)

    message = name_mismatch(
        repos, _DetailClient("Hillside - Long Trail Run"), "i9", 470
    )

    assert message is not None
    assert "Long Trail Run planned Sunday 1 November (session_id 477)" in message
    assert "activity's sheet" in message


@pytest.mark.parametrize(
    ("name", "session_id"),
    [
        ("Hillside - Long Trail Run", 477),
        ("Riverside Running", 470),
    ],
    ids=["the-named-session", "a-free-run"],
)
def test_a_link_the_name_allows_passes(name: str, session_id: int) -> None:
    repos = _repos(SimpleNamespace(id=7), _WEEK)

    assert name_mismatch(repos, _DetailClient(name), "i9", session_id) is None
