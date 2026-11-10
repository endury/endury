"""Activity intensity-trace projection: streams or interval rows -> bar data.

Pure functions over :mod:`endury.intervals.models` only — no HTTP, no DB, no
coach/LLM surface. Feeds the ``activity_trace`` RPC method, which the coach
never calls: this is deliberately a separate projection from
``endury.coach.tools.activity_tools.detail_view`` so the LLM never sees a
~96-number array.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

from endury.intervals.models import (
    SPORT_TYPE_MAP,
    ActivityStream,
    IntervalRow,
    IntervalsActivityDetail,
)
from endury.planner.foundations.types import SportType

#: Bar count the trace is bucketed to (streams source only).
_BUCKET_COUNT = 96

#: Scale-window floor/cap/step (see the wire contract's ``scale`` field).
_SCALE_LO_HR = 50
_SCALE_LO_DEFAULT = 30
#: The floor sits at least this far under the session's median, so a very
#: easy session (a hike, a recovery spin) still draws a shape, not a flat line.
_SCALE_LO_HEADROOM = 20
_SCALE_HI_FLOOR = 110
_SCALE_HI_CAP = 160
_SCALE_HI_STEP = 10

#: Minimum elevation range (metres) worth drawing as a profile.
_ELEVATION_MIN_RANGE_M = 30


def _is_pool_swim(detail: IntervalsActivityDetail, sport: SportType | None) -> bool:
    """Return True for a swim in a pool, which is never drawn from streams."""
    return sport is SportType.SWIMMING and bool(detail.pool_length)


def trace_stream_types(detail: IntervalsActivityDetail) -> list[str]:
    """List the raw stream types worth fetching for one activity's trace.

    Args:
        detail: The activity to plan a streams fetch for.

    Returns:
        Stream type names for ``get_activity_streams``, or ``[]`` for a pool
        swim (drawn from ``icu_intervals`` instead — see ``activity_trace``).
    """
    sport = SPORT_TYPE_MAP.get(detail.type or "")
    if sport is SportType.CYCLING:
        return ["watts", "heartrate", "altitude"]
    if sport is SportType.RUNNING:
        types = ["velocity_smooth", "heartrate", "altitude"]
        if detail.use_gap_zone_times:
            types.append("ga_velocity")
        return types
    if sport is SportType.SWIMMING:
        return [] if _is_pool_swim(detail, sport) else ["velocity_smooth", "heartrate"]
    return ["heartrate", "altitude"]


def activity_trace(
    detail: IntervalsActivityDetail, streams: list[ActivityStream]
) -> dict[str, Any]:
    """Project an activity to the debrief's intensity-trace wire shape.

    Picks the first drawable candidate: the sport's primary streams metric
    (power for cycling, pace for running/open-water swimming), else HR from
    streams, else a per-interval-row trace from ``icu_intervals``, else
    nothing drawable. A pool swim skips both streams candidates outright —
    its ``velocity_smooth`` reads 0.0 through every wall rest, so the
    lengths intervals.icu already scored are the only honest picture.

    Args:
        detail: The activity, with ``icu_intervals`` for the fallback path.
        streams: Raw data streams already fetched for this activity (per
            ``trace_stream_types``); empty for a pool swim.

    Returns:
        The wire shape from the contract, or ``{"activity_id", "source":
        None}`` when nothing is drawable.
    """
    sport = SPORT_TYPE_MAP.get(detail.type or "")
    usable = [] if _is_pool_swim(detail, sport) else streams
    stream_by_type = {stream.type: stream for stream in usable}
    candidate = _primary_streams_candidate(
        sport, detail, stream_by_type
    ) or _hr_candidate(detail, stream_by_type)
    if candidate is not None:
        metric, stream, threshold, grade_adjusted = candidate
        return _streams_trace(
            detail, metric, stream, threshold, grade_adjusted, stream_by_type
        )
    intervals_trace = _intervals_trace(detail, sport)
    if intervals_trace is not None:
        return intervals_trace
    return {"activity_id": detail.id, "source": None}


# Streams source


def _has_data(stream: ActivityStream | None) -> bool:
    """Return True when a stream is present with at least one real sample."""
    return stream is not None and any(v is not None for v in stream.data)


def _primary_streams_candidate(
    sport: SportType | None,
    detail: IntervalsActivityDetail,
    stream_by_type: dict[str, ActivityStream],
) -> tuple[str, ActivityStream, float, bool] | None:
    """Pick the sport's primary streams metric, if drawable.

    Returns:
        ``(metric, stream, threshold, grade_adjusted)``, or None when the
        sport has no primary streams metric (other/unmapped) or its
        threshold/stream is unusable. Pool swims never get here with
        streams: ``activity_trace`` withholds them.
    """
    if sport is SportType.CYCLING:
        watts = stream_by_type.get("watts")
        if watts is not None and detail.icu_ftp and _has_data(watts):
            return "power", watts, detail.icu_ftp, False
    elif sport is SportType.RUNNING:
        ga_velocity = stream_by_type.get("ga_velocity")
        if ga_velocity is not None and detail.threshold_pace and _has_data(ga_velocity):
            return "pace", ga_velocity, detail.threshold_pace, True
        velocity_smooth = stream_by_type.get("velocity_smooth")
        if (
            velocity_smooth is not None
            and detail.threshold_pace
            and _has_data(velocity_smooth)
        ):
            return "pace", velocity_smooth, detail.threshold_pace, False
    elif sport is SportType.SWIMMING:
        velocity_smooth = stream_by_type.get("velocity_smooth")
        if (
            velocity_smooth is not None
            and detail.threshold_pace
            and _has_data(velocity_smooth)
        ):
            return "pace", velocity_smooth, detail.threshold_pace, False
    return None


def _hr_candidate(
    detail: IntervalsActivityDetail, stream_by_type: dict[str, ActivityStream]
) -> tuple[str, ActivityStream, float, bool] | None:
    """Fall back to HR from streams, if drawable."""
    heartrate = stream_by_type.get("heartrate")
    if heartrate is not None and detail.lthr and _has_data(heartrate):
        return "hr", heartrate, detail.lthr, False
    return None


def _bucket_values(data: list[float | None], bucket_count: int) -> list[float | None]:
    """Bucket a raw sample stream to exactly `bucket_count` mean values.

    The count is passed in rather than derived so the altitude stream is cut
    into the same buckets as the metric stream — `pct`/`zone`/`value` and
    `elevation` are parallel arrays the client indexes together.

    Args:
        data: One sample per recorded second, numbers or None.
        bucket_count: How many buckets to produce (at least 1).

    Returns:
        `bucket_count` bucket means, in order; a bucket with no non-null
        samples (including every bucket of an empty stream) is None.
    """
    n = len(data)
    values: list[float | None] = []
    for i in range(bucket_count):
        lo = i * n // bucket_count
        hi = (i + 1) * n // bucket_count
        chunk = [v for v in data[lo:hi] if v is not None]
        values.append(sum(chunk) / len(chunk) if chunk else None)
    return values


def _zone_bounds(
    metric: str, detail: IntervalsActivityDetail
) -> Sequence[float] | None:
    """Return the zone-bound list a metric is scored against."""
    if metric == "power":
        return detail.icu_power_zones
    if metric == "pace":
        return detail.pace_zones
    return detail.icu_hr_zones


def _zone(bounds: Sequence[float] | None, x: float) -> int | None:
    """Return the 1-based zone of ``x`` against ascending upper bounds.

    Args:
        bounds: Ascending upper bounds, one per zone; missing/empty means
            the athlete has no zone scheme configured for this metric.
        x: The value to place (a percent for power/pace, bpm for HR).

    Returns:
        The index (1-based) of the first bound with ``x <= bound``, or the
        top zone when ``x`` exceeds every bound; None when `bounds` is
        missing or empty.
    """
    if not bounds:
        return None
    for i, bound in enumerate(bounds):
        if x <= bound:
            return i + 1
    return len(bounds)


def _round_value(x: float, metric: str) -> float | int:
    """Round a metric value per the contract: pace to 3 decimals, else int."""
    return round(x, 3) if metric == "pace" else round(x)


def _scale(pct_values: list[float | None], metric: str) -> dict[str, int]:
    """Compute the pct window bars are drawn in.

    Args:
        pct_values: The trace's `pct` array (may contain None entries).
        metric: `"hr"` uses a 50 floor; everything else uses 30.

    Returns:
        ``{"lo": ..., "hi": ...}`` per the contract's clamp/round-up rule;
        `lo` drops below the metric's floor (never below 0) when the median
        sits within `_SCALE_LO_HEADROOM` of it.
    """
    lo = _SCALE_LO_HR if metric == "hr" else _SCALE_LO_DEFAULT
    non_null = [p for p in pct_values if p is not None]
    max_pct = max(non_null) if non_null else 0.0
    if non_null:
        median_step = math.floor(statistics.median(non_null) / _SCALE_HI_STEP)
        lo = min(lo, max(0, median_step * _SCALE_HI_STEP - _SCALE_LO_HEADROOM))
    step_rounded = math.ceil(max_pct / _SCALE_HI_STEP) * _SCALE_HI_STEP
    hi = min(_SCALE_HI_CAP, max(_SCALE_HI_FLOOR, step_rounded))
    return {"lo": lo, "hi": hi}


def _duration_s(detail: IntervalsActivityDetail, sample_count: int) -> int:
    """Pick the trace's x-axis span: moving time, else elapsed, else samples."""
    return detail.moving_time or detail.elapsed_time or sample_count


