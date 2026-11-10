"""Pair completed activities with planned workouts on intervals.icu.

intervals.icu auto-pairs at upload time within a load-similarity
tolerance; this module pairs what it missed. :func:`pair_activity` is the
athlete's manual pairing: the target is either a local plan session row,
which :func:`link_activity` links (moving it onto the activity's day when
it was planned for another), or an intervals.icu event id directly (ad-hoc
workouts exist only there). Client errors on an event target propagate;
the two outcomes intervals.icu cannot report itself raise the typed errors
below. On every calendar load, :func:`pair_unambiguous` pairs each plan
session exactly one activity fits.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import activity_date
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    IntervalsActivity,
    IntervalsEvent,
)
from endury.intervals.push import session_external_id
from endury.planner.foundations.types import SportType
from endury.planning.activity_match import carries_title, link_view
from endury.planning.commit_plan import _is_race_day, move_session_row
from endury.planning.wire.activity_requests import (
    PairActivityRequest,
    PairByEventRequest,
)

logger = logging.getLogger(__name__)

#: Session marks the athlete set by hand; intervals.icu pairing never
#: overrides them.
MANUAL_OVERRIDE_STATUSES = ("unlinked", "skipped")

#: Events or activities keyed by their local day and sport.
_ByDayAndSport = dict[tuple[str, SportType], list[Any]]


class PairTargetNotFound(LookupError):
    """The planned session, or its intervals.icu event, does not exist."""


class PairingNotRecorded(RuntimeError):
    """intervals.icu answered the pairing write without recording it."""


def resolve_session_event_id(
    repos: Any, client: IntervalsClient, session_id: int
) -> int:
    """Resolve a plan session row id to its intervals.icu calendar event id.

    Args:
        repos: Repository container.
        client: The intervals.icu read client.
        session_id: The local ``plan_sessions`` row id to pair.

    Returns:
        The intervals.icu event id whose ``external_id`` matches the
        session's push scheme.

    Raises:
        PairTargetNotFound: When the session row does not exist in the
            active plan, or has no matching WORKOUT event on intervals.icu
            yet.
    """
    active_plan = repos.plan.get_active()
    if active_plan is None:
        raise PairTargetNotFound("This planned session no longer exists.")
    plan_sessions = repos.plan.get_sessions(active_plan.id)
    session_row = next((row for row in plan_sessions if row.id == session_id), None)
    if session_row is None:
        raise PairTargetNotFound("This planned session no longer exists.")
    external_id = session_external_id(active_plan.id, session_row.slot_id)
    session_date = date.fromisoformat(session_row.date)
    events = client.list_events(session_date, session_date, category="WORKOUT")
    event = next((e for e in events if e.external_id == external_id), None)
    if event is None or event.id is None:
        raise PairTargetNotFound(
            "This session is not on intervals.icu, so there is nothing to pair with."
        )
    return event.id


def pair_activity(
    client: IntervalsClient,
    activity_id: str,
    body: PairActivityRequest,
    get_repos: Callable[[], Any],
) -> dict[str, Any]:
    """Write ``paired_event_id`` on an activity so the calendar shows it done.

    Args:
        client: The intervals.icu client.
        activity_id: intervals.icu activity id (e.g. ``"i176644752"``).
        body: Which planned workout to pair with — a local plan session row
            or an intervals.icu event id directly (ad-hoc workouts).
        get_repos: Repository container factory; only called for a plan
            session target.

    Returns:
        ``{"status": "ok", "activity_id", "event_id"}``.

    Raises:
        PairTargetNotFound: When a plan session target cannot be resolved.
        PairingNotRecorded: When intervals.icu does not record the pairing.
    """
    if not isinstance(body, PairByEventRequest):
        return link_activity(get_repos(), client, activity_id, body.session_id)
    event_id = body.event_id
    activity = client.pair_activity(activity_id, event_id)
    if activity.paired_event_id != event_id:
        raise PairingNotRecorded("intervals.icu did not record the pairing.")
    return {"status": "ok", "activity_id": activity_id, "event_id": event_id}


def link_activity(
    repos: Any, client: IntervalsClient, activity_id: str, session_id: int
) -> dict[str, Any]:
    """Make one activity the one that completed one planned session.

    The athlete's tap on the activity sheet and the coach's link both land
    here. A session planned for another day (done early or late) moves onto
    the activity's day; the activity leaves any session it completed before,
    which reads as not done again, so one activity never completes two; and
    the pairing is written to intervals.icu so its calendar and the watch
    agree with the plan.

    Args:
        repos: Repository container.
        client: The intervals.icu client.
        activity_id: intervals.icu activity id (e.g. ``"i176644752"``).
        session_id: The plan session the activity completed.

    Returns:
        ``{"status": "ok", "activity_id", "session_id", "date", "event_id",
        "unlinked"}``, plus ``moved_from`` when the session changed day and
        ``warning`` when the day is double-booked or intervals.icu did not
        take the move or the pairing (the plan holds the link either way).

    Raises:
        PairTargetNotFound: When the session is not in the active plan.
        ValueError: When the session was skipped, or the activity's day is
            outside the plan or its race day.
    """
    active = repos.plan.get_active()
    rows = repos.plan.get_sessions(active.id) if active is not None else []
    row = next((r for r in rows if r.id == session_id), None)
    if row is None:
        raise PairTargetNotFound("This planned session no longer exists.")
    if str(row.status) == "skipped":
        raise ValueError(
            "This session was skipped. Bring it back before linking an activity."
        )
    day = activity_date(client.get_activity(activity_id))
    if day is None:
        raise ValueError("This activity has no date to link it on.")
    result: dict[str, Any] = {
        "status": "ok",
        "activity_id": activity_id,
        "session_id": session_id,
        "date": day.isoformat(),
    }
    warnings: list[str] = []
    if day.isoformat() != str(row.date):
        if not active.plan_start <= day.isoformat() <= active.goal_date:
            raise ValueError("That day is outside the plan.")
        if _is_race_day(active, day.isoformat()):
            raise ValueError("A training session cannot be linked on race day.")
        result["moved_from"] = str(row.date)
        warnings += move_session_row(repos, active, row, day, client=client)
    held = [r.id for r in rows if r.id != row.id and _holds(r, activity_id)]
    with repos.session.transaction():
        for other in held:
            repos.plan.update_session_status(other, "unlinked", None)
        repos.plan.update_session_status(row.id, "completed", activity_id)
    result["unlinked"] = held
    result["event_id"] = _pair_on_intervals(repos, client, activity_id, row.id)
    if result["event_id"] is None:
        warnings.append(
            "Linked in your plan, but intervals.icu did not take the pairing, "
            "so its calendar still shows the session as open."
        )
    if warnings:
        result["warning"] = " ".join(warnings)
    return result


def name_mismatch(
    repos: Any, client: IntervalsClient, activity_id: str, session_id: int
) -> str | None:
    """Refuse the coach a link the activity's own name contradicts.

    A watch names the activity after the workout it ran, so an activity
    named after one open session and linked to another is a misread day
    word, not the athlete's choice; the activity sheet, where the athlete
    picks the session, is not checked.

    Args:
        repos: Repository container.
        client: The intervals.icu client.
        activity_id: The activity to link.
        session_id: The session the coach would link it to.

    Returns:
        A refusal naming the session the activity is named after, or None.
    """
    active = repos.plan.get_active()
    activity = client.get_activity(activity_id)
    day = activity_date(activity)
    if active is None or day is None:
        return None
    link = link_view(
        repos.plan.get_sessions(active.id),
        activity_id,
        day=day,
        activity_type=activity.type,
        name=activity.name,
        minutes=(activity.moving_time or 0) / 60.0,
    )
    named = next(
        (c for c in link["candidates"] if c["session_id"] == link["looks_like"]),
        None,
    )
    if named is None or named["session_id"] == session_id:
        return None
    when = date.fromisoformat(named["date"])
    return (
        f'This activity is named "{activity.name}": the watch ran the '
        f"{named['title']} planned {when:%A} {when.day} {when:%B} (session_id "
        f"{named['session_id']}). Link it to that session, or ask the athlete "
        "which they mean with both sessions as replies. If they mean this one "
        "all the same, they pick it on the activity's sheet."
    )


def _pair_on_intervals(
    repos: Any, client: IntervalsClient, activity_id: str, session_id: int
) -> int | None:
    """Pair the activity with the session's event; its id, or None if it failed."""
    try:
        event_id = resolve_session_event_id(repos, client, session_id)
        recorded = client.pair_activity(activity_id, event_id).paired_event_id
    except Exception:  # the plan holds the link; the calendar can catch up
        logger.warning("Pairing %s on intervals.icu failed", activity_id, exc_info=True)
        return None
    return event_id if recorded == event_id else None


