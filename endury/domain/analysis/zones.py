"""Training zone computation from physiological thresholds.

Pure functions — no database access. Takes threshold values, returns
ZoneSet objects with computed zone boundaries.

Functions:
    compute_hr_zones        — 5-zone LTHR model
    compute_power_zones     — 7-zone Coggan model
    compute_pace_zones      — 6-zone threshold-pace model
    compute_swim_pace_zones — 6-zone CSS model
    athlete_zone_set        — the athlete's own zones from intervals.icu's bounds
    derive_cycling_lthr_bpm — estimate cycling LTHR from running LTHR
    compute_all_zones       — auto-detect and compute all applicable zones
"""

from __future__ import annotations

import math
from collections.abc import Callable

from endury.common import clock
from endury.domain.models.zones import (
    TrainingZones,
    ZoneBand,
    ZoneMetric,
    ZoneModel,
    ZoneSet,
)
from endury.training.zones import (
    HR_BAND_SPECS,
    PACE_BAND_SPECS,
    POWER_BAND_SPECS,
    SWIM_PACE_BAND_SPECS,
    ZoneBandSpec,
)


def _round_boundary(value: float, precision: int | None) -> float | int:
    """Round a boundary value with the requested precision."""
    return round(value) if precision is None else round(value, precision)


def _build_zone_bands(
    threshold_value: float,
    specs: tuple[ZoneBandSpec, ...],
    *,
    precision: int | None,
    invert: bool = False,
) -> list[ZoneBand]:
    """Build zone bands from declarative IF-space specs.

    Args:
        threshold_value: Athlete threshold (LTHR bpm, FTP watts, pace min/km,
            CSS sec/100m).
        specs: Band specs with factors in IF-space (ascending).
        precision: Rounding precision for boundary values.
        invert: If True, compute ``threshold / factor`` instead of
            ``threshold * factor``.  Used for pace metrics where
            higher IF means faster (lower) pace.  When inverted the
            *high* IF factor produces the *low* pace boundary and
            vice-versa.
    """
    if not invert:
        return [
            ZoneBand(
                zone_number=spec.zone_number,
                name=spec.name,
                low=_round_boundary(threshold_value * spec.low_factor, precision),
                high=_round_boundary(threshold_value * spec.high_factor, precision),
            )
            for spec in specs
        ]
    # Inverted: pace = threshold / IF.  Higher IF → lower (faster) pace.
    # So the low pace boundary comes from high_factor and vice-versa.
    return [
        ZoneBand(
            zone_number=spec.zone_number,
            name=spec.name,
            low=_round_boundary(threshold_value / spec.high_factor, precision),
            high=_round_boundary(threshold_value / spec.low_factor, precision),
        )
        for spec in specs
    ]


def _build_zone_set(
    *,
    sport: str,
    metric: ZoneMetric,
    model: ZoneModel,
    threshold_value: float,
    threshold_label: str,
    band_specs: tuple[ZoneBandSpec, ...],
    precision: int | None,
    source: str,
    invert: bool = False,
) -> ZoneSet:
    """Construct a zone set from declarative metadata."""
    return ZoneSet(
        sport=sport,
        metric=metric,
        model=model,
        threshold_value=threshold_value,
        threshold_label=threshold_label,
        bands=_build_zone_bands(
            threshold_value, band_specs, precision=precision, invert=invert
        ),
        source=source,
    )


def _append_zone_if_threshold(
    zone_sets: list[ZoneSet],
    threshold_value: int | float | None,
    builder: Callable[..., ZoneSet],
    *,
    source: str,
    **kwargs: str,
) -> None:
    """Append a zone set only when a positive threshold value is present."""
    if threshold_value is None or threshold_value <= 0:
        return
    zone_sets.append(builder(threshold_value, source=source, **kwargs))


def compute_hr_zones(
    lthr_bpm: int,
    sport: str = "all",
    *,
    source: str = "auto",
) -> ZoneSet:
    """Compute 5-zone heart rate zones from lactate threshold heart rate.

    Uses the standard LTHR percentage model:
        Z1: <85% LTHR (Recovery)
        Z2: 85-89% LTHR (Aerobic / Endurance)
        Z3: 90-94% LTHR (Tempo)
        Z4: 95-99% LTHR (Threshold)
        Z5: 100%+ LTHR (VO2max / Anaerobic)

    Args:
        lthr_bpm: Lactate threshold heart rate in beats per minute.
        sport: Sport label (e.g. "running", "cycling", "all").
        source: Data source ("auto" or "manual").

    Returns:
        ZoneSet with 5 heart rate zone bands in bpm.
    """
    return _build_zone_set(
        sport=sport,
        metric=ZoneMetric.HEART_RATE,
        model=ZoneModel.LTHR_5ZONE,
        threshold_value=float(lthr_bpm),
        threshold_label="LTHR",
        band_specs=HR_BAND_SPECS,
        precision=None,
        source=source,
    )


