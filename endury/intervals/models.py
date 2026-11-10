"""Pydantic models for the intervals.icu REST API (subset fields).

Only the fields the coach app actually consumes are modeled. Every model sets
``extra="ignore"`` so unmodeled API fields are silently dropped, and treats
its fields as optional-safe: intervals.icu's ``fields=`` projection omits
null values, and wellness/event rows commonly have gaps (an athlete who
never logs HRV, an event with no push errors yet).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from endury.planner.foundations.types import SportType


class SportSettingsRow(BaseModel):
    """One ``sportSettings`` entry from an intervals.icu athlete profile.

    Each row bundles a set of activity type names with the thresholds that
    apply to them (e.g. the "Run" row carries LTHR and threshold pace, the
    "Ride" row carries FTP).
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    types: list[str] | None = None
    ftp: int | None = None
    lthr: int | None = None
    max_hr: int | None = None
    threshold_pace: float | None = None  # m/s
    pace_units: str | None = None
    # The athlete's own zones, as upper bounds: HR in bpm (the top one at
    # max HR), power and pace in % of FTP / threshold speed (the top one
    # 999), each with the names the athlete gave them.
    hr_zones: list[float] | None = None
    hr_zone_names: list[str] | None = None
    power_zones: list[float] | None = None
    power_zone_names: list[str] | None = None
    pace_zones: list[float] | None = None
    pace_zone_names: list[str] | None = None


# Onward destinations intervals.icu can forward a planned workout to, by
# the athlete flag that enables each. Garmin's flag alone carries the
# ``icu_`` prefix. Polar and TrainerRoad are absent on purpose: neither
# accepts planned-workout upload from intervals.icu.
UPLOAD_DESTINATIONS: dict[str, str] = {
    "icu_garmin_upload_workouts": "Garmin",
    "wahoo_upload_workouts": "Wahoo",
    "zwift_upload_workouts": "Zwift",
    "coros_upload_workouts": "Coros",
    "suunto_upload_workouts": "Suunto",
    "zepp_upload_workouts": "Zepp",
    "huawei_upload_workouts": "Huawei",
}


class AthleteProfile(BaseModel):
    """Athlete identity, per-sport thresholds, and upload destinations.

    Returned by ``GET /athlete/{id}``, which responds with the athlete's
    own fields merged with a ``sportSettings`` array (camelCase on the
    wire).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None
    sport_settings: list[SportSettingsRow] | None = Field(
        default=None, alias="sportSettings"
    )
    # Tri-state on the wire: true (on), false (off), null (never connected).
    icu_garmin_upload_workouts: bool | None = None
    wahoo_upload_workouts: bool | None = None
    zwift_upload_workouts: bool | None = None
    coros_upload_workouts: bool | None = None
    suunto_upload_workouts: bool | None = None
    zepp_upload_workouts: bool | None = None
    huawei_upload_workouts: bool | None = None

    def upload_destinations(self) -> list[str]:
        """Return the platforms planned workouts are forwarded to.

        Returns:
            Display names of every enabled destination, in
            `UPLOAD_DESTINATIONS` order. Empty means a pushed plan stays
            on intervals.icu and never reaches a device.
        """
        return [
            label
            for field, label in UPLOAD_DESTINATIONS.items()
            if getattr(self, field, None)
        ]


class ZoneTime(BaseModel):
    """Seconds spent in one power zone (``icu_zone_times`` entry)."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None  # "Z1".."Z7", plus the "SS" sweet-spot overlay
    secs: int | None = None


