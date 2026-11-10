"""Completed-activity tools for the coach agent (intervals.icu reads).

Every payload here is a hand projection of the intervals.icu response: the
raw activity carries ~180 fields and a detail fetch is tens of KB, so the
LLM only ever sees the handful of numbers a coach would actually quote.
Speeds arrive as m/s and are rendered per sport (min/km, sec/100m, km/h).
"""

from __future__ import annotations

import json
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from endury.analysis.charts import MAX_SERIES, make_spec
from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.analysis_tools import CHARTS_KEY, HISTORY_DAYS
from endury.coach.tools.registry import coach_tool
from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import activity_date, activity_start_clock
from endury.intervals.models import (
    IntervalRow,
    IntervalsActivity,
    IntervalsActivityDetail,
    training_sport,
)
from endury.intervals.reps import read_activity_detail
from endury.planner.foundations.types import SportType

#: Cap on the activities listed in one ``get_recent_activities`` call so a
#: long window cannot flood a chat turn.
_MAX_LISTED_ACTIVITIES = 40

#: Cap on per-rep rows in one ``get_activity_detail`` payload; the grouped
#: view still covers the whole session when reps are dropped.
_MAX_DETAIL_INTERVALS = 30

#: Cap on activities detailed for a date lookup (a brick day is 2, a race
#: day with warm-up 3).
_MAX_DETAILS_PER_DATE = 3

#: Key sessions listed per week in the weekly summary.
_KEY_SESSIONS_PER_WEEK = 3

# intent: intervals.icu's "How did you feel?" runs 1 = strong ... 5 = weak (the
# lower the better), unlike RPE; the web app shows the same words.
FEEL_WORDS = {1: "Strong", 2: "Good", 3: "Normal", 4: "Poor", 5: "Weak"}


