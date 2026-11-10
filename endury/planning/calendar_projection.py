"""intervals.icu readback projection for the calendar payload.

Reads completed activities and pushed WORKOUT events for one date window
and projects them into the calendar wire shapes: activity payloads,
plan-session completion overlays, and the rows for the workouts that live
only on intervals.icu — the coach's ad-hoc workouts and the finished
sessions of a plan the app has since replaced or deleted — their
threshold-percent step bands converted to absolute pace/HR/power/swim-pace
bands via live thresholds when available, and left as raw percent otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.intervals.models import (
    SPORT_TYPE_MAP,
    IntervalsActivity,
    IntervalsEvent,
)
from endury.intervals.push import AD_HOC_EXTERNAL_ID_PREFIX, session_event_plan_id
from endury.intervals.workout_dsl import SportThresholds
from endury.planner.foundations.types import SportType
from endury.planning.pair_activity import pair_unambiguous
from endury.planning.schedule_workout import sport_thresholds
from endury.planning.wire.workout_steps import _serialize_duration
from endury.training.load import LoadMetricContext

#: intervals.icu workout_doc step metric keys -> the plan-row target metric
#: name (endury/planning/wire/workout_steps.py's target shape).
_STEP_METRIC_KEYS: tuple[tuple[str, str], ...] = (
    ("pace", "pace"),
    ("power", "power"),
    ("hr", "hr"),
)


def _activity_payload(activity: IntervalsActivity) -> dict[str, Any]:
    """Serialize one intervals.icu activity into the calendar wire shape."""
    sport = SPORT_TYPE_MAP.get(activity.type or "")
    return {
        "id": activity.id,
        "name": activity.name or (activity.type or "Activity"),
        "sport": sport.value if sport is not None else (activity.type or "other"),
        # The raw intervals.icu type: an outdoor one has a route to compare.
        "type": activity.type,
        "start_time": activity.start_date_local,
        "distance": activity.distance,
        "duration": activity.moving_time,
        "tss": activity.icu_training_load,
        "feel": activity.feel,
        "rpe": activity.icu_rpe,
        "source": activity.source,
        "device_name": activity.device_name,
        "hr_zone_times": activity.icu_hr_zone_times,
        "pace_zone_times": activity.pace_zone_times,
        # Moving pace first, as the debrief quotes it (activity_tools'
        # _primary_speed): only the run/swim Pace tile reads this key.
        "average_speed_mps": activity.pace or activity.average_speed,
        "gap_mps": activity.gap,
        "average_hr": activity.average_heartrate,
        "max_hr": activity.max_heartrate,
        # A run's is per leg, as its cadence stream (analysis WALK_CADENCE).
        "average_cadence": activity.average_cadence,
        "decoupling_pct": activity.decoupling,
        "average_power": activity.icu_average_watts,
        "normalized_power": activity.icu_weighted_avg_watts,
        "intensity_factor": (
            round(activity.icu_intensity / 100.0, 2)
            if activity.icu_intensity is not None
            else None
        ),
        "elevation_gain_meters": activity.total_elevation_gain,
        "intervals_activity_url": f"https://intervals.icu/activities/{activity.id}",
    }


def _completion_overlay(activity: IntervalsActivity) -> dict[str, Any]:
    """The completion keys a paired activity adds to a planned row."""
    return {
        "matched_activity_id": activity.id,
        "compliance_pct": activity.compliance,
        "intervals_activity_url": f"https://intervals.icu/activities/{activity.id}",
    }


def _is_calendar_only(external_id: str, plan_id: int) -> bool:
    """Whether an Endury event has no session row in the active plan.

    Args:
        external_id: The event's external_id.
        plan_id: The active plan's id.
    """
    if external_id.startswith(AD_HOC_EXTERNAL_ID_PREFIX):
        return True
    owner = session_event_plan_id(external_id)
    return owner is not None and owner != plan_id


def _calendar_only_row(
    event: IntervalsEvent,
    activity: IntervalsActivity | None,
    today: date,
    resolver: LoadMetricContext | None,
) -> dict[str, Any]:
    """One calendar-only workout with its state.

    Args:
        event: The readback event no active-plan session row backs.
        activity: The activity paired with it on intervals.icu, if any.
        today: The calendar's today.
        resolver: Live threshold anchors for the step bands, or None.

    Returns:
        The ad-hoc row plus ``plan_id`` (a former plan's, ``None`` for a
        coach extra) and ``status``: ``completed`` with the completion
        overlay when paired, ``adhoc`` for an open coach extra, and
        ``missed`` or ``pending`` for a former plan's unpaired session.
    """
    row = _ad_hoc_payload(event, resolver)
    row["plan_id"] = session_event_plan_id(event.external_id or "")
    if activity is not None and activity.id is not None:
        row["status"] = "completed"
        row.update(_completion_overlay(activity))
    elif row["plan_id"] is None:
        row["status"] = "adhoc"
    else:
        row["status"] = "missed" if row["date"] < today.isoformat() else "pending"
    return row


def _ad_hoc_payload(
    event: IntervalsEvent, resolver: LoadMetricContext | None = None
) -> dict[str, Any]:
    """Serialize one workout event that lives only on intervals.icu.

    A coach-scheduled ad-hoc workout persists nothing locally
    (``schedule_workout`` when no active plan covers the date), and a
    replaced or deleted plan takes its sessions out of the store while its
    events before today stay on the calendar — so this projection is the
    calendar view's sole window onto both. The description is coaching
    brief + blank line + workout DSL; only the brief is athlete-facing here.

    Args:
        event: The readback event.
        resolver: Live threshold anchors used to project the workout's
            threshold-percent step bands into absolute pace/HR/power/
            swim-pace, or None to leave every band as a raw percent.

    Returns:
        The ad-hoc row in the calendar wire shape.
    """
    sport = SPORT_TYPE_MAP.get(event.type or "")
    thresholds = sport_thresholds(sport, resolver) if sport is not None else None
    note = (event.description or "").split("\n\n")[0]
    return {
        "id": event.id,
        # The delete handle: DELETE /calendar/adhoc/{external_id}.
        "external_id": event.external_id,
        "date": (event.start_date_local or "")[:10],
        "sport": sport.value if sport is not None else (event.type or "other"),
        "name": event.name or "Workout",
        "note": note or None,
        "duration": event.moving_time,
        "tss": event.icu_training_load,
        "workout": _ad_hoc_workout(event, sport, thresholds),
    }


def _ad_hoc_workout(
    event: IntervalsEvent,
    sport: SportType | None,
    thresholds: SportThresholds | None,
) -> dict[str, Any] | None:
    """Project intervals.icu's server-parsed steps into the plan-row shape.

    ``workout_doc`` is only populated for WORKOUT events, and only once
    intervals.icu has parsed the DSL we pushed (``IntervalsEvent.workout_doc``).
    A step that doesn't carry a duration — or any other shape we haven't
    observed — is skipped rather than raising: this projection is the
    calendar's only window onto ad-hoc workouts and must degrade, not break.

    Args:
        event: The readback event carrying an ad-hoc external_id.
        sport: The event's mapped sport, or None for an unmapped type.
        thresholds: Live per-sport thresholds to project percent bands into
            absolute units, or None to leave them as percent.

    Returns:
        ``{"name", "steps"}`` in the wire shape planned sessions already use,
        or ``None`` when there is no usable step list.
    """
    workout_doc = getattr(event, "workout_doc", None) or {}
    raw_steps = workout_doc.get("steps")
    if not isinstance(raw_steps, list):
        return None
    steps = [
        step
        for step in (_ad_hoc_step(raw, sport, thresholds) for raw in raw_steps)
        if step
    ]
    if not steps:
        return None
    return {"name": event.name or "Workout", "steps": steps}


def _ad_hoc_step(
    raw: Any,
    sport: SportType | None,
    thresholds: SportThresholds | None,
) -> dict[str, Any] | None:
    """Serialize one intervals.icu ``workout_doc`` step, or None if malformed."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("steps"), list):
        return _ad_hoc_repeat(raw, sport, thresholds)
    duration_seconds = raw.get("duration")
    if not isinstance(duration_seconds, (int, float)):
        return None
    distance_meters = raw.get("distance")
    if not isinstance(distance_meters, (int, float)):
        distance_meters = 0.0
    text = raw.get("text") or ""
    return {
        "type": _ad_hoc_step_type(text),
        "description": text,
        "duration_seconds": float(duration_seconds),
        "duration_meters": float(distance_meters),
        "duration": _serialize_duration(
            float(duration_seconds), float(distance_meters)
        ),
        "target": _ad_hoc_step_target(raw, sport, thresholds),
        "secondary_target": None,
    }


