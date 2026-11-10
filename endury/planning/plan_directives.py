"""Typed plan directives: what the coach knows, applied at generation time.

A deterministic filter over already-materialized sessions, persisted in a
plan's stored request payload so a future replan re-applies it:
``resume_date``/``suppress_tests_until`` drop sessions (and test sessions)
dated before them, ``unavailable`` drops sessions inside any inclusive
date range, and ``weekly_hours_cap`` rides along unused here for
``GenerateRequest`` to read.

:func:`parse_directives` is the one validator, used both before a
directive is persisted and, tolerantly, inside :func:`apply_directives`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.planner.foundations.types import ActivityKey
from endury.planning.wire.types import SerializedSessionPayload

#: Activity keys the planner uses for periodic fitness tests — mirrors
#: ``session_generator._TEST_KEYS`` / ``test_injection._TEST_ACTIVITY_KEY``
#: (drift-guarded by ``test_test_key_set_matches_the_planner``).
TEST_ACTIVITY_KEYS: frozenset[str] = frozenset(
    {
        ActivityKey.RUN_THRESHOLD_TEST.value,
        ActivityKey.BIKE_FTP_TEST.value,
        ActivityKey.SWIM_CSS_TEST.value,
    }
)


@dataclass(frozen=True)
class PlanDirectives:
    """One plan's constraints from what the coach knows about the athlete.

    Every field is optional; an all-default instance is normalized to
    ``None`` by :func:`parse_directives` rather than persisted, so a plan
    with no directives hashes and replays exactly as it did before this
    field existed.
    """

    resume_date: date | None = None
    suppress_tests_until: date | None = None
    unavailable: tuple[tuple[date, date], ...] = ()
    weekly_hours_cap: float | None = None


def _parse_date(value: Any, label: str) -> date | str:
    """Parse one ISO date, or return a curated error string."""
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return f"{label} '{value}' is not a date — pass YYYY-MM-DD."


def _parse_unavailable(value: Any) -> tuple[tuple[date, date], ...] | str:
    """Parse the inclusive ``[start, end]`` date-range list, or an error."""
    ranges: list[tuple[date, date]] = []
    for entry in value:
        if not isinstance(entry, list | tuple) or len(entry) != 2:
            return '`unavailable` entries must be ["YYYY-MM-DD", "YYYY-MM-DD"] pairs.'
        start = _parse_date(entry[0], "unavailable start")
        if isinstance(start, str):
            return start
        end = _parse_date(entry[1], "unavailable end")
        if isinstance(end, str):
            return end
        if end < start:
            return f"unavailable range {entry[0]}..{entry[1]} ends before it starts."
        ranges.append((start, end))
    return tuple(ranges)


def parse_unavailable_arg(value: Any) -> list[Any] | str:
    """Tolerantly parse an ``unavailable`` arg: a JSON string or a parsed list.

    Shared by every coach-facing surface that takes ``unavailable`` as a
    string argument (``set_plan_directives``, ``create_plan``), so the
    JSON-string idiom and its curated parse error live in exactly one place.

    Args:
        value: A JSON string, an already-parsed list, or anything else.

    Returns:
        The parsed list — still needing :func:`parse_directives`'s own
        date validation — or a curated error string.
    """
    if isinstance(value, list):
        return value
    try:
        parsed: Any = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    return (
        '`unavailable` must be a JSON array of ["YYYY-MM-DD", "YYYY-MM-DD"] '
        'pairs, e.g. \'[["2026-10-01", "2026-10-05"]]\'.'
    )


def parse_directives(payload: dict[str, Any] | None) -> PlanDirectives | str | None:
    """Validate a candidate directives payload.

    Args:
        payload: The wire/stored directives object (every key optional),
            or ``None``.

    Returns:
        ``None`` when there is nothing to store (``payload`` is falsy, or
        every field it carries is empty), the validated
        :class:`PlanDirectives`, or a curated error string naming the
        first invalid field.
    """
    if not payload:
        return None

    resume_date: date | None = None
    if payload.get("resume_date"):
        parsed_resume = _parse_date(payload["resume_date"], "resume_date")
        if isinstance(parsed_resume, str):
            return parsed_resume
        resume_date = parsed_resume

    suppress_tests_until: date | None = None
    if payload.get("suppress_tests_until"):
        parsed_suppress = _parse_date(
            payload["suppress_tests_until"], "suppress_tests_until"
        )
        if isinstance(parsed_suppress, str):
            return parsed_suppress
        suppress_tests_until = parsed_suppress

    unavailable: tuple[tuple[date, date], ...] = ()
    if payload.get("unavailable"):
        parsed_ranges = _parse_unavailable(payload["unavailable"])
        if isinstance(parsed_ranges, str):
            return parsed_ranges
        unavailable = parsed_ranges

    weekly_hours_cap: float | None = None
    raw_cap = payload.get("weekly_hours_cap")
    if raw_cap is not None:
        try:
            weekly_hours_cap = float(raw_cap)
        except (TypeError, ValueError):
            return f"weekly_hours_cap '{raw_cap}' is not a number."
        if weekly_hours_cap <= 0:
            return "weekly_hours_cap must be greater than 0."

    directives = PlanDirectives(
        resume_date=resume_date,
        suppress_tests_until=suppress_tests_until,
        unavailable=unavailable,
        weekly_hours_cap=weekly_hours_cap,
    )
    return directives if directives != PlanDirectives() else None


def merge_unavailable_ranges(
    ranges: Sequence[tuple[date, date]],
) -> list[tuple[date, date]]:
    """Merge identical, overlapping or adjacent inclusive date ranges.

    Two ranges merge when one starts no later than the day after the other
    ends, so recording the same or a touching window twice (a pause the
    athlete confirms more than once) never grows the stored list.

    Args:
        ranges: Inclusive ``(start, end)`` date pairs, any order.

    Returns:
        The merged ranges, sorted by start date.
    """
    merged: list[tuple[date, date]] = []
    for start, end in sorted(ranges, key=lambda pair: pair[0]):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def apply_directives(
    sessions: list[SerializedSessionPayload],
    directives: dict[str, Any] | None,
) -> list[SerializedSessionPayload]:
    """Drop the sessions the coach's directives rule out.

    A pure filter: nothing here writes, pushes, or resizes what survives,
    and ``weekly_hours_cap`` has no effect here (see module docstring).
    ``directives`` is the raw stored/wire payload, validated internally via
    :func:`parse_directives`; a payload that fails validation is treated as
    no directives at all, same as an absent one. A past-dated
    ``resume_date``/``suppress_tests_until`` is inert by construction,
    since nothing before today reaches this filter in the first place.

    Args:
        sessions: Materialized sessions, already past
            ``materialize_plan.drop_past``.
        directives: The plan's stored directives object, or ``None``.

    Returns:
        The sessions that survive every directive, in input order.
    """
    parsed = parse_directives(directives)
    if not isinstance(parsed, PlanDirectives):
        return sessions

    def _keep(session: SerializedSessionPayload) -> bool:
        session_date = session["date"]
        if parsed.resume_date and session_date < parsed.resume_date.isoformat():
            return False
        if (
            parsed.suppress_tests_until
            and session["activity_key"] in TEST_ACTIVITY_KEYS
            and session_date < parsed.suppress_tests_until.isoformat()
        ):
            return False
        return all(
            not (start.isoformat() <= session_date <= end.isoformat())
            for start, end in parsed.unavailable
        )

    return [session for session in sessions if _keep(session)]


__all__ = [
    "TEST_ACTIVITY_KEYS",
    "PlanDirectives",
    "apply_directives",
    "merge_unavailable_ranges",
    "parse_directives",
    "parse_unavailable_arg",
]
