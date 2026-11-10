"""Serializer for the ``get_inputs`` planning-inputs payload.

Builds the athlete-facing snapshot the Planning Inputs panel renders: per-
sport thresholds/hours/CTL-ATL resolved from intervals.icu history (via
:mod:`endury.intervals.athlete_source`), intervals.icu's own combined
CTL/ATL as a cross-check, a 7-day wellness strip, and any onward push
errors recorded against recently pushed calendar events.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx

from endury.common import clock
from endury.intervals.athlete_source import (
    ResolvedAthleteState,
    resolve_from_intervals,
)
from endury.intervals.client import (
    IntervalsApiError,
    IntervalsAuthError,
    IntervalsClient,
)
from endury.intervals.models import IntervalsEvent, WellnessDay
from endury.planner.foundations.request import SportState
from endury.planner.foundations.types import GoalType, RaceGoal, SportType

logger = logging.getLogger(__name__)

#: Window backing the combined-CTL/ATL cross-check and the wellness strip.
_WELLNESS_LOOKBACK_DAYS = 14

#: Trailing days averaged/most-recent for the wellness strip.
_WELLNESS_STRIP_DAYS = 7

#: Push-error scan window, centered on today.
_PUSH_ERROR_WINDOW_DAYS = 14

#: Shown when no intervals.icu API key is configured.
NOT_CONFIGURED_REASON = (
    "The intervals.icu API key is not configured — add it in Settings."
)

#: Shown when intervals.icu rejects the configured API key.
AUTH_REJECTED_REASON = "intervals.icu rejected the API key (401/403)."

#: Fallback goal used to pick the reported sport set before any plan
#: exists -- the raceless triathlon profile reports all three sports.
_NO_PLAN_GOAL_TYPE = GoalType.TRIATHLON_MAINTENANCE


def unreachable_payload(exc: Exception) -> dict[str, Any]:
    """Return the ``available: false`` payload for an unreachable API.

    Args:
        exc: The error raised while talking to intervals.icu.
    """
    if isinstance(exc, IntervalsAuthError):
        return {"available": False, "reason": AUTH_REJECTED_REASON}
    return {"available": False, "reason": f"Could not reach intervals.icu: {exc}"}


def _goals_for_inputs(active_plan: Any) -> list[RaceGoal]:
    """Return the A-goal used to pick which sports get reported.

    Args:
        active_plan: The active ``PlanRow``, or ``None`` before any plan
            has been committed.
    """
    if active_plan is not None:
        return [
            RaceGoal(
                goal_type=GoalType(active_plan.goal_type),
                race_date=date.fromisoformat(active_plan.goal_date),
                priority="A",
            )
        ]
    return [
        RaceGoal(
            goal_type=_NO_PLAN_GOAL_TYPE,
            race_date=clock.today(),
            priority="A",
        )
    ]


def _sport_payload(
    sport: SportType,
    state: SportState,
    resolved: ResolvedAthleteState,
) -> dict[str, Any]:
    """Build one sport's entry: thresholds, hours, climb, CTL/ATL, and source."""
    threshold = resolved.thresholds.get(sport)
    return {
        "thresholds": {
            "ftp": threshold.ftp_watts if threshold else None,
            "lthr": threshold.lthr_bpm if threshold else None,
            "threshold_pace_sec_per_km": (
                threshold.threshold_pace_sec_per_km if threshold else None
            ),
            "css_sec_per_100m": threshold.css_sec_per_100m if threshold else None,
            "derived": list(resolved.notes.get(sport, [])),
        },
        "recent_weekly_hours": round(state.recent_weekly_hours, 1),
        "recent_weekly_elevation_m": resolved.recent_weekly_elevation_m.get(sport),
        "longest_recent_min": next(iter(state.longest_recent_min.values()), 0),
        "ctl": round(state.ctl_0, 1) if state.ctl_0 is not None else None,
        "atl": round(state.atl_0, 1) if state.atl_0 is not None else None,
        "source": resolved.sources.get(sport, "default"),
    }


def _combined_ctl_atl(wellness: list[WellnessDay]) -> dict[str, float | None]:
    """Return intervals.icu's own combined CTL/ATL from the latest row."""
    for row in sorted(wellness, key=lambda w: w.id or "", reverse=True):
        if row.ctl is not None or row.atl is not None:
            return {"intervals_ctl": row.ctl, "intervals_atl": row.atl}
    return {"intervals_ctl": None, "intervals_atl": None}


