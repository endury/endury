"""Shared training-load formulas and threshold context helpers.

This module centralizes the lightweight load semantics used across planner
activities and calendar projections so that planned sessions are expressed in
the same units we compare against in TrainingPeaks-style practice:

- Cycling: standard 100 TSS/hour threshold scale
- Running: rTSS-style 111 TSS/hour threshold scale
- Swimming: sTSS-like cubic load relative to CSS
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from endury.domain.models.zones import TrainingZones

# Maximum age in days before threshold data is considered stale.
THRESHOLD_MAX_AGE_DAYS = 120


@runtime_checkable
class ResolverProtocol(Protocol):
    """Protocol for activity resolvers providing athlete threshold values.

    Activities query the resolver to personalise pacing and power targets
    using the optional threshold attributes defined here.
    """

    @property
    def swim_css_sec_per_100m(self) -> float | None:
        """Critical Swim Speed in seconds per 100 m, or None if unknown."""
        ...  # pragma: no cover

    @property
    def running_threshold_pace_min_per_km(self) -> float | None:
        """Running threshold pace in min/km, or None if unknown."""
        ...  # pragma: no cover

    @property
    def running_ftp_watts(self) -> float | None:
        """Running FTP (Stryd/Garmin) in watts, or None if unknown."""
        ...  # pragma: no cover

    @property
    def cycling_ftp_watts(self) -> float | None:
        """Cycling FTP in watts, or None if unknown."""
        ...  # pragma: no cover

    @property
    def cycling_lthr_bpm(self) -> int | None:
        """Cycling lactate threshold heart rate in bpm, or None if unknown."""
        ...  # pragma: no cover

    @property
    def training_zones(self) -> TrainingZones | None:
        """Stored training zones for zone-boundary resolution, or None."""
        ...  # pragma: no cover

    @property
    def athlete_zones(self) -> TrainingZones | None:
        """The athlete's own intervals.icu zones, which number the cards, or None."""
        ...  # pragma: no cover


CYCLING_THRESHOLD_TSS_PER_HOUR = 100.0
RUNNING_THRESHOLD_TSS_PER_HOUR = 111.0
SWIMMING_THRESHOLD_TSS_PER_HOUR = 100.0
DEFAULT_THRESHOLD_TSS_PER_HOUR = CYCLING_THRESHOLD_TSS_PER_HOUR
RUN_BIKE_LOAD_EXPONENT = 2.0
SWIM_LOAD_EXPONENT = 3.0


@dataclass(frozen=True, slots=True)
class LoadMetricContext:
    """Threshold anchors used to calibrate planned-session materialization.

    Attributes:
        running_threshold_pace_min_per_km: Running threshold pace in min/km.
        running_lthr_bpm: Running lactate threshold heart rate in bpm.
        running_ftp_watts: Running FTP (Stryd/Garmin) in watts.
        cycling_ftp_watts: Cycling FTP in watts.
        cycling_lthr_bpm: Cycling lactate threshold heart rate in bpm.
            When None, callers should fall back to ``running_lthr_bpm`` or
            derive via :func:`~endury.domain.analysis.zones.derive_cycling_lthr_bpm`.
        swim_css_sec_per_100m: Critical swim speed in sec/100m.
        training_zones: Full zone model for sport-specific HR boundary
            resolution in workout step targets. Optional; when present,
            materializer steps include actual bpm ranges alongside zone numbers.
        athlete_zones: The zone sets the athlete keeps on intervals.icu, per
            sport and metric. Optional; when present, a step's zone number is
            read off them, so the card agrees with the calendar and the
            debrief. The prescription (pace, watts, bpm range) never changes.
        threshold_updated_at: Date when thresholds were last updated.
    """

    running_threshold_pace_min_per_km: float | None = None
    running_lthr_bpm: int | None = None
    running_ftp_watts: float | None = None
    cycling_ftp_watts: float | None = None
    cycling_lthr_bpm: int | None = None
    swim_css_sec_per_100m: float | None = None
    training_zones: TrainingZones | None = None
    athlete_zones: TrainingZones | None = None
    threshold_updated_at: date | None = None