def unlink_activity(repos: Any, activity_id: str) -> dict[str, Any]:
    """Undo an activity's link: the session it completed reads as not done.

    The mark is manual (``unlinked``), so the automatic pairing never puts
    the link back on a later calendar load.

    Args:
        repos: Repository container.
        activity_id: intervals.icu activity id.

    Returns:
        ``{"status": "ok", "activity_id", "session_id"}``.

    Raises:
        PairTargetNotFound: When no session of the active plan holds it.
    """
    active = repos.plan.get_active()
    rows = repos.plan.get_sessions(active.id) if active is not None else []
    row = next((r for r in rows if _holds(r, activity_id)), None)
    if row is None:
        raise PairTargetNotFound("This activity is not linked to a planned session.")
    with repos.session.transaction():
        repos.plan.update_session_status(row.id, "unlinked", None)
    return {"status": "ok", "activity_id": activity_id, "session_id": row.id}


def _holds(row: Any, activity_id: str) -> bool:
    """Whether a session row is completed by ``activity_id``."""
    return str(getattr(row, "matched_activity_id", None) or "") == activity_id


def _day_and_sport(
    start_date_local: str | None, sport_type: str | None
) -> tuple[str, SportType] | None:
    """Return the local day and sport an event or activity belongs to."""
    sport = SPORT_TYPE_MAP.get(sport_type or "")
    if not start_date_local or sport is None:
        return None
    return start_date_local[:10], sport


