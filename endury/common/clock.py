"""Application-wide clock abstraction.

All production code must use ``clock.today()`` and ``clock.now()``
instead of ``date.today()`` / ``datetime.now()`` to support the
devtools app simulator.

The simulated date is controlled via the ``COACHING_BUDDY_SIM_DATE``
environment variable (format: ``YYYY-MM-DD``).  When unset the real
system clock is used.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time


def today() -> date:
    """Return the current simulated or real date."""
    override = os.environ.get("COACHING_BUDDY_SIM_DATE")
    if override:
        return date.fromisoformat(override)
    return date.today()


def now() -> datetime:
    """Return the current simulated or real datetime.

    When ``COACHING_BUDDY_SIM_DATE`` is set the time component defaults
    to ``12:00:00`` unless ``COACHING_BUDDY_SIM_TIME`` is also set
    (format: ``HH:MM:SS``).
    """
    override = os.environ.get("COACHING_BUDDY_SIM_DATE")
    if override:
        sim_date = date.fromisoformat(override)
        sim_time_str = os.environ.get("COACHING_BUDDY_SIM_TIME", "12:00:00")
        return datetime.combine(sim_date, time.fromisoformat(sim_time_str))
    return datetime.now()


def daypart(hour: int) -> str:
    """Name the part of the day a 24-hour clock hour falls in.

    The single definition behind both the coach's system-context date line
    and the welcome opener, so the greeting and the chat can never disagree
    about whether it is still the morning.

    Args:
        hour: The local hour, 0-23.

    Returns:
        One of ``night``, ``early morning``, ``morning``, ``midday``,
        ``afternoon``, ``evening``.
    """
    if hour < 5:
        return "night"
    if hour < 9:
        return "early morning"
    if hour < 12:
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"