class IntervalsActivity(BaseModel):
    """One completed activity, projected to the fields the coach app reads.

    Beyond identity/duration/load, this carries the device-derived summary
    intervals.icu computes server-side (HR, power, pace, zone times,
    decoupling, its own interval recap) so the coach can talk about a
    session without a second fetch. Speeds are m/s; ``icu_intensity`` is a
    percentage (77 ≈ IF 0.77); zone-time lists are seconds per zone,
    index 0 = zone 1.
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None
    start_date_local: str | None = None
    type: str | None = None
    source: str | None = None  # e.g. GARMIN_CONNECT, STRAVA, UPLOAD
    device_name: str | None = None  # e.g. "Garmin Forerunner 965"
    moving_time: int | None = None  # seconds
    elapsed_time: int | None = None  # seconds
    icu_recording_time: int | None = None  # seconds, timer duration
    icu_training_load: float | None = None  # their TSS
    distance: float | None = None  # meters
    paired_event_id: int | None = None
    compliance: float | None = None  # planned-vs-actual %
    average_heartrate: int | None = None
    max_heartrate: int | None = None
    icu_average_watts: int | None = None
    icu_weighted_avg_watts: int | None = None  # normalized power
    average_speed: float | None = None  # m/s
    pace: float | None = None  # m/s (moving)
    gap: float | None = None  # grade-adjusted pace, m/s (runs)
    average_cadence: float | None = None
    total_elevation_gain: float | None = None  # meters
    icu_intensity: float | None = None  # IF-equivalent, percent
    icu_variability_index: float | None = None
    icu_efficiency_factor: float | None = None
    decoupling: float | None = None  # percent
    trimp: float | None = None
    icu_hr_zone_times: list[int] | None = None  # seconds per HR zone
    icu_zone_times: list[ZoneTime] | None = None  # power zones
    pace_zone_times: list[int] | None = None  # seconds per pace zone
    icu_ftp: int | None = None  # thresholds used to score this activity
    lthr: int | None = None
    threshold_pace: float | None = None  # m/s
    interval_summary: list[str] | None = None  # e.g. ["4x 7m26s 139bpm"]
    # Athlete-entered on the watch when saving; absent if never answered there.
    feel: int | None = None  # 1-5 ("How did you feel?"), 1 = strong
    icu_rpe: int | None = None  # 1-10 perceived effort
    average_temp: float | None = None  # device temperature, °C
    average_weather_temp: float | None = None  # weather at the start, °C
    carbs_ingested: float | None = None  # grams the athlete logged
    route_id: int | None = None  # intervals.icu's route, when it matched one


class IntervalRow(BaseModel):
    """One interval (rep) or interval group of an activity.

    intervals.icu returns reps in ``icu_intervals`` and rolls repeats up
    into ``icu_groups`` (same stats plus ``count``); one model serves both.
    """

    model_config = ConfigDict(extra="ignore")

    type: str | None = None  # WORK | RECOVERY
    label: str | None = None
    group_id: str | None = None
    count: int | None = None  # groups only
    start_time: int | None = None  # seconds from activity start
    elapsed_time: int | None = None
    moving_time: int | None = None
    distance: float | None = None  # meters
    average_heartrate: int | None = None
    max_heartrate: int | None = None
    average_watts: int | None = None
    weighted_average_watts: int | None = None
    average_speed: float | None = None  # m/s
    gap: float | None = None  # m/s
    average_cadence: float | None = None
    zone: int | None = None
    intensity: float | None = None  # percent
    decoupling: float | None = None


class IntervalsActivityDetail(IntervalsActivity):
    """One activity with its per-interval breakdown (``GET /activity/{id}``)."""

    # intervals.icu sends these as JSON null rather than [] when empty.
    icu_intervals: list[IntervalRow] = Field(default_factory=list)
    icu_groups: list[IntervalRow] = Field(default_factory=list)
    # Zone-bound lists and trace-selection flags, used by the activity trace
    # projection (endury.intervals.trace) -- not requested via `fields=`
    # elsewhere, so these live only on the detail model.
    icu_hr_zones: list[int] | None = None  # bpm upper bounds
    icu_power_zones: list[float] | None = None  # % of FTP upper bounds
    pace_zones: list[float] | None = None  # % of threshold speed upper bounds
    use_gap_zone_times: bool | None = None  # athlete scores runs on GAP
    pool_length: float | None = None  # metres, set only for a pool swim

    @field_validator("icu_intervals", "icu_groups", mode="before")
    @classmethod
    def _null_list_is_empty(cls, value: object) -> object:
        """Coerce a null list field to an empty list."""
        return [] if value is None else value


class ActivityStream(BaseModel):
    """One raw data stream row (``GET /activity/{id}/streams.json``)."""

    model_config = ConfigDict(extra="ignore")

    type: str
    # intervals.icu sends this as JSON null rather than [] when the stream
    # carries no data.
    data: list[float | None] = Field(default_factory=list)
    # The second series of a paired stream: ``latlng`` sends latitudes in
    # ``data`` and longitudes here.
    data2: list[float | None] = Field(default_factory=list)

    @field_validator("data", "data2", mode="before")
    @classmethod
    def _null_list_is_empty(cls, value: object) -> object:
        """Coerce a null data list to an empty list."""
        return [] if value is None else value


class WellnessDay(BaseModel):
    """One daily wellness row keyed by ISO date."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None  # ISO date, e.g. "2026-08-07"
    ctl: float | None = None  # combined CTL (cross-check only)
    atl: float | None = None  # combined ATL (cross-check only)
    sleep_secs: int | None = Field(default=None, alias="sleepSecs")
    sleep_score: float | None = Field(default=None, alias="sleepScore")
    hrv: float | None = None
    resting_hr: int | None = Field(default=None, alias="restingHR")
    weight: float | None = None
    fatigue: int | None = None
    soreness: int | None = None


