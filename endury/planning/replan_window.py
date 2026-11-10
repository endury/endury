"""The window a replan rewrites: where it cuts and how much it keeps.

A replan never rewrites what the athlete has already lived through. By
default the cut is the strictly-next Monday; an explicit ``resume_date``
moves it earlier for a mid-week resume. The planner only works in whole
Monday-based weeks, so a mid-week resume still generates from its Monday
and the days before it are trimmed off afterwards.

Everything here is keyed off that cut, including which calendar events it
invalidates (``pushed_events``, ``previous_race_events``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.intervals.push import race_external_id
from endury.planning.commit_plan import goals_from_races

logger = logging.getLogger(__name__)


def next_monday(today: date) -> date:
    """Strictly-next Monday — the current week is never disturbed."""
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


@dataclass(frozen=True, slots=True)
class ReplanWindow:
    """Where a replan cuts the plan in two.

    Attributes:
        start: The Monday the regenerated block is generated from.
        cutoff: The first day actually rewritten — equal to ``start``
            unless a resume date lands mid-week.
        prior: Whole weeks of the original plan before ``start``, fed to
            the engine as ``prior_training_weeks``.
    """

    start: date
    cutoff: date
    prior: int


def replan_window(
    today: date, original_start: date, resume_date: date | None = None
) -> ReplanWindow:
    """Compute the window a replan run on ``today`` would rewrite.

    Args:
        today: Reference date.
        original_start: The plan's first Monday.
        resume_date: First day to rewrite (defaults to next Monday).

    Returns:
        The generation start, the rewrite cutoff, and the prior weeks.

    Raises:
        ValueError: ``resume_date`` is not after ``today``.
    """
    if resume_date is not None and resume_date <= today:
        raise ValueError(
            "The resume date must be after today — the plan is not rewritten backwards."
        )
    cutoff = resume_date or next_monday(today)
    start = cutoff - timedelta(days=cutoff.weekday())
    prior = max(0, (start - original_start).days // 7)
    if prior == 0:
        # Replanning before the plan even started: regenerate it whole, and
        # never cut earlier than its own first day.
        start = original_start
        cutoff = max(cutoff, start)
    return ReplanWindow(start=start, cutoff=cutoff, prior=prior)


def pushed_events(rows: list[Any], cutoff: str) -> list[dict[str, Any]]:
    """Events we last pushed for the sessions a replan will replace.

    Mirrors ``delete_future_sessions``: only unmatched sessions from the
    cutoff onwards are rewritten, so only their calendar events may be
    deleted as stale.

    Args:
        rows: The plan's session rows.
        cutoff: ISO date the rewrite starts at (inclusive).

    Returns:
        The stored event payloads; rows never pushed (or with unreadable
        JSON) are skipped.
    """
    events: list[dict[str, Any]] = []
    for row in rows:
        if row.matched_activity_id is not None or str(row.date) < cutoff:
            continue
        raw = getattr(row, "pushed_event_json", None)
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.debug("Unreadable pushed_event_json on session %s", row.id)
    return events


def previous_race_events(
    plan_id: int, races: list[dict[str, Any]], cutoff: str
) -> list[dict[str, Any]]:
    """External-id stubs for the pushed races a replan may remove.

    A race the athlete took out of the plan leaves the calendar through the
    stale set, since ``pushed_events`` only ever sees session rows. A
    tune-up behind the cutoff is not stale: the weeks that hold it stay as
    lived (R-PHASE-002b), and a plan write never touches an event before
    the cut.

    Args:
        plan_id: The active plan's id.
        races: The race blocks of the *stored* request, before the replan
            filters them.
        cutoff: ISO date the rewrite starts at (inclusive).

    Returns:
        One ``{"external_id": ...}`` stub per stored race on or after the
        cutoff.
    """
    return [
        {"external_id": race_external_id(plan_id, goal)}
        for goal in goals_from_races(races)
        if goal.race_date.isoformat() >= cutoff
    ]