def _elevation(stream: ActivityStream | None, bucket_count: int) -> list[int] | None:
    """Bucket an altitude stream, if it is worth drawing as a profile.

    Args:
        stream: The activity's altitude stream, if it has one.
        bucket_count: The metric trace's bucket count, so the two arrays
            line up; an altitude stream too short to fill them all leaves
            empty buckets and is dropped.

    Returns:
        Whole-metre bucket means, only when every bucket has a value and
        the range across buckets is at least `_ELEVATION_MIN_RANGE_M`.
    """
    if stream is None:
        return None
    bucketed = _bucket_values(stream.data, bucket_count)
    if any(v is None for v in bucketed):
        return None
    rounded = [round(v) for v in bucketed if v is not None]
    if max(rounded) - min(rounded) < _ELEVATION_MIN_RANGE_M:
        return None
    return rounded


def _streams_trace(
    detail: IntervalsActivityDetail,
    metric: str,
    stream: ActivityStream,
    threshold: float,
    grade_adjusted: bool,
    stream_by_type: dict[str, ActivityStream],
) -> dict[str, Any]:
    """Build the ``source: "streams"`` wire payload for one chosen metric."""
    bucket_count = min(_BUCKET_COUNT, len(stream.data))
    bucketed = _bucket_values(stream.data, bucket_count)
    bounds = _zone_bounds(metric, detail)
    pct: list[float | None] = []
    zone: list[int | None] = []
    value: list[float | int | None] = []
    for v in bucketed:
        if v is None:
            pct.append(None)
            zone.append(None)
            value.append(None)
            continue
        rounded_pct = round(v / threshold * 100, 1)
        rounded_value = _round_value(v, metric)
        zone_input = rounded_value if metric == "hr" else rounded_pct
        pct.append(rounded_pct)
        zone.append(_zone(bounds, zone_input))
        value.append(rounded_value)
    result: dict[str, Any] = {
        "activity_id": detail.id,
        "source": "streams",
        "metric": metric,
        "grade_adjusted": grade_adjusted,
        "threshold": threshold,
        "duration_s": _duration_s(detail, len(stream.data)),
        "scale": _scale(pct, metric),
        "pct": pct,
        "zone": zone,
        "value": value,
    }
    elevation = _elevation(stream_by_type.get("altitude"), bucket_count)
    if elevation is not None:
        result["elevation"] = elevation
    return result


