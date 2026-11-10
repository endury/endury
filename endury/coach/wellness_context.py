"""Wellness context for the coach runtime.

Reads intervals.icu daily wellness rows (sleep, HRV, resting HR) and turns
them into the shapes the coach needs: a compact system-prompt snapshot, a
list of recovery flags for the today's-session recommendation, and per-metric
trends for the wellness-trend tool.

Every helper is fault tolerant by design: no API key, no network, or a
malformed payload degrades to "no wellness data" rather than failing a
chat turn.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import stdev
from typing import Any

from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.intervals.models import WellnessDay

logger = logging.getLogger(__name__)

#: Default wellness window (days, inclusive of today).
WELLNESS_WINDOW_DAYS = 7

#: Recent window (days) whose values are judged against the personal baseline.
_RECENT_DAYS = 7

#: Longest personal-baseline window (days), ending where the recent one starts.
_BASELINE_DAYS = 28

#: Fewest baseline days before a personal band is worth trusting.
_MIN_BASELINE_DAYS = 7

#: Wellness window (days) that fully covers a baseline plus the recent week.
WELLNESS_BASELINE_DAYS = _RECENT_DAYS + _BASELINE_DAYS

#: Fewest days of history before any trend can be called.
MIN_TREND_DAYS = _RECENT_DAYS + _MIN_BASELINE_DAYS

#: Average nightly sleep below this many hours raises a recovery flag.
_LOW_SLEEP_HOURS = 6.5

#: HRV this far below the window mean (fraction) raises a recovery flag.
_HRV_DROP_FRACTION = 0.10

#: Resting HR this many bpm above the window mean raises a recovery flag.
_RHR_RISE_BPM = 5.0


def fetch_wellness(
    client: IntervalsClient | None,
    today: date | None = None,
    days: int = WELLNESS_WINDOW_DAYS,
) -> list[WellnessDay]:
    """Fetch recent wellness rows, oldest first.

    Args:
        client: intervals.icu client, or None when no key is configured.
        today: Last day of the window (defaults to today).
        days: Window length in days, inclusive of ``today``.

    Returns:
        Dated wellness rows sorted by date, or an empty list when no client
        is configured or the read fails.
    """
    if client is None:
        return []
    end = today or clock.today()
    try:
        rows = client.list_wellness(end - timedelta(days=days - 1), end)
    except Exception as exc:
        logger.warning("Could not load intervals.icu wellness: %s", exc)
        return []
    return sorted((row for row in rows if row.id), key=lambda row: str(row.id))


def _mean(values: list[float]) -> float:
    """Return the mean of a non-empty value list."""
    return sum(values) / len(values)


def build_wellness_summary(
    rows: list[WellnessDay],
    days: int = WELLNESS_WINDOW_DAYS,
) -> str | None:
    """Build a one-line wellness snapshot for the system prompt.

    Args:
        rows: Wellness rows from :func:`fetch_wellness`.
        days: Window length the rows were fetched over (used in the label).

    Returns:
        A semicolon-separated summary (sleep average, latest HRV against its
        window mean, latest resting HR), or None when nothing is known.
    """
    if not rows:
        return None

    parts: list[str] = []
    sleep_hours = [row.sleep_secs / 3600.0 for row in rows if row.sleep_secs]
    if sleep_hours:
        parts.append(
            f"sleep {_mean(sleep_hours):.1f}h/night avg over {len(sleep_hours)} nights"
        )

    hrvs = [row.hrv for row in rows if row.hrv is not None]
    if hrvs:
        parts.append(f"HRV {hrvs[-1]:.0f} (window avg {_mean(hrvs):.0f})")

    rhrs = [float(row.resting_hr) for row in rows if row.resting_hr is not None]
    if rhrs:
        parts.append(f"resting HR {rhrs[-1]:.0f} (window avg {_mean(rhrs):.0f})")

    if not parts:
        return None
    return f"Last {days} days — " + "; ".join(parts)


#: Deviation from the personal baseline, in baseline standard deviations,
#: that a reading must clear before it counts as signal rather than noise.
#:
#: Day-to-day HRV noise is large: the coefficient of variation of daily
#: lnRMSSD sits around 5-10% for a healthy athlete, and
#: ``docs/research/endurance_methodology.md`` (HRV-Guided Training, Plews et
#: al. 2013) treats a 7-day CV above ~10% as fatigue rather than as a
#: measurement artefact. A flat percentage gate cannot account for this: at
#: CV 8% an athlete averaging 60 has a daily SD near 5, so a fixed-percentage
#: threshold trips on noise alone.
#:
#: The trend path compares a 7-day *mean*, whose standard error is
#: ``sd / sqrt(7)`` ~= 0.38 sd; 0.75 sd is therefore about two standard
#: errors — roughly 95% confidence that the shift is real, i.e. one false
#: call in twenty rather than the coin flip a half-window comparison gives.
#: The flag path applies the same 0.75 sd to a *single* day's reading, where
#: it is deliberately a looser gate (that is a today-signal, not a trend) and
#: is always AND-ed with an absolute threshold.
_DEVIATION_SD = 0.75


@dataclass(frozen=True)
class _PersonalBaseline:
    """A metric's own recent normal: the centre and spread of its history."""

    mean: float
    sd: float

    def deviation(self, value: float) -> float:
        """Return how far ``value`` sits from the baseline, in baseline SDs.

        Args:
            value: The reading (or window mean) to judge.

        Returns:
            The signed deviation. A perfectly flat baseline has no spread to
            measure against, so any departure from it reads as infinite.
        """
        if self.sd <= 0:
            return (
                0.0
                if value == self.mean
                else math.copysign(math.inf, value - self.mean)
            )
        return (value - self.mean) / self.sd


