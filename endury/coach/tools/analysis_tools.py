"""The coach's deep-analysis tool: a short script against the athlete's data.

The model writes numpy against the helper library :mod:`endury.analysis.library`
(``ana``); this module gathers the data the script may read (the last 120
days of activities, the athlete's thresholds, the streams and laps of the
activities the call names) and hands script and data to an executor. In the
browser the executor is the sandbox worker, which holds no key and no
network; natively it is :func:`endury.analysis.runner.run_local`. The
result goes back to the model as text; the charts ride beside it to the
reply (:func:`take_charts`). Nothing here writes anything.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from endury.analysis.charts import checked_specs
from endury.analysis.library import sport_of
from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.common import clock
from endury.intervals.client import IntervalsApiError, IntervalsClient
from endury.intervals.models import AthleteProfile, IntervalsActivity

logger = logging.getLogger(__name__)

ANALYSIS_TOOL = "run_analysis"
#: How far back ``ana.history`` reaches.
HISTORY_DAYS = 120
#: The most activities one script may open.
MAX_ACTIVITIES = 12
#: The streams a script can read; intervals.icu refuses an unknown name.
STREAM_TYPES = (
    "time",
    "distance",
    "altitude",
    "fixed_altitude",
    "velocity_smooth",
    "ga_velocity",
    "heartrate",
    "cadence",
    "watts",
    "grade_smooth",
    "temp",
    "latlng",
)
#: Metres per degree of latitude, and of longitude at the equator.
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LNG = 111_320.0
#: The key the full chart specs ride under until :func:`take_charts`.
CHARTS_KEY = "charts"
#: Opens a stored chart line: a reply's charts are kept with its text, one
#: ``%%CHART%%{spec}`` line each, so a reload draws them again.
CHART_MARKER = "%%CHART%%"

Executor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _easy(activity: IntervalsActivity) -> bool | None:
    """Whether at least 80 % of the heart-rate time sat in the athlete's zones 1-2."""
    zones = activity.icu_hr_zone_times or []
    total = sum(zones)
    if total <= 0:
        return None
    return sum(zones[:2]) / total >= 0.8


def history_row(activity: IntervalsActivity) -> dict[str, Any]:
    """One activity as the compact row ``ana.history`` holds."""
    sport = sport_of(activity.type)
    speed = activity.average_speed
    return {
        "id": activity.id,
        "date": (activity.start_date_local or "")[:10],
        "type": activity.type,
        "sport": sport,
        "name": activity.name,
        "km": round((activity.distance or 0) / 1000, 2),
        "moving_min": round((activity.moving_time or 0) / 60, 1),
        "elapsed_min": round((activity.elapsed_time or 0) / 60, 1),
        "climb_m": round(activity.total_elevation_gain)
        if activity.total_elevation_gain is not None
        else None,
        "avg_hr": activity.average_heartrate,
        "max_hr": activity.max_heartrate,
        "avg_w": activity.icu_average_watts,
        "np_w": activity.icu_weighted_avg_watts,
        "speed_mps": round(speed, 3) if speed else None,
        "gap_mps": round(activity.gap, 3) if activity.gap else None,
        "load": activity.icu_training_load,
        "rpe": activity.icu_rpe,
        "feel": activity.feel,
        "temp": round(activity.average_temp, 1)
        if activity.average_temp is not None
        else None,
        "easy": _easy(activity),
        "route_id": activity.route_id,
    }


def athlete_thresholds(profile: AthleteProfile | None) -> dict[str, Any]:
    """The athlete's thresholds per sport, as ``ana.athlete`` holds them."""
    out: dict[str, Any] = {}
    for row in (profile.sport_settings if profile else None) or []:
        types = set(row.types or [])
        sport = next(
            (sport_of(t) for t in types if sport_of(t) in ("run", "ride", "swim")), None
        )
        if sport is None or f"{sport}_lthr" in out:
            continue
        out[f"{sport}_lthr"] = row.lthr
        out[f"{sport}_max_hr"] = row.max_hr
        if row.ftp:
            out[f"{sport}_ftp"] = row.ftp
        if row.threshold_pace:
            per = 100 if sport == "swim" else 1000
            sec = per / row.threshold_pace
            out[f"{sport}_threshold_pace"] = (
                f"{int(sec // 60)}:{round(sec % 60) % 60:02d}"
            )
    return out


def _streams(client: IntervalsClient, activity_id: str) -> dict[str, list[Any]]:
    """The activity's streams by type; GPS as ``lat`` and ``lng``."""
    out: dict[str, list[Any]] = {}
    for s in client.get_activity_streams(activity_id, STREAM_TYPES):
        if s.type == "latlng":
            out["lat"], out["lng"] = s.data, s.data2
        else:
            out[s.type] = s.data
    return out


