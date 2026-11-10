"""Remove coach-scheduled ad-hoc workout(s) from the intervals.icu calendar.

Reverse of :mod:`endury.planning.schedule_workout`. Only events keyed by
:data:`endury.intervals.push.AD_HOC_EXTERNAL_ID_PREFIX` are touched — plan
sessions and race events use their own schemes, so they are structurally
out of reach here. Nothing is stored locally, so intervals.icu is read
live and the remote delete is the whole operation.

Two-phase, unlike ``schedule_workout``'s one-call booking: a preview
deletes nothing, and only ``confirm=True`` removes anything.
:func:`remove_ad_hoc_workouts` is the shared "just remove them" path
behind this verb's confirm, a plan pause's ad-hoc sweep, and
:func:`pause_without_plan`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from endury.common import clock
from endury.intervals.push import AD_HOC_EXTERNAL_ID_PREFIX

logger = logging.getLogger(__name__)

_PREVIEW_MESSAGE = (
    "Nothing has been removed yet. The athlete removes these exact "
    "workout(s) from their card; relay what it shows."
)

#: Caveat every plan-less pause carries: there is nothing here to skip or
#: replan, so the coach is the only place the constraint gets recorded.
NO_PLAN_CAVEAT = (
    "There is no training plan, so nothing was replanned and no plan "
    "sessions were skipped. Record the constraint yourself, including a "
    '"- Next check-in YYYY-MM-DD: <why>" bullet, via update_memory.'
)
_NO_PLAN_PREVIEW_MESSAGE = (
    "Nothing has been removed yet. The athlete removes these exact "
    "workout(s) from their card; relay what it shows. There is no active "
    "plan, so nothing else changes."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a refusal the coach relays verbatim. Nothing was written."""
    return {"status": "error", "message": message, **extra}


def _not_found(day: date, through: date) -> dict[str, Any]:
    """Report an empty window: nothing coach-scheduled was ever there."""
    return {
        "status": "not_found",
        "message": (
            "No coach-scheduled workout was found between "
            f"{day.isoformat()} and {through.isoformat()}."
        ),
    }


def ad_hoc_workouts(events: list[Any]) -> list[dict[str, Any]]:
    """Filter remote calendar events down to coach-scheduled ad-hoc workouts.

    A plan session or a race event never starts with
    ``AD_HOC_EXTERNAL_ID_PREFIX`` (see ``session_external_id``/
    ``race_external_id`` in ``endury.intervals.push``), so this filter is
    the whole boundary between this tool and the rest of the plan.

    Args:
        events: Calendar events read back from intervals.icu.

    Returns:
        One dict per matching event: ``date``, ``name``, ``external_id``,
        ``event_id``.
    """
    workouts = []
    for event in events:
        external_id = getattr(event, "external_id", None) or ""
        if not external_id.startswith(AD_HOC_EXTERNAL_ID_PREFIX):
            continue
        workouts.append(
            {
                "date": (getattr(event, "start_date_local", None) or "")[:10],
                "name": getattr(event, "name", None) or "",
                "external_id": external_id,
                "event_id": getattr(event, "id", None),
            }
        )
    return workouts


