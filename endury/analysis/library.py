"""The ``ana`` library: what the coach's analysis scripts call.

The coach model writes a short numpy script against these helpers; the
runner loads one payload (the athlete's history, thresholds and the streams
of the activities the call asked for) and runs the script with this module
as ``ana``. Every helper prints or returns compact rows, never whole arrays,
and owns its definition (what counts as uphill, walking, a fair comparison),
so two scripts asking the same question get the same answer.

Measured on the coach model (2026-10-25): with only arrays it chose its own
definitions and its verdicts flipped from run to run; with these helpers its
readings held.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from endury.analysis.charts import MAX_CHARTS, ChartError, make_spec

Array = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]
Row = dict[str, Any]

#: intervals.icu activity types by sport.
SPORTS: dict[str, str] = {
    "Run": "run",
    "TrailRun": "run",
    "VirtualRun": "run",
    "Ride": "ride",
    "VirtualRide": "ride",
    "MountainBikeRide": "ride",
    "GravelRide": "ride",
    "EBikeRide": "ride",
    "Swim": "swim",
    "OpenWaterSwim": "swim",
    "Hike": "hike",
    "Walk": "hike",
}

#: A run's cadence (steps per minute per leg) below this is walking.
WALK_CADENCE = 70
#: Below this many minutes a comparison is a small sample, and says so.
SMALL_SAMPLE_MIN = 10
GRADE_BANDS = (
    (-99, -10),
    (-10, -5),
    (-5, -2),
    (-2, 2),
    (2, 5),
    (5, 10),
    (10, 15),
    (15, 99),
)

history: list[Row] = []
athlete: Row = {}
CHARTS: list[dict[str, Any]] = []
_ACTIVITIES: dict[str, Row] = {}


def load(payload: Row) -> None:
    """Install one run's data: history, thresholds, activities; clear charts.

    Args:
        payload: ``history`` rows, ``athlete`` thresholds and ``activities``
            keyed by id, each with ``meta``, ``streams`` and ``laps``.
    """
    history[:] = list(payload.get("history") or [])
    athlete.clear()
    athlete.update(payload.get("athlete") or {})
    _ACTIVITIES.clear()
    _ACTIVITIES.update(payload.get("activities") or {})
    CHARTS.clear()


def sport_of(activity_type: str | None) -> str:
    """Return ``run``, ``ride``, ``swim``, ``hike`` or ``other`` for a type."""
    return SPORTS.get(activity_type or "", "other")


def pace(mps: float | None, per: int = 1000) -> str:
    """Speed in m/s as an ``m:ss`` pace per ``per`` metres (100 for a swim)."""
    if mps is None or not math.isfinite(mps) or mps <= 0.05:
        return "-"
    sec = per / mps
    return f"{int(sec // 60)}:{round(sec % 60) % 60:02d}"


def seconds(mps: Any, per: int = 1000) -> Any:
    """Speed in m/s as seconds per ``per`` metres, for a pace chart."""
    speed = np.asarray(mps, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(speed > 0.05, per / speed, np.nan)
    return float(out) if out.ndim == 0 else out


def smooth(x: Any, seconds_: int = 30) -> Array:
    """Moving average over ``seconds_`` samples, gaps filled, edges held."""
    arr = np.asarray(x, dtype=float)
    ok = np.isfinite(arr)
    if not ok.any():
        return arr
    if not ok.all():
        arr = np.interp(np.arange(len(arr)), np.flatnonzero(ok), arr[ok])
    k = max(1, int(seconds_))
    padded = np.pad(arr, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")


class Activity:
    """One activity's streams as arrays, one sample per recorded second.

    Attributes:
        meta: The history row: date, type, sport, name, km, minutes, climb.
        sport: ``run``, ``ride``, ``swim``, ``hike`` or ``other``.
        t: Seconds since the start.
        km, alt, hr, w, cad: Distance (km), altitude (m, smoothed), heart
            rate, power (W), cadence (rpm on a bike, steps per leg running,
            strokes in the water).
        speed, gap_speed: Speed and grade-adjusted speed (m/s).
        grade, temp: Gradient (%), the watch's temperature (°C).
        moving, walking: Boolean masks; walking is a run's cadence under 70.
        x, y: Position in metres east and north of a point all of this
            run's activities share (NaN without GPS).
        dt: Seconds each sample stands for; pauses count as nothing.
        lap_rows: intervals.icu's laps or detected intervals.
    """

    def __init__(self, meta: Row, streams: Row, laps: list[Row] | None = None) -> None:
        self.meta = meta
        self.sport = meta.get("sport") or sport_of(meta.get("type"))
        n = len(streams.get("time") or [])

        def stream(key: str) -> Array:
            values = streams.get(key)
            if not values or len(values) != n:
                return np.full(n, np.nan)
            return np.array([np.nan if v is None else v for v in values], dtype=float)

        self.t = stream("time")
        self.km = stream("distance") / 1000
        altitude = (
            stream("fixed_altitude")
            if streams.get("fixed_altitude")
            else stream("altitude")
        )
        self.alt = smooth(altitude, 30)
        self.hr, self.w, self.cad = (
            stream("heartrate"),
            stream("watts"),
            stream("cadence"),
        )
        self.speed, self.gap_speed = stream("velocity_smooth"), stream("ga_velocity")
        self.grade, self.temp = stream("grade_smooth"), stream("temp")
        # Metres east and north of the payload's shared origin; the sandbox
        # never sees the GPS coordinates themselves.
        self.x, self.y = stream("x"), stream("y")
        self.moving: Mask = np.nan_to_num(self.speed) > (
            0.1 if self.sport == "swim" else 0.3
        )
        is_run = self.sport in ("run", "hike")
        self.walking: Mask = (
            self.moving & (np.nan_to_num(self.cad, nan=999) < WALK_CADENCE)
            if is_run
            else np.zeros(n, bool)
        )
        self.dt = (
            np.clip(np.diff(self.t, append=self.t[-1] if n else 0), 0, 10)
            if n
            else np.zeros(0)
        )
        self.lap_rows = list(laps or [])

    def __repr__(self) -> str:
        """Return the activity's date, sport, name and distance."""
        m = self.meta
        return f"<{m.get('date')} {self.sport} {m.get('name')} {m.get('km')} km>"