def _by_day_and_sport(
    items: Iterable[IntervalsEvent] | Iterable[IntervalsActivity],
) -> _ByDayAndSport:
    """Group events or activities by their local day and sport."""
    groups: _ByDayAndSport = defaultdict(list)
    for item in items:
        key = _day_and_sport(item.start_date_local, item.type)
        if key is not None:
            groups[key].append(item)
    return groups


def _still_open(
    events: list[IntervalsEvent], activities: list[IntervalsActivity]
) -> tuple[_ByDayAndSport, _ByDayAndSport]:
    """Group the unpaired workouts and the unpaired activities by day and sport."""
    taken = {a.paired_event_id for a in activities if a.paired_event_id is not None}
    open_events = _by_day_and_sport(
        e for e in events if e.id is not None and e.id not in taken
    )
    open_activities = _by_day_and_sport(
        a
        for a in activities
        if a.id and a.paired_event_id is None and a.type not in LOAD_ONLY_ACTIVITY_TYPES
    )
    return open_events, open_activities


def _try_pair(
    client: IntervalsClient, activity: IntervalsActivity, event_id: int
) -> IntervalsActivity | None:
    """Pair on intervals.icu; return the paired activity, or None if it failed."""
    try:
        recorded = client.pair_activity(str(activity.id), event_id)
    except Exception:  # the calendar still renders; the athlete can pair by hand
        logger.warning("Pairing activity %s failed", activity.id, exc_info=True)
        return None
    if recorded.paired_event_id != event_id:
        return None
    return activity.model_copy(
        update={"paired_event_id": event_id, "compliance": recorded.compliance}
    )


def _names_another_workout(
    activity: IntervalsActivity, event: IntervalsEvent, open_events: _ByDayAndSport
) -> bool:
    """Whether ``activity`` is named after an open workout other than ``event``."""
    if carries_title(activity.name, event.name):
        return False
    sport = SPORT_TYPE_MAP.get(activity.type or "")
    return any(
        carries_title(activity.name, other.name)
        for (_, other_sport), group in open_events.items()
        if other_sport == sport
        for other in group
        if other is not event
    )


def pair_unambiguous(
    client: IntervalsClient,
    plan_id: int,
    sessions: list[Any],
    events: list[IntervalsEvent],
    activities: list[IntervalsActivity],
) -> list[IntervalsActivity]:
    """Pair each plan session intervals.icu left open with its one activity.

    A session pairs only when its day holds exactly one open workout and
    one unpaired activity of its sport, and only if it was never paired: a
    match on record, or a manual mark, means someone undid or refused a
    pairing, and redoing it on every calendar load would fight them. A hike
    or walk never pairs here, so a dog walk cannot complete a run that never
    happened. Nor does an activity named after another open workout of its
    sport: the watch ran that session, on a day it was not planned for, and
    the athlete links it from the activity's sheet.

    Args:
        client: The intervals.icu client.
        plan_id: The active plan's id (keys the external_id scheme).
        sessions: Plan session rows in the calendar window.
        events: The window's WORKOUT events.
        activities: The window's completed activities.

    Returns:
        ``activities``, with each new pairing applied.
    """
    open_events, open_activities = _still_open(events, activities)
    by_external_id = {
        event.external_id: (key, event)
        for key, group in open_events.items()
        for event in group
    }
    paired: dict[str, IntervalsActivity] = {}
    for session in sessions:
        if (
            session.status in MANUAL_OVERRIDE_STATUSES
            or session.matched_activity_id is not None
        ):
            continue
        found = by_external_id.get(session_external_id(plan_id, session.slot_id))
        if found is None:
            continue
        key, event = found
        if len(open_events[key]) != 1 or len(open_activities[key]) != 1:
            continue
        activity = open_activities[key][0]
        if _names_another_workout(activity, event, open_events):
            continue
        pairing = _try_pair(client, activity, event.id)
        if pairing is not None:
            paired[str(activity.id)] = pairing
    return [paired.get(str(a.id), a) for a in activities]