def _relative_positions(activities: dict[str, dict[str, Any]]) -> None:
    """Swap every activity's GPS for metres east and north of one shared point.

    The point is the first fix of the first activity with GPS, so files on
    the same ground line up, while the sandbox and the model never see a
    coordinate: where the athlete lives stays in this worker.
    """
    origin = next(
        (
            (lat, lng)
            for act in activities.values()
            for lat, lng in zip(
                act["streams"].get("lat") or [],
                act["streams"].get("lng") or [],
                strict=False,
            )
            if lat is not None and lng is not None
        ),
        None,
    )
    for act in activities.values():
        lat = act["streams"].pop("lat", None) or []
        lng = act["streams"].pop("lng", None) or []
        if origin is None or not lat or len(lat) != len(lng):
            continue
        east = _M_PER_DEG_LNG * math.cos(math.radians(origin[0]))
        act["streams"]["x"] = [
            None if v is None else round((v - origin[1]) * east, 1) for v in lng
        ]
        act["streams"]["y"] = [
            None if v is None else round((v - origin[0]) * _M_PER_DEG_LAT, 1)
            for v in lat
        ]


def activity_payload(client: IntervalsClient, activity_id: str) -> dict[str, Any]:
    """One activity's history row, streams and laps for the script."""
    detail = client.get_activity(activity_id)
    laps = [
        lap.model_dump(
            include={
                "type",
                "start_time",
                "moving_time",
                "elapsed_time",
                "distance",
                "average_speed",
                "average_heartrate",
                "average_watts",
                "average_cadence",
            }
        )
        for lap in detail.icu_intervals
    ]
    return {
        "meta": history_row(detail),
        "streams": _streams(client, activity_id),
        "laps": laps,
    }


def build_payload(
    client: IntervalsClient, code: str, activity_ids: list[str]
) -> dict[str, Any]:
    """Everything one script may read, as plain JSON."""
    today = clock.today()
    listed = client.list_activities(today - timedelta(days=HISTORY_DAYS), today)
    try:
        profile = client.get_athlete()
    except IntervalsApiError:
        profile = None
    activities = {i: activity_payload(client, i) for i in activity_ids}
    _relative_positions(activities)
    return {
        "code": code,
        "history": [history_row(a) for a in listed if a.id],
        "athlete": athlete_thresholds(profile),
        "activities": activities,
    }


def take_charts(result: str) -> tuple[str, list[dict[str, Any]]]:
    """Split a tool result into what the model reads and the charts.

    The model reads each chart's title and size, never its points; the
    checked specs go to the reply.

    Args:
        result: The tool's JSON result.

    Returns:
        The result without the chart points, and the chart specs.
    """
    try:
        parsed = json.loads(result)
    except ValueError:
        return result, []
    if not isinstance(parsed, dict) or CHARTS_KEY not in parsed:
        return result, []
    charts = checked_specs(parsed.pop(CHARTS_KEY))
    if charts:
        parsed["charts_attached"] = [
            f"{c['title']} ({c['kind']}, {len(c['ys'])} series, {len(c['x'])} points)"
            for c in charts
        ]
    return json.dumps(parsed, ensure_ascii=False), charts


def with_chart_lines(text: str, charts: list[dict[str, Any]]) -> str:
    """The reply as stored: its text, then one marker line per chart."""
    lines = [
        CHART_MARKER + json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        for c in charts
    ]
    return "\n".join([text, *lines]) if lines else text


def charts_for_model(text: str) -> str:
    """A stored reply as the model rereads it: each chart as its title only."""
    if CHART_MARKER not in text:
        return text
    out = []
    for line in text.split("\n"):
        if line.startswith(CHART_MARKER):
            try:
                title = json.loads(line[len(CHART_MARKER) :]).get("title", "")
            except ValueError:
                title = ""
            out.append(f"[Chart shown under this reply: {title}]")
        else:
            out.append(line)
    return "\n".join(out)