def activity(activity_id: str) -> Activity:
    """Return an activity the call listed in ``activity_ids``.

    Raises:
        LookupError: The activity was not passed in ``activity_ids``.
    """
    data = _ACTIVITIES.get(activity_id)
    if data is None:
        raise LookupError(f"{activity_id} is not loaded: list it in activity_ids")
    return Activity(
        data.get("meta") or {"id": activity_id},
        data.get("streams") or {},
        data.get("laps"),
    )


def _wmean(x: Array, weights: Array) -> float:
    """Weighted mean over the finite values."""
    ok = np.isfinite(x) & (weights > 0)
    return (
        float(np.sum(x[ok] * weights[ok]) / np.sum(weights[ok]))
        if ok.any()
        else math.nan
    )


def _r(value: float, digits: int = 1) -> float | None:
    """Round a float; None when it is not finite."""
    return (
        round(float(value), digits)
        if value is not None and math.isfinite(value)
        else None
    )


def _all(a: Activity) -> Mask:
    return np.ones(len(a.t), dtype=bool)


def window(a: Activity, from_km: float, to_km: float) -> Mask:
    """Mask of the samples between two distances (km)."""
    return (a.km >= from_km) & (a.km < to_km)


def between(a: Activity, from_min: float, to_min: float) -> Mask:
    """Mask of the samples between two times (minutes from the start)."""
    return (a.t >= from_min * 60) & (a.t < to_min * 60)


def halves(a: Activity) -> tuple[Mask, Mask]:
    """Masks of the first and second half, split by distance (by time in the water)."""
    if a.sport == "swim" or not np.isfinite(a.km).any():
        mid = a.t[-1] / 2
        return a.t < mid, a.t >= mid
    mid = np.nanmax(a.km) / 2
    return a.km < mid, a.km >= mid


def quarters(a: Activity) -> list[Mask]:
    """Four masks by elapsed time."""
    end = a.t[-1] if len(a.t) else 0
    return [
        (a.t >= end * i / 4) & ((a.t < end * (i + 1) / 4) | (i == 3)) for i in range(4)
    ]


def uphill(a: Activity, min_grade: float = 5) -> Mask:
    """Moving samples at or above ``min_grade`` percent."""
    return a.moving & (a.grade >= min_grade)


def downhill(a: Activity, min_grade: float = 5) -> Mask:
    """Moving samples at or below minus ``min_grade`` percent."""
    return a.moving & (a.grade <= -min_grade)


def _climb(a: Activity, mask: Mask) -> tuple[float, float]:
    """Metres up and down inside a mask."""
    if len(a.alt) < 2:
        return 0.0, 0.0
    d = np.diff(a.alt)
    both = mask[1:] & mask[:-1] & np.isfinite(d)
    return float(np.sum(np.clip(d[both], 0, None))), float(
        -np.sum(np.clip(d[both], None, 0))
    )


def _normalized_power(w: Array) -> float:
    """Normalized power: the fourth-power mean of 30-second power.

    Takes the selected samples only, in order; a gap counts as zero watts.
    """
    if np.isfinite(w).sum() < 30:
        return math.nan
    rolled = smooth(np.nan_to_num(w), 30)
    return float(np.mean(rolled**4) ** 0.25)


def summary(a: Activity, mask: Mask | None = None) -> Row:
    """Time, distance, climbing, pace or speed, HR and power over a mask.

    A run reports ``pace`` and grade-adjusted ``gap_pace`` per km and the
    share walked; a ride ``kmh`` and normalized power ``np``; a swim
    ``pace_100m``.
    """
    mask = _all(a) if mask is None else mask
    weights = a.dt * (mask & a.moving)
    moving_s = float(np.sum(weights))
    hr, w = _wmean(a.hr, weights), _wmean(a.w, weights)
    speed = _wmean(a.speed, weights)
    up, _ = _climb(a, mask)
    row: Row = {
        "minutes": _r(np.sum(a.dt[mask]) / 60),
        "km": _r(np.nansum(np.nan_to_num(a.speed) * weights) / 1000, 2),
    }
    if a.sport != "swim":
        row["climb_m"] = round(up)
    if a.sport in ("run", "hike", "other"):
        row["pace"] = pace(speed)
        row["gap_pace"] = pace(_wmean(a.gap_speed, weights))
    elif a.sport == "ride":
        row["kmh"] = _r(speed * 3.6)
    else:
        row["pace_100m"] = pace(speed, 100)
    row["hr"] = _r(hr)
    if math.isfinite(w):
        row["w"] = _r(w)
        if a.sport == "ride":
            row["np"] = _r(_normalized_power(a.w[mask & a.moving]), 0)
        row["w_per_hr"] = round(w / hr, 2) if math.isfinite(hr) and hr else None
    if a.sport in ("run", "hike"):
        row["walked_pct"] = _r(
            100 * float(np.sum(a.dt[mask & a.walking])) / max(moving_s, 1)
        )
    if np.isfinite(a.temp[mask]).any():
        row["temp"] = _r(float(np.nanmean(a.temp[mask])))
    return row


def table(rows: Sequence[Row], cols: Sequence[str] | None = None) -> None:
    """Print rows of dicts as an aligned table."""
    if not rows:
        print("(no rows)")
        return
    # Every key any row has, in the order they first appear.
    cols = list(cols or dict.fromkeys(k for r in rows for k in r))
    cells = [["-" if r.get(c) is None else str(r.get(c)) for c in cols] for r in rows]
    width = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("  ".join(c.rjust(width[i]) for i, c in enumerate(cols)))
    for row in cells:
        print("  ".join(v.rjust(width[i]) for i, v in enumerate(row)))


def compare(a: Activity, masks: Sequence[Mask], labels: Sequence[str]) -> list[Row]:
    """Print one summary row per mask, with the minutes behind each figure.

    Flags any part under ten minutes as a small sample.
    """
    rows = [
        {"part": label, **summary(a, m)}
        for label, m in zip(labels, masks, strict=False)
    ]
    table(rows)
    small = [r["part"] for r in rows if (r["minutes"] or 0) < SMALL_SAMPLE_MIN]
    if small:
        print(f"small sample (under {SMALL_SAMPLE_MIN} minutes): {', '.join(small)}")
    return rows


