"""Day assignment for the session-menu architecture.

The menus author the calendar: every slot carries a mandatory weekday
(hard/easy alternation, brick co-location, and weekend long sessions
are authoring concerns, enforced by the import-time day-layout lints in
:mod:`endury.planner.sessions.menus.contracts`). This layer maps
each session's authored day to a calendar date and applies the one
piece of runtime conflict handling menus cannot know statically:
race-day eviction (menus assume a Sunday race; real races land on any
weekday).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta

from endury.planner.foundations.types import (
    ActivityAssignment,
    PlacedSession,
    RaceEvent,
    SessionOrigin,
    SportType,
)

_log = logging.getLogger(__name__)

#: Per-day session ceiling honoured by race-day eviction — mirrors the
#: menu day-layout lints' ``_MAX_SESSIONS_PER_DAY`` so an eviction never
#: stacks a third session onto race-eve.
_MAX_SESSIONS_PER_DAY = 2


def assign_days(
    sessions: list[PlacedSession],
    training_start: date,
    race_events: Sequence[RaceEvent] = (),
) -> list[ActivityAssignment]:
    """Assign each session to its menu-authored day, evicting race-day clashes.

    The menu ``day_of_week`` is the placement (Monday fallback when a
    hand-built session omits it). Race weeks get deterministic conflict
    handling — menus are authored around a Sunday race, so collisions
    only arise for non-Sunday races and C-race recovery fallbacks:

    * A non-TEST session landing on a race day moves to the nearest
      earlier day of the same week that is not a race day, hosts no
      same-sport session, and has fewer than two sessions; with no such
      day it is dropped (logged).
    * In the A-race week, non-TEST sessions dated after the race day
      are dropped (logged) — the plan ends at its goal race. B/C-race
      weeks keep their post-race sessions (mid-season training).
    * ``SessionOrigin.TEST`` sessions are pinned to their preset day,
      even on a race day (an explicitly scheduled protocol wins).

    Args:
        sessions: All sessions with QP-optimised target_hours.
        training_start: Plan start date (Monday of week 0).
        race_events: Race fixtures whose days evict colliding sessions.

    Returns:
        List of ``ActivityAssignment`` with calendar dates — one per
        input session except documented race-week drops — sorted by
        ``(date, slot_id)``.
    """
    race_days = {(re.week, re.day_of_week) for re in race_events}
    a_day_by_week = {
        re.week: re.day_of_week for re in race_events if re.goal.priority == "A"
    }

    placed: list[tuple[PlacedSession, int]] = []
    colliding: list[tuple[PlacedSession, int]] = []
    # Sports per (week, day), duplicates kept: length is the day's
    # session count (injected TEST sessions can share a day and sport
    # with a menu session, so a set would under-count).
    occupied: dict[tuple[int, int], list[SportType]] = defaultdict(list)

    def keep(sess: PlacedSession, day: int) -> None:
        placed.append((sess, day))
        occupied[(sess.week, day)].append(sess.sport)

    # Pass 1 — pin tests, drop post-A-race sessions, defer collisions.
    # Input order (generator emission order) keeps both passes
    # deterministic.
    for sess in sessions:
        day = sess.day_of_week if sess.day_of_week is not None else 0
        if sess.origin == SessionOrigin.TEST:
            keep(sess, day)
            continue
        a_day = a_day_by_week.get(sess.week)
        if a_day is not None and day > a_day:
            _log.warning(
                "dropping post-race session %s: week %d day %d is after "
                "the A-race day %d",
                sess.slot_id,
                sess.week,
                day,
                a_day,
            )
            continue
        if (sess.week, day) in race_days:
            colliding.append((sess, day))
            continue
        keep(sess, day)

    # Pass 2 — evict race-day collisions once full occupancy is known.
    for sess, race_day in colliding:
        new_day = next(
            (
                d
                for d in range(race_day - 1, -1, -1)
                if (sess.week, d) not in race_days
                and sess.sport not in occupied[(sess.week, d)]
                and len(occupied[(sess.week, d)]) < _MAX_SESSIONS_PER_DAY
            ),
            None,
        )
        if new_day is None:
            _log.warning(
                "dropping race-day session %s: week %d has no earlier "
                "day free of %s with capacity left",
                sess.slot_id,
                sess.week,
                sess.sport.value,
            )
            continue
        _log.info(
            "race-day eviction: %s (week %d) moved day %d -> %d",
            sess.slot_id,
            sess.week,
            race_day,
            new_day,
        )
        keep(sess, new_day)

    assignments = [
        ActivityAssignment(
            slot_id=sess.slot_id,
            date=training_start + timedelta(weeks=sess.week, days=day),
            activity_key=sess.activity_key,
            target_hours=sess.target_hours,
        )
        for sess, day in placed
    ]
    assignments.sort(key=lambda a: (a.date, a.slot_id))
    return assignments


__all__ = [
    "assign_days",
]