def _rolling_baseline(values: list[float]) -> _PersonalBaseline | None:
    """Build the personal baseline for a metric, excluding the recent window.

    The baseline is the athlete's own normal *before* the period under
    judgement: up to :data:`_BASELINE_DAYS` values ending where the trailing
    :data:`_RECENT_DAYS` begin. Leaving the recent window out keeps a
    genuinely suppressed week from quietly dragging its own reference down.

    Args:
        values: Oldest-first metric values, one per day that has data.

    Returns:
        The baseline, or None when there is less than
        :data:`MIN_TREND_DAYS` of history to build one from.
    """
    if len(values) < MIN_TREND_DAYS:
        return None
    prior = values[-(_RECENT_DAYS + _BASELINE_DAYS) : -_RECENT_DAYS]
    return _PersonalBaseline(mean=_mean(prior), sd=stdev(prior))


def _classify(deviation: float) -> str:
    """Label a baseline deviation as ``rising`` / ``declining`` / ``stable``."""
    if deviation >= _DEVIATION_SD:
        return "rising"
    if deviation <= -_DEVIATION_SD:
        return "declining"
    return "stable"


def _outside_band(
    history: list[float | int | None],
    value: float,
    *,
    rising: bool,
) -> bool:
    """Say whether one reading leaves the athlete's personal baseline band.

    Args:
        history: Oldest-first raw metric history (Nones are skipped).
        value: The single reading to judge.
        rising: True to test for a rise above the band, False for a drop
            below it.

    Returns:
        True when the reading is at least :data:`_DEVIATION_SD` baseline SDs
        away in the given direction — and also True when there is too little
        history for a baseline, so the caller's absolute threshold stands
        alone rather than being silently suppressed.
    """
    baseline = _rolling_baseline([float(v) for v in history if v is not None])
    if baseline is None:
        return True
    deviation = baseline.deviation(value)
    return deviation >= _DEVIATION_SD if rising else deviation <= -_DEVIATION_SD


def detect_trend(values: list[float]) -> str | None:
    """Detect the trend direction in a time-ordered list of values.

    Compares the mean of the trailing 7 days against the athlete's own
    rolling baseline (the up-to-28 days before that), in units of the
    baseline's standard deviation — so what counts as a move scales with how
    noisy this particular metric is for this particular athlete.

    Args:
        values: Oldest-first metric values, one per day that has data.

    Returns:
        ``"rising"`` or ``"declining"`` when the recent mean is at least
        0.75 baseline SDs away, ``"stable"`` when it is not, or None when
        there is less than :data:`MIN_TREND_DAYS` of history.
    """
    baseline = _rolling_baseline(values)
    if baseline is None:
        return None
    return _classify(baseline.deviation(_mean(values[-_RECENT_DAYS:])))


