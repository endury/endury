"""An in-memory intervals.icu: the offline tests, the browser E2E harness, the demo.

:class:`FakeIntervals` answers the REST surface ``IntervalsClient`` speaks —
the athlete profile, activities (list, detail, streams, pairing), wellness,
and the calendar events Endury writes — from in-memory state. It works at
the HTTP level, so the code under test drives a real ``IntervalsClient``
(``fake.client()``, over an ``httpx.MockTransport``) and every request and
response goes through the same paths, params, JSON bodies and pydantic
parsing as against the live API. ``handle`` is the transport-free core a
stdlib HTTP server can front for a browser run. The demo athlete
(``endury/browser/demo.py``) boots against one inside the worker, so it
lives here rather than under ``devtools/``.

Event semantics follow the verified API facts in
``docs/reference/intervals_api.md``: a bulk upsert matches on
``external_id`` (a match is updated in place and keeps its server id), a
bulk delete by ``external_id`` ignores unknown ids, ``start_date_local``
must be a full LocalDateTime, and ``list_events`` filters on the date part
of ``start_date_local`` plus an optional comma-separated ``category``.
Fields the server never interprets for us — ``hide_from_athlete``, ``tags``,
a NOTE's ``description`` — are stored and returned verbatim, up to the
1 MB description the live API was measured to round-trip.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel

from endury.intervals.client import IntervalsClient
from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalsActivity,
    IntervalsActivityDetail,
    WellnessDay,
)

#: Longest event ``description`` the live API was measured to round-trip
#: exactly; a longer one is refused here.
MAX_DESCRIPTION_CHARS = 1_000_000

#: Path prefix of the default base URL, stripped before routing so the fake
#: serves both ``/api/v1/athlete/0`` and ``/athlete/0``.
_API_PREFIX = "/api/v1"

#: First server id handed to a created event.
_FIRST_EVENT_ID = 1001


@dataclass(frozen=True)
class FakeResponse:
    """One answered request: an HTTP status and its JSON body."""

    status: int
    body: Any


def _dump(model: BaseModel) -> dict[str, Any]:
    """Serialize a model the way the API sends it (wire aliases, no nulls)."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _day(value: Any) -> date | None:
    """Return the date part of an ISO date/datetime string, if it has one."""
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _error(status: int, message: str) -> FakeResponse:
    """Build an error response carrying the API's ``{"error": ...}`` shape."""
    return FakeResponse(status, {"error": message})