def _read_ad_hoc_workouts(
    client: Any, day: date, through: date
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Read back ad-hoc workouts in ``[day, through]``. Never raises.

    Returns:
        ``(workouts, None)`` on success, or ``(None, message)`` when the
        calendar read failed.
    """
    try:
        events = client.list_events(day, through, category="WORKOUT")
    except Exception as exc:
        logger.warning(
            "Listing calendar events failed for %s..%s: %s", day, through, exc
        )
        return None, f"Reading the intervals.icu calendar failed: {exc}"
    return ad_hoc_workouts(events), None


def remove_ad_hoc_workouts(
    *, client: Any | None, day: date, through: date
) -> dict[str, Any]:
    """List, then delete, coach-scheduled ad-hoc workouts in ``[day, through]``.

    The one path behind ``unschedule_workout``'s own confirmed delete, the
    ad-hoc sweep a plan pause runs alongside its own skips, and a plan-less
    pause's whole job — reused rather than forked so a workout is removed
    the same way everywhere it is removed at all. Never raises.

    Args:
        client: intervals.icu client, or ``None`` when unconfigured.
        day: First date to look on.
        through: Last date to look through (inclusive).

    Returns:
        ``{"status": "ok", "deleted": n, "workouts": [...]}``,
        ``{"status": "not_found", "deleted": 0, "workouts": []}`` when
        nothing matches, or ``{"status": "error", "message": ..., "deleted":
        0, "workouts": [...]}`` (the last carries the read-back list only
        when the read succeeded and the delete itself failed).
    """
    if client is None:
        return {
            "status": "error",
            "message": "intervals.icu is not configured — nothing was removed.",
            "deleted": 0,
            "workouts": [],
        }
    workouts, error = _read_ad_hoc_workouts(client, day, through)
    if error is not None:
        return {"status": "error", "message": error, "deleted": 0, "workouts": []}
    if not workouts:
        return {"status": "not_found", "deleted": 0, "workouts": []}

    external_ids = [item["external_id"] for item in workouts]
    try:
        deleted = int(client.bulk_delete_events(external_ids))
    except Exception as exc:
        logger.warning("Removing workouts %s failed: %s", external_ids, exc)
        return {
            "status": "error",
            "message": f"Removing the workout(s) from intervals.icu failed: {exc}",
            "deleted": 0,
            "workouts": workouts,
        }
    if deleted != len(external_ids):
        return {
            "status": "error",
            "message": (
                f"Only {deleted} of {len(external_ids)} workout(s) were removed "
                "from intervals.icu — check the calendar before trying again."
            ),
            "deleted": deleted,
            "workouts": workouts,
        }

    logger.info("Removed %d ad-hoc workout(s): %s", deleted, external_ids)
    return {"status": "ok", "deleted": deleted, "workouts": workouts}


def _no_plan_response(
    status: str, window: dict[str, str], **extra: Any
) -> dict[str, Any]:
    """The shared shape every ``pause_without_plan`` outcome answers with."""
    return {
        "status": status,
        "no_plan": True,
        "window": window,
        "workouts": [],
        "replan": None,
        "first_week": None,
        **extra,
    }


def _no_plan_not_found(
    window: dict[str, str], today: date, through: date
) -> dict[str, Any]:
    """Build the "nothing to remove, and no plan either" report.

    The constraint still has to land somewhere, and with no plan and no
    booking the coach's own memory document is the only place left — so
    this outcome carries the same caveat as the others.
    """
    return _no_plan_response(
        "not_found",
        window,
        caveats=[NO_PLAN_CAVEAT],
        message=(
            "There is no active training plan, and no coach-scheduled "
            f"workout was found between {today.isoformat()} and "
            f"{through.isoformat()} either. Nothing was replanned and no "
            "plan sessions were skipped."
        ),
    )


def pause_without_plan(
    *, through: date, confirm: bool, client: Any | None, today: date
) -> dict[str, Any]:
    """Preview or apply a pause when there is no active plan to pause.

    There is nothing to skip or replan, so a plan-less pause is exactly
    ``unschedule_workout`` over ``[today, through]`` — including today's own
    bookings, unlike the ad-hoc sweep an active-plan pause runs — wearing
    the pause's own wire shape, with the caveat that the coach itself has to
    record the constraint.

    Args:
        through: Last day the athlete is out (inclusive).
        confirm: Must be True to remove anything.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date.

    Returns:
        The preview (``status`` "preview"), the applied outcome (``status``
        "ok"), ``status`` "not_found" when nothing matches, or an
        error — never raises.
    """
    window = {"from": today.isoformat(), "through": through.isoformat()}
    if through < today:
        return _no_plan_response(
            "error",
            window,
            message=(
                f"{through.isoformat()} has already passed, so there is "
                "nothing to pause. Pass the last day the athlete is out."
            ),
        )
    if client is None:
        return _no_plan_response(
            "error",
            window,
            message="intervals.icu is not configured — nothing was removed.",
        )

    if not confirm:
        workouts, error = _read_ad_hoc_workouts(client, today, through)
        if error is not None:
            return _no_plan_response("error", window, message=error)
        if not workouts:
            return _no_plan_not_found(window, today, through)
        return _no_plan_response(
            "preview",
            window,
            confirm_required=True,
            workouts=workouts,
            caveats=[NO_PLAN_CAVEAT],
            message=_NO_PLAN_PREVIEW_MESSAGE,
        )

    removal = remove_ad_hoc_workouts(client=client, day=today, through=through)
    if removal["status"] == "not_found":
        return _no_plan_not_found(window, today, through)
    if removal["status"] == "error":
        return _no_plan_response("error", window, message=removal["message"])
    return _no_plan_response(
        "ok",
        window,
        confirm_required=False,
        workouts=removal["workouts"],
        deleted=removal["deleted"],
        caveats=[NO_PLAN_CAVEAT],
        message=(
            f"Removed {removal['deleted']} workout(s) from the calendar. "
            "There is no active training plan, so nothing was replanned."
        ),
    )


def unschedule_workout(
    *,
    date_str: str,
    through_str: str | None,
    confirm: bool,
    client: Any | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Preview or remove coach-scheduled ad-hoc workout(s) from the calendar.

    Args:
        date_str: First date to look on, ISO ``YYYY-MM-DD``.
        through_str: Last date to look through (inclusive), or ``None`` to
            look on ``date_str`` alone.
        confirm: Must be True to remove anything.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).

    Returns:
        ``{"status": "not_found", "message": ...}`` when nothing matches,
        the preview (``status`` "preview"), the applied outcome (``status``
        "ok"), or an error/refusal dict — never raises.
    """
    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        return _error(f"'{date_str}' is not a date — pass YYYY-MM-DD.")

    through = day
    if through_str is not None:
        try:
            through = date.fromisoformat(through_str)
        except ValueError:
            return _error(f"'{through_str}' is not a date — pass YYYY-MM-DD.")

    today = today or clock.today()
    if day < today:
        return _error(
            f"{day.isoformat()} has passed — only today or a future date can "
            "be unscheduled."
        )
    if through < day:
        return _error(
            f"{through.isoformat()} is before {day.isoformat()} — "
            "through_date must be on or after date."
        )
    if client is None:
        return _error("intervals.icu is not configured — nothing was removed.")

    if not confirm:
        workouts, error = _read_ad_hoc_workouts(client, day, through)
        if error is not None:
            return _error(error)
        if not workouts:
            return _not_found(day, through)
        return {
            "status": "preview",
            "confirm_required": True,
            "workouts": workouts,
            "message": _PREVIEW_MESSAGE,
        }

    removal = remove_ad_hoc_workouts(client=client, day=day, through=through)
    if removal["status"] == "not_found":
        return _not_found(day, through)
    if removal["status"] == "error":
        return _error(str(removal["message"]))
    return {
        "status": "ok",
        "confirm_required": False,
        "deleted": removal["deleted"],
        "workouts": removal["workouts"],
        "message": f"Removed {removal['deleted']} workout(s) from the calendar.",
    }


__all__ = [
    "NO_PLAN_CAVEAT",
    "ad_hoc_workouts",
    "pause_without_plan",
    "remove_ad_hoc_workouts",
    "unschedule_workout",
]
