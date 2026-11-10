"""Per-sport CTL/ATL over intervals.icu activity load.

intervals.icu only exposes a *combined* CTL/ATL on its wellness rows, so
the per-sport fitness the planner needs stays ours: a daily Banister
exponentially-weighted average over each sport's own ``icu_training_load``
(their TSS), with the classic 42-day CTL / 7-day ATL decay constants.

Unit trap (load-bearing): the Banister model tracks CTL/ATL as **average
daily TSS**, while the planner's macro QP expects ``ctl_0``/``atl_0`` in
**weekly TSS** — hence the x7 bridge applied on the way out. Sanity
anchor: an 8 h/wk runner has daily CTL ~ 57, weekly ~ 400 ~ 8 h x 50
TSS/h.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from endury.intervals.models import IntervalsActivity, training_sport
from endury.planner.foundations.types import SportType

#: Daily Banister decay constants (42-day CTL / 7-day ATL).
_CTL_DECAY = math.exp(-1 / 42)
_ATL_DECAY = math.exp(-1 / 7)

#: Banister CTL/ATL are avg-daily-TSS; planner ctl_0/atl_0 are weekly TSS.
DAILY_TO_WEEKLY_TSS = 7.0


def advance_training_load(ctl: float, atl: float, tss: float) -> tuple[float, float]:
    """Advance CTL and ATL by one day for a given TSS input.

    Args:
        ctl: Chronic training load (average daily TSS) before the day.
        atl: Acute training load (average daily TSS) before the day.
        tss: Training stress recorded on the day.

    Returns:
        The ``(ctl, atl)`` pair after the day.
    """
    next_ctl = ctl * _CTL_DECAY + tss * (1 - _CTL_DECAY)
    next_atl = atl * _ATL_DECAY + tss * (1 - _ATL_DECAY)
    return next_ctl, next_atl


def activity_date(activity: IntervalsActivity) -> date | None:
    """Return the local calendar date of an activity.

    Args:
        activity: An intervals.icu activity.

    Returns:
        The local calendar date, or ``None`` when the activity carries no
        start timestamp.
    """
    value = activity.start_date_local
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def activity_start_clock(activity: IntervalsActivity) -> str | None:
    """Return the local start time of an activity as ``HH:MM``.

    Args:
        activity: An intervals.icu activity.

    Returns:
        The local wall-clock start, or ``None`` when the activity carries no
        timestamp (or only a bare date).
    """
    value = str(activity.start_date_local or "")
    if len(value) < 16:
        return None
    return value[11:16]


def bucket_by_sport(
    activities: list[IntervalsActivity],
) -> dict[SportType, list[IntervalsActivity]]:
    """Group activities by planner sport.

    Activity types outside ``SPORT_TYPE_MAP`` (yoga, weight training,
    ...) and walks under the walk floor are dropped (``training_sport``);
    sports with no activity are absent from the result.

    Args:
        activities: Activities to bucket.

    Returns:
        Sport -> its activities, in input order.
    """
    by_sport: dict[SportType, list[IntervalsActivity]] = {}
    for activity in activities:
        sport = training_sport(activity)
        if sport is not None:
            by_sport.setdefault(sport, []).append(activity)
    return by_sport


def _daily_load(
    activities: list[IntervalsActivity],
) -> tuple[dict[date, float], bool]:
    """Sum ``icu_training_load`` per calendar date.

    Args:
        activities: One sport's activities.

    Returns:
        ``(daily_tss, measured)`` — the per-date sums, and whether *any*
        dated activity actually carried a load. An athlete training
        without HR or power gets ``icu_training_load: null`` on every row,
        and the caller must not read that as a fitness of zero.
    """
    daily: dict[date, float] = {}
    measured = False
    for activity in activities:
        day = activity_date(activity)
        if day is None:
            continue
        load = activity.icu_training_load
        measured = measured or load is not None
        daily[day] = daily.get(day, 0.0) + float(load or 0.0)
    return daily, measured


def per_sport_ctl_atl(
    activities: list[IntervalsActivity],
    today: date,
) -> dict[SportType, tuple[float, float] | None]:
    """Compute per-sport CTL/ATL in weekly-TSS space.

    The EWA is seeded at zero on each sport's earliest activity date, so
    pass a warmup window (42+ days) ahead of the period of interest for
    CTL to be meaningful. Daily values are rounded to one decimal before
    the x7 weekly bridge.

    Args:
        activities: Activities across all sports (already date-filtered).
        today: Reference date; the recursion runs up to and including it.

    Returns:
        Sport -> ``(ctl_0, atl_0)`` in weekly TSS, or ``None`` for a sport
        whose activities carry no ``icu_training_load`` at all (unmeasured
        load — the caller seeds fitness from hours instead). Sports with no
        dated activity are absent.
    """
    result: dict[SportType, tuple[float, float] | None] = {}
    for sport, acts in bucket_by_sport(activities).items():
        daily, measured = _daily_load(acts)
        if not daily:
            continue
        if not measured:  # trained, but no load recorded on any activity
            result[sport] = None
            continue
        ctl = 0.0
        atl = 0.0
        current = min(daily)
        while current <= today:
            ctl, atl = advance_training_load(ctl, atl, daily.get(current, 0.0))
            current += timedelta(days=1)
        result[sport] = (
            round(ctl, 1) * DAILY_TO_WEEKLY_TSS,
            round(atl, 1) * DAILY_TO_WEEKLY_TSS,
        )
    return result
