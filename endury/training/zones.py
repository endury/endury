"""Declarative zone-band specifications in intensity-factor space.

These are the canonical, athlete-independent band tables. The domain zone
builders (:mod:`endury.domain.analysis.zones`) turn them into athlete-facing
``ZoneSet`` objects, and ``planner.activity.config`` derives IF-to-zone
lookup tables from them. Pure data — no host coupling.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoneBandSpec:
    """Declarative zone-band definition as intensity-factor boundaries.

    All band specs use the same convention: ``low_factor`` < ``high_factor``,
    with higher values meaning harder effort.  For HR and power this is a
    direct fraction of threshold (LTHR, FTP).  For pace and swim pace
    this is the *intensity factor* (IF); athlete-facing pace values are
    derived as ``threshold / factor`` by the zone builder (``invert=True``).

    Used by ``endury.domain.analysis.zones`` to build athlete-facing zone
    sets, and by ``planner.activity.config`` to derive IF-to-zone lookup
    tables (``high_factor`` is the zone upper bound for all four metrics).
    """

    zone_number: int
    name: str
    low_factor: float
    high_factor: float


HR_BAND_SPECS: tuple[ZoneBandSpec, ...] = (
    ZoneBandSpec(1, "Recovery", 0.00, 0.85),
    ZoneBandSpec(2, "Aerobic", 0.85, 0.90),
    ZoneBandSpec(3, "Tempo", 0.90, 0.95),
    ZoneBandSpec(4, "Threshold", 0.95, 1.00),
    ZoneBandSpec(5, "VO2max", 1.00, 1.10),
)

POWER_BAND_SPECS: tuple[ZoneBandSpec, ...] = (
    ZoneBandSpec(1, "Active Recovery", 0.00, 0.55),
    ZoneBandSpec(2, "Endurance", 0.56, 0.75),
    ZoneBandSpec(3, "Tempo", 0.76, 0.90),
    ZoneBandSpec(4, "Threshold", 0.91, 1.05),
    ZoneBandSpec(5, "VO2max", 1.06, 1.20),
    ZoneBandSpec(6, "Anaerobic", 1.21, 1.50),
    ZoneBandSpec(7, "Neuromuscular", 1.51, 2.00),
)

# Pace and swim-pace specs are in IF-space (ascending), same convention as
# HR and power.  Athlete-facing pace values are derived as threshold / IF
# by the zone builder (invert=True).  Zone 1 low_factor uses 1/1.40 so the
# inverted builder produces a finite slow-pace boundary.
#
# Running pace takes Friel's seven threshold-pace zones as intervals.icu
# ships them by default (77.5 / 87.7 / 94.3 / 100 / 103.4 / 111.5 % of
# threshold speed), so a runner without zones of their own reads the
# numbers their calendar shows. A run step's heart-rate zone is read at the
# same rung (``_hr_zone`` in planner/activity/shapes/base.py), so pace and
# heart rate name one zone. Friel's zones are in docs/research/triathlon.md's
# references; checked 2026-10-14 against intervals.icu's defaults.

PACE_BAND_SPECS: tuple[ZoneBandSpec, ...] = (
    ZoneBandSpec(1, "Recovery", round(1 / 1.40, 4), 0.775),
    ZoneBandSpec(2, "Aerobic", 0.775, 0.877),
    ZoneBandSpec(3, "Tempo", 0.877, 0.943),
    ZoneBandSpec(4, "Threshold", 0.943, 1.0),
    ZoneBandSpec(5, "VO2max", 1.0, 1.034),
    ZoneBandSpec(6, "Anaerobic", 1.034, 1.115),
    ZoneBandSpec(7, "Speed", 1.115, round(1 / 0.75, 4)),
)

SWIM_PACE_BAND_SPECS: tuple[ZoneBandSpec, ...] = (
    # Zones 1-5 are the 1/hr_upper inverse of HR_BAND_SPECS (0.85 / 0.90 /
    # 0.95 / 1.00 / 1.10); Z6 Sprint covers efforts above HR's resolution.
    # intent: kept over intervals.icu's default pace zones (77.5 / 87.7 /
    # 94.3 / 100 % of threshold speed, Friel's run pace zones), checked
    # 2026-10-14: swim speed zones are compressed, Swim Smooth puts steady
    # aerobic swimming at CSS +5-6 s/100 m (~95 %) and easy swimming at
    # CSS +8-10 s (~92 %), so the run-derived defaults would file an easy
    # aerobic swim (88 % of CSS) as zone 3. An athlete's own intervals.icu
    # zones still label their cards (R-ACT-007).
    ZoneBandSpec(1, "Recovery", round(1 / 1.40, 4), round(1 / 1.1765, 4)),
    ZoneBandSpec(2, "Aerobic", round(1 / 1.1765, 4), round(1 / 1.1111, 4)),
    ZoneBandSpec(3, "Tempo", round(1 / 1.1111, 4), round(1 / 1.0526, 4)),
    ZoneBandSpec(4, "Threshold", round(1 / 1.0526, 4), round(1 / 1.0000, 4)),
    ZoneBandSpec(5, "VO2max", round(1 / 1.0000, 4), round(1 / 0.9091, 4)),
    ZoneBandSpec(6, "Sprint", round(1 / 0.9091, 4), round(1 / 0.75, 4)),
)


__all__ = [
    "HR_BAND_SPECS",
    "PACE_BAND_SPECS",
    "POWER_BAND_SPECS",
    "SWIM_PACE_BAND_SPECS",
    "ZoneBandSpec",
]
