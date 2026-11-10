"""Plan-vs-intervals.icu sync state for the planner UI.

One read of the calendar answers both questions the plan view asks: how
many upcoming sessions are out of step with intervals.icu, and which ones.
Plan sessions are classified against their pushed payload; coach-scheduled
ad-hoc workouts, which live only on intervals.icu, against device delivery
alone — so the answer exists with or without an active plan. Replaced
workouts a push failed to delete are counted as ``leftover``. Kept separate
from the ``get_inputs`` snapshot, which reports athlete *setup* rather than plan
*delivery*.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import httpx

from endury.common import clock
from endury.intervals.client import IntervalsApiError, IntervalsAuthError
from endury.intervals.push import (
    SessionSyncState,
    ad_hoc_sync_states,
    session_sync_states,
)

logger = logging.getLogger(__name__)

#: States that mean "intervals.icu does not match the plan".
_OUT_OF_SYNC: tuple[str, ...] = ("missing", "removed", "delivery_failed", "edited")

#: Slack on both readback bounds so a hand-moved event is still found (and
#: reads ``edited``) instead of falling outside the window and reading
#: ``removed``. Extra events are keyed out by external_id. Shared with the
#: replan read-back, which has the same blind spot.
READBACK_PAD_DAYS = 30

NO_PLAN_REASON = "No active plan."
NOT_CONFIGURED_REASON = "intervals.icu is not connected."
AUTH_REJECTED_REASON = "intervals.icu rejected the API key (401/403)."

#: Solve-payload key holding the external ids of replaced workouts a push
#: failed to delete from intervals.icu.
PENDING_DELETES_KEY = "pending_event_deletes"


def _solve_payload(plan: Any) -> dict[str, Any]:
    """Return a plan's solve payload, or ``{}`` when absent or unreadable."""
    try:
        payload = json.loads(getattr(plan, "solve_result_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def pending_deletes(plan: Any) -> list[str]:
    """Replaced workouts a push could not delete from intervals.icu.

    A push upserts the new events first and deletes the replaced ones after,
    so a failed delete leaves old and new workouts side by side on the
    calendar. Their external ids stay on the plan's solve payload until a
    later push removes them; every push retries them.

    Args:
        plan: A plan row, or ``None``.

    Returns:
        The external ids still to delete, empty when there are none.
    """
    if plan is None:
        return []
    ids = _solve_payload(plan).get(PENDING_DELETES_KEY)
    return [str(external_id) for external_id in ids] if isinstance(ids, list) else []


def _active_plan(repos: Any, plan_id: int) -> Any:
    """The active plan when it is ``plan_id`` (every push writes the active plan)."""
    plan = repos.plan.get_active()
    return plan if plan is not None and plan.id == plan_id else None


def active_pending_deletes(repos: Any, plan_id: int) -> list[str]:
    """:func:`pending_deletes` of the active plan, when it is ``plan_id``."""
    return pending_deletes(_active_plan(repos, plan_id))


def record_pending_deletes(repos: Any, plan_id: int, external_ids: list[str]) -> None:
    """Store the external ids a push still owes a delete, or clear them.

    Writes nothing when the stored list already matches.

    Args:
        repos: Repository container.
        plan_id: The plan the pending deletes belong to.
        external_ids: The ids still on the calendar; empty clears the list.
    """
    plan = _active_plan(repos, plan_id)
    if plan is None or sorted(pending_deletes(plan)) == sorted(external_ids):
        return
    solve = _solve_payload(plan)
    if external_ids:
        solve[PENDING_DELETES_KEY] = list(external_ids)
    else:
        solve.pop(PENDING_DELETES_KEY, None)
    repos.plan.update_solve_result_json(plan_id, json.dumps(solve))


def repushable_sessions(
    sessions: Sequence[Mapping[str, Any]], rows: Sequence[Any], today: date
) -> list[Mapping[str, Any]]:
    """Sessions a sync may legitimately write, i.e. today onwards, unskipped.

    The ownership contract gives the athlete everything up to today, so a
    sync never resurrects a deleted event, never clobbers an edit to a
    session that already happened, and never back-fills a workout the
    athlete had no chance to do — including a session with no
    ``pushed_event_json`` on file, where "never pushed" must not become
    "needs pushing".

    A skipped session is excluded for the same reason: the skip deleted its
    event on purpose, and a sync must not put it back.

    Args:
        sessions: The plan's materialized sessions.
        rows: The plan's ``plan_sessions`` rows (the status lives there).
        today: Reference date.

    Returns:
        The subset of ``sessions`` to upsert, in input order.
    """
    cutoff = today.isoformat()
    skipped = {
        str(getattr(row, "slot_id", ""))
        for row in rows
        if str(getattr(row, "status", "")) == "skipped"
    }
    return [
        session
        for session in sessions
        if str(session.get("date", "")) >= cutoff
        and str(session.get("slot_id", "")) not in skipped
    ]


def _summary(states: list[SessionSyncState]) -> dict[str, int]:
    """Return per-state counts, always carrying every key."""
    counts = dict.fromkeys(("in_sync", *_OUT_OF_SYNC), 0)
    for entry in states:
        counts[entry.state] = counts.get(entry.state, 0) + 1
    return counts


def _unavailable(reason: str) -> dict[str, Any]:
    """Return the payload shape used when no sync check could run."""
    return {"available": False, "reason": reason, "sessions": {}}


def build_sync_status(
    repos: Any, client: Any | None, *, today: date | None = None
) -> dict[str, Any]:
    """Return the sync state of everything the app put on intervals.icu.

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).

    Returns:
        ``{"available": bool, "summary": {...}, "sessions": {key: {...}},
        "out_of_sync": int}`` — ``sessions`` is keyed by slot_id for plan
        sessions and by external_id for coach-scheduled ad-hoc workouts;
        ``summary["leftover"]`` counts replaced workouts a failed push left
        on the calendar;
        ``available`` is ``False`` with a ``reason`` when there is no key,
        intervals.icu is unreachable, or there is neither a plan nor a
        coach-scheduled workout ahead to report on.
    """
    if client is None:
        return _unavailable(NOT_CONFIGURED_REASON)
    reference = today or clock.today()
    active = repos.plan.get_active()
    rows = (
        [
            row
            for row in repos.plan.get_sessions(active.id)
            if str(getattr(row, "date", "")) >= reference.isoformat()
        ]
        if active is not None
        else []
    )
    pad = timedelta(days=READBACK_PAD_DAYS)
    if rows:
        newest = max(date.fromisoformat(str(row.date)) for row in rows)
        window = (reference - pad, newest + pad)
    else:
        # Nothing planned ahead: the read only serves coach-scheduled
        # workouts, which are booked week by week.
        window = (reference, reference + pad)
    try:
        remote = client.list_events(*window)
    except IntervalsAuthError as exc:
        logger.warning("intervals.icu rejected the API key for sync status: %s", exc)
        return _unavailable(AUTH_REJECTED_REASON)
    except (IntervalsApiError, httpx.HTTPError) as exc:
        logger.warning("intervals.icu unreachable for sync status: %s", exc)
        return _unavailable(f"Could not reach intervals.icu: {exc}")

    states = session_sync_states(active.id, rows, remote, reference) if rows else []
    states += ad_hoc_sync_states(remote, reference)
    if active is None and not states:
        return _unavailable(NO_PLAN_REASON)
    summary = _summary(states)
    on_calendar = {getattr(event, "external_id", None) for event in remote}
    summary["leftover"] = len(set(pending_deletes(active)) & on_calendar)
    return {
        "available": True,
        "summary": summary,
        "out_of_sync": sum(summary[state] for state in (*_OUT_OF_SYNC, "leftover")),
        "sessions": {
            entry.slot_id: {
                "state": entry.state,
                "services": list(entry.services),
                "changed_fields": list(entry.changed_fields),
            }
            for entry in states
        },
    }