class IntervalsPushError(BaseModel):
    """One onward device-sync failure recorded against a pushed event."""

    model_config = ConfigDict(extra="ignore")

    service: str | None = None
    message: str | None = None
    date: str | None = None


class IntervalsEvent(BaseModel):
    """One calendar event (planned workout, race, or note)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    external_id: str | None = None
    start_date_local: str | None = None
    type: str | None = None
    name: str | None = None
    description: str | None = None
    category: str | None = None
    # Server-computed for WORKOUT events from the DSL in the description —
    # we never set them on push (docs/reference/intervals_api.md), only read them back.
    moving_time: int | None = None
    icu_training_load: float | None = None
    # Server-parsed steps of a WORKOUT event, read back for the calendar
    # (never sent on push — we author the DSL, intervals.icu parses it).
    workout_doc: dict[str, Any] | None = None
    # intervals.icu sends these as JSON null rather than [] when empty.
    tags: list[str] = Field(default_factory=list)
    push_errors: list[IntervalsPushError] = Field(default_factory=list)

    @field_validator("tags", "push_errors", mode="before")
    @classmethod
    def _null_list_is_empty(cls, value: object) -> object:
        """Coerce a null list field to an empty list."""
        return [] if value is None else value


#: Maps intervals.icu activity-type strings to our SportType; unmapped or
#: non-endurance types (e.g. "WeightTraining", "Yoga") are intentionally
#: absent so ``SPORT_TYPE_MAP.get(activity.type)`` returns None for them.
#: "Hike" and "Walk" count as RUNNING load: trail plans prescribe
#: power-hiking on steep climbs (activity run_vert_hike), watches log that
#: as "Hike", and walk-heavy trail outings are real training for
#: low-volume athletes.
SPORT_TYPE_MAP: dict[str, SportType] = {
    "Run": SportType.RUNNING,
    "TrailRun": SportType.RUNNING,
    "VirtualRun": SportType.RUNNING,
    "Hike": SportType.RUNNING,
    "Walk": SportType.RUNNING,
    "Ride": SportType.CYCLING,
    "VirtualRide": SportType.CYCLING,
    "GravelRide": SportType.CYCLING,
    "MountainBikeRide": SportType.CYCLING,
    "Swim": SportType.SWIMMING,
    "OpenWaterSwim": SportType.SWIMMING,
}

#: Activity types that carry training load (see ``SPORT_TYPE_MAP``) but
#: MUST NOT resolve sport-settings rows or serve as a race-prediction
#: basis: a dedicated Hike/Walk settings row holds hiking thresholds, not
#: the running LTHR/threshold pace the planner and coach need, and a hike
#: is not a pace-representative running effort.
LOAD_ONLY_ACTIVITY_TYPES: frozenset[str] = frozenset({"Hike", "Walk"})

#: A walk shorter than this is everyday movement, not training: it adds
#: no running load or volume. A hike always counts (trail plans prescribe it).
WALK_FLOOR_SECONDS = 30 * 60


def training_sport(activity: IntervalsActivity) -> SportType | None:
    """Return the planner sport an activity trains, or None.

    Args:
        activity: An intervals.icu activity.

    Returns:
        The sport from ``SPORT_TYPE_MAP``; None for an unmapped type and
        for a walk under ``WALK_FLOOR_SECONDS`` of moving time.
    """
    if activity.type == "Walk" and (activity.moving_time or 0) < WALK_FLOOR_SECONDS:
        return None
    return SPORT_TYPE_MAP.get(activity.type or "")
