"""Which planned session a recorded activity was, read off the activity itself.

A watch that ran a planned workout names the activity after it — Garmin
saves "<place> - <workout title>" — so the title is the surest sign of the
session the athlete did, on its own day or another one. Length is no such
sign: planned sessions are run at two to four times their booked length and
still meant (measured 2026-10-26 on both alpha accounts), so it only orders
the sessions an activity without a title could have been.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from endury.intervals.models import SPORT_TYPE_MAP
from endury.planner.activity.materialize import activity_display_name
from endury.planner.foundations.types import ActivityKey
from endury.planning.materialize_plan import OPTIONAL_TITLE_PREFIX

#: Days either side of the activity a planned session may sit and still be
#: offered as the one it was: a session moved up or done late in its week.
LINK_WINDOW_DAYS = 7

#: How many sessions the activity sheet offers at most.
MAX_CANDIDATES = 3

#: Session states an activity cannot be linked to from the sheet.
_CLOSED_STATUSES = ("skipped",)


def _plain(title: str) -> str:
    """Lower-case ``title`` without the optional-session prefix."""
    text = " ".join(title.split()).lower()
    prefix = OPTIONAL_TITLE_PREFIX.strip().lower()
    return text.removeprefix(prefix).strip()


def carries_title(activity_name: str | None, title: str | None) -> bool:
    """Whether an activity is named after a workout ``title``.

    Args:
        activity_name: The activity's name as intervals.icu holds it.
        title: A planned workout's title.

    Returns:
        True for the title itself or "<anything> - <title>", ignoring case
        and the "Optional: " prefix on either side.
    """
    if not activity_name or not title:
        return False
    wanted = _plain(title)
    return wanted in (_plain(activity_name), _plain(activity_name.rsplit(" - ", 1)[-1]))


def session_title(row: Any) -> str:
    """The title the athlete's calendar and watch show for a session row."""
    for raw in (getattr(row, "pushed_event_json", None), row.workout_json):
        try:
            name = (json.loads(raw) or {}).get("name") if raw else None
        except ValueError:
            name = None
        if name:
            return str(name)
    title = activity_display_name(ActivityKey(str(row.activity_key)))
    return f"{OPTIONAL_TITLE_PREFIX}{title}" if row.optional else title


def _summary(row: Any) -> dict[str, Any]:
    """The session as the sheet and the coach name it."""
    return {
        "session_id": int(row.id),
        "title": session_title(row),
        "date": str(row.date),
        "duration_minutes": row.duration_minutes,
    }


def _rank(row: Any, name: str | None, day: date, minutes: float | None) -> tuple:
    """Order: named by the watch, then closest in length, then in days."""
    planned = float(row.duration_minutes or 0.0)
    length = abs(math.log(minutes / planned)) if minutes and planned else math.inf
    distance = abs((date.fromisoformat(str(row.date)) - day).days)
    return (not carries_title(name, session_title(row)), length, distance)


def link_view(
    rows: list[Any],
    activity_id: str,
    *,
    day: date,
    activity_type: str | None,
    name: str | None,
    minutes: float | None,
) -> dict[str, Any]:
    """Describe an activity's place in the plan, for its sheet and the coach.

    Args:
        rows: The active plan's session rows.
        activity_id: The intervals.icu activity id.
        day: The activity's local date.
        activity_type: The activity's intervals.icu type ("Run", "Ride", …).
        name: The activity's name.
        minutes: The activity's moving minutes.

    Returns:
        ``linked`` (the session it completes, or None), ``candidates`` (up
        to :data:`MAX_CANDIDATES` open sessions of its sport within
        :data:`LINK_WINDOW_DAYS`, best first) and ``looks_like`` (the
        session id the activity is named after, when it is not the linked
        one).
    """
    linked = next(
        (row for row in rows if str(row.matched_activity_id or "") == activity_id),
        None,
    )
    sport = SPORT_TYPE_MAP.get(activity_type or "")
    open_rows = [
        row
        for row in rows
        if sport is not None
        and str(row.sport) == sport.value
        and row.matched_activity_id is None
        and str(row.status) not in _CLOSED_STATUSES
        and abs((date.fromisoformat(str(row.date)) - day).days) <= LINK_WINDOW_DAYS
    ]
    ranked = sorted(open_rows, key=lambda row: _rank(row, name, day, minutes))
    named = [row for row in ranked if carries_title(name, session_title(row))]
    linked_by_name = linked is not None and carries_title(name, session_title(linked))
    return {
        "linked": _summary(linked) if linked is not None else None,
        "candidates": [_summary(row) for row in ranked[:MAX_CANDIDATES]],
        "looks_like": int(named[0].id) if named and not linked_by_name else None,
    }