def _ad_hoc_repeat(
    raw: dict[str, Any],
    sport: SportType | None,
    thresholds: SportThresholds | None,
) -> dict[str, Any] | None:
    """Serialize one ``workout_doc`` repeat block in the plan-row shape.

    intervals.icu nests a repeat's steps under ``steps`` with the count in
    ``reps``; the web draws ``iterations`` + ``steps`` as "Repeat xN" over
    each rep's own target, as it does for a planned session.
    """
    steps = [
        step
        for step in (_ad_hoc_step(child, sport, thresholds) for child in raw["steps"])
        if step
    ]
    if not steps:
        return None
    reps = raw.get("reps")
    return {
        "type": "repeat",
        "description": raw.get("text") or "",
        "iterations": reps if isinstance(reps, int) and reps > 0 else 1,
        "steps": steps,
    }


def _ad_hoc_step_type(text: str) -> str:
    """Infer the plan-row step type from step text.

    intervals.icu has not been observed to send warmup/cooldown/recovery
    flags on a step, only free text.
    """
    lowered = text.lower()
    if "warm-up" in lowered or "warmup" in lowered:
        return "warmup"
    if "cool-down" in lowered or "cooldown" in lowered:
        return "cooldown"
    if "recovery" in lowered or "rest" in lowered:
        return "recovery"
    return "interval"


