"""Reps recounted from the power stream when dropouts hid them from intervals.icu.

intervals.icu detects intervals on the power it is given. A recording that
drops to 0 W for a few seconds while the rider keeps pedalling (seen at ERG
step-ups) splits a short rep, and the detector folds what is left of it into
the recovery: a 3 x 13 30/15 ride read 29 reps. When the activity is paired
with a planned workout of repeated power reps and intervals.icu's count
misses the plan, the reps are recounted from the stream with those dropouts
filled, at the level halfway between the plan's rep and recovery targets.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import activity_date
from endury.intervals.models import (
    ActivityStream,
    IntervalRow,
    IntervalsActivityDetail,
)

#: Longest run of 0 W while pedalling that reads as a dropout, not a stop.
MAX_DROPOUT_S = 5

#: A dip below the rep level this short stays inside the rep.
_MAX_DIP_S = 2

_STREAM_TYPES = ["time", "watts", "cadence", "heartrate"]


@dataclass(frozen=True)
class PlannedReps:
    """The repeated power reps a planned workout asks for.

    Attributes:
        count: Reps over every repeat block.
        seconds: The shortest planned rep.
        threshold_watts: Halfway between the lowest rep target and the
            highest recovery target.
    """

    count: int
    seconds: float
    threshold_watts: float


def _watts(step: dict[str, Any], bound: str, ftp: float) -> float | None:
    """Return one bound of a step's power target in watts, or None."""
    power = step.get("power") or {}
    value = power.get(bound, power.get("value"))
    if value is None:
        return None
    if power.get("units") == "%ftp":
        return float(value) * ftp / 100.0
    return float(value) if power.get("units") == "w" else None


def planned_reps(workout_doc: dict[str, Any] | None, ftp: float) -> PlannedReps | None:
    """Read the repeated power reps off a planned workout's parsed steps.

    Args:
        workout_doc: The paired event's ``workout_doc``.
        ftp: The FTP the activity was scored against.

    Returns:
        The plan's reps, or None when it repeats no power-targeted rep.
    """
    count, seconds, work_low, rest_high = 0, [], [], []
    for block in (workout_doc or {}).get("steps") or []:
        steps = [s for s in block.get("steps") or [] if _watts(s, "start", ftp)]
        if not block.get("reps") or len(steps) < 2:
            continue
        work = max(steps, key=lambda s: _watts(s, "start", ftp) or 0.0)
        rest = min(steps, key=lambda s: _watts(s, "start", ftp) or 0.0)
        count += int(block["reps"])
        seconds.append(float(work.get("duration") or 0))
        work_low.append(_watts(work, "start", ftp) or 0.0)
        rest_high.append(_watts(rest, "end", ftp) or 0.0)
    if not count or not min(seconds):
        return None
    return PlannedReps(count, min(seconds), (min(work_low) + max(rest_high)) / 2.0)


def fill_dropouts(
    watts: Sequence[float | None], cadence: Sequence[float | None]
) -> tuple[list[float], int]:
    """Fill short runs of 0 W recorded while the cadence says pedalling.

    Args:
        watts: The power stream.
        cadence: The cadence stream, sample for sample.

    Returns:
        The power with each dropout filled linearly from its neighbours,
        and how many dropouts were filled.
    """
    filled = [float(w or 0.0) for w in watts]

    def dropped(k: int) -> bool:
        return filled[k] <= 0.0 and k < len(cadence) and (cadence[k] or 0) > 0

    count, i = 0, 0
    while i < len(filled):
        if not dropped(i):
            i += 1
            continue
        j = i
        while j < len(filled) and dropped(j):
            j += 1
        if i > 0 and j < len(filled) and j - i <= MAX_DROPOUT_S:
            before, after = filled[i - 1], filled[j]
            for k in range(i, j):
                filled[k] = before + (after - before) * (k - i + 1) / (j - i + 1)
            count += 1
        i = j
    return filled, count


def _bouts(watts: list[float], threshold: float, min_s: float) -> list[tuple[int, int]]:
    """Return ``(start, end)`` sample ranges at or above the rep level."""
    bouts: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for i, value in enumerate([*watts, *[0.0] * (_MAX_DIP_S + 1)]):
        if value >= threshold:
            start = i if start is None else start
            end = i + 1
        elif start is not None and i - end >= _MAX_DIP_S:
            if end - start >= min_s:
                bouts.append((start, end))
            start = None
    return bouts


