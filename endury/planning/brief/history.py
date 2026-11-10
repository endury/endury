"""What the athlete's own intervals.icu history says about a coming session.

Pure functions over ``list[IntervalsActivity]``: the heart rate and power
they have held for an hour, two, three and four, how fast they climb, the
routes that fit, a trail finish-time range and the recent temperature. A
hike or a walk counts as running load elsewhere but is never a running
effort here (``LOAD_ONLY_ACTIVITY_TYPES``). Every function returns rows
already rounded for display.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    IntervalsActivity,
)
from endury.planner.foundations.types import SportType

#: Duration buckets (minutes) the hold and power curves are read at.
HOLD_BUCKETS = (60, 120, 180, 240)
POWER_BUCKETS = (60, 120, 180)

_VIRTUAL_TYPES = frozenset({"VirtualRide", "VirtualRun"})


def _sport(activity: IntervalsActivity) -> SportType | None:
    """Return the sport an activity is an effort in; hikes and walks are none."""
    if activity.type in LOAD_ONLY_ACTIVITY_TYPES:
        return None
    return SPORT_TYPE_MAP.get(activity.type or "")


def _day(activity: IntervalsActivity) -> date | None:
    """Return the activity's local calendar date."""
    if not activity.start_date_local:
        return None
    return date.fromisoformat(activity.start_date_local[:10])


def _minutes(activity: IntervalsActivity) -> float:
    """Return the activity's moving time in minutes."""
    return (activity.moving_time or 0) / 60.0


def _is_outdoor(activity: IntervalsActivity) -> bool:
    """Whether the activity happened outside (not a virtual session)."""
    return activity.type not in _VIRTUAL_TYPES


def _of_sport(
    activities: list[IntervalsActivity], sport: SportType | None
) -> list[IntervalsActivity]:
    """Return the dated activities of one sport (every sport when None)."""
    return [
        a
        for a in activities
        if _day(a) is not None
        and _sport(a) is not None
        and (sport is None or _sport(a) is sport)
    ]


def _newest_first(activities: list[IntervalsActivity]) -> list[IntervalsActivity]:
    """Sort activities newest first."""
    return sorted(activities, key=lambda a: a.start_date_local or "", reverse=True)


def best_by_bucket(
    activities: list[IntervalsActivity],
    sport: SportType,
    field: str,
    buckets: tuple[int, ...] = HOLD_BUCKETS,
) -> dict[int, IntervalsActivity]:
    """Return, per duration bucket, the effort with the highest ``field``.

    An interval workout counts like any other activity: its average over
    the whole duration is what the athlete held, and intervals.icu lap-marks
    most long runs anyway (a race shows as "21x 4m23s"), so a summary-based
    filter would drop the very efforts the curve is for.

    Args:
        activities: The athlete's activities.
        sport: The sport to read.
        field: ``average_heartrate`` or ``icu_weighted_avg_watts``.
        buckets: Bucket lengths in minutes; an activity qualifies for a
            bucket when its moving time is at least that long.

    Returns:
        Bucket minutes -> the winning activity, newest first on ties;
        a bucket nothing qualifies for is absent.
    """
    pool = [
        a
        for a in _newest_first(_of_sport(activities, sport))
        if getattr(a, field) is not None
    ]
    best: dict[int, IntervalsActivity] = {}
    for bucket in buckets:
        eligible = [a for a in pool if _minutes(a) >= bucket]
        if eligible:
            # max keeps the first of equals, and the pool is newest first.
            best[bucket] = max(eligible, key=lambda a: getattr(a, field))
    return best


def _row(activity: IntervalsActivity) -> dict[str, Any]:
    """Return the shared identity of a history row."""
    day = _day(activity)
    return {
        "date": day.isoformat() if day else None,
        "name": activity.name or activity.type or "",
    }