#: Wellness metrics the trend tool reports on: label -> row attribute.
_TREND_METRICS: dict[str, str] = {
    "sleep_hours": "sleep_secs",
    "sleep_score": "sleep_score",
    "hrv": "hrv",
    "resting_hr": "resting_hr",
    "weight_kg": "weight",
}


def wellness_trends(rows: list[WellnessDay]) -> dict[str, dict[str, Any]]:
    """Trend each wellness metric over a window of rows.

    Args:
        rows: Wellness rows, oldest first (from :func:`fetch_wellness`).

    Returns:
        Per metric (only those with any data): the number of days with a
        value, the latest reading, the personal baseline (mean and SD) with
        the recent 7-day average judged against it, and the trend label —
        or ``"insufficient data"`` under :data:`MIN_TREND_DAYS` of history.
    """
    trends: dict[str, dict[str, Any]] = {}
    for label, attr in _TREND_METRICS.items():
        raw = [getattr(row, attr) for row in rows]
        values = [float(v) for v in raw if v is not None]
        if attr == "sleep_secs":
            values = [v / 3600.0 for v in values]
        if not values:
            continue
        entry: dict[str, Any] = {"days": len(values), "latest": round(values[-1], 1)}
        baseline = _rolling_baseline(values)
        if baseline is None:
            entry["trend"] = "insufficient data"
        else:
            recent_avg = _mean(values[-_RECENT_DAYS:])
            entry.update(
                {
                    "baseline_avg": round(baseline.mean, 1),
                    "baseline_sd": round(baseline.sd, 1),
                    "recent_avg": round(recent_avg, 1),
                    "trend": detect_trend(values),
                }
            )
        trends[label] = entry
    return trends


def wellness_flags(rows: list[WellnessDay]) -> list[str]:
    """Return recovery flags from a recent wellness window.

    Flags are advisory heuristics: short average sleep, a latest HRV well
    below its own window mean, and a latest resting HR well above its
    window mean. The last two are double-gated when the caller supplies
    enough history — the absolute threshold is the floor (so a big move
    still flags for an athlete with barely any data), and on top of it the
    reading must also leave the athlete's personal band
    (:data:`_DEVIATION_SD` baseline SDs), which is what stops a normal bad
    night from reading as fatigue for someone with naturally noisy HRV.

    Args:
        rows: Wellness rows from :func:`fetch_wellness`. Only the trailing
            :data:`WELLNESS_WINDOW_DAYS` drive the flags themselves; any
            earlier rows (fetch over :data:`WELLNESS_BASELINE_DAYS` to get
            them) are used solely to build the personal baseline.

    Returns:
        Human-readable flag strings (empty when nothing stands out or no
        wellness data is available).
    """
    flags: list[str] = []
    if not rows:
        return flags
    window = rows[-WELLNESS_WINDOW_DAYS:]

    sleep_hours = [row.sleep_secs / 3600.0 for row in window if row.sleep_secs]
    if sleep_hours and _mean(sleep_hours) < _LOW_SLEEP_HOURS:
        flags.append(
            f"average sleep {_mean(sleep_hours):.1f}h over the last "
            f"{len(sleep_hours)} nights (below {_LOW_SLEEP_HOURS}h)"
        )

    hrvs = [row.hrv for row in window if row.hrv is not None]
    if len(hrvs) >= 2:
        hrv_mean = _mean(hrvs[:-1])
        if (
            hrv_mean > 0
            and hrvs[-1] < hrv_mean * (1 - _HRV_DROP_FRACTION)
            and _outside_band([row.hrv for row in rows], hrvs[-1], rising=False)
        ):
            drop = (1 - hrvs[-1] / hrv_mean) * 100
            flags.append(
                f"HRV {hrvs[-1]:.0f} is {drop:.0f}% below its recent average "
                f"({hrv_mean:.0f})"
            )

    rhrs = [float(row.resting_hr) for row in window if row.resting_hr is not None]
    if len(rhrs) >= 2:
        rhr_mean = _mean(rhrs[:-1])
        if rhrs[-1] > rhr_mean + _RHR_RISE_BPM and _outside_band(
            [row.resting_hr for row in rows], rhrs[-1], rising=True
        ):
            flags.append(
                f"resting HR {rhrs[-1]:.0f} is {rhrs[-1] - rhr_mean:.0f} bpm "
                f"above its recent average ({rhr_mean:.0f})"
            )

    return flags