def compute_power_zones(
    ftp_watts: int,
    sport: str = "cycling",
    *,
    source: str = "auto",
) -> ZoneSet:
    """Compute 7-zone power zones from FTP using the Coggan model.

    Zone boundaries as percentage of FTP:
        Z1 Active Recovery: <55%
        Z2 Endurance: 56-75%
        Z3 Tempo: 76-90%
        Z4 Threshold: 91-105%
        Z5 VO2max: 106-120%
        Z6 Anaerobic: 121-150%
        Z7 Neuromuscular: >150%

    Args:
        ftp_watts: Functional Threshold Power in watts.
        sport: Sport label (e.g. "cycling", "running").
        source: Data source ("auto" or "manual").

    Returns:
        ZoneSet with 7 power zone bands in watts.
    """
    return _build_zone_set(
        sport=sport,
        metric=ZoneMetric.POWER,
        model=ZoneModel.COGGAN_7ZONE,
        threshold_value=float(ftp_watts),
        threshold_label="FTP",
        band_specs=POWER_BAND_SPECS,
        precision=None,
        source=source,
    )


def compute_pace_zones(
    threshold_pace_min_per_km: float,
    *,
    source: str = "auto",
) -> ZoneSet:
    """Compute 6-zone running pace zones from lactate threshold pace.

    Friel's seven threshold-pace zones, as intervals.icu ships them by
    default (IF = fraction of threshold speed):
        Z1 Recovery:  IF < 0.775  (pace > 129% threshold)
        Z2 Aerobic:   IF 0.775-0.877
        Z3 Tempo:     IF 0.877-0.943
        Z4 Threshold: IF 0.943-1.00
        Z5 VO2max:    IF 1.00-1.034
        Z6 Anaerobic: IF 1.034-1.115
        Z7 Speed:     IF > 1.115  (pace < 90% threshold)

    For pace zones, low = fast boundary (lower min/km), high = slow boundary.

    Args:
        threshold_pace_min_per_km: Lactate threshold pace in min/km.
        source: Data source ("auto" or "manual").

    Returns:
        ZoneSet with 7 pace zone bands in min/km.
    """
    return _build_zone_set(
        sport="running",
        metric=ZoneMetric.PACE,
        model=ZoneModel.THRESHOLD_PACE_7ZONE,
        threshold_value=threshold_pace_min_per_km,
        threshold_label="Threshold Pace",
        band_specs=PACE_BAND_SPECS,
        precision=2,
        source=source,
        invert=True,
    )


def compute_swim_pace_zones(
    css_sec_per_100m: float,
    *,
    source: str = "auto",
) -> ZoneSet:
    """Compute 6-zone swim pace zones from critical swim speed.

    Shares IF-space upper bounds with the running pace model (HR-aligned
    Z1-Z5) with a renamed Z6 "Sprint" for neuromuscular efforts:
        Z1 Recovery:  IF < 0.85  (pace > 118% CSS)
        Z2 Aerobic:   IF 0.85-0.90
        Z3 Tempo:     IF 0.90-0.95
        Z4 Threshold: IF 0.95-1.00
        Z5 VO2max:    IF 1.00-1.10
        Z6 Sprint:    IF > 1.10  (pace < 91% CSS)

    For swim pace zones, low = fast boundary, high = slow boundary.

    Args:
        css_sec_per_100m: Critical swim speed in seconds per 100m.
        source: Data source ("auto" or "manual").

    Returns:
        ZoneSet with 6 swim pace zone bands in sec/100m.
    """
    return _build_zone_set(
        sport="swimming",
        metric=ZoneMetric.SWIM_PACE,
        model=ZoneModel.CSS_6ZONE,
        threshold_value=css_sec_per_100m,
        threshold_label="CSS",
        band_specs=SWIM_PACE_BAND_SPECS,
        precision=1,
        source=source,
        invert=True,
    )