# Intervals (per-row) source


def _intervals_metric(
    sport: SportType | None, detail: IntervalsActivityDetail
) -> tuple[str, str, float | None]:
    """Pick the metric, source field, and threshold for the intervals path."""
    if sport is SportType.CYCLING:
        return "power", "average_watts", detail.icu_ftp
    if sport in (SportType.RUNNING, SportType.SWIMMING):
        return "pace", "average_speed", detail.threshold_pace
    return "hr", "average_heartrate", detail.lthr


def _intervals_trace(
    detail: IntervalsActivityDetail, sport: SportType | None
) -> dict[str, Any] | None:
    """Build a trace from ``icu_intervals`` rows, when streams are unusable.

    Args:
        detail: The activity, read for ``icu_intervals``.
        sport: The activity's planner sport (selects the metric).

    Returns:
        The ``source: "intervals"`` wire payload, or None when no row has a
        usable duration, or none of them yields a non-null ``pct``.
    """
    rows = [row for row in detail.icu_intervals if row.elapsed_time]
    if not rows:
        return None
    metric, field, threshold = _intervals_metric(sport, detail)
    total_elapsed = sum(row.elapsed_time or 0 for row in rows)
    pct: list[float | None] = []
    zone: list[int | None] = []
    value: list[float | int | None] = []
    frac: list[float] = []
    drawable = False
    for row in rows:
        raw_value: float | None = getattr(row, field)
        row_pct = _row_pct(row, raw_value, threshold)
        if row_pct is None:
            pct.append(None)
            zone.append(None)
            value.append(None)
        else:
            drawable = True
            pct.append(round(row_pct, 1))
            zone.append(row.zone)
            row_value = None if raw_value is None else _round_value(raw_value, metric)
            value.append(row_value)
        frac.append(round((row.elapsed_time or 0) / total_elapsed, 4))
    if not drawable:
        return None
    return {
        "activity_id": detail.id,
        "source": "intervals",
        "metric": metric,
        "grade_adjusted": False,
        "threshold": threshold,
        # The rows tile elapsed time, rests included, so that is the span.
        "duration_s": total_elapsed,
        "scale": _scale(pct, metric),
        "pct": pct,
        "zone": zone,
        "value": value,
        "frac": frac,
    }


def _row_pct(
    row: IntervalRow, raw_value: float | None, threshold: float | None
) -> float | None:
    """Compute one interval row's percent-of-threshold, per the contract."""
    if row.intensity is not None:
        return row.intensity
    if raw_value and threshold:
        return raw_value / threshold * 100
    return None
