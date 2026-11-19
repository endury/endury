"""HTTP client for the intervals.icu REST API.

Wraps :class:`httpx.Client` with the auth/transport shape intervals.icu
expects (HTTP Basic with the literal username ``"API_KEY"``, or an OAuth
access token as a Bearer header), a single retry
on ``429`` honoring ``Retry-After``, a short bounded retry on transient
network failures and ``5xx`` responses, and typed errors so callers can tell
an auth failure apart from any other API error. The transport is injectable
so tests run fully offline against :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import date
from typing import Any

import httpx

from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalsActivity,
    IntervalsActivityDetail,
    IntervalsEvent,
    SportSettingsRow,
    WellnessDay,
)

_USER_AGENT = "endury/0.1 (+https://localhost)"
_TIMEOUT_S = 30.0
_DEFAULT_BASE_URL = "https://intervals.icu/api/v1"
_DEFAULT_RETRY_AFTER_S = 1.0

#: Upper bound on an honored ``Retry-After``. The 15-minute bucket can ask
#: for a delay far longer than any request should block for; past this we
#: retry once and let the caller see the error instead.
_MAX_RETRY_AFTER_S = 30.0

#: Extra attempts for a transient failure — a dropped/timed-out connection
#: or a ``5xx`` response — beyond the first. Covers a single network blip
#: on the phone -> tunnel -> host deployment path so it does not
#: surface as a failed plan commit. Separate from the ``429`` retry above,
#: which honors the server's own ``Retry-After`` instead of a fixed backoff.
_TRANSIENT_MAX_RETRIES = 2
_TRANSIENT_RETRY_BACKOFF_S = 0.25

#: httpx exceptions that represent a transient network failure worth
#: retrying, as opposed to a malformed request or a real API error.
_TRANSIENT_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)

#: Projection for GET .../activities — always ask for exactly the fields we
#: use, per the API facts in docs/reference/intervals_api.md. Mirrors
#: ``IntervalsActivity``.
_ACTIVITY_FIELDS = (
    "id,name,start_date_local,type,source,device_name,moving_time,elapsed_time,icu_recording_time,"
    "icu_training_load,distance,paired_event_id,compliance,"
    "average_heartrate,max_heartrate,icu_average_watts,icu_weighted_avg_watts,"
    "average_speed,pace,gap,average_cadence,total_elevation_gain,"
    "icu_intensity,icu_variability_index,icu_efficiency_factor,decoupling,trimp,"
    "icu_hr_zone_times,icu_zone_times,pace_zone_times,"
    "icu_ftp,lthr,threshold_pace,interval_summary,feel,icu_rpe,"
    "average_temp,average_weather_temp,carbs_ingested,route_id"
)


class IntervalsApiError(Exception):
    """Raised when the API responds with a non-2xx status other than auth."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize the error.

        Args:
            status_code: HTTP status code returned by the API.
            message: Response body text (or a short description).
        """
        self.status_code = status_code
        super().__init__(f"intervals.icu API error {status_code}: {message}")


class IntervalsAuthError(IntervalsApiError):
    """Raised on 401/403 responses (a missing, invalid or withdrawn credential)."""


def _parse_retry_after(header: str | None) -> float:
    """Parse a ``Retry-After`` header value into seconds.

    Args:
        header: The raw ``Retry-After`` header value, if present.

    Returns:
        Seconds to wait, clamped to ``[0, _MAX_RETRY_AFTER_S]`` and falling
        back to a small default when the header is missing or not a plain
        integer/float (intervals.icu always sends delta-seconds, not an
        HTTP-date, so that is all we support).
    """
    if header is None:
        return _DEFAULT_RETRY_AFTER_S
    try:
        return min(max(0.0, float(header)), _MAX_RETRY_AFTER_S)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_S


class IntervalsClient:
    """Thin, typed wrapper over the intervals.icu REST API."""

    def __init__(
        self,
        api_key: str = "",
        athlete_id: str = "0",
        base_url: str = _DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: intervals.icu API key, used as the Basic-Auth password.
            athlete_id: Athlete id path segment; "0" means the authenticated
                athlete.
            base_url: API base URL, overridable for tests.
            transport: Optional injectable httpx transport for offline tests.
            access_token: An OAuth access token; sent as a Bearer header
                in place of the key.
        """
        self._athlete_id = athlete_id
        headers = {"User-Agent": _USER_AGENT}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._client = httpx.Client(
            base_url=base_url,
            auth=None if access_token else ("API_KEY", api_key),
            headers=headers,
            timeout=_TIMEOUT_S,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> IntervalsClient:
        """Return self for use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client on context-manager exit."""
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue one request, retrying once on 429 and raising typed errors.

        Each of the (up to two) attempts here goes through
        `_send_with_transient_retry`, which absorbs a dropped connection or
        a ``5xx`` on its own before this method ever sees it.

        Args:
            method: HTTP method.
            path: Request path, relative to the client's base URL.
            **kwargs: Forwarded to :meth:`httpx.Client.request` (``params``,
                ``json``, etc.).

        Returns:
            The successful response.

        Raises:
            IntervalsAuthError: On a 401/403 response.
            IntervalsApiError: On any other non-2xx response (after the
                transient retry below is exhausted, for a 5xx).
        """
        response = self._send_with_transient_retry(method, path, **kwargs)
        if response.status_code == 429:
            time.sleep(_parse_retry_after(response.headers.get("Retry-After")))
            response = self._send_with_transient_retry(method, path, **kwargs)
        if response.status_code in (401, 403):
            raise IntervalsAuthError(response.status_code, response.text)
        if response.status_code >= 400:
            raise IntervalsApiError(response.status_code, response.text)
        return response

    def _send_with_transient_retry(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """Send one request, retrying transient failures a bounded number of times.

        Retries `_TRANSIENT_EXCEPTIONS` and ``5xx`` responses up to
        `_TRANSIENT_MAX_RETRIES` extra times, with a short exponential
        backoff. Every write this client makes
        (`bulk_upsert_events`/`bulk_delete_events`) is an external_id-keyed
        upsert/delete, so re-sending after a transient failure is safe.

        Args:
            method: HTTP method.
            path: Request path, relative to the client's base URL.
            **kwargs: Forwarded to :meth:`httpx.Client.request`.

        Returns:
            The response, which may still carry a non-2xx status (429, or
            a non-retried 4xx) for `_request` to interpret.

        Raises:
            httpx.ConnectError: If every attempt fails to connect.
            httpx.ConnectTimeout: If every attempt times out connecting.
            httpx.ReadTimeout: If every attempt times out reading.
        """
        attempt = 0
        while True:
            try:
                response = self._client.request(method, path, **kwargs)
            except _TRANSIENT_EXCEPTIONS:
                if attempt >= _TRANSIENT_MAX_RETRIES:
                    raise
            else:
                if response.status_code < 500 or attempt >= _TRANSIENT_MAX_RETRIES:
                    return response
            time.sleep(_TRANSIENT_RETRY_BACKOFF_S * 2**attempt)
            attempt += 1

    def get_athlete(self) -> AthleteProfile:
        """Fetch the athlete's profile, including per-sport settings.

        Returns:
            The parsed athlete profile.
        """
        response = self._request("GET", f"/athlete/{self._athlete_id}")
        return AthleteProfile.model_validate(response.json())

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        """List completed activities in a date window.

        Args:
            oldest: Oldest local date to include (inclusive).
            newest: Newest local date to include (inclusive).

        Returns:
            Activities projected to the fields the coach app uses.
        """
        response = self._request(
            "GET",
            f"/athlete/{self._athlete_id}/activities",
            params={
                "oldest": oldest.isoformat(),
                "newest": newest.isoformat(),
                "fields": _ACTIVITY_FIELDS,
            },
        )
        return [IntervalsActivity.model_validate(row) for row in response.json()]

    def get_activity(self, activity_id: str) -> IntervalsActivityDetail:
        """Fetch one activity with its per-interval breakdown.

        ``GET /activity/{id}`` has no ``fields=`` projection, so the raw
        response is large (tens of KB); callers project it down before
        handing anything to the LLM.

        Args:
            activity_id: intervals.icu activity id (e.g. ``"i176644752"``).

        Returns:
            The activity with ``icu_intervals`` (reps) and ``icu_groups``
            (rolled-up repeats).
        """
        response = self._request(
            "GET", f"/activity/{activity_id}", params={"intervals": "true"}
        )
        return IntervalsActivityDetail.model_validate(response.json())

    def get_activity_streams(
        self, activity_id: str, types: Sequence[str]
    ) -> list[ActivityStream]:
        """Fetch one or more raw per-sample data streams for an activity.

        ``GET /activity/{id}/streams.json`` has no ``fields=`` projection;
        a requested type the activity does not have is simply absent from
        the response rather than an error.

        Args:
            activity_id: intervals.icu activity id (e.g. ``"i176644752"``).
            types: Stream type names to request (e.g. ``["watts",
                "heartrate"]``).

        Returns:
            The streams the activity actually has, in the API's own order.
        """
        response = self._request(
            "GET",
            f"/activity/{activity_id}/streams.json",
            params={"types": ",".join(types)},
        )
        return [ActivityStream.model_validate(row) for row in response.json()]

    def pair_activity(self, activity_id: str, event_id: int) -> IntervalsActivity:
        """Pair one activity with a planned calendar event.

        ``PUT /activity/{id}`` accepts a partial ``Activity`` body; sending
        only ``paired_event_id`` leaves everything else untouched. Idempotent,
        so the transient retry in ``_request`` is safe here too.

        Args:
            activity_id: intervals.icu activity id (e.g. ``"i176644752"``).
            event_id: intervals.icu calendar event id to pair with.

        Returns:
            The updated activity (``paired_event_id`` reflects the pairing).
        """
        response = self._request(
            "PUT", f"/activity/{activity_id}", json={"paired_event_id": event_id}
        )
        return IntervalsActivity.model_validate(response.json())

    def update_activity_verdict(
        self, activity_id: str, *, feel: int | None = None, icu_rpe: int | None = None
    ) -> IntervalsActivity:
        """Record the athlete's own verdict on one activity.

        ``PUT /activity/{id}`` accepts a partial ``Activity`` body; only the
        fields given are sent, so everything else stays untouched. Idempotent,
        so the transient retry in ``_request`` is safe here too.

        Args:
            activity_id: intervals.icu activity id (e.g. ``"i176644752"``).
            feel: "How did you feel?", 1 (strong) to 5 (weak).
            icu_rpe: Perceived effort, 1 to 10.

        Returns:
            The updated activity.
        """
        body = {
            name: value
            for name, value in (("feel", feel), ("icu_rpe", icu_rpe))
            if value is not None
        }
        response = self._request("PUT", f"/activity/{activity_id}", json=body)
        return IntervalsActivity.model_validate(response.json())

    def update_sport_settings(
        self, row_id: int, fields: dict[str, Any], *, recalc_hr_zones: bool
    ) -> SportSettingsRow:
        """Update one sport-settings row's thresholds.

        ``PUT /athlete/{id}/sport-settings/{row_id}`` accepts a partial
        ``SportSettings`` body; fields not sent stay as they are. Past
        activities are not re-scored. Idempotent, so the transient retry in
        ``_request`` is safe here too.

        Args:
            row_id: The sport-settings row id.
            fields: Wire fields to set (``ftp``, ``lthr``, ``threshold_pace``).
            recalc_hr_zones: Let intervals.icu recompute the row's HR zones
                from the new LTHR.

        Returns:
            The updated row.
        """
        response = self._request(
            "PUT",
            f"/athlete/{self._athlete_id}/sport-settings/{row_id}",
            params={"recalcHrZones": "true" if recalc_hr_zones else "false"},
            json=fields,
        )
        return SportSettingsRow.model_validate(response.json())

    def list_wellness(self, oldest: date, newest: date) -> list[WellnessDay]:
        """List daily wellness rows in a date window.

        Args:
            oldest: Oldest local date to include (inclusive).
            newest: Newest local date to include (inclusive).

        Returns:
            Wellness rows keyed by ISO date.
        """
        response = self._request(
            "GET",
            f"/athlete/{self._athlete_id}/wellness",
            params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )
        return [WellnessDay.model_validate(row) for row in response.json()]

    def list_events(
        self, oldest: date, newest: date, category: str | None = None
    ) -> list[IntervalsEvent]:
        """List calendar events in a date window.

        Args:
            oldest: Oldest local date to include (inclusive).
            newest: Newest local date to include (inclusive).
            category: Optional category filter (e.g. "WORKOUT"); comma
                separated for multiple categories, per the API.

        Returns:
            Matching calendar events.
        """
        params: dict[str, str] = {
            "oldest": oldest.isoformat(),
            "newest": newest.isoformat(),
        }
        if category is not None:
            params["category"] = category
        response = self._request(
            "GET", f"/athlete/{self._athlete_id}/events", params=params
        )
        return [IntervalsEvent.model_validate(row) for row in response.json()]

    def bulk_upsert_events(self, events: list[dict[str, Any]]) -> list[IntervalsEvent]:
        """Create or update calendar events keyed on their ``external_id``.

        ``upsertOnUid`` and ``updatePlanApplied`` are marked required by
        the published OpenAPI spec; both are sent explicitly false — we key
        on our own ``external_id``, never their uid, and we do not manage
        plan-applied state.

        Args:
            events: Event payload dicts (see the event field list in
                docs/reference/intervals_api.md); each must carry an ``external_id``.

        Returns:
            The created/updated events as returned by the API.
        """
        response = self._request(
            "POST",
            f"/athlete/{self._athlete_id}/events/bulk",
            params={
                "upsert": "true",
                "upsertOnUid": "false",
                "updatePlanApplied": "false",
            },
            json=events,
        )
        return [IntervalsEvent.model_validate(row) for row in response.json()]

    def bulk_delete_events(self, external_ids: list[str]) -> int:
        """Delete calendar events by ``external_id``.

        Args:
            external_ids: External ids of the events to delete. Events that
                do not exist are ignored by the API.

        Returns:
            The number of events actually deleted.
        """
        response = self._request(
            "PUT",
            f"/athlete/{self._athlete_id}/events/bulk-delete",
            json=[{"external_id": external_id} for external_id in external_ids],
        )
        body: dict[str, Any] = response.json()
        return int(body.get("eventsDeleted", 0))