def _latest_present(
    rows: list[WellnessDay], get: Callable[[WellnessDay], float | int | None]
) -> float | int | None:
    """Return the most recent non-null value ``get`` reads off ``rows``."""
    for row in sorted(rows, key=lambda w: w.id or "", reverse=True):
        value = get(row)
        if value is not None:
            return value
    return None


def _wellness_last7(wellness: list[WellnessDay], today: date) -> dict[str, Any]:
    """Return the 7-day wellness strip: sleep average, latest HRV/RHR."""
    window_start = (today - timedelta(days=_WELLNESS_STRIP_DAYS - 1)).isoformat()
    today_iso = today.isoformat()
    recent = [row for row in wellness if row.id and window_start <= row.id <= today_iso]
    sleep_hours = [row.sleep_secs / 3600.0 for row in recent if row.sleep_secs]
    return {
        "sleep_hours_avg": (
            round(sum(sleep_hours) / len(sleep_hours), 1) if sleep_hours else None
        ),
        "hrv_latest": _latest_present(recent, lambda w: w.hrv),
        "resting_hr_latest": _latest_present(recent, lambda w: w.resting_hr),
    }


def _push_errors(events: list[IntervalsEvent]) -> list[dict[str, Any]]:
    """Return one entry per event that carries onward device-sync failures."""
    return [
        {
            "external_id": event.external_id,
            "name": event.name,
            "date": event.start_date_local,
            "errors": [
                {"service": pe.service, "message": pe.message, "date": pe.date}
                for pe in event.push_errors
            ],
        }
        for event in events
        if event.push_errors
    ]


def build_inputs_payload(
    client: IntervalsClient,
    repos: Any,
    active_plan: Any,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the full ``get_inputs`` payload from live intervals.icu data.

    Args:
        client: The intervals.icu client (already confirmed configured).
        repos: Repository container (used for stored self-assessment).
        active_plan: The active ``PlanRow``, or ``None`` before any plan
            has been committed -- picks which sports get reported.
        today: Reference date (defaults to the app clock).

    Returns:
        The JSON-safe payload, with ``available: true``.

    Raises:
        IntervalsApiError: Propagated from the underlying client calls so
            :func:`inputs_snapshot` can degrade to ``available: false``
            instead of a 500.
    """
    today = today or clock.today()
    goals = _goals_for_inputs(active_plan)
    resolved = resolve_from_intervals(
        client, today, goals, repos.self_assessment.get_all()
    )
    wellness = client.list_wellness(
        today - timedelta(days=_WELLNESS_LOOKBACK_DAYS), today
    )
    events = client.list_events(
        today - timedelta(days=_PUSH_ERROR_WINDOW_DAYS),
        today + timedelta(days=_PUSH_ERROR_WINDOW_DAYS),
    )

    return {
        "available": True,
        "reason": None,
        "sports": {
            sport.value: _sport_payload(sport, state, resolved)
            for sport, state in resolved.athlete.sports.items()
        },
        "combined": _combined_ctl_atl(wellness),
        "wellness_last7": _wellness_last7(wellness, today),
        "push_errors": _push_errors(events),
        "upload_destinations": resolved.upload_destinations,
    }


def inputs_snapshot(
    client: IntervalsClient | None, get_repos: Callable[[], Any]
) -> dict[str, Any]:
    """Return the ``get_inputs`` payload, degrading instead of raising.

    When intervals.icu is unconfigured or unreachable the payload carries
    ``available: false`` and a ``reason`` so the frontend renders a setup
    hint instead of an error state.

    Args:
        client: The intervals.icu client, or ``None`` when unconfigured.
        get_repos: Repository container factory; not called when
            intervals.icu is unconfigured.

    Returns:
        The JSON-safe payload.
    """
    if client is None:
        return {"available": False, "reason": NOT_CONFIGURED_REASON}
    repos = get_repos()
    try:
        return build_inputs_payload(client, repos, repos.plan.get_active())
    except (IntervalsApiError, httpx.HTTPError) as exc:
        # httpx.HTTPError covers the transport failures (connect, DNS,
        # timeout) that never reach a status code.
        logger.warning("intervals.icu unreachable for the inputs snapshot: %s", exc)
        return unreachable_payload(exc)


__all__ = [
    "AUTH_REJECTED_REASON",
    "NOT_CONFIGURED_REASON",
    "build_inputs_payload",
    "inputs_snapshot",
    "unreachable_payload",
]