def make_activity_tools(intervals_client: IntervalsClient) -> list:
    """Create the completed-activity tools with an injected intervals client.

    Args:
        intervals_client: Read client for intervals.icu activities.

    Returns:
        List of activity coach tools.
    """
    _client = intervals_client

    @coach_tool
    def get_recent_activities(days: int = 14) -> str:
        """List the athlete's recently completed activities.

        Reads what the athlete actually did (from intervals.icu): date,
        sport, name, duration, training load, distance, elevation gain,
        average HR, and intensity factor per activity. Use it to ground any
        statement about completed training and to find the ``activity_id``
        for ``get_activity_detail``.

        Args:
            days: How many days back to look (default 14).

        Returns:
            JSON string with the window and its activities, newest first.
        """
        try:
            today = clock.today()
            oldest = today - timedelta(days=max(days, 1) - 1)
            activities = _client.list_activities(oldest, today)
            rows = sorted(
                (_activity_row(act) for act in activities),
                key=lambda row: (str(row["date"]), str(row["start_time"] or "")),
                reverse=True,
            )
            payload: dict[str, Any] = {
                "start_date": oldest.isoformat(),
                "end_date": today.isoformat(),
                "count": len(rows),
                "activities": rows[:_MAX_LISTED_ACTIVITIES],
            }
            if len(rows) > _MAX_LISTED_ACTIVITIES:
                # intent: a cut listing read as the start of the athlete's data.
                payload["older_not_listed"] = len(rows) - _MAX_LISTED_ACTIVITIES
                payload["note"] = (
                    f"Only the newest {_MAX_LISTED_ACTIVITIES} are listed; the "
                    "older ones exist. For a trend over the window use "
                    "get_weekly_training_summary, or run_analysis, whose "
                    f"ana.history holds the last {HISTORY_DAYS} days."
                )
            return json.dumps(payload, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve recent activities", exc)

    @coach_tool
    def get_activity_detail(
        activity_id: str | None = None, date: str | None = None
    ) -> str:
        """Get the full picture of one completed session, split by split.

        The tool for a session debrief. Returns the session overview (pace
        or power, HR, intensity, decoupling, time in zones, elevation, the
        thresholds intervals.icu scored it against), intervals.icu's own
        one-line recap, the interval groups (repeats rolled up), and every
        rep with duration, distance, pace/power, HR, cadence, and zone. Look
        up by ``activity_id`` (from ``get_recent_activities``) or by
        ``date`` (every activity that day, up to 3). A continuous run
        carries ``halves``: the pace and HR of each distance half and the
        split they make. When short power dropouts hid reps from
        intervals.icu, the reps, groups and recap are recounted from the
        power stream and ``note`` says so.

        Reply contract — when relaying the result:
        * Call a run's split what ``halves.split`` says, with
          ``second_half_slower_pct``; never call a positive split even or
          negative.
        * Quote ``elapsed_time`` as the finish time of a race.
        * The athlete's own ``feel`` / ``rpe`` is their verdict on the session
          and outranks the load ratio when judging how it went; with
          ``source`` GARMIN_CONNECT the figures come from the Garmin watch.

        Args:
            activity_id: intervals.icu activity id, e.g. "i176644752".
            date: ISO date (YYYY-MM-DD) to look up instead of an id.

        Returns:
            JSON string with the detailed session view(s).
        """
        try:
            if activity_id:
                return json.dumps(
                    detail_view(*read_activity_detail(_client, activity_id)), indent=2
                )
            if not date:
                return json.dumps(
                    {"message": "Give either an activity_id or a date (YYYY-MM-DD)."}
                )
            day = date_type.fromisoformat(date)
            listed = _client.list_activities(day, day)
            if not listed:
                return json.dumps({"date": day.isoformat(), "activities": []})
            details = [
                detail_view(*read_activity_detail(_client, str(act.id)))
                for act in listed[:_MAX_DETAILS_PER_DATE]
                if act.id
            ]
            payload: dict[str, Any] = {"date": day.isoformat(), "activities": details}
            if len(listed) > _MAX_DETAILS_PER_DATE:
                payload["activities_omitted"] = len(listed) - _MAX_DETAILS_PER_DATE
            return json.dumps(payload, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve activity detail", exc)

    @coach_tool
    def get_weekly_training_summary(weeks: int = 4, chart: bool = False) -> str:
        """Summarize completed training per week and per sport.

        Buckets the athlete's intervals.icu activities into Monday-based
        weeks and reports hours, training load, elevation gain, and session
        counts — overall and split by sport — plus the week's intensity
        distribution (easy / moderate / hard minutes from time in zones) and
        its key sessions by load. Use it for "how much have I been
        training?", "how much climbing did I do?", and "how did my week go?"
        questions instead of adding up individual activities.

        Args:
            weeks: Number of trailing weeks to summarize, including the
                current (partial) one (default 4).
            chart: Draw the hours per week by sport under your reply; set it
                when the athlete asks how their volume developed or to see it.

        Returns:
            JSON string with one entry per week, newest last.
        """
        try:
            today = clock.today()
            this_monday = today - timedelta(days=today.weekday())
            first_monday = this_monday - timedelta(weeks=max(weeks, 1) - 1)
            activities = _client.list_activities(first_monday, today)
            buckets = _weekly_buckets(activities, first_monday, max(weeks, 1))
            charts = [_hours_chart(buckets)] if chart and activities else []
            return json.dumps(
                {
                    "start_date": first_monday.isoformat(),
                    "end_date": today.isoformat(),
                    "weeks": buckets,
                    CHARTS_KEY: charts,
                    "note": (
                        "The last week is partial — it runs through today, "
                        "not through Sunday. intensity_minutes buckets time in "
                        "zones as easy (Z1-2), moderate (Z3), hard (Z4+) over "
                        "the activities that carry zone data. "
                        "elevation_gain_m is the week's total metres climbed, "
                        "null when no activity that week recorded elevation."
                    ),
                },
                indent=2,
            )
        except Exception as exc:
            return tool_error_response("retrieve weekly training summary", exc)

    return [get_recent_activities, get_activity_detail, get_weekly_training_summary]


def _hours_chart(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    """Hours per week as bars stacked by sport, the smallest sports as Other."""
    totals: dict[str, float] = {}
    for week in weeks:
        for sport, row in week["by_sport"].items():
            totals[sport] = totals.get(sport, 0.0) + row["hours"]
    ranked = sorted(totals, key=totals.__getitem__, reverse=True)
    groups = [[sport] for sport in ranked]
    if len(groups) > MAX_SERIES:
        groups = [*groups[: MAX_SERIES - 1], ranked[MAX_SERIES - 1 :]]
    return make_spec(
        "bar",
        [week["week_start"] for week in weeks],
        [
            [
                round(
                    sum(week["by_sport"].get(s, {}).get("hours", 0.0) for s in group), 2
                )
                for week in weeks
            ]
            for group in groups
        ],
        "Hours per week by sport",
        x_label="week",
        unit="h",
        labels=[
            group[0].capitalize() if len(group) == 1 else "Other" for group in groups
        ],
        stacked=True,
    )


# Projection helpers (shared with plan_tools' delivered view)


def _sport(activity: IntervalsActivity) -> SportType | None:
    """Return the planner sport for an activity, or None when it trains none."""
    return training_sport(activity)


def _sport_name(activity: IntervalsActivity) -> str:
    """Return the planner sport for an activity, or its raw type."""
    sport = _sport(activity)
    return sport.value if sport is not None else (activity.type or "unknown")


def _minutes(seconds: int | float | None) -> float | None:
    """Convert seconds to minutes rounded to one decimal."""
    return round(seconds / 60.0, 1) if seconds else None


def _clock(seconds: int | float | None) -> str | None:
    """Format seconds as ``M:SS`` (or ``H:MM:SS`` past an hour)."""
    if seconds is None:
        return None
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _min_per_km(speed_m_s: float | None) -> str | None:
    """Render a speed in m/s as a ``M:SS`` per-km pace."""
    return _clock(1000.0 / speed_m_s) if speed_m_s else None


def _min_per_100m(speed_m_s: float | None) -> str | None:
    """Render a speed in m/s as a ``M:SS`` per-100 m pace."""
    return _clock(100.0 / speed_m_s) if speed_m_s else None


def _kmh(speed_m_s: float | None) -> float | None:
    """Render a speed in m/s as km/h."""
    return round(speed_m_s * 3.6, 1) if speed_m_s else None


def _elevation_m(gain: float | None) -> int | None:
    """Round an elevation gain to whole metres, passing an unmeasured None through."""
    return round(gain) if gain is not None else None


def _intensity_factor(percent: float | None) -> float | None:
    """Convert intervals.icu's percent intensity to an IF (0.77)."""
    return round(percent / 100.0, 2) if percent is not None else None


def _speed_fields(
    sport: SportType | None, speed: float | None, gap: float | None
) -> dict[str, Any]:
    """Render pace/speed per sport, dropping absent values."""
    fields: dict[str, Any]
    if sport is SportType.RUNNING:
        fields = {"pace_min_km": _min_per_km(speed), "gap_min_km": _min_per_km(gap)}
    elif sport is SportType.SWIMMING:
        fields = {"pace_min_100m": _min_per_100m(speed)}
    else:
        fields = {"speed_kmh": _kmh(speed)}
    return {k: v for k, v in fields.items() if v is not None}


def _primary_speed(activity: IntervalsActivity) -> float | None:
    """Pick the speed a coach quotes: moving pace for run/swim, avg for rides."""
    if _sport(activity) in (SportType.RUNNING, SportType.SWIMMING):
        return activity.pace or activity.average_speed
    return activity.average_speed or activity.pace


def _dropping_none(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued keys so absent metrics do not read as zero."""
    return {k: v for k, v in fields.items() if v is not None}


def _activity_row(activity: IntervalsActivity) -> dict[str, Any]:
    """Project one activity to the lean fields the coach quotes in a list."""
    day = activity_date(activity)
    return {
        "activity_id": activity.id,
        "date": day.isoformat() if day else None,
        "start_time": activity_start_clock(activity),
        "sport": _sport_name(activity),
        "type": activity.type,
        "name": activity.name,
        "duration_minutes": round((activity.moving_time or 0) / 60.0, 1),
        "training_load": activity.icu_training_load,
        "distance_km": (
            round(activity.distance / 1000.0, 2) if activity.distance else None
        ),
        "elevation_gain_m": _elevation_m(activity.total_elevation_gain),
        "avg_hr": activity.average_heartrate,
        "intensity_factor": _intensity_factor(activity.icu_intensity),
        **_speed_fields(_sport(activity), _primary_speed(activity), activity.gap),
    }


def _verdict_fields(activity: IntervalsActivity) -> dict[str, Any]:
    """The athlete's own post-session verdict: feel (1-5), its word, RPE (1-10)."""
    return {
        "feel": activity.feel,
        "feel_word": FEEL_WORDS.get(activity.feel) if activity.feel else None,
        "rpe": activity.icu_rpe,
    }


def delivered_view(activity: IntervalsActivity) -> dict[str, Any]:
    """Project what a completed activity delivered, for planned-vs-actual.

    Args:
        activity: The intervals.icu activity linked to a planned session.

    Returns:
        Load/duration/compliance plus the session-shape numbers a coach
        needs to judge it (HR, IF, NP or pace, decoupling, interval recap).
    """
    sport = _sport(activity)
    fields: dict[str, Any] = {
        "activity_id": activity.id,
        "name": activity.name,
        "source": activity.source,
        "training_load": activity.icu_training_load,
        "duration_minutes": round((activity.moving_time or 0) / 60.0, 1),
        "distance_km": (
            round(activity.distance / 1000.0, 2) if activity.distance else None
        ),
        "compliance_pct": activity.compliance,
        "avg_hr": activity.average_heartrate,
        "max_hr": activity.max_heartrate,
        "intensity_factor": _intensity_factor(activity.icu_intensity),
        "normalized_power": activity.icu_weighted_avg_watts,
        "decoupling_pct": _round1(activity.decoupling),
        "interval_summary": activity.interval_summary,
    }
    fields.update(_verdict_fields(activity))
    fields.update(_speed_fields(sport, _primary_speed(activity), activity.gap))
    return _dropping_none(fields)


def _round1(value: float | None) -> float | None:
    """Round to one decimal, passing None through."""
    return round(value, 1) if value is not None else None


def _zone_minutes(secs: list[int] | None) -> list[float] | None:
    """Convert a seconds-per-zone list to minutes per zone."""
    if not secs:
        return None
    return [round(s / 60.0, 1) for s in secs]


def _interval_view(
    row: IntervalRow, sport: SportType | None, index: int | None
) -> dict[str, Any]:
    """Project one rep or group to the numbers a coach reads off a split."""
    fields: dict[str, Any] = {
        "n": index,
        "count": row.count,
        "type": row.type,
        "label": row.label,
        "start": _clock(row.start_time),
        "duration": _clock(row.elapsed_time),
        "distance_km": round(row.distance / 1000.0, 2) if row.distance else None,
        "avg_hr": row.average_heartrate,
        "max_hr": row.max_heartrate,
        "avg_watts": row.average_watts,
        "normalized_power": row.weighted_average_watts,
        "cadence": round(row.average_cadence) if row.average_cadence else None,
        "zone": row.zone,
        "intensity_factor": _intensity_factor(row.intensity),
        "decoupling_pct": _round1(row.decoupling),
    }
    fields.update(_speed_fields(sport, row.average_speed, row.gap))
    return _dropping_none(fields)


def _half_view(seconds: float, metres: float, hr_seconds: float) -> dict[str, Any]:
    """Render one half of a run as its pace and time-weighted average HR."""
    return _dropping_none(
        {
            "pace_min_km": _min_per_km(metres / seconds),
            "avg_hr": round(hr_seconds / seconds) if hr_seconds else None,
        }
    )


def _halves(reps: list[IntervalRow]) -> dict[str, Any] | None:
    """Compare the two distance halves of a continuous run.

    Reps are cut at half the distance, the rep that straddles it shared
    pro rata. A session whose recoveries hold more than a tenth of the time
    is intervals, not one effort, and gets no halves.

    Args:
        reps: The run's intervals in order.

    Returns:
        Each half's pace and HR, the split (``even`` within 1 %,
        ``negative`` when the second half is faster, else ``positive``) and
        how much slower the second half was, or None for intervals or reps
        without distance and time.
    """
    if not reps or any(not rep.distance or not rep.elapsed_time for rep in reps):
        return None
    total_s = sum(float(rep.elapsed_time or 0) for rep in reps)
    work_s = sum(float(rep.elapsed_time or 0) for rep in reps if rep.type == "WORK")
    if work_s < 0.9 * total_s:
        return None
    half_m = sum(float(rep.distance or 0) for rep in reps) / 2.0
    halves = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]  # seconds, metres, HR x seconds
    covered = 0.0
    for rep in reps:
        metres, seconds = float(rep.distance or 0), float(rep.elapsed_time or 0)
        first = min(max(half_m - covered, 0.0), metres) / metres
        for half, share in ((halves[0], first), (halves[1], 1.0 - first)):
            half[0] += seconds * share
            half[1] += metres * share
            half[2] += (rep.average_heartrate or 0) * seconds * share
        covered += metres
    (s1, m1, h1), (s2, m2, h2) = halves
    slower_pct = round(((s2 / m2) / (s1 / m1) - 1.0) * 100.0, 1)
    split = (
        "even"
        if abs(slower_pct) < 1.0
        else "negative"
        if slower_pct < 0
        else "positive"
    )
    return {
        "first": _half_view(s1, m1, h1),
        "second": _half_view(s2, m2, h2),
        "split": split,
        "second_half_slower_pct": slower_pct,
    }


def detail_view(
    detail: IntervalsActivityDetail, note: str | None = None
) -> dict[str, Any]:
    """Project a full activity detail to the debrief payload.

    Shared by the coach's ``get_activity_detail`` tool and the web app's
    in-app debrief (the ``activity_debrief`` RPC method), so both
    surfaces quote the same numbers.

    Args:
        detail: The full intervals.icu activity with reps and groups.
        note: Says the reps were recounted from the power stream
            (``endury.intervals.reps``), in place of the auto-detect note.

    Returns:
        Overview, time in zones, interval groups, and per-rep splits.
    """
    sport = _sport(detail)
    day = activity_date(detail)
    overview: dict[str, Any] = {
        "activity_id": detail.id,
        "date": day.isoformat() if day else None,
        "start_time": activity_start_clock(detail),
        "sport": _sport_name(detail),
        "type": detail.type,
        "name": detail.name,
        # intent: intervals.icu's API terms require Garmin-sourced data to be
        # attributed; the debrief shows the source and device.
        "source": detail.source,
        "device_name": detail.device_name,
        "duration_minutes": _minutes(detail.moving_time),
        "elapsed_minutes": _minutes(detail.elapsed_time),
        "elapsed_time": _clock(detail.elapsed_time),
        "distance_km": round(detail.distance / 1000.0, 2) if detail.distance else None,
        "elevation_gain_m": (
            round(detail.total_elevation_gain) if detail.total_elevation_gain else None
        ),
        "training_load": detail.icu_training_load,
        "trimp": _round1(detail.trimp),
        "intensity_factor": _intensity_factor(detail.icu_intensity),
        "avg_hr": detail.average_heartrate,
        "max_hr": detail.max_heartrate,
        "lthr": detail.lthr,
        "avg_watts": detail.icu_average_watts,
        "normalized_power": detail.icu_weighted_avg_watts,
        "ftp": detail.icu_ftp,
        "variability_index": round(detail.icu_variability_index, 2)
        if detail.icu_variability_index is not None
        else None,
        "efficiency_factor": round(detail.icu_efficiency_factor, 2)
        if detail.icu_efficiency_factor is not None
        else None,
        "decoupling_pct": _round1(detail.decoupling),
        "avg_cadence": round(detail.average_cadence)
        if detail.average_cadence
        else None,
        "hr_zone_minutes": _zone_minutes(detail.icu_hr_zone_times),
        "pace_zone_minutes": _zone_minutes(detail.pace_zone_times),
        "power_zone_minutes": (
            {
                z.id: round((z.secs or 0) / 60.0, 1)
                for z in detail.icu_zone_times
                if z.id
            }
            if detail.icu_zone_times
            else None
        ),
        "interval_summary": detail.interval_summary,
    }
    overview.update(_verdict_fields(detail))
    overview.update(_speed_fields(sport, _primary_speed(detail), detail.gap))
    if sport is SportType.RUNNING:
        overview["threshold_pace_min_km"] = _min_per_km(detail.threshold_pace)
        overview["halves"] = _halves(detail.icu_intervals)
    elif sport is SportType.SWIMMING:
        overview["css_min_100m"] = _min_per_100m(detail.threshold_pace)
    view = _dropping_none(overview)

    view["groups"] = [_interval_view(g, sport, None) for g in detail.icu_groups]
    reps = detail.icu_intervals
    view["intervals"] = [
        _interval_view(rep, sport, i + 1)
        for i, rep in enumerate(reps[:_MAX_DETAIL_INTERVALS])
    ]
    if len(reps) > _MAX_DETAIL_INTERVALS:
        view["intervals_omitted"] = len(reps) - _MAX_DETAIL_INTERVALS
    if note:
        view["note"] = note
    elif reps and all(rep.label is None for rep in reps):
        view["note"] = (
            "Intervals were auto-detected by intervals.icu, not athlete-marked laps."
        )
    return view


# Weekly buckets


def _intensity_seconds(activity: IntervalsActivity) -> tuple[int, int, int] | None:
    """Bucket an activity's time in zones as (easy, moderate, hard) seconds.

    Prefers HR zones, then pace zones, then power zones (minus the
    sweet-spot overlay). Zones 1-2 are easy, 3 moderate, 4+ hard.
    """
    secs = activity.icu_hr_zone_times or activity.pace_zone_times
    if not secs and activity.icu_zone_times:
        secs = [z.secs or 0 for z in activity.icu_zone_times if z.id != "SS"]
    if not secs:
        return None
    return sum(secs[:2]), sum(secs[2:3]), sum(secs[3:])


def _key_session(activity: IntervalsActivity) -> dict[str, Any]:
    """Project one activity to the key-session line of a weekly summary."""
    day = activity_date(activity)
    return _dropping_none(
        {
            "activity_id": activity.id,
            "date": day.isoformat() if day else None,
            "sport": _sport_name(activity),
            "name": activity.name,
            "training_load": activity.icu_training_load,
            "intensity_factor": _intensity_factor(activity.icu_intensity),
            "interval_summary": activity.interval_summary,
        }
    )


def _weekly_buckets(
    activities: list[IntervalsActivity],
    first_monday: date_type,
    weeks: int,
) -> list[dict[str, Any]]:
    """Aggregate activities into ``weeks`` Monday-based weekly summaries.

    ``elevation_gain_m`` sums what the week actually climbed: an activity
    without an elevation figure counts as zero, but a week where *nothing*
    recorded elevation reads ``None`` rather than a fabricated flat 0 —
    the same unmeasured-vs-zero distinction the load fields keep.
    """
    buckets: list[dict[str, Any]] = [
        {
            "week_start": (first_monday + timedelta(weeks=index)).isoformat(),
            "activities": 0,
            "hours": 0.0,
            "training_load": 0.0,
            "elevation_gain_m": None,
            "by_sport": {},
            "intensity_minutes": {"easy": 0.0, "moderate": 0.0, "hard": 0.0},
            "activities_with_zone_data": 0,
            "key_sessions": [],
        }
        for index in range(weeks)
    ]
    members: list[list[IntervalsActivity]] = [[] for _ in range(weeks)]
    for activity in activities:
        day = activity_date(activity)
        if day is None or day < first_monday:
            continue
        index = (day - first_monday).days // 7
        if index >= weeks:
            continue
        bucket = buckets[index]
        members[index].append(activity)
        hours = (activity.moving_time or 0) / 3600.0
        load = float(activity.icu_training_load or 0.0)
        bucket["activities"] += 1
        bucket["hours"] += hours
        bucket["training_load"] += load
        sport = bucket["by_sport"].setdefault(
            _sport_name(activity),
            {
                "activities": 0,
                "hours": 0.0,
                "training_load": 0.0,
                "elevation_gain_m": None,
            },
        )
        sport["activities"] += 1
        sport["hours"] += hours
        sport["training_load"] += load
        gain = activity.total_elevation_gain
        if gain is not None:
            bucket["elevation_gain_m"] = (bucket["elevation_gain_m"] or 0.0) + gain
            sport["elevation_gain_m"] = (sport["elevation_gain_m"] or 0.0) + gain
        intensity = _intensity_seconds(activity)
        if intensity is not None:
            bucket["activities_with_zone_data"] += 1
            for key, secs in zip(("easy", "moderate", "hard"), intensity, strict=True):
                bucket["intensity_minutes"][key] += secs / 60.0

    for bucket, week_members in zip(buckets, members, strict=True):
        bucket["hours"] = round(bucket["hours"], 2)
        bucket["training_load"] = round(bucket["training_load"], 1)
        bucket["elevation_gain_m"] = _elevation_m(bucket["elevation_gain_m"])
        for sport in bucket["by_sport"].values():
            sport["hours"] = round(sport["hours"], 2)
            sport["training_load"] = round(sport["training_load"], 1)
            sport["elevation_gain_m"] = _elevation_m(sport["elevation_gain_m"])
        for key, minutes in bucket["intensity_minutes"].items():
            bucket["intensity_minutes"][key] = round(minutes, 1)
        ranked = sorted(
            week_members, key=lambda a: float(a.icu_training_load or 0.0), reverse=True
        )
        bucket["key_sessions"] = [
            _key_session(a) for a in ranked[:_KEY_SESSIONS_PER_WEEK]
        ]
    return buckets