def hold_curve(
    activities: list[IntervalsActivity], sport: SportType
) -> list[dict[str, Any]]:
    """Return the heart rate the athlete has held for one to four hours.

    Args:
        activities: The athlete's activities.
        sport: The sport to read.

    Returns:
        One ``{duration_min, avg_hr, date, name}`` row per winning activity
        (one activity winning several buckets is listed once), longest first.
    """
    seen: dict[str, IntervalsActivity] = {}
    for winner in best_by_bucket(activities, sport, "average_heartrate").values():
        seen[winner.id or winner.start_date_local or ""] = winner
    rows = [
        {
            "duration_min": round(_minutes(a)),
            "avg_hr": int(a.average_heartrate or 0),
            **_row(a),
        }
        for a in seen.values()
    ]
    return sorted(rows, key=lambda r: r["duration_min"], reverse=True)


def power_curve(activities: list[IntervalsActivity]) -> dict[str, int | None]:
    """Return the highest normalized power held in rides of one to three hours.

    Args:
        activities: The athlete's activities.

    Returns:
        ``np_60``, ``np_120`` and ``np_180`` in watts, None where no
        continuous ride was that long.
    """
    best = best_by_bucket(
        activities, SportType.CYCLING, "icu_weighted_avg_watts", POWER_BUCKETS
    )
    return {
        f"np_{bucket}": (
            int(best[bucket].icu_weighted_avg_watts or 0) if bucket in best else None
        )
        for bucket in POWER_BUCKETS
    }


def climb_rate_m_per_h(activities: list[IntervalsActivity]) -> int | None:
    """Return how many metres an hour the athlete climbs on long hilly runs.

    Args:
        activities: The athlete's activities.

    Returns:
        The median of the three best rates among runs of at least 90
        minutes with at least 500 m of climbing, rounded to 10; None
        without three such runs.
    """
    rates = sorted(
        (
            (a.total_elevation_gain or 0) / (_minutes(a) / 60.0)
            for a in _of_sport(activities, SportType.RUNNING)
            if (a.total_elevation_gain or 0) >= 500 and _minutes(a) >= 90
        ),
        reverse=True,
    )
    if len(rates) < 3:
        return None
    return int(round(median(rates[:3]) / 10.0) * 10)


def route_row(activity: IntervalsActivity) -> dict[str, Any]:
    """Return one ``{date, name, distance_km, climb_m, duration_min}`` row.

    Args:
        activity: The activity to describe.
    """
    return {
        **_row(activity),
        "distance_km": (
            round(activity.distance / 1000.0, 1) if activity.distance else None
        ),
        "climb_m": (
            round(activity.total_elevation_gain)
            if activity.total_elevation_gain is not None
            else None
        ),
        "duration_min": round(_minutes(activity)),
    }