def _ad_hoc_step_target(
    raw: dict[str, Any],
    sport: SportType | None,
    thresholds: SportThresholds | None,
) -> dict[str, Any] | None:
    """Read whichever of pace/power/hr bands a step carries.

    A band is converted from threshold-percent to an absolute pace/HR/
    power/swim-pace band only when it is percent-relative (``units``
    starts with ``%``), both bounds are usable percentages, and the
    matching live threshold is on file for the event's sport; otherwise
    the raw percent band is returned unchanged.
    A band already in absolute units — e.g. from a hand-edited event —
    carries no ``%`` units and is not detected here, so it is still
    mislabelled as percent.
    """
    for key, metric in _STEP_METRIC_KEYS:
        band = raw.get(key)
        if not isinstance(band, dict):
            continue
        low, high = band.get("start"), band.get("end")
        if low is None or high is None:
            continue
        units = band.get("units")
        if (
            thresholds is not None
            and isinstance(units, str)
            and units.startswith("%")
            and _usable_percent(low)
            and _usable_percent(high)
        ):
            absolute = _absolute_band(metric, low, high, sport, thresholds)
            if absolute is not None:
                return absolute
        return {
            "target_type": "threshold_percent",
            "metric": metric,
            "low_percent": low,
            "high_percent": high,
        }
    return None


def _usable_percent(value: Any) -> bool:
    """Return whether a band bound is a percentage the inversions can use.

    ``workout_doc`` mirrors an event description anyone can hand-edit on
    intervals.icu, so a bound can arrive as text or as zero — both raise in
    the percent inversions below, and this projection must degrade, not
    break.

    Args:
        value: One ``start``/``end`` bound off a ``workout_doc`` band.

    Returns:
        True for a strictly positive number, the only shape every
        inversion can divide by.
    """
    return isinstance(value, (int, float)) and value > 0


def _absolute_band(
    metric: str,
    low_percent: float,
    high_percent: float,
    sport: SportType | None,
    thresholds: SportThresholds,
) -> dict[str, Any] | None:
    """Convert one percent band to absolute units, per metric and sport."""
    if metric == "pace":
        return _pace_band(low_percent, high_percent, sport, thresholds)
    if metric == "hr":
        return _hr_band(low_percent, high_percent, thresholds)
    return _power_band(low_percent, high_percent, thresholds)


def _pace_band(
    low_percent: float,
    high_percent: float,
    sport: SportType | None,
    thresholds: SportThresholds,
) -> dict[str, Any] | None:
    """Invert a ``%pace`` band into absolute pace, keyed by sport.

    Running inverts against ``threshold_pace_sec_per_km`` into min/km;
    swimming inverts against ``css_sec_per_100m`` into sec/100m — both are
    speed-relative, so the band's faster (higher-percent) bound becomes the
    lower absolute value. Cycling has no pace threshold, so it always falls
    through to the percent fallback.

    Args:
        low_percent: Lower percent bound (the slower effort).
        high_percent: Upper percent bound (the faster effort).
        sport: The event's mapped sport.
        thresholds: Live thresholds for that sport.

    Returns:
        A ``pace`` or ``swim_pace`` wire target, or None when the matching
        threshold is unavailable.
    """
    if sport is SportType.RUNNING and thresholds.threshold_pace_sec_per_km:
        threshold = thresholds.threshold_pace_sec_per_km
        return {
            "target_type": "pace",
            "low_min_per_km": round(threshold / (high_percent / 100) / 60, 2),
            "high_min_per_km": round(threshold / (low_percent / 100) / 60, 2),
        }
    if sport is SportType.SWIMMING and thresholds.css_sec_per_100m:
        css = thresholds.css_sec_per_100m
        return {
            "target_type": "swim_pace",
            "low_sec_per_100m": round(css / (high_percent / 100), 1),
            "high_sec_per_100m": round(css / (low_percent / 100), 1),
        }
    return None


