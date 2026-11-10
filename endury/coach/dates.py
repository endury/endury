"""Day names the coach reads instead of working them out.

A small model mixes up today and tomorrow when it has to count days from one
timestamp. So the coach is handed the answers: a table naming the days around
today (:func:`build_day_table`), a weekday and a relative label beside every
date a tool returns (:func:`label_dates`), and "today", "tomorrow" and
"yesterday" accepted in any date argument (:func:`resolve_date_args`).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

#: Days the table reaches back and ahead of today: last week's same weekday
#: through next week's, so any weekday the athlete names is on it.
TABLE_DAYS_BACK = 6
TABLE_DAYS_AHEAD = 7

#: Days either side of today that get a relative label ("in 3 days").
_RELATIVE_SPAN = 7

_DAY_WORDS = {"yesterday": -1, "today": 0, "tomorrow": 1}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _relative(day: date, today: date) -> str | None:
    """Name ``day`` relative to ``today``, or None when it is too far off."""
    delta = (day - today).days
    if abs(delta) > _RELATIVE_SPAN:
        return None
    if delta in (-1, 0, 1):
        return {-1: "yesterday", 0: "today", 1: "tomorrow"}[delta]
    words = f"in {delta} days" if delta > 0 else f"{-delta} days ago"
    monday = today - timedelta(days=today.weekday())
    if day < monday:
        words += ", last week"
    elif day > monday + timedelta(days=6):
        words += ", next week"
    return words


def day_label(day: date, today: date) -> str:
    """Return ``day``'s weekday, plus where it sits from ``today`` when near.

    Args:
        day: The day to name.
        today: The reference day.

    Returns:
        E.g. ``"Sunday, tomorrow"``, ``"Monday, in 2 days, next week"``, or
        a bare ``"Tuesday"`` more than a week away.
    """
    relative = _relative(day, today)
    return f"{day:%A}, {relative}" if relative else f"{day:%A}"


def build_day_table(today: date) -> str:
    """Build the system context's day table.

    Args:
        today: The day to centre the table on.

    Returns:
        A ``## Dates`` section with one line per day, last week's same
        weekday through next week's.
    """
    lines = [
        "## Dates",
        "Resolve every day word — today, tomorrow, a weekday, next week — "
        "from this table, and never work a date out yourself. A weekday on "
        "both sides of today is the one whose session they name; when both "
        "fit, ask which, with both dates as replies.",
    ]
    for offset in range(-TABLE_DAYS_BACK, TABLE_DAYS_AHEAD + 1):
        day = today + timedelta(days=offset)
        lines.append(f"- {day:%A} {day.isoformat()} — {_relative(day, today)}")
    return "\n".join(lines)


def resolve_date_args(args: dict[str, Any], today: date) -> dict[str, Any]:
    """Replace "today", "tomorrow" and "yesterday" in date arguments.

    A date argument is one whose name ends in ``date``. Weekday names stay
    as they are: "Thursday" may mean last week's or this week's, so it is
    left to the tool's own validation rather than guessed.

    Args:
        args: The tool call's arguments.
        today: The day the words are relative to.

    Returns:
        A new dict with those words replaced by ISO dates.
    """
    resolved = dict(args)
    for key, value in args.items():
        if key.endswith("date") and isinstance(value, str):
            offset = _DAY_WORDS.get(value.strip().lower())
            if offset is not None:
                resolved[key] = (today + timedelta(days=offset)).isoformat()
    return resolved


def _label(node: Any, today: date) -> bool:
    """Add a ``…day`` label beside each ISO ``…date`` key; return if any was."""
    added = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if (
                key.endswith("date")
                and isinstance(value, str)
                and _ISO_DATE.fullmatch(value)
                and f"{key[:-4]}day" not in node
            ):
                node[f"{key[:-4]}day"] = day_label(date.fromisoformat(value), today)
                added = True
            else:
                added = _label(value, today) or added
    elif isinstance(node, list):
        for item in node:
            added = _label(item, today) or added
    return added


def label_dates(result: str, today: date) -> str:
    """Label every date in a tool's JSON result with its day.

    ``{"date": "2026-09-27"}`` gains ``"day": "Sunday, tomorrow"``;
    ``race_date`` gains ``race_day``. A result that is not JSON, or carries
    no date, comes back unchanged.

    Args:
        result: The tool's result string.
        today: The day the labels are relative to.

    Returns:
        The labelled result.
    """
    try:
        parsed = json.loads(result)
    except ValueError:
        return result
    if not _label(parsed, today):
        return result
    return json.dumps(parsed, ensure_ascii=False)
