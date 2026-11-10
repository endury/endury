"""Read a completed fitness test into the thresholds it measured.

The plan books one test per sport: a 30-minute run time trial, a 20-minute
ride time trial, and a 400 m + 200 m swim time trial. This finds the test
effort among intervals.icu's interval rows and turns it into the values
``set_athlete_thresholds`` takes, so the coach can offer them as an Apply
card. Nothing is written here.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from endury.coach.tools.activity_tools import _clock, _interval_view, _min_per_km
from endury.intervals.models import ActivityStream, IntervalRow, IntervalsActivityDetail
from endury.planner.foundations.types import SportType

#: The shortest interval read as a run or ride test effort, in seconds.
_MIN_EFFORT_SECONDS = 15 * 60

# intent: Coggan's field test: FTP is 95 % of the best 20-minute average
# power, the 5 % taking off what an hour cannot hold.
_FTP_SHARE_OF_20_MIN = 0.95

# intent: Friel reads a run's LTHR as the average heart rate of the 30-minute
# time trial's last 20 minutes, once the heart rate has settled.
_LTHR_WINDOW_SECONDS = 20 * 60

# intent: a 20-minute all-out effort's heart rate sits above threshold; the
# same 98 % of the best 20-minute heart rate that intervals.icu's own LTHR
# detection applies brings it back to the hour.
_LTHR_SHARE_OF_20_MIN_HR = 0.98


def _main_effort(reps: list[IntervalRow]) -> tuple[IntervalRow | None, str | None]:
    """The test effort: the longest work interval of 15 min or more.

    Falls back to the longest interval of any kind, with a note saying so,
    when no work interval is that long.

    Returns:
        The effort (None without any timed interval) and the fallback note.
    """
    timed = [rep for rep in reps if rep.elapsed_time]
    work = [
        rep
        for rep in timed
        if rep.type == "WORK" and (rep.elapsed_time or 0) >= _MIN_EFFORT_SECONDS
    ]
    if work:
        return max(work, key=lambda rep: rep.elapsed_time or 0), None
    if not timed:
        return None, None
    return max(timed, key=lambda rep: rep.elapsed_time or 0), (
        "No work interval of 15 minutes or more was marked, so this reads the "
        "longest interval; check it is the test effort."
    )


def _late_heartrate(
    effort: IntervalRow, streams: Sequence[ActivityStream]
) -> int | None:
    """The average heart rate of the effort's last 20 minutes, or None.

    Args:
        effort: The test effort, timed from the activity's start.
        streams: The activity's ``time`` and ``heartrate`` streams.
    """
    data = {stream.type: stream.data for stream in streams}
    time, heartrate = data.get("time") or [], data.get("heartrate") or []
    if (
        effort.start_time is None
        or not effort.elapsed_time
        or len(time) != len(heartrate)
    ):
        return None
    end = effort.start_time + effort.elapsed_time
    window = [
        bpm
        for second, bpm in zip(time, heartrate, strict=True)
        if second is not None and bpm and end - _LTHR_WINDOW_SECONDS <= second < end
    ]
    return round(statistics.fmean(window)) if window else None


def _run_readout(
    reps: list[IntervalRow], streams: Sequence[ActivityStream]
) -> dict[str, Any]:
    """Threshold pace and LTHR from a 30-minute run time trial."""
    effort, note = _main_effort(reps)
    if effort is None:
        return {"note": "intervals.icu found no intervals in this run."}
    # intent: a 30-minute solo time trial is run at about threshold pace, so
    # its average pace is the threshold pace. Without the heart-rate stream
    # LTHR falls back to the whole effort's average, a beat or two lower.
    suggested = {
        "threshold_pace_min_km": _min_per_km(effort.average_speed),
        "lthr_bpm": _late_heartrate(effort, streams) or effort.average_heartrate,
    }
    return _readout(SportType.RUNNING, [effort], suggested, note)


def _ride_readout(reps: list[IntervalRow]) -> dict[str, Any]:
    """FTP and LTHR from a 20-minute ride time trial."""
    effort, note = _main_effort(reps)
    if effort is None:
        return {"note": "intervals.icu found no intervals in this ride."}
    ftp = (
        round(_FTP_SHARE_OF_20_MIN * effort.average_watts)
        if effort.average_watts
        else None
    )
    lthr = (
        round(_LTHR_SHARE_OF_20_MIN_HR * effort.average_heartrate)
        if effort.average_heartrate
        else None
    )
    suggested = {"ftp_watts": ftp, "lthr_bpm": lthr}
    return _readout(SportType.CYCLING, [effort], suggested, note)


def _swim_readout(reps: list[IntervalRow]) -> dict[str, Any]:
    """CSS from the 400 m and 200 m time trials."""
    work = sorted(
        (
            rep
            for rep in reps
            if rep.type == "WORK" and rep.distance and rep.elapsed_time
        ),
        # Of equal lengths the faster one is the trial, not the warm-up.
        key=lambda rep: (rep.distance or 0.0, -(rep.elapsed_time or 0)),
        reverse=True,
    )[:2]
    if len(work) < 2:
        return {
            "note": (
                "The 400 m and 200 m efforts were not both found among the "
                "swim's work intervals, so there is no CSS to read."
            )
        }
    long, short = work
    metres = (long.distance or 0.0) - (short.distance or 0.0)
    seconds = float((long.elapsed_time or 0) - (short.elapsed_time or 0))
    if metres <= 0 or seconds <= 0:
        return {
            "efforts": [_interval_view(rep, SportType.SWIMMING, None) for rep in work],
            "note": "The two longest efforts are the same length, so there is no CSS.",
        }
    # intent: critical swim speed is the slope of distance over time between
    # the two trials, (400 - 200) m / (t400 - t200); per 100 m that is
    # (t400 - t200) / 2 for exact 400 m and 200 m efforts.
    suggested = {"css_min_100m": _clock(100.0 * seconds / metres)}
    return _readout(SportType.SWIMMING, work, suggested, None)


def _readout(
    sport: SportType,
    efforts: list[IntervalRow],
    suggested: dict[str, Any],
    note: str | None,
) -> dict[str, Any]:
    """Assemble the readout, dropping the values the efforts did not carry."""
    values = {key: value for key, value in suggested.items() if value is not None}
    result: dict[str, Any] = {
        "efforts": [_interval_view(rep, sport, None) for rep in efforts]
    }
    if values:
        result["suggested_thresholds"] = {"sport": sport.value, **values}
    else:
        note = "The test effort carried no pace, power or heart rate to read."
    if note:
        result["note"] = note
    return result


_READOUTS = {
    SportType.CYCLING.value: _ride_readout,
    SportType.SWIMMING.value: _swim_readout,
}


def read_fitness_test(
    sport: str,
    detail: IntervalsActivityDetail,
    streams: Sequence[ActivityStream] = (),
) -> dict[str, Any]:
    """Read a completed test activity into the thresholds it measured.

    Args:
        sport: The plan session's sport (``running``/``cycling``/
            ``swimming``).
        detail: The linked activity with its interval rows.
        streams: A run's ``time`` and ``heartrate`` streams, for LTHR.

    Returns:
        ``efforts`` (the intervals read), ``suggested_thresholds`` (the
        ``set_athlete_thresholds`` arguments) when a usable effort was
        found, and a ``note`` when something is missing or uncertain.
    """
    if sport == SportType.RUNNING.value:
        return _run_readout(detail.icu_intervals, streams)
    readout = _READOUTS.get(sport)
    if readout is None:
        return {"note": f"There is no readout for a {sport} test."}
    return readout(detail.icu_intervals)


__all__ = ["read_fitness_test"]
