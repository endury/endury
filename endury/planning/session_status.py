"""Calendar-aware status writes on one planned session.

Marking a session ``skipped`` deletes its intervals.icu event; un-skipping
puts it back from the payload last pushed, re-dated to the session's
current day. :func:`skip_session` is the coach's way in: a preview first,
the write only once the athlete applies its card.

The remote side is best-effort: the local mark is the source of truth,
and a failed calendar write is reported in the ``push`` block, never
raised or rolled back.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from endury.intervals.push import EVENT_TAG, event_start, session_external_id
from endury.planner.activity.materialize import activity_display_name
from endury.planner.foundations.types import ActivityKey
from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE, _remote_result
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.pair_activity import link_activity

logger = logging.getLogger(__name__)

#: The statuses a session may be marked with.
VALID_SESSION_STATUSES = frozenset({"completed", "skipped", "pending", "unlinked"})

#: Statuses that mean "back in the plan" — the ones that restore the event.
_UN_SKIP_STATUSES = frozenset({"pending", "unlinked"})

#: Reported when an un-skipped session has no payload to put back: it never
#: reached intervals.icu in the first place.
_NEVER_PUSHED_MESSAGE = (
    "The session is planned again here, but it was never on intervals.icu; "
    "Sync now adds it."
)


def delete_session_events(
    plan_id: int, slot_ids: Sequence[str], *, client: Any | None
) -> dict[str, Any]:
    """Remove plan sessions' events from the intervals.icu calendar.

    The external_ids are derived from ``(plan_id, slot_id)`` rather than read
    out of the stored payload, so a session that was never pushed (or whose
    stored payload is stale) is still addressed correctly — intervals.icu
    ignores an external_id it does not know.

    Args:
        plan_id: The plan the sessions belong to.
        slot_ids: Slot ids of the sessions to remove.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The wire status block for the delete.
    """
    if client is None:
        return _remote_result("skipped", message=PUSH_SKIPPED_MESSAGE, deleted=0)
    external_ids = [session_external_id(plan_id, str(slot)) for slot in slot_ids]
    try:
        deleted = client.bulk_delete_events(external_ids)
    except Exception as exc:  # the local mark already stands
        logger.warning("intervals.icu delete failed for plan %s: %s", plan_id, exc)
        return _remote_result(
            "error",
            errors=[str(exc)],
            message=(
                "The session is skipped here, but removing it from your "
                f"intervals.icu calendar failed: {exc}"
            ),
            deleted=0,
        )
    return _remote_result("ok", deleted=int(deleted))


def restore_session_event(
    repos: Any, plan_id: int, row: Any, *, client: Any | None
) -> dict[str, Any]:
    """Put a previously skipped session's event back on intervals.icu.

    The stored payload is re-dated to the session row's own date rather than
    re-rendered: the workout did not change while it was skipped, and the
    session may have been moved in the meantime.

    Args:
        repos: Repository container (re-stores the pushed event).
        plan_id: The plan the session belongs to.
        row: The session row (carries ``pushed_event_json``).
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The wire status block for the re-push.
    """
    if client is None:
        return _remote_result("skipped", message=PUSH_SKIPPED_MESSAGE, pushed=0)
    raw = getattr(row, "pushed_event_json", None)
    if not raw:
        return _remote_result("skipped", message=_NEVER_PUSHED_MESSAGE, pushed=0)
    try:
        event = json.loads(raw)
        event["start_date_local"] = event_start(str(row.date))
        event["tags"] = [EVENT_TAG]
        client.bulk_upsert_events([event])
        with repos.session.transaction():
            repos.plan.update_pushed_events(
                plan_id, {str(row.slot_id): json.dumps(event)}
            )
    except Exception as exc:  # the local mark already stands
        logger.warning("Restoring session %s on intervals.icu failed: %s", row.id, exc)
        return _remote_result(
            "error",
            errors=[str(exc)],
            message=(
                "The session is planned again here, but putting it back on "
                f"your intervals.icu calendar failed: {exc}"
            ),
            pushed=0,
        )
    return _remote_result("ok", pushed=1)


def _calendar_effect(
    repos: Any,
    plan_id: int,
    row: Any,
    status: str,
    *,
    was_skipped: bool,
    client: Any | None,
) -> dict[str, Any] | None:
    """Mirror a status transition onto the calendar, or nothing.

    Only the two edges of "skipped" move an event: entering it deletes,
    leaving it for a planned status restores. "completed" never touches the
    calendar — a workout that happened needs no event either way.

    Args:
        repos: Repository container.
        plan_id: The active plan's id.
        row: The session row as it was before the write.
        status: The status just written.
        was_skipped: Whether the row was already skipped.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The wire status block for the remote write, or ``None`` when the
        transition leaves the calendar alone.
    """
    if status == "skipped" and not was_skipped:
        return delete_session_events(plan_id, [str(row.slot_id)], client=client)
    if was_skipped and status in _UN_SKIP_STATUSES:
        return restore_session_event(repos, plan_id, row, client=client)
    return None


def set_session_status(
    *,
    repos: Any,
    client: Any | None,
    session_id: int,
    status: str,
    activity_id: str | None = None,
) -> dict[str, Any]:
    """Mark one planned session's status and mirror it onto the calendar.

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        session_id: The plan session to mark.
        status: One of :data:`VALID_SESSION_STATUSES`.
        activity_id: Optional intervals.icu activity to link. ``None`` keeps
            any existing link; "unlinked" clears it regardless. "completed"
            with an activity and a client is the activity sheet's link
            (:func:`~endury.planning.pair_activity.link_activity`): the
            session moves onto the activity's day and nothing else keeps it.

    Returns:
        ``{"status": "ok", "session_id": id, "new_status": str,
        "activity_id": str | None, "push": {...} | None}`` (a link answers
        ``link_activity``'s result instead), or an error dict when the
        status or the session is unusable.
    """
    if status not in VALID_SESSION_STATUSES:
        return {
            "error": f"Invalid status '{status}'.",
            "options": sorted(VALID_SESSION_STATUSES),
        }
    active = repos.plan.get_active()
    if active is None:
        return {"message": NO_ACTIVE_PLAN_MESSAGE}
    row = next(
        (item for item in repos.plan.get_sessions(active.id) if item.id == session_id),
        None,
    )
    if row is None:
        return {"error": f"Session {session_id} is not part of the active plan."}
    if status == "completed" and activity_id is not None and client is not None:
        try:
            linked = link_activity(repos, client, activity_id, session_id)
        except ValueError as exc:
            return {"error": str(exc)}
        return {**linked, "new_status": status}
    # Omitting activity_id must not wipe an existing link — the repository
    # write sets matched_activity_id unconditionally. "unlinked" is the one
    # status that deliberately clears it.
    effective_activity_id = (
        None
        if status == "unlinked"
        else (activity_id if activity_id is not None else row.matched_activity_id)
    )
    was_skipped = str(getattr(row, "status", "")) == "skipped"
    repos.plan.update_session_status(session_id, status, effective_activity_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "new_status": status,
        "activity_id": effective_activity_id,
        "push": _calendar_effect(
            repos, active.id, row, status, was_skipped=was_skipped, client=client
        ),
    }


def skip_session(
    *, repos: Any, client: Any | None, session_id: int, confirm: bool = False
) -> dict[str, Any]:
    """Preview, or apply, skipping one planned session.

    The preview reads the session and writes nothing; applying runs
    :func:`set_session_status` with ``"skipped"``, which also takes the
    workout off the intervals.icu calendar. A session already skipped, or
    one a completed activity fulfils, offers nothing to apply.

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        session_id: The plan session to skip.
        confirm: True only from the athlete's Apply.

    Returns:
        ``{"status": "preview", "confirm_required": True, "session": {...}}``
        before the Apply, the status write's own result plus a ``message``
        after it, ``{"status": "refused", "message"}`` for a session with
        nothing to skip, or the status write's error shapes.
    """
    active = repos.plan.get_active()
    if active is None:
        return {"message": NO_ACTIVE_PLAN_MESSAGE}
    row = next(
        (item for item in repos.plan.get_sessions(active.id) if item.id == session_id),
        None,
    )
    if row is None:
        return {"error": f"Session {session_id} is not part of the active plan."}
    name = activity_display_name(ActivityKey(str(row.activity_key)))
    day = date.fromisoformat(str(row.date))
    label = f"{name} on {day:%A} {day.day} {day:%B}"
    status = str(getattr(row, "status", ""))
    if status == "skipped":
        return {"status": "refused", "message": f"The {label} is already skipped."}
    if status == "completed" or getattr(row, "matched_activity_id", None):
        return {
            "status": "refused",
            "message": (
                f"The {label} is already done: an activity fulfils it. If that "
                'is the wrong match, set_session_status "unlinked" drops it first.'
            ),
        }
    session = {
        "session_id": session_id,
        "date": str(row.date),
        "activity_key": str(row.activity_key),
        "duration_minutes": getattr(row, "duration_minutes", None),
    }
    if not confirm:
        return {
            "status": "preview",
            "confirm_required": True,
            "session": session,
            "message": (
                f"Skipping takes the {label} out of the plan and off the "
                "intervals.icu calendar once the athlete applies the card."
            ),
        }
    result = set_session_status(
        repos=repos, client=client, session_id=session_id, status="skipped"
    )
    if result.get("status") == "ok":
        result["message"] = f"The {label} is skipped and off your calendar."
        result["session"] = session
    return result