def _hr_band(
    low_percent: float, high_percent: float, thresholds: SportThresholds
) -> dict[str, Any] | None:
    """Invert a ``%LTHR`` band into absolute bpm, or None with no LTHR on file."""
    if not thresholds.lthr_bpm:
        return None
    lthr = thresholds.lthr_bpm
    return {
        "target_type": "heart_rate",
        "low_bpm": round(lthr * low_percent / 100),
        "high_bpm": round(lthr * high_percent / 100),
    }


def _power_band(
    low_percent: float, high_percent: float, thresholds: SportThresholds
) -> dict[str, Any] | None:
    """Invert a ``%FTP`` band into absolute watts, or None with no FTP on file."""
    if not thresholds.ftp_watts:
        return None
    ftp = thresholds.ftp_watts
    return {
        "target_type": "power",
        "low_watts": round(ftp * low_percent / 100),
        "high_watts": round(ftp * high_percent / 100),
    }


def _thresholds(
    needed: bool,
    thresholds_source: Callable[[], LoadMetricContext | None] | None,
) -> LoadMetricContext | None:
    """Fetch the threshold anchors, but only when a calendar-only row needs them.

    Args:
        needed: Whether this calendar load projects any calendar-only event.
        thresholds_source: The lazy fetcher, or None when the caller
            supplied none.

    Returns:
        The live anchors from a single ``thresholds_source`` call, or None
        when there is nothing to project or no source to ask — a calendar
        load with no calendar-only row must not pay for an intervals.icu
        athlete-profile read.
    """
    if not needed or thresholds_source is None:
        return None
    return thresholds_source()


def fetch_intervals_calendar(
    client: IntervalsClient,
    plan_id: int,
    sessions: list[Any],
    window: tuple[date, date],
    thresholds_source: Callable[[], LoadMetricContext | None] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Read completed activities and match them to plan sessions.

    ``IntervalsEvent`` carries no pairing fields of its own (see
    ``endury/intervals/models.py``), so pairing is read off
    ``IntervalsActivity.paired_event_id`` instead: pushed events are read
    back by our ``external_id`` scheme (``coachie:{plan_id}:{slot_id}``,
    docs/reference/intervals_api.md) to learn their intervals.icu event id, then
    activities are matched to that id. A session intervals.icu left unpaired
    is first paired there when exactly one activity fits it
    (:func:`~endury.planning.pair_activity.pair_unambiguous`).

    Args:
        client: The intervals.icu client.
        plan_id: The active plan's id (keys the external_id scheme); any
            other plan's session events are calendar-only rows.
        sessions: Plan session rows in the queried date range.
        window: ``(oldest, newest)`` local dates to read back.
        thresholds_source: Lazily fetches live threshold anchors for
            projecting calendar-only workouts' percent bands into absolute
            units. Called at most once, and only when there is at least one
            such event to project, so a load without one never pays for an
            intervals.icu athlete-profile read.

    Returns:
        ``(activities, overlays, calendar_only)`` — every activity in the
        window in the calendar wire shape, plan session id -> completion
        overlay (``matched_activity_id``, ``compliance_pct``,
        ``intervals_activity_url``) for sessions with a pushed event that
        has a paired, completed activity, and the workouts that live only on
        intervals.icu (the coach's ad-hoc events and the sessions of a plan
        the app has replaced or deleted, whose events before today stay on
        the calendar), each with its state and, when paired, its activity.
        Sessions with no matching event yet, or an event nobody has
        completed, are absent from the overlays.
    """
    oldest, newest = window
    activities = client.list_activities(oldest, newest)
    events = client.list_events(oldest, newest, category="WORKOUT")
    activities = pair_unambiguous(client, plan_id, sessions, events, activities)
    event_id_by_external_id = {
        event.external_id: event.id
        for event in events
        if event.external_id and event.id is not None
    }
    activity_by_event_id = {
        activity.paired_event_id: activity
        for activity in activities
        if activity.paired_event_id is not None
    }

    overlays: dict[int, dict[str, Any]] = {}
    for session in sessions:
        external_id = f"coachie:{plan_id}:{session.slot_id}"
        event_id = event_id_by_external_id.get(external_id)
        if event_id is None:
            continue
        activity = activity_by_event_id.get(event_id)
        if activity is None or activity.id is None:
            continue
        overlays[session.id] = _completion_overlay(activity)
    calendar_only = {
        event.id: event
        for event in events
        if event.id is not None and _is_calendar_only(event.external_id or "", plan_id)
    }
    resolver = _thresholds(bool(calendar_only), thresholds_source)
    today = clock.today()
    rows = [
        _calendar_only_row(event, activity_by_event_id.get(event_id), today, resolver)
        for event_id, event in calendar_only.items()
    ]
    return [_activity_payload(activity) for activity in activities], overlays, rows