class FakeIntervals:
    """In-memory intervals.icu account: scripted reads plus a live calendar.

    Every attribute is public and mutable, so a test can script more
    history mid-run or inspect exactly what was written. ``events`` holds
    the raw stored event dicts keyed by ``external_id`` (the shape the API
    returns), ``calls`` logs every ``(method, path)`` served.
    """

    def __init__(
        self,
        *,
        athlete: AthleteProfile | None = None,
        activities: Sequence[IntervalsActivity] = (),
        details: Mapping[str, IntervalsActivityDetail] | None = None,
        streams: Mapping[str, Sequence[ActivityStream]] | None = None,
        wellness: Sequence[WellnessDay] = (),
    ) -> None:
        """Initialize the account.

        Args:
            athlete: The profile ``GET /athlete/{id}`` returns; defaults to
                an athlete with no sport settings.
            activities: Completed activities, served by the list endpoint
                and — without a scripted detail — the detail endpoint.
            details: Per-activity ``GET /activity/{id}`` bodies (with
                interval rows), keyed by activity id.
            streams: Per-activity data streams, keyed by activity id.
            wellness: Daily wellness rows.
        """
        self.athlete = athlete or AthleteProfile(id="i1", sportSettings=[])
        self.activities = list(activities)
        self.details = dict(details or {})
        self.streams = {key: list(rows) for key, rows in (streams or {}).items()}
        self.wellness = list(wellness)
        self.events: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self._next_event_id = _FIRST_EVENT_ID
        self._failures: list[tuple[int, str | None]] = []

    # -- wiring --------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        """Return an httpx transport that routes every request to this fake."""
        return httpx.MockTransport(self._serve)

    def client(self, athlete_id: str = "0") -> IntervalsClient:
        """Return a real ``IntervalsClient`` bound to this fake.

        Args:
            athlete_id: Athlete id path segment the client sends.
        """
        return IntervalsClient(
            api_key="fake-key", athlete_id=athlete_id, transport=self.transport()
        )

    def fail_next(self, status: int, method: str | None = None) -> None:
        """Answer the next request (of ``method``, if given) with ``status``.

        Queued failures are consumed in order, one per matching request.
        Use a 4xx to exercise an error path: the client retries a 5xx and
        sleeps on a 429 before giving up.

        Args:
            status: HTTP status to answer with.
            method: Only fail a request of this HTTP method.
        """
        self._failures.append((status, method.upper() if method else None))

    def event(self, external_id: str) -> dict[str, Any] | None:
        """Return the stored event with this ``external_id``, if any."""
        return self.events.get(external_id)

    # -- dispatch --------------------------------------------------------------

    def _serve(self, request: httpx.Request) -> httpx.Response:
        """Answer one httpx request (the ``MockTransport`` handler)."""
        body = json.loads(request.content) if request.content else None
        answer = self.handle(
            request.method, request.url.path, dict(request.url.params), body
        )
        return httpx.Response(answer.status, json=answer.body)

    def handle(
        self, method: str, path: str, params: Mapping[str, str], body: Any
    ) -> FakeResponse:
        """Answer one API request.

        Args:
            method: HTTP method.
            path: Request path, with or without the ``/api/v1`` prefix.
            params: Query parameters.
            body: Parsed JSON body, or ``None``.

        Returns:
            The status and JSON body the live API would send.
        """
        method = method.upper()
        path = path.removeprefix(_API_PREFIX)
        self.calls.append((method, path))
        failure = self._take_failure(method)
        if failure is not None:
            return _error(failure, "scripted failure")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "athlete":
            return self._athlete_route(method, parts[2:], params, body)
        if len(parts) >= 2 and parts[0] == "activity":
            return self._activity_route(method, parts[1], parts[2:], params, body)
        return _error(404, f"no route for {method} {path}")

    def _take_failure(self, method: str) -> int | None:
        """Pop the first queued failure that applies to ``method``."""
        for index, (status, only) in enumerate(self._failures):
            if only is None or only == method:
                del self._failures[index]
                return status
        return None

    def _athlete_route(
        self, method: str, rest: list[str], params: Mapping[str, str], body: Any
    ) -> FakeResponse:
        """Route ``/athlete/{id}/...``."""
        route = (method, "/".join(rest))
        if route == ("GET", ""):
            return FakeResponse(200, _dump(self.athlete))
        if route == ("GET", "activities"):
            return FakeResponse(200, self._list_activities(params))
        if route == ("GET", "wellness"):
            return FakeResponse(200, self._list_wellness(params))
        if route == ("GET", "events"):
            return FakeResponse(200, self._list_events(params))
        if route == ("POST", "events/bulk"):
            return self._bulk_upsert(body)
        if route == ("PUT", "events/bulk-delete"):
            return self._bulk_delete(body)
        if method == "PUT" and rest[:1] == ["sport-settings"] and len(rest) == 2:
            return self._update_sport_settings(rest[1], body)
        return _error(404, f"no athlete route for {method} /{route[1]}")

    def _activity_route(
        self,
        method: str,
        activity_id: str,
        rest: list[str],
        params: Mapping[str, str],
        body: Any,
    ) -> FakeResponse:
        """Route ``/activity/{id}/...``."""
        route = (method, "/".join(rest))
        if route == ("GET", "streams.json"):
            wanted = set(params.get("types", "").split(","))
            rows = self.streams.get(activity_id, [])
            return FakeResponse(200, [_dump(r) for r in rows if r.type in wanted])
        if route not in (("GET", ""), ("PUT", "")):
            return _error(404, f"no activity route for {method} /{route[1]}")
        index = next(
            (i for i, a in enumerate(self.activities) if a.id == activity_id), None
        )
        if method == "PUT" and index is not None:
            return self._pair_activity(index, body)
        detail = self.details.get(activity_id)
        if method == "GET" and detail is not None:
            return FakeResponse(200, _dump(detail))
        if method == "GET" and index is not None:
            return FakeResponse(200, _dump(self.activities[index]))
        return _error(404, f"activity {activity_id} not found")

    # -- reads -----------------------------------------------------------------

    @staticmethod
    def _window(params: Mapping[str, str]) -> tuple[date, date]:
        """Parse the inclusive ``oldest``/``newest`` window of a list call."""
        return date.fromisoformat(params["oldest"]), date.fromisoformat(
            params["newest"]
        )

    def _list_activities(self, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """Activities whose local start date falls inside the window."""
        oldest, newest = self._window(params)
        return [
            _dump(activity)
            for activity in self.activities
            if (day := _day(activity.start_date_local)) and oldest <= day <= newest
        ]

    def _list_wellness(self, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """Wellness rows whose ISO date falls inside the window."""
        oldest, newest = self._window(params)
        return [
            _dump(row)
            for row in self.wellness
            if (day := _day(row.id)) and oldest <= day <= newest
        ]

    def _list_events(self, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """Events inside the window, optionally narrowed to categories."""
        oldest, newest = self._window(params)
        categories = set(filter(None, params.get("category", "").split(",")))
        return [
            dict(event)
            for event in sorted(self.events.values(), key=lambda e: e["id"])
            if (day := _day(event.get("start_date_local"))) is not None
            and oldest <= day <= newest
            and (not categories or event.get("category") in categories)
        ]

    # -- writes ----------------------------------------------------------------

    def _bulk_upsert(self, body: Any) -> FakeResponse:
        """Create or update events keyed on ``external_id``; all or nothing."""
        if not isinstance(body, list):
            return _error(422, "expected a JSON array of events")
        for payload in body:
            refusal = self._refuse_event(payload)
            if refusal is not None:
                return refusal
        stored = []
        for payload in body:
            external_id = str(payload["external_id"])
            existing = self.events.get(external_id)
            if existing is None:
                event_id = self._next_event_id
                self._next_event_id += 1
                event = {**payload, "id": event_id}
            else:
                event = {**existing, **payload, "id": existing["id"]}
            self.events[external_id] = event
            stored.append(dict(event))
        return FakeResponse(200, stored)

    @staticmethod
    def _refuse_event(payload: Any) -> FakeResponse | None:
        """Return the 422 the API would send for a malformed event, if any."""
        if not isinstance(payload, dict) or not payload.get("external_id"):
            return _error(422, "every event needs an external_id")
        start = str(payload.get("start_date_local") or "")
        if "T" not in start or _day(start) is None:
            return _error(422, "Invalid start date")
        if len(str(payload.get("description") or "")) > MAX_DESCRIPTION_CHARS:
            return _error(413, "description too long")
        return None

    def _update_sport_settings(self, row_id: str, body: Any) -> FakeResponse:
        """Merge a partial body into the sport-settings row with this id."""
        rows = self.athlete.sport_settings or []
        for index, row in enumerate(rows):
            if str(row.id) == row_id:
                rows[index] = row.model_copy(update=dict(body))
                return FakeResponse(200, _dump(rows[index]))
        return _error(404, f"sport settings {row_id} not found")

    def _bulk_delete(self, body: Any) -> FakeResponse:
        """Delete events by ``external_id``, ignoring ids that do not exist."""
        if not isinstance(body, list):
            return _error(422, "expected a JSON array of {external_id}")
        deleted = 0
        for entry in body:
            if self.events.pop(str(entry.get("external_id")), None) is not None:
                deleted += 1
        return FakeResponse(200, {"eventsDeleted": deleted})

    def _pair_activity(self, index: int, body: Any) -> FakeResponse:
        """Apply a partial ``PUT /activity/{id}`` (pairing, feel and effort)."""
        fields = {
            name: value
            for name, value in (body or {}).items()
            if name in ("paired_event_id", "feel", "icu_rpe")
        }
        updated = self.activities[index].model_copy(update=fields)
        self.activities[index] = updated
        return FakeResponse(200, _dump(updated))


__all__ = ["MAX_DESCRIPTION_CHARS", "FakeIntervals", "FakeResponse"]