def splits(
    a: Activity, every_km: float | None = None, every_min: float | None = None
) -> list[Row]:
    """One row per ``every_km`` of distance, or per ``every_min`` of time."""
    rows: list[Row] = []
    if every_min:
        end, step = a.t[-1] / 60, float(every_min)
        start = 0.0
        while start < end - 0.05:
            rows.append(
                {
                    "from_min": round(start),
                    "to_min": round(min(start + step, end)),
                    **summary(a, between(a, start, start + step)),
                }
            )
            start += step
        return rows
    end = float(np.nanmax(a.km)) if np.isfinite(a.km).any() else 0.0
    step = float(every_km or 1)
    start = 0.0
    while start < end - 0.05:
        rows.append(
            {
                "from_km": round(start, 2),
                "to_km": round(min(start + step, end), 2),
                **summary(a, window(a, start, start + step)),
            }
        )
        start += step
    return rows


def climbs(a: Activity, min_gain_m: float = 40, dip_m: float = 10) -> list[Row]:
    """Every ascent of at least ``min_gain_m``, allowing dips of ``dip_m``.

    Each row carries the climbing rate (``vam``, metres per hour) and the
    mean gradient: compare rates only between climbs of similar gradient.
    """
    alt, n = a.alt, len(a.alt)
    rows: list[Row] = []
    for lo, hi in _runs(alt, 1, min_gain_m, dip_m):
        m = np.zeros(n, dtype=bool)
        m[lo : hi + 1] = True
        s = summary(a, m)
        gain = float(alt[hi] - alt[lo])
        run_m = float(a.km[hi] - a.km[lo]) * 1000
        minutes = s["minutes"] or 0.1
        rows.append(
            {
                "n": len(rows) + 1,
                "from_km": _r(a.km[lo], 2),
                "to_km": _r(a.km[hi], 2),
                "climb_m": round(gain),
                "grade_pct": _r(100 * gain / max(run_m, 1)),
                "vam": round(gain / minutes * 60),
                **{k: v for k, v in s.items() if k not in ("km", "climb_m")},
            }
        )
    return rows


def _runs(
    alt: Array, sign: int, min_gain_m: float, dip_m: float = 10
) -> list[tuple[int, int]]:
    """Index pairs of every ascent (sign 1) or descent (sign -1) of a size.

    A run tolerates a reverse of ``dip_m`` and starts where the road leaves
    its base, not on the flat before.
    """
    z, n, i = alt * sign, len(alt), 0
    out: list[tuple[int, int]] = []
    while i < n - 1:
        j, lo, hi = i, i, i
        while j < n - 1 and z[j + 1] >= z[hi] - dip_m:
            j += 1
            if z[j] > z[hi]:
                hi = j
        if z[hi] - z[lo] >= min_gain_m:
            lo += int(np.flatnonzero(z[lo : hi + 1] <= z[lo] + 2.0)[-1])
            out.append((lo, hi))
            i = hi + 1
        else:
            i = max(j, i + 1)
    return out


def by_grade(a: Activity, mask: Mask | None = None) -> list[Row]:
    """One row per gradient band (moving samples only)."""
    mask = _all(a) if mask is None else mask
    rows: list[Row] = []
    for lo, hi in GRADE_BANDS:
        m = mask & (a.grade >= lo) & (a.grade < hi)
        if not (m & a.moving).any():
            continue
        name = (
            f"under {hi}%"
            if lo == -99
            else f"over {lo}%"
            if hi == 99
            else f"{lo} to {hi}%"
        )
        rows.append({"grade": name, **summary(a, m)})
    return rows


def laps(a: Activity, work_only: bool = False) -> list[Row]:
    """The laps or intervals intervals.icu found, one row each.

    A swim reports metres and pace per 100 m; a run pace per km; a ride
    speed. ``work_only`` keeps the efforts and drops the rests.
    """
    rows: list[Row] = []
    per = 100 if a.sport == "swim" else 1000
    for lap in a.lap_rows:
        kind = str(lap.get("type") or "").lower()
        if work_only and kind not in ("work", ""):
            continue
        dist = lap.get("distance") or 0
        speed = lap.get("average_speed")
        row: Row = {
            "n": len(rows) + 1,
            "type": kind or "lap",
            "start_min": _r((lap.get("start_time") or 0) / 60),
            "minutes": _r(
                (lap.get("moving_time") or lap.get("elapsed_time") or 0) / 60, 2
            ),
        }
        if a.sport == "swim":
            row["m"] = round(dist)
            row["pace_100m"] = pace(speed, per)
        else:
            row["km"] = _r(dist / 1000, 2)
            if a.sport == "ride":
                row["kmh"] = _r(speed * 3.6) if speed else None
            else:
                row["pace"] = pace(speed)
        row["hr"] = lap.get("average_heartrate")
        if lap.get("average_watts") is not None:
            row["w"] = lap.get("average_watts")
        rows.append(row)
    return rows


def lap_sets(a: Activity) -> list[Row]:
    """The work laps grouped by distance: how many, and how even they were.

    One row per distance (a swim's 100s, a run's 1 km reps): the mean,
    fastest and slowest pace and the spread (standard deviation, seconds).
    """
    per = 100 if a.sport == "swim" else 1000
    groups: dict[int, list[float]] = {}
    for lap in a.lap_rows:
        speed, dist = lap.get("average_speed"), lap.get("distance") or 0
        if str(lap.get("type") or "work").lower() == "work" and speed and dist:
            groups.setdefault(round(dist / 5) * 5, []).append(per / speed)
    rows: list[Row] = []
    for dist, secs in sorted(groups.items()):
        arr = np.array(secs)
        rows.append(
            {
                "distance_m": dist,
                "count": len(arr),
                "mean": pace(per / arr.mean(), per),
                "fastest": pace(per / arr.min(), per),
                "slowest": pace(per / arr.max(), per),
                "spread_s": _r(float(arr.std()), 1),
            }
        )
    return rows


