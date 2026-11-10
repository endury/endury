"""Live intervals.icu load context and zones for the read-only planner views."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from endury.common import clock
from endury.domain.analysis.zones import (
    athlete_zone_set,
    compute_all_zones,
    derive_cycling_lthr_bpm,
)
from endury.domain.models.zones import TrainingZones, ZoneMetric, ZoneSet
from endury.intervals.client import IntervalsClient
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    SportSettingsRow,
)
from endury.planner.foundations.types import SportType
from endury.training.load import LoadMetricContext

logger = logging.getLogger(__name__)


class PlannerRepos(Protocol):
    """Protocol describing the repositories the planner view helpers use."""

    @property
    def athlete(self) -> Any:
        """Athlete profile repository."""
        ...  # pragma: no cover

    @property
    def plan(self) -> Any:
        """Plan repository."""
        ...  # pragma: no cover

    @property
    def self_assessment(self) -> Any:
        """Self-reported training-volume repository."""
        ...  # pragma: no cover

    @property
    def session(self) -> Any:
        """Database session."""
        ...  # pragma: no cover

    @property
    def intervals_client(self) -> IntervalsClient:
        """intervals.icu read/write client."""
        ...  # pragma: no cover


def _settings_by_sport(
    rows: list[SportSettingsRow],
) -> dict[SportType, SportSettingsRow]:
    """Map each planner sport to the first sport-settings row covering it.

    Load-only types (Hike/Walk) never claim a sport's row: a dedicated
    hiking row must not supply RUNNING thresholds.
    """
    by_sport: dict[SportType, SportSettingsRow] = {}
    for row in rows:
        for type_name in row.types or []:
            if type_name in LOAD_ONLY_ACTIVITY_TYPES:
                continue
            sport = SPORT_TYPE_MAP.get(type_name)
            if sport is not None and sport not in by_sport:
                by_sport[sport] = row
    return by_sport


def athlete_zone_sets(sport: SportType, row: SportSettingsRow) -> list[ZoneSet]:
    """Return the zone sets a sport-settings row carries from intervals.icu.

    One set per metric the row holds both a threshold and zone bounds for:
    heart rate on any row, power on the cycling row, pace on the running
    row (min/km) and swim pace on the swimming row (sec/100m).

    Args:
        sport: The planner sport the row covers.
        row: The row, as :func:`_settings_by_sport` maps it.
    """
    sets: list[ZoneSet] = []
    if row.lthr and row.hr_zones:
        sets.append(
            athlete_zone_set(
                sport=sport.value,
                metric=ZoneMetric.HEART_RATE,
                threshold_value=float(row.lthr),
                threshold_label="LTHR",
                upper_bounds=row.hr_zones,
                names=row.hr_zone_names,
                scale=1.0,
            )
        )
    if sport is SportType.CYCLING and row.ftp and row.power_zones:
        sets.append(
            athlete_zone_set(
                sport=sport.value,
                metric=ZoneMetric.POWER,
                threshold_value=float(row.ftp),
                threshold_label="FTP",
                upper_bounds=row.power_zones,
                names=row.power_zone_names,
                scale=row.ftp / 100.0,
            )
        )
    if sport is SportType.RUNNING and row.threshold_pace and row.pace_zones:
        sets.append(
            athlete_zone_set(
                sport=sport.value,
                metric=ZoneMetric.PACE,
                threshold_value=(1000.0 / row.threshold_pace) / 60.0,
                threshold_label="Threshold Pace",
                upper_bounds=row.pace_zones,
                names=row.pace_zone_names,
                scale=1 / 100.0,
                invert=True,
            )
        )
    if sport is SportType.SWIMMING and row.threshold_pace and row.pace_zones:
        sets.append(
            athlete_zone_set(
                sport=sport.value,
                metric=ZoneMetric.SWIM_PACE,
                threshold_value=100.0 / row.threshold_pace,
                threshold_label="CSS",
                upper_bounds=row.pace_zones,
                names=row.pace_zone_names,
                scale=1 / 100.0,
                invert=True,
            )
        )
    return sets


def _athlete_zones(
    by_sport: dict[SportType, SportSettingsRow],
) -> TrainingZones | None:
    """Collect every row's zone sets; ``None`` when no row carries any."""
    sets = [
        zone_set
        for sport, row in by_sport.items()
        for zone_set in athlete_zone_sets(sport, row)
    ]
    return TrainingZones(zone_sets=sets) if sets else None


def get_planner_load_context(repos: PlannerRepos) -> LoadMetricContext | None:
    """Return live threshold anchors for planner load math.

    Reads sport-settings straight from intervals.icu, the source of truth
    for thresholds, so a successful read is stamped with today's date
    rather than tracking staleness locally. A missing cycling LTHR is
    derived from the running one.

    Args:
        repos: Repository container (intervals_client).

    Returns:
        Threshold anchors, or ``None`` when intervals.icu is unreachable
        (missing API key, network failure, ...) or the athlete has no
        sport-settings thresholds on file yet.
    """
    try:
        profile = repos.intervals_client.get_athlete()
        by_sport = _settings_by_sport(profile.sport_settings or [])
    except Exception:
        logger.debug("Could not fetch intervals.icu athlete profile", exc_info=True)
        return None

    run_row = by_sport.get(SportType.RUNNING)
    cycling_row = by_sport.get(SportType.CYCLING)
    swim_row = by_sport.get(SportType.SWIMMING)

    run_lthr = run_row.lthr if run_row is not None else None
    cycling_lthr = cycling_row.lthr if cycling_row is not None else None
    if not cycling_lthr and run_lthr:
        cycling_lthr = derive_cycling_lthr_bpm(int(run_lthr))

    run_pace = run_row.threshold_pace if run_row is not None else None
    swim_pace = swim_row.threshold_pace if swim_row is not None else None
    cycling_ftp = cycling_row.ftp if cycling_row is not None else None

    running_pace_min_per_km = (1000.0 / run_pace) / 60.0 if run_pace else None
    swim_css_sec_per_100m = 100.0 / swim_pace if swim_pace else None

    if not any([running_pace_min_per_km, run_lthr, cycling_ftp, swim_css_sec_per_100m]):
        return None

    training_zones = compute_all_zones(
        lthr_bpm=int(run_lthr) if run_lthr else None,
        cycling_ftp_watts=int(cycling_ftp) if cycling_ftp else None,
        threshold_pace_min_per_km=running_pace_min_per_km,
        css_sec_per_100m=swim_css_sec_per_100m,
        cycling_lthr_bpm=int(cycling_lthr) if cycling_lthr else None,
    )

    return LoadMetricContext(
        running_threshold_pace_min_per_km=running_pace_min_per_km,
        running_lthr_bpm=int(run_lthr) if run_lthr else None,
        running_ftp_watts=None,
        cycling_ftp_watts=float(cycling_ftp) if cycling_ftp else None,
        cycling_lthr_bpm=int(cycling_lthr) if cycling_lthr else None,
        swim_css_sec_per_100m=swim_css_sec_per_100m,
        training_zones=training_zones,
        athlete_zones=_athlete_zones(by_sport),
        threshold_updated_at=clock.today(),
    )