def positive_float_or_none(value: Any) -> float | None:
    """Return a positive float when ``value`` is numeric, else ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0.0:
        return None
    return number


def tss_per_hour_at_if(
    intensity_factor: float,
    *,
    exponent: float,
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR,
) -> float:
    """Return load accumulated per hour at a given threshold-relative intensity.

    Named for its input: this is the per-sport IF-curve, not the planner's
    TID-mix bridge ``macro.tss_estimator.tss_per_hour``, which takes a zone
    distribution instead of a single intensity.
    """
    return threshold_tss_per_hour * float(intensity_factor**exponent)


def tss_per_minute(
    intensity_factor: float,
    *,
    exponent: float,
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR,
) -> float:
    """Return load accumulated per minute at a given threshold-relative intensity."""
    return (
        tss_per_hour_at_if(
            intensity_factor,
            exponent=exponent,
            threshold_tss_per_hour=threshold_tss_per_hour,
        )
        / 60.0
    )


def tss_from_duration_minutes(
    duration_minutes: float,
    intensity_factor: float,
    *,
    exponent: float,
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR,
) -> float:
    """Convert planned minutes at a relative intensity into load."""
    return duration_minutes * tss_per_minute(
        intensity_factor,
        exponent=exponent,
        threshold_tss_per_hour=threshold_tss_per_hour,
    )


def duration_minutes_from_tss(
    target_tss: float,
    intensity_factor: float,
    *,
    exponent: float,
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR,
) -> float:
    """Convert planned load into minutes at a relative intensity."""
    rate = tss_per_minute(
        intensity_factor,
        exponent=exponent,
        threshold_tss_per_hour=threshold_tss_per_hour,
    )
    if rate <= 0.0:
        return 0.0
    return target_tss / rate


def pace_sec_per_100m_from_css(
    css_sec_per_100m: float,
    intensity_factor: float,
) -> float:
    """Return swim pace implied by CSS and a threshold-relative intensity."""
    return css_sec_per_100m / max(intensity_factor, 1e-6)


def pace_min_per_km_from_threshold(
    threshold_pace: float,
    intensity_factor: float,
) -> float:
    """Return running pace implied by threshold pace and intensity factor.

    Higher intensity → faster pace (lower min/km).

    Args:
        threshold_pace: Threshold pace in min/km.
        intensity_factor: Fraction of threshold (e.g. 0.85 for easy).

    Returns:
        Pace in min/km at the given intensity.
    """
    return threshold_pace / max(intensity_factor, 1e-6)


def power_watts_from_ftp(
    ftp_watts: float,
    intensity_factor: float,
) -> float:
    """Return power target from FTP and intensity factor.

    Args:
        ftp_watts: Functional threshold power in watts.
        intensity_factor: Fraction of threshold (e.g. 0.88 for sweet spot).

    Returns:
        Power in watts at the given intensity.
    """
    return ftp_watts * intensity_factor


def thresholds_are_fresh(
    ctx: LoadMetricContext | None,
    today: date,
) -> bool:
    """Return False if threshold data is missing or older than THRESHOLD_MAX_AGE_DAYS.

    Args:
        ctx: Load metric context with optional freshness date.
        today: Reference date to check against.
    """
    if ctx is None:
        return False
    if ctx.threshold_updated_at is None:
        return True
    return (today - ctx.threshold_updated_at).days <= THRESHOLD_MAX_AGE_DAYS


__all__ = [
    "CYCLING_THRESHOLD_TSS_PER_HOUR",
    "DEFAULT_THRESHOLD_TSS_PER_HOUR",
    "RUNNING_THRESHOLD_TSS_PER_HOUR",
    "RUN_BIKE_LOAD_EXPONENT",
    "SWIMMING_THRESHOLD_TSS_PER_HOUR",
    "SWIM_LOAD_EXPONENT",
    "THRESHOLD_MAX_AGE_DAYS",
    "LoadMetricContext",
    "ResolverProtocol",
    "duration_minutes_from_tss",
    "pace_min_per_km_from_threshold",
    "pace_sec_per_100m_from_css",
    "positive_float_or_none",
    "power_watts_from_ftp",
    "thresholds_are_fresh",
    "tss_from_duration_minutes",
    "tss_per_hour_at_if",
    "tss_per_minute",
]