def _row(
    kind: str,
    span: tuple[int, int],
    streams: dict[str, list[Any]],
    watts: list[float],
    detail: IntervalsActivityDetail,
) -> IntervalRow:
    """Summarize one stretch of the ride as an interval row."""
    a, b = span
    time = streams["time"]
    seconds = int(time[b - 1] - time[a]) + 1
    hr = [x for x in (streams.get("heartrate") or [])[a:b] if x]
    cadence = [x for x in (streams.get("cadence") or [])[a:b] if x]
    avg = statistics.fmean(watts[a:b])
    percent = avg / float(detail.icu_ftp or 1) * 100.0
    zones = detail.icu_power_zones or []
    return IntervalRow(
        type=kind,
        start_time=int(time[a]),
        elapsed_time=seconds,
        moving_time=seconds,
        average_watts=round(avg),
        average_heartrate=round(statistics.fmean(hr)) if hr else None,
        max_heartrate=round(max(hr)) if hr else None,
        average_cadence=statistics.fmean(cadence) if cadence else None,
        intensity=round(percent),
        zone=1 + sum(1 for top in zones if percent > top) if zones else None,
    )


def _clock(seconds: float) -> str:
    """Format a rep length the way intervals.icu's recap does (``4m30s``)."""
    minutes, secs = divmod(round(seconds), 60)
    return (
        f"{minutes}m{secs}s"
        if minutes and secs
        else f"{minutes}m"
        if minutes
        else f"{secs}s"
    )


def recount(
    detail: IntervalsActivityDetail,
    plan: PlannedReps,
    streams: Sequence[ActivityStream],
) -> tuple[IntervalsActivityDetail, str] | None:
    """Recount the reps from the power stream with its dropouts filled.

    Args:
        detail: The activity as intervals.icu detected it.
        plan: The paired workout's reps.
        streams: The activity's time, watts, cadence and heart-rate streams.

    Returns:
        The activity with its reps, groups and recap recounted, and the
        note that says so — or None when the stream had no dropout or the
        recount is no closer to the plan than intervals.icu's.
    """
    by_type = {s.type: s.data for s in streams}
    if not by_type.get("watts") or len(by_type.get("time") or []) != len(
        by_type["watts"]
    ):
        return None
    watts, dropouts = fill_dropouts(by_type["watts"], by_type.get("cadence") or [])
    work = _bouts(watts, plan.threshold_watts, plan.seconds / 2.0)
    detected = sum(1 for row in detail.icu_intervals if row.type == "WORK")
    if not (dropouts and work) or abs(len(work) - plan.count) >= abs(
        detected - plan.count
    ):
        return None
    edges = [0, *[x for span in work for x in span], len(watts)]
    rows = [
        _row("WORK" if i % 2 else "RECOVERY", (a, b), by_type, watts, detail)
        for i, (a, b) in enumerate(pairwise(edges))
        if b > a
    ]
    reps = [row for row in rows if row.type == "WORK"]
    group = IntervalRow(
        count=len(reps),
        elapsed_time=round(statistics.fmean(r.elapsed_time or 0 for r in reps)),
        average_watts=round(statistics.fmean(r.average_watts or 0 for r in reps)),
        average_heartrate=round(statistics.fmean(hr))
        if (hr := [r.average_heartrate for r in reps if r.average_heartrate])
        else None,
        average_cadence=statistics.fmean(cad)
        if (cad := [r.average_cadence for r in reps if r.average_cadence])
        else None,
    )
    recap = f"{group.count}x {_clock(group.elapsed_time or 0)} {group.average_watts}w"
    note = (
        f"Reps counted from the power stream: {dropouts} short power dropouts "
        f"(0 W while pedalling) were filled in. intervals.icu read {detected} reps."
    )
    updated = {
        "icu_intervals": rows,
        "icu_groups": [group],
        "interval_summary": [recap],
    }
    return detail.model_copy(update=updated), note


def read_activity_detail(
    client: IntervalsClient, activity_id: str
) -> tuple[IntervalsActivityDetail, str | None]:
    """Read one activity, its reps recounted when dropouts hid some of them.

    The paired workout and the streams are read only when intervals.icu's
    rep count misses the plan.

    Args:
        client: The intervals.icu read client.
        activity_id: intervals.icu activity id.

    Returns:
        The activity and the note that its reps were recounted, or None.
    """
    detail = client.get_activity(activity_id)
    day = activity_date(detail)
    if detail.paired_event_id is None or day is None or not detail.icu_ftp:
        return detail, None
    events = client.list_events(day, day, category="WORKOUT")
    event = next((e for e in events if e.id == detail.paired_event_id), None)
    plan = planned_reps(event.workout_doc if event else None, detail.icu_ftp)
    detected = sum(1 for row in detail.icu_intervals if row.type == "WORK")
    if plan is None or detected == plan.count:
        return detail, None
    streams = client.get_activity_streams(activity_id, _STREAM_TYPES)
    return recount(detail, plan, streams) or (detail, None)