def best_efforts(a: Activity, minutes: Sequence[float] = (1, 5, 20, 60)) -> list[Row]:
    """The best average power (and a run's best pace) for each duration."""
    rows: list[Row] = []
    for mins in minutes:
        k = int(mins * 60)
        if k > len(a.t) or k < 1:
            continue
        row: Row = {"minutes": mins}
        if np.isfinite(a.w).sum() >= k:
            rolled = np.convolve(np.nan_to_num(a.w), np.ones(k) / k, mode="valid")
            row["best_w"] = round(float(rolled.max()))
            row["at_km"] = _r(a.km[int(rolled.argmax())], 1)
        if a.sport in ("run", "hike") and np.isfinite(a.speed).sum() >= k:
            rolled = np.convolve(np.nan_to_num(a.speed), np.ones(k) / k, mode="valid")
            row["best_pace"] = pace(float(rolled.max()))
        rows.append(row)
    return rows


def decoupling(a: Activity, mask: Mask | None = None) -> Row:
    """Output per heartbeat in the first and second half of the moving time.

    Output is power where there is power, else grade-adjusted speed. A drop
    of more than about 5 % is the usual sign of aerobic drift.
    """
    mask = (_all(a) if mask is None else mask) & a.moving & np.isfinite(a.hr)
    idx = np.flatnonzero(mask)
    if len(idx) < 120:
        return {"note": "too little heart-rate data"}
    first, second = np.zeros_like(mask), np.zeros_like(mask)
    first[idx[: len(idx) // 2]] = True
    second[idx[len(idx) // 2 :]] = True
    output = (
        a.w
        if np.isfinite(a.w[mask]).mean() > 0.8
        else np.where(np.isfinite(a.gap_speed), a.gap_speed, a.speed) * 60
    )
    unit = "W per beat" if output is a.w else "m/min per beat"
    ef = [_wmean(output, a.dt * m) / _wmean(a.hr, a.dt * m) for m in (first, second)]
    change = 100 * (ef[1] - ef[0]) / ef[0] if ef[0] else math.nan
    row = {
        "first_half": _r(ef[0], 3),
        "second_half": _r(ef[1], 3),
        "change_pct": _r(change),
        "unit": unit,
    }
    print(
        f"output per beat ({unit}): first half {row['first_half']}, "
        f"second half {row['second_half']}, change {row['change_pct']}%"
    )
    return row


def walk_vs_run(a: Activity, min_grade: float = 10) -> list[Row]:
    """Walking against running on the same gradient, with the trade-off.

    Prints how much faster running climbed and how much more power it took,
    so the choice is read on power, not on heart rate alone (heart rate lags
    and averages out between the two).
    """
    up = uphill(a, min_grade)
    rise = np.clip(np.diff(a.alt, append=a.alt[-1] if len(a.alt) else 0), 0, None)
    rows: list[Row] = []
    for label, m in (("walking", up & a.walking), ("running", up & ~a.walking)):
        mins = float(np.sum(a.dt[m]) / 60)
        if mins <= 0:
            continue
        s = summary(a, m)
        rows.append(
            {
                "how": label,
                "minutes": round(mins, 1),
                "pace": s.get("pace"),
                "climb_rate_m_h": round(float(np.sum(rise[m])) / (mins / 60)),
                "hr": s.get("hr"),
                "w": s.get("w"),
            }
        )
    print(f"gradient {min_grade}% and steeper")
    table(rows)
    if len(rows) == 2 and rows[0]["w"] and rows[1]["w"]:
        faster = 100 * (
            rows[1]["climb_rate_m_h"] / max(rows[0]["climb_rate_m_h"], 1) - 1
        )
        dearer = 100 * (rows[1]["w"] / rows[0]["w"] - 1)
        print(
            f"trade-off: running climbed {faster:.0f}% faster for {dearer:.0f}% "
            f"more power; walking climbed {faster:.0f}% slower for {dearer:.0f}% less"
        )
    return rows


def trend(
    dates: Sequence[str],
    values: Sequence[float],
    label: str = "",
    higher_is_better: bool = True,
) -> Row:
    """Fit a straight line through dated values and say whether it is real.

    The verdict compares the fitted change over the period with the usual
    scatter between sessions: a change smaller than the scatter is no clear
    trend.
    """
    pairs = [
        (np.datetime64(str(d)[:10]), float(v))
        for d, v in zip(dates, values, strict=False)
        if v is not None and math.isfinite(float(v))
    ]
    if len(pairs) < 4:
        return {"n": len(pairs), "verdict": "too few points for a trend"}
    days = np.array([(d - pairs[0][0]).astype(int) for d, _ in pairs], dtype=float)
    y = np.array([v for _, v in pairs])
    slope, intercept = np.polyfit(days, y, 1)
    fitted = slope * days + intercept
    spread = float(np.std(y - fitted))
    change = float(fitted[-1] - fitted[0])
    if abs(change) < spread:
        verdict = (
            "no clear trend: the change is smaller than the usual scatter "
            "between sessions"
        )
    else:
        better = (change > 0) == higher_is_better
        way = "rising" if change > 0 else "falling"
        verdict = f"{way} ({'better' if better else 'worse'}), larger than the scatter"
    # The sessions an answer quotes to show the scatter, printed so it never
    # reads them off a bare tuple (Seen live: 1.43 quoted as 3.4).
    low, high = (
        {"date": str(pairs[int(i)][0]), "value": _r(pairs[int(i)][1], 3)}
        for i in (np.argmin(y), np.argmax(y))
    )
    row = {
        "n": len(y),
        "start": _r(fitted[0], 3),
        "end": _r(fitted[-1], 3),
        "change_pct": _r(100 * change / fitted[0]) if fitted[0] else None,
        "scatter": _r(spread, 3),
        "lowest": low,
        "highest": high,
        "verdict": verdict,
    }
    print(
        f"trend {label}: {row['n']} points, fitted {row['start']} -> {row['end']} "
        f"({row['change_pct']}%), scatter {row['scatter']}, lowest "
        f"{low['value']} on {low['date']}, highest {high['value']} on "
        f"{high['date']}: {verdict}"
    )
    return row


def weekly(value: str = "hours", weeks: int = 8) -> list[Row]:
    """Totals per week (Monday to Sunday) and sport from the history.

    ``value`` is ``hours``, ``km`` or ``load``.
    """
    if not history:
        return []
    last = max(np.datetime64(str(r["date"])[:10]) for r in history)
    monday = last - (last.astype("datetime64[D]").view("int64") - 4) % 7
    rows: list[Row] = []
    for w in range(weeks - 1, -1, -1):
        start = monday - np.timedelta64(7 * w, "D")
        row: Row = {"week": str(start)}
        for sport in ("swim", "ride", "run", "other"):
            row[sport] = 0.0
        for r in history:
            day = np.datetime64(str(r["date"])[:10])
            if start <= day < start + np.timedelta64(7, "D"):
                key = str(r.get("sport"))
                if key not in ("swim", "ride", "run"):
                    key = "other"
                amount = {
                    "hours": (r.get("moving_min") or 0) / 60,
                    "km": r.get("km") or 0,
                    "load": r.get("load") or 0,
                }[value]
                row[key] = round(row[key] + amount, 1)
        rows.append(row)
    table(rows)
    return rows


# Routes: the same ground in two files

#: A GPS track is resampled every this many metres of its own path, so two
#: files compare without trusting either device's odometer.
ROUTE_STEP_M = 10.0
#: Points closer than this, heading the same way, are the same ground.
MATCH_RADIUS_M = 25.0
#: Shared ground shorter than this is not a section.
MIN_SECTION_M = 300.0
#: Level stretches between climbs and descents count from this length.
MIN_FLAT_M = 1000.0
#: A climb or descent averages at least this gradient (%); a long rolling
#: stretch that merely ends lower is level ground for comparing.
MIN_SLOPE_PCT = 1.5
#: How many of the other track's points one step may advance its match: a
#: wiggly GPS path is longer than the same ground walked straighter.
_AHEAD = 3
#: A shared stretch survives this many points of GPS gap (trees, a
#: switchback cut differently) ...
_MAX_GAP = 10
#: ... and the other file covering this many points more ground at a spot.
#: Measured on two runs of one trail loop: 9 to 25 points.
_MAX_JUMP = 20


class _Track:
    """A GPS path resampled every :data:`ROUTE_STEP_M` metres."""

    def __init__(self, s: Array, x: Array, y: Array, t: Array, alt: Array) -> None:
        self.s, self.x, self.y, self.t, self.alt = s, x, y, t, alt
        if len(s) > 2:
            # Heading from a 50 m smoothed line: GPS jitter flips raw headings.
            self.h = np.arctan2(np.gradient(smooth(y, 5)), np.gradient(smooth(x, 5)))
        else:
            self.h = np.zeros(len(s))

    @classmethod
    def of(cls, a: Activity) -> _Track:
        """The track of an activity; empty without GPS."""
        ok = np.isfinite(a.x) & np.isfinite(a.y) & np.isfinite(a.t)
        t, alt = a.t[ok], np.nan_to_num(a.alt[ok])
        if ok.sum() < 2:
            return cls(*(np.zeros(0) for _ in range(5)))
        # Five seconds smoothed, so standing jitter does not lengthen the path.
        x, y = smooth(a.x[ok], 5), smooth(a.y[ok], 5)
        path = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        keep = np.concatenate([[True], np.diff(path) > 0.01])
        path, x, y, t, alt = path[keep], x[keep], y[keep], t[keep], alt[keep]
        s = np.arange(0.0, path[-1], ROUTE_STEP_M)
        return cls(s, *(np.interp(s, path, v) for v in (x, y, t, alt)))

    def part(self, i0: int, i1: int) -> _Track:
        """The points from ``i0`` to ``i1``, both included."""
        cut = slice(i0, i1 + 1)
        return _Track(self.s[cut], self.x[cut], self.y[cut], self.t[cut], self.alt[cut])

    def time_at(self, index: Array) -> Array:
        """The clock at (fractional) point indices."""
        return np.interp(index, np.arange(len(self.t)), self.t)


def _nearest(q: _Track, r: _Track) -> npt.NDArray[np.int64]:
    """For each point of ``q``, its point of ``r`` on the same ground.

    Same ground is within :data:`MATCH_RADIUS_M`, heading the same way; a
    point with none gets -1. Where the other track passes the same spot
    twice (a loop crossing itself, an out-and-back), the match that carries
    on from the previous one wins over the nearest.
    """
    size = MATCH_RADIUS_M
    grid: dict[tuple[int, int], list[int]] = {}
    for i, key in enumerate(
        zip(
            (r.x // size).astype(int).tolist(),
            (r.y // size).astype(int).tolist(),
            strict=True,
        )
    ):
        grid.setdefault(key, []).append(i)
    out = np.full(len(q.s), -1, dtype=np.int64)
    qx, qy = (q.x // size).astype(int).tolist(), (q.y // size).astype(int).tolist()
    pi = pj = -1
    for i in range(len(q.s)):
        near = [
            k
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for k in grid.get((qx[i] + dx, qy[i] + dy), ())
        ]
        if not near:
            continue
        c = np.asarray(near)
        d = np.hypot(r.x[c] - q.x[i], r.y[c] - q.y[i])
        ok = (d < size) & (np.cos(r.h[c] - q.h[i]) > 0.5)
        if not ok.any():
            continue
        c, d = c[ok], d[ok]
        if pj >= 0:
            on = (c - pj >= -2) & (c - pj <= _AHEAD * (i - pi) + _MAX_JUMP)
            if on.any():
                c, d = c[on], d[on]
        out[i] = pj = int(c[np.argmin(d)])
        pi = i
    return out


def _sections(
    j: npt.NDArray[np.int64], min_m: float = MIN_SECTION_M, max_gap: int = _MAX_GAP
) -> list[tuple[int, int]]:
    """Runs of points whose match moves forward along the other track.

    A run survives a few unmatched points (GPS dropouts) and ends where the
    match jumps or turns back: the paths parted.
    """
    out: list[tuple[int, int]] = []
    start: int | None = None
    pi = pj = -1
    for i, jj in enumerate(j.tolist()):
        if jj < 0:
            if start is not None and i - pi - 1 > max_gap:
                out.append((start, pi))
                start = None
            continue
        if start is not None and (
            i - pi - 1 > max_gap or not -2 <= jj - pj <= _AHEAD * (i - pi) + _MAX_JUMP
        ):
            out.append((start, pi))
            start = None
        if start is None:
            start = i
        pi, pj = i, jj
    if start is not None:
        out.append((start, pi))
    return [(a, b) for a, b in out if (b - a) * ROUTE_STEP_M >= min_m]


def _filled(j: npt.NDArray[np.int64], i0: int, i1: int) -> Array:
    """The match over a section, its unmatched points interpolated."""
    idx = np.arange(i0, i1 + 1)
    part = j[i0 : i1 + 1]
    ok = part >= 0
    filled: Array = np.interp(idx, idx[ok], part[ok].astype(float))
    return filled


def _terrain(
    tr: _Track, i0: int, i1: int, min_gain_m: float
) -> list[tuple[str, int, int]]:
    """A section cut into climbs, descents and the level stretches between."""
    alt = tr.alt[i0 : i1 + 1]

    def steep(lo: int, hi: int) -> bool:
        length = (hi - lo) * ROUTE_STEP_M
        return bool(abs(alt[hi] - alt[lo]) >= MIN_SLOPE_PCT / 100 * length)

    parts = [
        ("climb", i0 + lo, i0 + hi)
        for lo, hi in _runs(alt, 1, min_gain_m)
        if steep(lo, hi)
    ]
    parts += [
        ("descent", i0 + lo, i0 + hi)
        for lo, hi in _runs(alt, -1, min_gain_m)
        if steep(lo, hi)
    ]
    out: list[tuple[str, int, int]] = []
    k = i0
    for kind, lo, hi in sorted(parts, key=lambda p: p[1]):
        if lo < k:
            continue
        if (lo - k) * ROUTE_STEP_M >= MIN_FLAT_M:
            out.append(("flat", k, lo))
        out.append((kind, lo, hi))
        k = hi
    if (i1 - k) * ROUTE_STEP_M >= MIN_FLAT_M:
        out.append(("flat", k, i1))
    return out


def _effort(
    a: Activity, t0: float, t1: float, length_m: float, gain_m: float, kind: str
) -> Row:
    """One file's time and effort over one stretch of shared ground."""
    m = (a.t >= t0) & (a.t <= t1)
    s = summary(a, m)
    secs = max(float(t1 - t0), 1.0)
    speed = length_m / secs
    row: Row = {"min": round(secs / 60, 2)}
    if a.sport in ("run", "hike"):
        row["pace"] = pace(speed)
    elif a.sport == "swim":
        row["pace_100m"] = pace(speed, 100)
    else:
        row["kmh"] = _r(speed * 3.6)
    if kind == "climb":
        row["vam"] = round(gain_m / secs * 3600)
    if kind == "descent" and a.sport == "ride" and np.isfinite(a.speed[m]).any():
        row["max_kmh"] = _r(float(np.nanmax(a.speed[m])) * 3.6)
    row["hr"] = s.get("hr")
    for key, name in (("w", "w"), ("walked_pct", "walked")):
        if key in s:
            row[name] = s[key]
    return row


def _label(a: Activity) -> str:
    return f"{a.meta.get('date')} {a.meta.get('name')}"


def compare_route(a: Activity, b: Activity, min_gain_m: float = 30) -> list[Row]:
    """Compare two files over the ground they share, climb by climb.

    Finds the stretches both covered in the same direction from their GPS
    tracks (another start, a detour or a longer loop is fine), cuts them
    into climbs, descents and level stretches from a's elevation, and times
    both files over the same ground: ``a_min`` and ``b_min``, ``change_pct``
    (+ when b took longer), speed or pace, ``vam`` on a climb, ``max_kmh``
    on a ride's descent, heart rate, power and walking; ``b_extra_m`` when b
    covered more ground there (a detour), so its time is not like for like.
    ``km`` is the position along a's track. No shared ground means another
    route: compare by gradient band instead (``ana.by_grade`` on each).

    Returns:
        The rows it printed, a list of dicts; draw them with
        :func:`chart_route`.
    """
    ta, tb = _Track.of(a), _Track.of(b)
    if len(ta.s) < 10 or len(tb.s) < 10:
        print("no GPS track in", _label(a) if len(ta.s) < 10 else _label(b))
        return []
    j = _nearest(ta, tb)
    sections = _sections(j)
    shared = sum(i1 - i0 for i0, i1 in sections) * ROUTE_STEP_M
    back = _Track(tb.s, tb.x[::-1], tb.y[::-1], tb.t[::-1], tb.alt[::-1])
    reverse = sum(i1 - i0 for i0, i1 in _sections(_nearest(ta, back))) * ROUTE_STEP_M
    print(
        f"a = {_label(a)}, b = {_label(b)}: {shared / 1000:.1f} km shared of "
        f"a's {ta.s[-1] / 1000:.1f} km, in {len(sections)} stretch(es)"
        + (
            f"; {reverse / 1000:.1f} km more the other way round, not compared"
            if reverse
            else ""
        )
    )
    if not sections:
        print("no shared ground: another route, compare by gradient band instead")
        return []
    rows: list[Row] = []
    for i0, i1 in sections:
        jf = _filled(j, i0, i1)
        for kind, k0, k1 in _terrain(ta, i0, i1, min_gain_m):
            length = (k1 - k0) * ROUTE_STEP_M
            rise = np.diff(ta.alt[k0 : k1 + 1])
            gain = float(np.sum(np.clip(rise, 0, None)))
            tb0, tb1 = tb.time_at(np.array([jf[k0 - i0], jf[k1 - i0]]))
            ea = _effort(a, ta.t[k0], ta.t[k1], length, gain, kind)
            eb = _effort(b, tb0, tb1, length, gain, kind)
            row: Row = {
                "n": len(rows) + 1,
                "kind": kind,
                "km": f"{ta.s[k0] / 1000:.1f}-{ta.s[k1] / 1000:.1f}",
                "length_m": round(length),
                "grade_pct": _r(100 * float(ta.alt[k1] - ta.alt[k0]) / max(length, 1)),
                "a_min": ea.pop("min"),
                "b_min": eb.pop("min"),
            }
            row["change_pct"] = _r(100 * (row["b_min"] / row["a_min"] - 1))
            for key in dict.fromkeys([*ea, *eb]):
                row[f"a_{key}"], row[f"b_{key}"] = ea.get(key), eb.get(key)
            # b went further here (a detour, a hairpin cut differently): its
            # time covers more ground, which the row says out loud.
            extra = (jf[k1 - i0] - jf[k0 - i0]) * ROUTE_STEP_M - length
            if extra > max(50.0, 0.1 * length):
                row["b_extra_m"] = round(extra)
            rows.append(row)
    table(rows, _column_order(rows))
    return rows


#: A compare_route value as a chart title names it.
_ROUTE_WORDS = {
    "min": "Time",
    "kmh": "Speed",
    "max_kmh": "Top speed",
    "vam": "Climbing speed",
    "hr": "Heart rate",
    "w": "Power",
    "walked": "Walking",
    "pace": "Pace",
    "pace_100m": "Pace",
}
#: A compare_route value and the unit its chart reads in.
_ROUTE_UNITS = {
    "min": "min",
    "kmh": "km/h",
    "max_kmh": "km/h",
    "vam": "m/h",
    "hr": "bpm",
    "w": "W",
    "walked": "%",
    "pace": "min/km",
    "pace_100m": "min/100m",
}


def _short_date(a: Activity) -> str:
    """``25 Jul`` from the activity's date."""
    day = str(a.meta.get("date") or "")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    if len(day) < 10:
        return day or str(a.meta.get("id"))
    return f"{int(day[8:10])} {months[int(day[5:7]) - 1]}"


def chart_route(
    rows: Sequence[Row],
    a: Activity,
    b: Activity,
    value: str = "min",
    kinds: Sequence[str] = ("climb", "descent", "flat"),
    title: str = "",
) -> None:
    """Draw a :func:`compare_route` result: two bars per piece, one colour per file.

    Args:
        rows: What ``compare_route(a, b)`` returned.
        a: The first file, the first colour.
        b: The second file.
        value: ``min``, ``kmh``, ``pace``, ``vam``, ``max_kmh``, ``hr``,
            ``w`` or ``walked``.
        kinds: Which pieces to draw, e.g. ``("climb",)``.
        title: The chart's title; one is made when empty.
    """
    if value not in _ROUTE_UNITS:
        raise ChartError(f"value must be one of {sorted(_ROUTE_UNITS)}")
    picked = [
        r for r in rows if r.get("kind") in kinds and r.get(f"a_{value}") is not None
    ]
    if not picked:
        raise ChartError(f"no {'/'.join(kinds)} rows with {value} to draw")

    def number(v: Any) -> float | None:
        if isinstance(v, str):
            whole, _, sec = v.partition(":")
            return int(whole) * 60 + int(sec) if sec else None
        return None if v is None else float(v)

    def grade(g: float) -> str:
        return "0%" if round(g) == 0 else f"{round(g):+d}%"

    names = [
        f"km {float(str(r['km']).split('-')[0]):.0f} · {grade(r['grade_pct'])}"
        for r in picked
    ]
    ys = [[number(r.get(f"{side}_{value}")) for r in picked] for side in ("a", "b")]
    labels = [_short_date(a), _short_date(b)]
    one = len(kinds) == 1 and kinds[0] in ("climb", "descent")
    piece = kinds[0] if one else "stretch"
    chart(
        "bar",
        names,
        ys,
        title or f"{_ROUTE_WORDS[value]} per {piece}, {labels[0]} and {labels[1]}",
        "",
        _ROUTE_UNITS[value],
        labels=labels,
    )


def _column_order(rows: list[Row]) -> list[str]:
    """The comparison's columns, each a-and-b pair together."""
    seen = list(dict.fromkeys(k for r in rows for k in r))
    head = [k for k in seen if not k.startswith(("a_", "b_"))]
    pairs = [k for k in seen if k.startswith("a_")]
    tail = [k for k in seen if k.startswith("b_") and f"a_{k[2:]}" not in seen]
    return [*head, *(c for k in pairs for c in (k, f"b_{k[2:]}")), *tail]


def time_gap(a: Activity, b: Activity) -> Row:
    """How far b ran behind (+) or ahead (-) of a, in seconds, along shared ground.

    Returns ``km`` (along a's track) and ``gap_s``, every 100 m, ready for
    ``ana.chart("line", g["km"], [g["gap_s"]], ..., "km", "s")``; the gap
    restarts at zero on each shared stretch, with a break between them.
    """
    ta, tb = _Track.of(a), _Track.of(b)
    j = (
        _nearest(ta, tb)
        if len(ta.s) >= 10 and len(tb.s) >= 10
        else np.zeros(0, dtype=np.int64)
    )
    km: list[float] = []
    gap: list[float | None] = []
    for i0, i1 in _sections(j):
        tb_at = tb.time_at(_filled(j, i0, i1))
        g = (tb_at - tb_at[0]) - (ta.t[i0 : i1 + 1] - ta.t[i0])
        step = max(1, round(100 / ROUTE_STEP_M))
        if km:
            km.append(round(float(ta.s[i0]) / 1000, 2))
            gap.append(None)
        km += [round(float(v) / 1000, 2) for v in ta.s[i0 : i1 + 1 : step]]
        gap += [round(float(v), 1) for v in g[::step]]
        print(
            f"km {ta.s[i0] / 1000:.1f}-{ta.s[i1] / 1000:.1f}: "
            f"b {'+' if g[-1] >= 0 else ''}{g[-1]:.0f} s against a"
        )
    if not km:
        print("no shared ground between", _label(a), "and", _label(b))
    return {"km": km, "gap_s": gap}


def chart_gap(a: Activity, b: Activity, title: str = "") -> None:
    """Draw :func:`time_gap` as a line: seconds b was behind (+) along the ground."""
    gap = time_gap(a, b)
    if not gap["km"]:
        raise ChartError("no shared ground to draw a gap for")
    chart(
        "line",
        gap["km"],
        [gap["gap_s"]],
        title or f"Time gap, {_short_date(b)} against {_short_date(a)}",
        "km",
        "s",
    )


def efforts(
    ref: Activity, from_km: float, to_km: float, others: Sequence[Activity] = ()
) -> list[Row]:
    """Every pass over one stretch of ref's route, in ref itself and in others.

    The stretch is ref between ``from_km`` and ``to_km`` of its own
    distance; a pass is a file covering all of it in the same direction,
    so repeats and laps in one file count once each. One row per pass:
    date, minutes, speed or pace, climbing rate on a climb, HR, power.
    """
    tr = _Track.of(ref)
    if len(tr.s) < 10:
        print("no GPS track in", _label(ref))
        return []
    km = np.fmax.accumulate(np.nan_to_num(ref.km))
    t0, t1 = np.interp([from_km, to_km], km, ref.t)
    i0, i1 = (int(v) for v in np.searchsorted(tr.t, [t0, t1]))
    i1 = min(i1, len(tr.s) - 1)
    if (i1 - i0) * ROUTE_STEP_M < 100:
        print("the stretch is shorter than 100 m")
        return []
    stretch = tr.part(i0, i1)
    length = (i1 - i0) * ROUTE_STEP_M
    gain = float(np.sum(np.clip(np.diff(stretch.alt), 0, None)))
    climb = float(stretch.alt[-1] - stretch.alt[0])
    kind = "climb" if climb >= 20 else "descent" if climb <= -20 else "flat"
    print(
        f"stretch: {_label(ref)} km {from_km}-{to_km}, {length:.0f} m, "
        f"{climb:+.0f} m ({100 * climb / length:.1f}%)"
    )
    rows: list[Row] = []
    n = len(stretch.s)
    for c in (ref, *others):
        tc = tr if c is ref else _Track.of(c)
        if len(tc.s) < 10:
            continue
        j = _nearest(tc, stretch)
        for k0, k1 in _sections(j, min_m=0.8 * length):
            if j[k0] <= 3 and j[k1] >= n - 4:
                passes = sum(1 for r in rows if r["id"] == c.meta.get("id"))
                rows.append(
                    {
                        "id": c.meta.get("id"),
                        "date": c.meta.get("date"),
                        "pass": passes + 1,
                        **_effort(c, tc.t[k0], tc.t[k1], length, gain, kind),
                    }
                )
    table(rows)
    return rows


def check(a: Activity) -> None:
    """Print the file's own totals beside the streams' totals."""
    m, s = a.meta, summary(a)
    recorded = float(np.sum(a.dt)) / 60
    km = float(np.nanmax(a.km)) if np.isfinite(a.km).any() else 0.0
    print(
        f"check {m.get('date')} {m.get('name')}: file {m.get('km')} km, "
        f"{m.get('climb_m')} m, {m.get('elapsed_min')} min elapsed | streams "
        f"{km:.2f} km, {s.get('climb_m', '-')} m, {recorded:.1f} min recorded"
    )


def overview(a: Activity) -> None:
    """Print the whole session, read it all before you write.

    Totals, splits, climbs, laps, quarters by time (heart rate, power, power
    per beat, temperature) and, on a bike, the best efforts.
    """
    check(a)
    print("totals", summary(a))
    km = float(np.nanmax(a.km)) if np.isfinite(a.km).any() else 0.0
    if a.sport in ("run", "hike"):
        step = 1 if km <= 12 else 2 if km <= 25 else 5
        print(f"\nsplits every {step} km")
        table(splits(a, every_km=step))
    elif a.sport == "ride":
        step = 10 if a.t[-1] <= 2 * 3600 else 30
        print(f"\nsplits every {step} min")
        table(splits(a, every_min=step))
    work = laps(a, work_only=True)
    if a.sport == "swim":
        print("\nwork laps by distance")
        table(lap_sets(a))
    elif any(
        str(lap.get("type")).lower() == "recovery"
        and (lap.get("elapsed_time") or 0) >= 30
        for lap in a.lap_rows
    ):
        # Real rests mark a structured session; auto-laps every km do not.
        print("\nwork intervals")
        table(work)
    found = climbs(a) if a.sport != "swim" else []
    if found:
        print("\nclimbs of 40 m or more")
        table(found)
    if a.sport != "swim" or np.isfinite(a.hr).any():
        print("\nquarters by time")
        compare(a, quarters(a), ["Q1", "Q2", "Q3", "Q4"])
    if a.sport == "ride" and np.isfinite(a.w).any():
        print("\nbest efforts")
        table(best_efforts(a))


def chart(
    kind: str,
    x: Any,
    ys: Sequence[Any],
    title: str,
    x_label: str = "",
    unit: str = "",
    labels: Sequence[str] | None = None,
    stacked: bool = False,
) -> None:
    """Attach a chart to the reply (at most three per script).

    Args:
        kind: ``line``, ``area``, ``bar`` or ``scatter``.
        x: Numbers (km, minutes), ``YYYY-MM-DD`` dates, or names for bars.
        ys: One to three series as long as ``x``, all in ``unit``.
        title: What the chart shows, in the athlete's words.
        x_label: The x-axis label, e.g. ``km``.
        unit: One unit for every series; pace units take seconds (use
            ``ana.seconds``) and draw as m:ss.
        labels: A name per series, needed for two or more.
        stacked: Stack the bars, e.g. hours per sport per week.

    Raises:
        ChartError: The chart breaks a rule; the message says which.
    """
    if len(CHARTS) >= MAX_CHARTS:
        raise ChartError(f"at most {MAX_CHARTS} charts per script")
    xs = list(np.asarray(x).tolist()) if not isinstance(x, list) else x
    series = [list(np.asarray(y, dtype=float).tolist()) for y in ys]
    CHARTS.append(
        make_spec(
            kind,
            xs,
            series,
            title,
            x_label=x_label,
            unit=unit,
            labels=list(labels) if labels else None,
            stacked=stacked,
        )
    )