def routes_like(
    activities: list[IntervalsActivity],
    sport: SportType,
    duration_min: float,
    *,
    today: date,
    climb_m: float | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return recent outdoor sessions that fit a coming session.

    Args:
        activities: The athlete's activities.
        sport: The coming session's sport.
        duration_min: Its planned length in minutes.
        today: The day the sheet is built for.
        climb_m: Its climb, when known; then the climb must fit too.
        limit: Most rows returned.

    Returns:
        Route rows from the last 180 days within 25 % of the length (and
        40 % of the climb), most recent first.
    """
    since = today - timedelta(days=180)
    fits = [
        a
        for a in _newest_first(_of_sport(activities, sport))
        if _is_outdoor(a)
        and since <= (_day(a) or since) <= today
        and abs(_minutes(a) - duration_min) <= 0.25 * duration_min
        and (
            climb_m is None
            or abs((a.total_elevation_gain or 0) - climb_m) <= 0.4 * climb_m
        )
    ]
    return [route_row(a) for a in fits[:limit]]


def _flat_km(distance_km: float, climb_m: float) -> float:
    """Return flat-equivalent kilometres: each 100 m of climb is one more."""
    return distance_km + climb_m / 100.0


def trail_estimate(
    activities: list[IntervalsActivity],
    distance_km: float,
    climb_m: float,
    *,
    today: date,
) -> dict[str, Any] | None:
    """Return a finish-time range for a trail race from recent hilly runs.

    Each run of 90 minutes or more with 300 m or more of climbing in the
    last 120 days gives a pace per flat-equivalent kilometre; the three
    fastest are the basis (the nearest thing to race effort an easy long-run
    diet holds), and the range applies their fastest and slowest pace to the
    race. A race more than 20 % longer than the longest of them widens the
    slow end by 5 %.

    Args:
        activities: The athlete's activities.
        distance_km: The race distance.
        climb_m: The race climb.
        today: The day the sheet is built for.

    Returns:
        ``{range_min, method, basis}``, or None with fewer than two basis runs.
    """
    since = today - timedelta(days=120)
    pool = [
        a
        for a in _newest_first(_of_sport(activities, SportType.RUNNING))
        if since <= (_day(a) or since) <= today
        and _minutes(a) >= 90
        and (a.total_elevation_gain or 0) >= 300
        and a.distance
    ]
    flat_pace: dict[str | None, tuple[float, float]] = {}
    for a in pool:
        km = _flat_km((a.distance or 0) / 1000.0, a.total_elevation_gain or 0)
        flat_pace[a.id] = (km, _minutes(a) / km)
    basis = sorted(pool, key=lambda a: flat_pace[a.id][1])[:3]
    if len(basis) < 2:
        return None
    flat = [flat_pace[a.id][0] for a in basis]
    paces = [flat_pace[a.id][1] for a in basis]
    race_km = _flat_km(distance_km, climb_m)
    low, high = race_km * min(paces), race_km * max(paces)
    method = (
        f"Your pace on your {len(basis)} fastest long hilly runs of the last four "
        "months, counting every 100 m of climbing as one more kilometre, applied "
        "to this course; a race effort usually comes in a little under."
    )
    if race_km > 1.2 * max(flat):
        high *= 1.05
        method += (
            " The slow end is a little wider because the race is longer than"
            " any of them."
        )
    return {
        "range_min": [round(low), round(high)],
        "method": method,
        "basis": [route_row(a) for a in basis],
    }


def recent_temp_c(
    activities: list[IntervalsActivity], *, today: date, days: int = 14
) -> list[int] | None:
    """Return the coolest and warmest temperature of recent outdoor sessions.

    Args:
        activities: The athlete's activities.
        today: The day the sheet is built for.
        days: How far back to look.

    Returns:
        ``[min, max]`` in whole °C from the weather temperature (the
        device's own reading when there is none), or None when no recent
        outdoor run or ride carries one.
    """
    since = today - timedelta(days=days)
    temps = [
        t
        for a in _of_sport(activities, None)
        if _is_outdoor(a)
        and _sport(a) is not SportType.SWIMMING
        and since <= (_day(a) or since) <= today
        for t in [
            a.average_weather_temp
            if a.average_weather_temp is not None
            else a.average_temp
        ]
        if t is not None
    ]
    if not temps:
        return None
    return [round(min(temps)), round(max(temps))]


def recent_sessions(
    activities: list[IntervalsActivity],
    sport: SportType | None,
    *,
    limit: int = 3,
    min_minutes: int = 60,
) -> list[dict[str, Any]]:
    """Return the athlete's latest sessions of a sport, newest first.

    Args:
        activities: The athlete's activities.
        sport: The sport to list; None lists every sport.
        limit: Most rows returned.
        min_minutes: Shortest session listed.

    Returns:
        ``{date, name, duration_min, avg_hr, load, distance_km, climb_m}``
        rows.
    """
    rows = [
        a
        for a in _newest_first(_of_sport(activities, sport))
        if _minutes(a) >= min_minutes
    ]
    return [
        {
            **route_row(a),
            "avg_hr": a.average_heartrate,
            "load": (
                round(a.icu_training_load) if a.icu_training_load is not None else None
            ),
        }
        for a in rows[:limit]
    ]


__all__ = [
    "HOLD_BUCKETS",
    "POWER_BUCKETS",
    "best_by_bucket",
    "climb_rate_m_per_h",
    "hold_curve",
    "power_curve",
    "recent_sessions",
    "recent_temp_c",
    "route_row",
    "routes_like",
    "trail_estimate",
]
