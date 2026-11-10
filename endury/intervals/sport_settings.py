"""Write an athlete's thresholds to their intervals.icu sport settings.

One planner sport can span several sport-settings rows (an athlete may keep
"Ride" and "VirtualRide" apart), so an edit is written to every row that
covers the sport. Thresholds stay live on intervals.icu: nothing is stored
here, and the next read picks the new values up.
"""

from __future__ import annotations

from typing import Any, Protocol

from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    AthleteProfile,
    SportSettingsRow,
)
from endury.planner.foundations.types import SportType

#: How intervals.icu names each planner sport in its settings.
_SPORT_NAMES = {
    SportType.RUNNING: "Run",
    SportType.CYCLING: "Ride",
    SportType.SWIMMING: "Swim",
}


class SportSettingsWriter(Protocol):
    """The slice of :class:`~endury.intervals.client.IntervalsClient` used here."""

    def get_athlete(self) -> AthleteProfile:
        """Return the athlete profile including sport-settings rows."""

    def update_sport_settings(
        self, row_id: int, fields: dict[str, Any], *, recalc_hr_zones: bool
    ) -> SportSettingsRow:
        """Update one sport-settings row."""


def _sport_types(row: SportSettingsRow, sport: SportType) -> list[str]:
    """The row's activity types that belong to ``sport``.

    Load-only types (Hike/Walk) never claim a row, as when thresholds are
    read, so a dedicated hiking row keeps its own values.
    """
    return [
        name
        for name in row.types or []
        if name not in LOAD_ONLY_ACTIVITY_TYPES and SPORT_TYPE_MAP.get(name) is sport
    ]


def set_thresholds(
    client: SportSettingsWriter,
    sport: SportType,
    *,
    lthr_bpm: int | None = None,
    ftp_watts: int | None = None,
    threshold_pace_sec_per_km: float | None = None,
    css_sec_per_100m: float | None = None,
) -> list[str]:
    """Write thresholds to every sport-settings row covering ``sport``.

    A value left ``None`` is not sent, so it stays as it is on intervals.icu.
    HR zones are recomputed only when LTHR changes, so an FTP edit keeps an
    athlete's own HR zones.

    Args:
        client: The intervals.icu client.
        sport: The planner sport being edited.
        lthr_bpm: Lactate threshold heart rate.
        ftp_watts: Cycling functional threshold power.
        threshold_pace_sec_per_km: Running threshold pace.
        css_sec_per_100m: Swim critical speed.

    Returns:
        The activity types whose thresholds changed, e.g. ``["Ride",
        "VirtualRide"]``.

    Raises:
        ValueError: Nothing to write, or intervals.icu has no settings for
            the sport.
    """
    fields: dict[str, Any] = {}
    if lthr_bpm is not None:
        fields["lthr"] = lthr_bpm
    if ftp_watts is not None:
        fields["ftp"] = ftp_watts
    if threshold_pace_sec_per_km is not None:
        fields["threshold_pace"] = 1000.0 / threshold_pace_sec_per_km
    if css_sec_per_100m is not None:
        fields["threshold_pace"] = 100.0 / css_sec_per_100m
    if not fields:
        raise ValueError("Enter at least one threshold to save.")
    rows = [
        row
        for row in client.get_athlete().sport_settings or []
        if row.id is not None and _sport_types(row, sport)
    ]
    if not rows:
        name = _SPORT_NAMES[sport]
        raise ValueError(
            f"intervals.icu has no {name} sport settings yet. Add {name} under "
            "Settings → Sports on intervals.icu, then save here again."
        )
    updated: list[str] = []
    for row in rows:
        assert row.id is not None  # filtered above; narrows the type
        client.update_sport_settings(
            row.id, fields, recalc_hr_zones=lthr_bpm is not None
        )
        updated.extend(_sport_types(row, sport))
    return updated


__all__ = ["SportSettingsWriter", "set_thresholds"]