def make_analysis_tools(intervals_client: IntervalsClient, executor: Executor) -> list:
    """Create the analysis tool with injected dependencies.

    Args:
        intervals_client: Read client for intervals.icu activities and streams.
        executor: Runs one payload and returns ``{"stdout", "error", "charts"}``.

    Returns:
        List holding the ``run_analysis`` coach tool.
    """

    @coach_tool
    async def run_analysis(code: str, activity_ids: str | None = None) -> str:
        """Analyse the athlete's own data with a short Python script, and draw charts.

        Use it for what the other tools cannot answer directly: pacing,
        splits, climbs, laps and intervals, heart-rate drift, walking, best
        efforts, comparing sessions, trends over weeks; and whenever the
        athlete asks to see, show or plot something. `np` is numpy and `ana`
        the library below; the tool returns what the script prints (cut at
        4,000 characters) and attaches its charts to your reply.

        How to work: for one session or race start with ana.overview(a) and
        read all of it before you write. Compare like with like (ana.uphill,
        ana.compare with its minutes, the same gradient or lap type); under
        10 minutes is a small sample, say so. A cause (fatigue, heat,
        fuelling, a push to the finish) needs a number that points to it,
        such as power, temperature or gradient. A trend needs ana.trend: a
        change smaller than the scatter is no clear trend. Print compact
        results with every number named (dicts or ana.table, never bare
        tuples), never whole arrays; if the script fails, fix it and run it
        again. Attach a chart whenever the result is a series or a
        comparison (weeks, sessions, climbs, laps, splits), asked or not: at
        most two, one unit each. To
        compare sessions, give each its own series named by its date over
        one shared x (the matched climb, lap or split, named like "km 9 ·
        14%"); never several sessions in a row on one axis.

        The same ground in two files, any sport, from GPS: same-route
        candidates share route_id in ana.history, or a similar name,
        distance and climb. rows = ana.compare_route(a, b) finds what both
        covered in the same direction and returns (and prints) one row per
        climb, descent and level stretch (a_min, b_min, change_pct, speed
        or pace, vam, max_kmh, hr, w, walked; b_extra_m marks a detour);
        ana.chart_route(rows, a, b, value="min", kinds=("climb",)) draws it
        as two bars per piece. No shared ground means another route, then
        compare by gradient band. ana.time_gap(a, b) returns {"km": [...],
        "gap_s": [...]}, the seconds b was behind along the shared ground;
        ana.chart_gap(a, b) draws it. ana.efforts(ref, from_km, to_km,
        others) returns one row per pass and times every pass
        over one stretch of ref's route: laps and repeats in ref, and other
        days.

        The library: a = ana.activity(id) opens an activity listed in
        activity_ids; a.sport is run, ride, swim or hike; a.meta its history
        row; arrays a.t (s), a.km, a.alt (m), a.hr, a.w (power), a.cad,
        a.speed and a.gap_speed (m/s), a.grade (%), a.temp; masks a.moving,
        a.walking. ana.history: the last 120 days, one dict per activity
        (id, date, type, sport, name, km, moving_min, climb_m, avg_hr,
        avg_w, np_w, speed_mps, gap_mps, load, rpe, feel, easy, route_id).
        ana.athlete: thresholds such as run_lthr, run_threshold_pace,
        ride_ftp, swim_threshold_pace. Helpers: ana.overview(a);
        ana.summary(a, mask) -> dict; ana.compare(a, masks, labels);
        ana.splits(a, every_km=1) or (a, every_min=10); ana.laps(a,
        work_only=False); ana.climbs(a); ana.by_grade(a, mask);
        ana.best_efforts(a, minutes=(1, 5, 20, 60)); ana.decoupling(a);
        ana.walk_vs_run(a, min_grade=10); ana.trend(dates, values, label,
        higher_is_better=True); ana.weekly(value="hours", weeks=8) (or
        "km", "load"); ana.table(rows, cols); ana.check(a). Masks:
        ana.window(a, from_km, to_km), ana.between(a, from_min, to_min),
        ana.halves(a), ana.quarters(a), ana.uphill(a, 5), ana.downhill(a,
        5). ana.pace(mps, per=1000) -> "m:ss"; ana.seconds(mps, per=1000)
        gives seconds for a pace chart. ana.chart(kind, x, ys, title,
        x_label, unit, labels=None, stacked=False): kind line, area, bar or
        scatter; ys 1-3 series in one unit (bpm, W, W/bpm, km/h, min/km,
        min/100m, m, km, m/h, %, rpm, spm, s, min, h, ms, load, °C or "");
        pace units take seconds and draw as m:ss; x numbers, YYYY-MM-DD
        dates or bar names; labels name two or more series.

        Reply contract — when relaying the result:
        * Every number comes from the output; never a figure worked out in
          your head.
        * Refer to an attached chart in words ("the chart below"), and only
          to one the result lists under ``charts_attached``; never mention
          code, scripts or the library.

        Args:
            code: The script; ana and np are already imported.
            activity_ids: Comma-separated ids of every activity the script
                opens with ana.activity (at most 12), from
                get_recent_activities or ana.history.

        Returns:
            JSON with the printed ``output``, an ``error`` to fix, and the
            charts.
        """
        ids = [i.strip() for i in (activity_ids or "").split(",") if i.strip()]
        if len(ids) > MAX_ACTIVITIES:
            return json.dumps(
                {"error": f"At most {MAX_ACTIVITIES} activities per script."}
            )
        try:
            payload = build_payload(intervals_client, code, list(dict.fromkeys(ids)))
        except Exception as exc:
            return tool_error_response("read the activities for the analysis", exc)
        try:
            result = await executor(payload)
        except Exception as exc:
            logger.warning("Analysis executor failed: %s", exc)
            return tool_error_response("run the analysis", exc)
        return json.dumps(
            {
                "output": result.get("stdout") or "(nothing printed)",
                "error": result.get("error"),
                CHARTS_KEY: result.get("charts") or [],
            },
            ensure_ascii=False,
        )

    return [run_analysis]
