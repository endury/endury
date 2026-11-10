"""Training zone models for heart rate, power, pace, and swim pace.

Defines the zone hierarchy used across the coaching system:
    ZoneMetric   -- what is being measured (HR, power, pace, swim pace)
    ZoneModel    -- the physiological model (LTHR 5-zone, Coggan 7-zone, etc.)
    ZoneBand     -- a single zone band with boundaries in native units
    ZoneSet      -- a complete set of zone bands for one sport + metric
    TrainingZones -- collection of all ZoneSets for an athlete
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from endury.common import clock


class ZoneMetric(StrEnum):
    """What is being measured for this zone set."""

    HEART_RATE = "heart_rate"  # bpm
    POWER = "power"  # watts
    PACE = "pace"  # min/km
    SWIM_PACE = "swim_pace"  # sec/100m


class ZoneModel(StrEnum):
    """Physiological model used to derive zone boundaries."""

    LTHR_5ZONE = "lthr_5zone"
    COGGAN_7ZONE = "coggan_7zone"
    THRESHOLD_PACE_7ZONE = "threshold_pace_7zone"
    CSS_6ZONE = "css_6zone"
    CUSTOM = "custom"


class ZoneBand(BaseModel):
    """A single zone band with boundaries in native units.

    For HR: low/high in bpm (low < high).
    For power: low/high in watts (low < high).
    For pace: low = fast boundary (lower min/km), high = slow boundary.
    For swim pace: low = fast boundary (lower sec/100m), high = slow boundary.
    """

    zone_number: int  # 1-based
    name: str  # e.g. "Zone 2", "Endurance", "Tempo"
    low: float  # lower boundary in native units
    high: float  # upper boundary in native units


class ZoneSet(BaseModel):
    """Complete set of zone bands for one sport + metric combination.

    Represents all zones computed from a single threshold value (e.g.,
    5 HR zones from LTHR, or 7 power zones from FTP).
    """

    sport: str  # "running", "cycling", "swimming", "all"
    metric: ZoneMetric
    model: ZoneModel
    threshold_value: float  # the input threshold in native units
    threshold_label: str  # e.g. "LTHR", "FTP", "Threshold Pace", "CSS"
    bands: list[ZoneBand]
    source: str = "auto"  # "auto" (computed) or "manual" (user override)
    updated_at: datetime = Field(default_factory=clock.now)


class TrainingZones(BaseModel):
    """Collection of all zone sets for an athlete.

    Typically contains HR zones (for running/cycling/all), power zones
    (cycling and/or running), pace zones, and swim pace zones depending
    on what threshold data is available.
    """

    zone_sets: list[ZoneSet] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=clock.now)
    notes: str | None = None

    def get_zone_set(self, sport: str, metric: ZoneMetric) -> ZoneSet | None:
        """Find a zone set by sport and metric.

        Args:
            sport: Sport name (e.g. "running", "cycling").
            metric: Zone metric to match.

        Returns:
            Matching ZoneSet or None.
        """
        for zs in self.zone_sets:
            if zs.sport == sport and zs.metric == metric:
                return zs
        return None

    def get_hr_zones(self, sport: str = "all") -> ZoneSet | None:
        """Get heart rate zones with sport-specific fallback chain.

        Lookup order: exact sport match → "running" → "all".

        The "running" fallback serves as a proxy for sports without a
        dedicated HR zone set (e.g. swimming, or the generic "all" request).
        The "all" fallback covers zone sets stored without a specific sport.

        Args:
            sport: Preferred sport to match (default "all").

        Returns:
            HR ZoneSet for the given sport, or the best available fallback,
            or None if no HR zones are stored.
        """
        specific = self.get_zone_set(sport, ZoneMetric.HEART_RATE)
        if specific:
            return specific
        # Fall back to "running" unless that's already what we tried.
        if sport != "running":
            running = self.get_zone_set("running", ZoneMetric.HEART_RATE)
            if running:
                return running
        # Last resort: a set stored without a specific sport.
        return self.get_zone_set("all", ZoneMetric.HEART_RATE)