def athlete_zone_set(
    *,
    sport: str,
    metric: ZoneMetric,
    threshold_value: float,
    threshold_label: str,
    upper_bounds: list[float],
    names: list[str] | None,
    scale: float,
    invert: bool = False,
) -> ZoneSet:
    """Build the athlete's own zone set from the bounds intervals.icu holds.

    intervals.icu stores a zone scheme as ascending upper bounds, one per
    zone: bpm for heart rate, percent of FTP or of threshold speed for
    power and pace. Zone 1 starts at nothing; a pace zone 1 therefore has
    no slow limit (``math.inf``).

    Args:
        sport: Sport name (e.g. "running").
        metric: Zone metric of the set.
        threshold_value: The threshold in native units (bpm, watts, min/km,
            sec/100m).
        threshold_label: e.g. "LTHR", "FTP", "Threshold Pace", "CSS".
        upper_bounds: The zones' upper bounds, ascending in effort.
        names: The athlete's zone names, or ``None`` for "Zone N".
        scale: Multiplies a bound into native units (1 for bpm, FTP / 100
            for percent of FTP, 1 / 100 for percent of threshold speed).
        invert: ``True`` for pace metrics, where a bound divides the
            threshold instead: a faster bound is a lower pace.

    Returns:
        A ``ZoneSet`` of ``ZoneModel.CUSTOM``.
    """
    bands: list[ZoneBand] = []
    lower = 0.0
    for index, upper in enumerate(upper_bounds):
        name = names[index] if names and index < len(names) else f"Zone {index + 1}"
        if invert:
            low = threshold_value / (upper * scale)
            high = threshold_value / (lower * scale) if lower else math.inf
        else:
            low, high = lower * scale, upper * scale
        bands.append(ZoneBand(zone_number=index + 1, name=name, low=low, high=high))
        lower = upper
    return ZoneSet(
        sport=sport,
        metric=metric,
        model=ZoneModel.CUSTOM,
        threshold_value=threshold_value,
        threshold_label=threshold_label,
        bands=bands,
        source="intervals.icu",
    )


# Running LTHR is typically higher than cycling LTHR due to the upright
# posture, gravitational loading, and greater muscle mass engaged in running.
# 8 bpm is a widely-used clinical approximation.
CYCLING_LTHR_OFFSET_BPM: int = 8


def derive_cycling_lthr_bpm(
    running_lthr_bpm: int,
    offset: int = CYCLING_LTHR_OFFSET_BPM,
) -> int:
    """Derive cycling LTHR from running LTHR using a standard physiological offset.

    Cycling LTHR is typically 5-10 bpm lower than running LTHR. The default
    offset of 8 bpm reflects the commonly cited clinical approximation.

    Args:
        running_lthr_bpm: Running lactate threshold heart rate in bpm.
        offset: Bpm offset to subtract (default 8).

    Returns:
        Estimated cycling LTHR in bpm (minimum 1).
    """
    return max(1, running_lthr_bpm - offset)


def compute_all_zones(
    lthr_bpm: int | None = None,
    cycling_ftp_watts: int | None = None,
    running_power_ftp: int | None = None,
    threshold_pace_min_per_km: float | None = None,
    css_sec_per_100m: float | None = None,
    cycling_lthr_bpm: int | None = None,
    *,
    source: str = "auto",
) -> TrainingZones:
    """Compute all applicable training zones from available threshold data.

    Only computes zones for metrics where threshold data is provided.
    Missing thresholds are silently skipped.

    When ``lthr_bpm`` is supplied but ``cycling_lthr_bpm`` is not, cycling HR
    zones are derived automatically via :func:`derive_cycling_lthr_bpm`; a
    cycling LTHR on its own builds the cycling set without a running one.
    Sport-specific HR zones (``sport="running"`` and ``sport="cycling"``) are
    created instead of the previous generic ``sport="all"`` set.

    Args:
        lthr_bpm: Running lactate threshold heart rate in bpm.
        cycling_ftp_watts: Cycling FTP in watts.
        running_power_ftp: Running power FTP in watts.
        threshold_pace_min_per_km: Running threshold pace in min/km.
        css_sec_per_100m: Critical swim speed in sec/100m.
        cycling_lthr_bpm: Cycling LTHR in bpm. When ``None`` and ``lthr_bpm``
            is provided, derived automatically from ``lthr_bpm``.
        source: Data source ("auto" or "manual").

    Returns:
        TrainingZones containing all computed ZoneSets.
    """
    zone_sets: list[ZoneSet] = []

    _append_zone_if_threshold(
        zone_sets,
        lthr_bpm,
        compute_hr_zones,
        sport="running",
        source=source,
    )

    if cycling_lthr_bpm is not None and cycling_lthr_bpm > 0:
        effective_cycling_lthr = cycling_lthr_bpm
    elif lthr_bpm is not None and lthr_bpm > 0:
        effective_cycling_lthr = derive_cycling_lthr_bpm(lthr_bpm)
    else:
        effective_cycling_lthr = None
    _append_zone_if_threshold(
        zone_sets,
        effective_cycling_lthr,
        compute_hr_zones,
        sport="cycling",
        source=source,
    )
    _append_zone_if_threshold(
        zone_sets,
        cycling_ftp_watts,
        compute_power_zones,
        sport="cycling",
        source=source,
    )
    _append_zone_if_threshold(
        zone_sets,
        running_power_ftp,
        compute_power_zones,
        sport="running",
        source=source,
    )
    _append_zone_if_threshold(
        zone_sets,
        threshold_pace_min_per_km,
        compute_pace_zones,
        source=source,
    )
    _append_zone_if_threshold(
        zone_sets,
        css_sec_per_100m,
        compute_swim_pace_zones,
        source=source,
    )

    return TrainingZones(
        zone_sets=zone_sets,
        computed_at=clock.now(),
    )
