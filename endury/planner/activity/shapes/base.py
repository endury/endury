"""ActivityDefinition base class and shared helpers for planner activity library."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from endury.planner.activity.capabilities import ActivityCapabilityProfile
from endury.planner.activity.config import (
    HR_ZONE_THRESHOLDS,
    POWER_ZONE_FLOOR,
    POWER_ZONE_THRESHOLDS,
    REST_TSS_THRESHOLD,
    RUNNING_PACE_ZONE_FLOOR,
    RUNNING_PACE_ZONE_THRESHOLDS,
    TARGET_BAND_FRACTION,
)
from endury.planner.foundations.types import (
    TID_CUTS,
    TID_HIGH_IF_THRESHOLD,
    TID_LOW_IF_THRESHOLD,
    ActivityFamily,
    ActivityHardness,
    ActivityKey,
    SportType,
    TargetPreference,
    TIDFractions,
)
from endury.training.load import (
    ResolverProtocol,
    pace_min_per_km_from_threshold,
    power_watts_from_ftp,
)
from endury.training.workout import (
    DurationDef,
    StructuredWorkout,
    TargetDef,
    WorkoutStep,
)
from endury.training.zones import HR_BAND_SPECS, POWER_BAND_SPECS, ZoneBandSpec

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _dur_seconds(seconds: float) -> DurationDef:
    """Construct a time duration."""
    return DurationDef(type="time", value_seconds=float(seconds))


def _rpe_target(rpe: int) -> TargetDef:
    """Create an RPE target."""
    return TargetDef(type="rpe", rpe=int(rpe))


def _open_target() -> TargetDef:
    """Create an open target."""
    return TargetDef(type="open")


def _if_to_tid_fractions(if_value: float) -> TIDFractions:
    """Return a single-band TIDFractions for a constant intensity factor.

    Args:
        if_value: Intensity factor for the step.

    Returns:
        TIDFractions with 1.0 in the matching zone band.
    """
    if if_value < TID_LOW_IF_THRESHOLD:
        return TIDFractions(z_low=1.0, z_mid=0.0, z_high=0.0)
    if if_value < TID_HIGH_IF_THRESHOLD:
        return TIDFractions(z_low=0.0, z_mid=1.0, z_high=0.0)
    return TIDFractions(z_low=0.0, z_mid=0.0, z_high=1.0)


def _tid_from_segments(
    segments: list[tuple[float, float]], sport: SportType
) -> TIDFractions:
    """Compute TIDFractions by duration-weighting a list of (minutes, if) segments.

    Args:
        segments: List of (duration_minutes, intensity_factor) pairs.
        sport: The sport whose cut points read the IF (``TID_CUTS``).

    Returns:
        Duration-weighted TIDFractions across all segments.
    """
    total = sum(dur for dur, _ in segments)
    if total <= 0.0:
        return TIDFractions(z_low=1.0, z_mid=0.0, z_high=0.0)
    low, high = TID_CUTS[sport]
    z_low = sum(dur for dur, if_ in segments if if_ < low) / total
    z_mid = sum(dur for dur, if_ in segments if low <= if_ < high) / total
    z_high = sum(dur for dur, if_ in segments if if_ >= high) / total
    return TIDFractions(z_low=z_low, z_mid=z_mid, z_high=z_high)


def _if_to_zone(if_value: float, thresholds: tuple[tuple[int, float], ...]) -> int:
    """Map an intensity factor to a zone number.

    Args:
        if_value: Intensity factor (fraction of threshold).
        thresholds: Ordered (zone_number, upper_bound) pairs.

    Returns:
        Zone number (1-based).
    """
    for zone, upper in thresholds:
        if if_value <= upper:
            return zone
    return thresholds[-1][0]


def _hr_zone(if_value: float, sport: SportType | None) -> int:
    """Return the HR zone a step shows, at the rung its prescribed metric sits.

    A run's intensity factor is a fraction of threshold speed, so its HR
    zone is its pace zone (Friel's pace and HR zones are rungs of one
    ladder), the HR table's top zone at most: pace and HR name one zone.
    Any other step reads the HR table, which takes an IF as a fraction of
    LTHR and so puts work at tempo or harder a zone low: from power Z3 up
    the step shows the higher of the two zones (the HR table's top zone at
    most), while easy work keeps the HR table's zone, since power Z2
    reaches efforts the HR table rightly calls Z1.

    Args:
        if_value: Intensity factor for the step.
        sport: Sport of the step; ``None`` reads as a non-running step.

    Returns:
        HR zone number (1-based).
    """
    top = HR_ZONE_THRESHOLDS[-1][0]
    if sport == SportType.RUNNING:
        return min(_if_to_zone(if_value, RUNNING_PACE_ZONE_THRESHOLDS), top)
    zone = _if_to_zone(if_value, HR_ZONE_THRESHOLDS)
    power = _if_to_zone(if_value, POWER_ZONE_THRESHOLDS)
    if power < 3:
        return zone
    return max(zone, min(power, top))


def _athlete_zone_set(
    resolver: ResolverProtocol | None, sport: SportType, metric: str
) -> Any | None:
    """Return the athlete's own intervals.icu zone set for ``metric``, or ``None``.

    The band specs prescribe a step (its intensity, pace, watts, bpm); the
    zone *number* beside the prescription is the athlete's, read off the
    zones they keep on intervals.icu, so the card, the calendar and the
    debrief agree. Without such a set the band spec's zone stands in.

    Args:
        resolver: Optional resolver carrying ``athlete_zones``.
        sport: Sport of the step.
        metric: ``"heart_rate"``, ``"power"``, ``"pace"`` or ``"swim_pace"``.
    """
    zones = getattr(resolver, "athlete_zones", None)
    if zones is None:
        return None
    return zones.get_zone_set(sport.value, metric)


def _zone_holding(zone_set: Any, low: float | None, high: float) -> int | None:
    """Return the number of the zone that holds the whole ``low``-``high`` band.

    Every target passes the band it prescribes (``low`` ``None`` for an
    open HR zone 1) and gets a number only when the band lies inside one
    zone: a band that crosses the athlete's zones is shown as its pace,
    watts or bpm alone, since a number from either scheme would contradict
    what the calendar files the step under.

    Args:
        zone_set: The athlete's zone set for the step's metric.
        low: Bottom of the band in native units, or ``None`` for no bottom.
        high: Top of the band in native units.
    """
    bands = zone_set.bands
    if low is None:
        low = bands[0].low
    return next(
        (band.zone_number for band in bands if band.low <= low and high <= band.high),
        None,
    )


def _zone_tag(
    resolver: ResolverProtocol | None,
    sport: SportType,
    metric: str,
    target_type: str,
    low: float,
    high: float,
    if_value: float,
    thresholds: tuple[tuple[int, float], ...],
    floor: float,
) -> TargetDef | None:
    """Return the zone tag beside an explicit pace or power band, if any.

    With the athlete's own zone set for ``metric`` the tag is the zone
    holding the whole ``low``-``high`` band, and there is none when the
    band crosses their zones (:func:`_zone_holding`). Without their zones
    Endury's band spec numbers it (:func:`_zone_tag_target`).

    Args:
        resolver: Optional resolver carrying ``athlete_zones``.
        sport: Sport of the step.
        metric: See :func:`_athlete_zone_set`.
        target_type: Tag discriminator (``pace_zone`` or ``power_zone``).
        low: Bottom of the band in native units.
        high: Top of the band in native units.
        if_value: Intensity factor for the step.
        thresholds: Endury's ordered (zone_number, upper_bound) pairs.
        floor: Lowest intensity factor Endury's zone table covers.
    """
    zone_set = _athlete_zone_set(resolver, sport, metric)
    if zone_set is None:
        return _zone_tag_target(target_type, if_value, thresholds, floor)
    zone = _zone_holding(zone_set, low, high)
    return None if zone is None else TargetDef(type=target_type, zone=zone)


def _zone_tag_target(
    target_type: str,
    if_value: float,
    thresholds: tuple[tuple[int, float], ...],
    floor: float,
) -> TargetDef | None:
    """Build the zone tag that accompanies an explicit pace/power band.

    Returns ``None`` when ``if_value`` sits below ``floor``, the bottom of
    the zone table.  :func:`_if_to_zone` only checks upper bounds, so a
    sub-Zone-1 effort would otherwise be tagged Zone 1 while the explicit
    pace/power band printed beside it lies outside the athlete's real Zone 1
    — a warm-up at IF 0.65 against a pace table whose Zone 1 starts at IF
    0.7143.  Dropping the tag leaves the explicit band to carry the
    prescription; sub-Zone-1 recovery effort has no honest zone number.

    Args:
        target_type: Target discriminator (``pace_zone`` or ``power_zone``).
        if_value: Intensity factor for the step.
        thresholds: Ordered (zone_number, upper_bound) pairs.
        floor: Lowest intensity factor the zone table covers.

    Returns:
        A zone-tag ``TargetDef``, or ``None`` below the bottom zone.
    """
    if if_value < floor:
        return None
    return TargetDef(type=target_type, zone=_if_to_zone(if_value, thresholds))


def _zone_target(
    if_value: float,
    rpe: int,
    sport: SportType = SportType.RUNNING,
    resolver: ResolverProtocol | None = None,
    easy_hr_band: tuple[float, float] | None = None,
) -> tuple[TargetDef, TargetDef]:
    """Create an HR-zone target with RPE secondary.

    Args:
        if_value: Intensity factor for the step.
        rpe: RPE value for the secondary target.
        sport: Sport type used to select the correct zone set.
        resolver: Optional resolver with threshold and zone data.
        easy_hr_band: Fractions of LTHR that replace Zone 1's open band
            when the step lands in Zone 1 and the athlete's LTHR is known.

    Returns:
        Tuple of (HR zone target, RPE target).
    """
    zone = _hr_zone(if_value, sport)
    low_bpm: int | None = None
    high_bpm: int | None = None

    if resolver is not None:
        tz = getattr(resolver, "training_zones", None)
        if tz is not None:
            sport_str = sport.value.lower()
            hr_zones = tz.get_hr_zones(sport_str)
            if hr_zones is not None:
                band = next((b for b in hr_zones.bands if b.zone_number == zone), None)
                if band is not None:
                    low_bpm = int(band.low) if band.low > 0 else None
                    high_bpm = int(band.high)
                if easy_hr_band is not None and zone == 1:
                    lthr = hr_zones.threshold_value
                    low_bpm, high_bpm = (round(lthr * f) for f in easy_hr_band)

    label: int | None = zone
    athlete = _athlete_zone_set(resolver, sport, "heart_rate")
    if athlete is not None and high_bpm is not None:
        label = _zone_holding(athlete, low_bpm, high_bpm)
    primary = TargetDef(
        type="heart_rate_zone", zone=label, low_bpm=low_bpm, high_bpm=high_bpm
    )
    return primary, _rpe_target(rpe)


def _build_step_targets(
    if_value: float,
    sport: SportType,
    rpe: int,
    preference: TargetPreference,
    resolver: ResolverProtocol | None = None,
    easy_hr_band: tuple[float, float] | None = None,
) -> tuple[TargetDef, TargetDef, list[TargetDef]]:
    """Build (primary, secondary, additional_targets) based on preference.

    Args:
        if_value: Intensity factor for the step.
        sport: Sport type to determine zone metric.
        rpe: RPE value.
        preference: Which metric the athlete should see first.
        resolver: Optional resolver with threshold data.
        easy_hr_band: See :func:`_zone_target`.

    Returns:
        Tuple of (primary, secondary, additional_targets list).
    """
    hr_target, _ = _zone_target(if_value, rpe, sport, resolver, easy_hr_band)
    rpe_target = _rpe_target(rpe)

    pace_target: TargetDef | None = None
    run_power_target: TargetDef | None = None
    bike_power_target: TargetDef | None = None
    pace_zone_target: TargetDef | None = None
    power_zone_target: TargetDef | None = None

    if resolver is not None:
        if sport == SportType.RUNNING:
            threshold_pace = getattr(
                resolver,
                "running_threshold_pace_min_per_km",
                None,
            )
            if threshold_pace is not None and threshold_pace > 0:
                center = pace_min_per_km_from_threshold(threshold_pace, if_value)
                low = round(center * (1 - TARGET_BAND_FRACTION), 2)
                high = round(center * (1 + TARGET_BAND_FRACTION), 2)
                pace_target = TargetDef(
                    type="pace", low_min_per_km=low, high_min_per_km=high
                )
                pace_zone_target = _zone_tag(
                    resolver,
                    sport,
                    "pace",
                    "pace_zone",
                    low,
                    high,
                    if_value,
                    RUNNING_PACE_ZONE_THRESHOLDS,
                    RUNNING_PACE_ZONE_FLOOR,
                )
            run_ftp = getattr(resolver, "running_ftp_watts", None)
            if run_ftp is not None and run_ftp > 0:
                center_w = power_watts_from_ftp(run_ftp, if_value)
                run_power_target = TargetDef(
                    type="power",
                    low_watts=round(center_w * (1 - TARGET_BAND_FRACTION)),
                    high_watts=round(center_w * (1 + TARGET_BAND_FRACTION)),
                )
        elif sport == SportType.CYCLING:
            cycling_ftp = getattr(resolver, "cycling_ftp_watts", None)
            if cycling_ftp is not None and cycling_ftp > 0:
                center_w = power_watts_from_ftp(cycling_ftp, if_value)
                low_w = round(center_w * (1 - TARGET_BAND_FRACTION))
                high_w = round(center_w * (1 + TARGET_BAND_FRACTION))
                bike_power_target = TargetDef(
                    type="power", low_watts=low_w, high_watts=high_w
                )
                power_zone_target = _zone_tag(
                    resolver,
                    sport,
                    "power",
                    "power_zone",
                    low_w,
                    high_w,
                    if_value,
                    POWER_ZONE_THRESHOLDS,
                    POWER_ZONE_FLOOR,
                )

    power_target = run_power_target or bike_power_target

    primary, secondary, additional = _order_targets(
        preference,
        hr_target,
        rpe_target,
        pace_target,
        power_target,
    )
    zone_extras = [t for t in (pace_zone_target, power_zone_target) if t is not None]
    return primary, secondary, additional + zone_extras


def _order_targets(
    preference: TargetPreference,
    hr_target: TargetDef,
    rpe_target: TargetDef,
    pace_target: TargetDef | None,
    power_target: TargetDef | None,
) -> tuple[TargetDef, TargetDef, list[TargetDef]]:
    """Order targets by preference, collecting extras into additional_targets.

    Args:
        preference: Which metric the athlete should see first.
        hr_target: Heart rate zone target (always available).
        rpe_target: RPE target (always available).
        pace_target: Optional pace target.
        power_target: Optional power target.

    Returns:
        Tuple of (primary, secondary, additional_targets list).
    """
    if preference == TargetPreference.PACE_FIRST:
        if pace_target is not None:
            extras = [t for t in (power_target, rpe_target) if t is not None]
            return pace_target, hr_target, extras
        if power_target is not None:
            return power_target, hr_target, [rpe_target]
        return hr_target, rpe_target, []

    if preference == TargetPreference.POWER_FIRST:
        if power_target is not None:
            extras = [t for t in (pace_target, rpe_target) if t is not None]
            return power_target, hr_target, extras
        return hr_target, rpe_target, []

    if preference == TargetPreference.RPE_FIRST:
        extras = [t for t in (pace_target, power_target) if t is not None]
        return rpe_target, hr_target, extras

    # HR_FIRST (default).
    extras = [t for t in (pace_target, power_target) if t is not None]
    if extras:
        return hr_target, rpe_target, extras
    return hr_target, rpe_target, []


def _rest_workout(name: str) -> StructuredWorkout:
    """Create a zero-load rest workout."""
    return StructuredWorkout(
        workout_name=name,
        steps=[WorkoutStep(type="rest", description="Rest day")],
    )


def _power_zone_mismatch_note(if_value: float) -> str:
    """Return a one-line advisory when running-power zone differs from pace zone.

    Coggan power Z4 spans IF 0.91-1.05, while pace/HR Z4 (Threshold) caps at
    1.00.  So for IF in 1.00-1.05 the athlete-facing power display shows
    Z4 while pace/HR show Z5.  Similarly for IF 1.05-1.10 power may lag
    behind pace by one zone.  The note flags this so the athlete doesn't
    chase a higher power target thinking the pace zone is lower than
    intended.

    Args:
        if_value: Intensity factor (fraction of threshold) for the step.

    Returns:
        Advisory sentence, or empty string when zones agree.
    """
    pace_zone = _if_to_zone(if_value, RUNNING_PACE_ZONE_THRESHOLDS)
    power_zone = _if_to_zone(if_value, POWER_ZONE_THRESHOLDS)
    if pace_zone != power_zone:
        return (
            " (Note: running-power zone may read differently from pace/HR "
            "at this intensity — trust the pace/HR target.)"
        )
    return ""


#: Athlete-facing coaching cue per zone band, keyed by ``ZoneBandSpec.name``
#: across the HR and power tables (power Z1/Z2 carry HR Z1/Z2's wording).
#: The wording matches the band name so a step's cue never disagrees with
#: its displayed zone: an interval in HR Z2 Aerobic always reads "Aerobic
#: effort", never "tempo" or "threshold".
_ZONE_BAND_PHRASES: dict[str, str] = {
    "Recovery": ("Easy aerobic effort — conversational pace, building base endurance"),
    "Aerobic": "Aerobic effort — comfortable, full sentences possible",
    "Tempo": "Tempo effort — comfortably hard, short phrases only",
    "Threshold": ("Threshold effort — sustainably hard, at or near lactate threshold"),
    "VO2max": "VO₂max effort — hard, near maximal sustainable",
}
_ZONE_BAND_PHRASES["Active Recovery"] = _ZONE_BAND_PHRASES["Recovery"]
_ZONE_BAND_PHRASES["Endurance"] = _ZONE_BAND_PHRASES["Aerobic"]

#: Bands at or above VO2max, where rep length picks the wording.
_SUPRA_THRESHOLD_BANDS: frozenset[str] = frozenset(
    {"VO2max", "Anaerobic", "Neuromuscular"}
)


def _description_band(if_value: float, sport: SportType | None) -> ZoneBandSpec:
    """Return the zone band that names a step's effort.

    A cycling step's intensity factor is a fraction of FTP, so its band
    comes from the power table: 87 % FTP is power Z3 Tempo, where the HR
    table (85-90 % LTHR) would call it Z2 Aerobic.  Any other step is named
    by the HR zone its card shows (:func:`_hr_zone`).

    Args:
        if_value: Intensity factor (fraction of threshold) for the step.
        sport: Sport for the step; ``None`` reads as a non-cycling step.

    Returns:
        The band whose zone the step's prescribed metric is tagged with.
    """
    if sport == SportType.CYCLING:
        zone_num = _if_to_zone(if_value, POWER_ZONE_THRESHOLDS)
        return next(b for b in POWER_BAND_SPECS if b.zone_number == zone_num)
    zone_num = _hr_zone(if_value, sport)
    return next(b for b in HR_BAND_SPECS if b.zone_number == zone_num)


def _step_description(
    step_type: str,
    if_value: float,
    *,
    sport: SportType | None = None,
    target_preference: TargetPreference | None = None,
    work_duration_s: float | None = None,
    rest_duration_s: float | None = None,
) -> str:
    """Return a brief athlete-facing coaching cue for a workout step.

    The interval phrasing is derived from the zone band the IF lands in for
    the sport's prescribed metric — power for cycling, HR otherwise
    (:func:`_description_band`) — so the words and the displayed zone
    number agree by construction. The running-power-vs-pace disclaimer is
    only appended when the athlete actually sees power as the primary
    metric — i.e. running with
    ``TargetPreference.POWER_FIRST``.

    Two duration-aware refinements override the band phrasing:

    * Neuromuscular reps in the VO2max band or above that are short enough to be
      pure sprints/strides (work ≤ 30 s) use sprint wording, not the
      "near maximal sustainable" boilerplate that suits 3-5 min reps.
      Reps between 30 and 90 s in that band read as "speed rep" instead:
      VO2 kinetics need roughly 90 s of sustained work before uptake
      approaches VO2max, so a shorter rep cannot honestly claim that
      effort.
    * Recovery steps whose duration meets or exceeds the preceding work
      step are not really recovery — they are steady-state work between
      race-pace blocks — and read as such rather than as
      "Active recovery".

    Args:
        step_type: One of 'warmup', 'cooldown', 'recovery', 'rest', or 'interval'.
        if_value: Intensity factor (fraction of threshold) for the step.
        sport: Sport for the step; used to scope the power disclaimer.
        target_preference: Whether the athlete sees power, pace, HR, or RPE
            first; used to scope the power disclaimer.
        work_duration_s: Duration of the work rep in seconds.  When supplied,
            short VO2max-band intervals (≤ 30 s) render as sprint/stride
            cues instead of the standard VO2max copy.
        rest_duration_s: Duration of the recovery rep in seconds.  When
            ``step_type == "recovery"`` and ``rest_duration_s`` meets or
            exceeds ``work_duration_s``, the cue describes steady-state
            riding/running rather than active recovery.

    Returns:
        Short description string for display in a workout app or plan view.
    """
    if step_type == "warmup":
        return "Easy warm-up — gradually increase effort, keep HR in Zone 1-2"
    if step_type == "cooldown":
        return "Easy cool-down — flush legs and bring HR down gradually"
    if step_type == "recovery":
        if (
            work_duration_s is not None
            and rest_duration_s is not None
            and rest_duration_s >= work_duration_s
            and rest_duration_s >= 300.0
        ):
            return (
                "Steady-state aerobic — keep pedalling/running smoothly between "
                "race-pace blocks, not coasting"
            )
        return "Active recovery — keep moving, very easy effort"
    if step_type == "rest":  # pragma: no cover
        return "Rest"  # pragma: no cover
    band = _description_band(if_value, sport)
    short_sprint = (
        band.name in _SUPRA_THRESHOLD_BANDS
        and work_duration_s is not None
        and work_duration_s <= 30.0
    )
    speed_rep = (
        band.name in _SUPRA_THRESHOLD_BANDS
        and work_duration_s is not None
        and 30.0 < work_duration_s < 90.0
    )
    if short_sprint:
        base = (
            "Short sprint / stride — fast, relaxed turnover, accelerate "
            "smoothly and float to the finish"
        )
    elif speed_rep:
        base = (
            "Speed rep — fast but controlled, well above threshold; take the "
            "full recovery so every rep stays quick"
        )
    else:
        base = _ZONE_BAND_PHRASES.get(
            band.name, f"Z{band.zone_number} {band.name} effort"
        )
    note = ""
    if sport == SportType.RUNNING and target_preference == TargetPreference.POWER_FIRST:
        note = _power_zone_mismatch_note(if_value)
    return base + note


# ---------------------------------------------------------------------------
# ActivityDefinition base class
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityDefinition:
    """Abstract activity with a single continuous TSS knob."""

    key: ActivityKey
    sport: SportType
    family: ActivityFamily
    hardness: ActivityHardness
    tss_min: float
    tss_max: float
    target_preference: TargetPreference
    capability_profile: ActivityCapabilityProfile | None

    def __post_init__(self) -> None:
        """Validate activity definition bounds."""
        if self.tss_min < 0:
            raise ValueError("tss_min must be >= 0")
        if self.tss_max < self.tss_min:
            raise ValueError("tss_max must be >= tss_min")
        if self.capability_profile is None:
            object.__setattr__(
                self,
                "capability_profile",
                ActivityCapabilityProfile(
                    sport=self.sport,
                    family=self.family,
                    stimulus_tags=(),
                    recovery_ok=self.family
                    in {ActivityFamily.EASY, ActivityFamily.RECOVERY},
                    taper_ok=self.family != ActivityFamily.INTERVAL,
                ),
            )
        assert self.capability_profile is not None
        if self.capability_profile.sport != self.sport:
            raise ValueError("capability_profile sport must match activity sport")
        if self.capability_profile.family != self.family:
            raise ValueError("capability_profile family must match activity family")

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Materialize a structured workout for a target TSS."""
        raise NotImplementedError  # pragma: no cover

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact workout duration in minutes."""
        raise NotImplementedError  # pragma: no cover

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute achieved TSS after all deterministic quantization."""
        raise NotImplementedError  # pragma: no cover

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return the session's ``(minutes, intensity factor)`` segments, in order.

        The physics every load reading derives from: TSS is the sum of
        ``minutes / 60 * IF**2 * 100`` over the segments and the training
        intensity distribution is their duration-weighted zone split. Wall
        rest in swim sets is not a segment. A rest target has no segments.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver (unused in base; available
                to subclasses).

        Returns:
            ``(minutes, intensity factor)`` per segment, in order.
        """
        raise NotImplementedError  # pragma: no cover

    def tid_fractions(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> TIDFractions:
        """Compute training intensity distribution for a given TSS target.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver, passed to :meth:`segments`.

        Returns:
            TIDFractions representing the fraction of session time in each
            zone band for this activity at the given load.
        """
        return _tid_from_segments(self.segments(target_tss, resolver), self.sport)

    def _clamp_target(self, target_tss: float) -> float:
        """Clamp requested TSS into realizable activity range."""
        return min(max(target_tss, 0.0), self.tss_max)

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Project a requested TSS value onto the activity's feasible set."""
        _ = resolver
        lower = self.tss_min if lower_bound_tss is None else lower_bound_tss
        upper = self.tss_max if upper_bound_tss is None else upper_bound_tss
        if upper < lower:
            upper = lower
        return min(max(target_tss, lower), upper)

    def _cadence_target(self) -> TargetDef | None:
        """Return a cadence target if cadence bounds are configured."""
        low_rpm = getattr(self, "cadence_low_rpm", None)
        high_rpm = getattr(self, "cadence_high_rpm", None)
        if low_rpm is not None and high_rpm is not None:
            return TargetDef(
                type="cadence",
                low_rpm=low_rpm,
                high_rpm=high_rpm,
            )
        return None

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from a target duration for duration-first materialization.

        Returns None by default, meaning this activity uses TSS-primary
        materialization.  Volume-oriented subclasses override to return a
        concrete TSS so the materializer can honour the duration intent.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Optional threshold resolver.

        Returns:
            Derived TSS, or None if duration-first is not supported.
        """
        _ = resolver
        return None

    def _closest_duration_tss(
        self,
        duration_minutes: float,
        candidates: Iterable[float],
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Return the candidate TSS whose realized duration best matches.

        Shared tail of the flexible-rep ``tss_from_duration`` inversions
        (`docs/specs/planner/activity_library.md`, R-SESS-B-008): the caller
        enumerates one lattice candidate per rep count and this picks the
        one whose *post-solve* duration (``duration_minutes_exact``
        re-solves the rep pattern, including clamping and quantization)
        lands closest to the requested duration. Ties resolve to the
        earliest candidate (fewest reps), matching the TSS-space solvers'
        preference.

        Args:
            duration_minutes: Desired total session duration in minutes.
            candidates: Achievable lattice TSS values, ordered by rep count.
            resolver: Optional threshold resolver.

        Returns:
            The candidate TSS minimizing realized-duration error.
        """
        return min(
            candidates,
            key=lambda tss: abs(
                self.duration_minutes_exact(tss, resolver=resolver) - duration_minutes
            ),
        )

    def _is_rest_target(self, target_tss: float) -> bool:
        """Return True when a target should become a no-load rest session."""
        return target_tss <= REST_TSS_THRESHOLD


# ---------------------------------------------------------------------------
# FixedProtocolActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixedProtocolActivity(ActivityDefinition):
    """Fixed-structure fitness test with predetermined workout steps.

    Unlike other activity types the workout structure is not parameterised by
    target TSS — the protocol is fixed (e.g. "20 min all-out"). ``tss_min``
    and ``tss_max`` bracket the expected TSS range for planning purposes, and
    ``build()`` always produces the canonical protocol steps.

    Duration-independent by design: ``tss_from_duration`` stays ``None``, so
    the card never resizes with solved hours — the protocol is the protocol
    (the one shape exempt from the duration-first sizing every other shape
    implements; see `docs/specs/planner/activity_library.md`, R-SESS-B-008).

    Optional structured warm-up: when ``warmup_strides_count > 0``, ``build()``
    inserts a strides repeat block between the warm-up and the protocol step
    (e.g. SWIM_CSS_TEST's "4 x 50 m build strokes" priming before the 400 m
    TT). Strides duration / rest are timed in seconds and contribute their
    own zone target.
    """

    protocol_duration_min: float
    warmup_min: float
    cooldown_min: float
    if_warmup: float
    if_protocol: float
    if_cooldown: float
    protocol_description: str
    protocol_steps: tuple[WorkoutStep, ...] = ()
    warmup_strides_count: int = 0
    warmup_strides_duration_seconds: float = 0.0
    warmup_strides_rest_seconds: float = 0.0
    if_warmup_strides: float = 0.0
    warmup_strides_description: str = "build stride"

    def _strides_total_minutes(self) -> float:
        """Return total minutes consumed by strides + their rests."""
        if self.warmup_strides_count <= 0:
            return 0.0
        return (
            self.warmup_strides_count
            * (self.warmup_strides_duration_seconds + self.warmup_strides_rest_seconds)
            / 60.0
        )

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return fixed total duration regardless of target TSS."""
        _ = resolver
        return (
            self.warmup_min
            + self._strides_total_minutes()
            + self.protocol_duration_min
            + self.cooldown_min
        )

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return midpoint of the TSS range (protocol load is fixed)."""
        _ = resolver
        return (self.tss_min + self.tss_max) / 2.0

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        segments: list[tuple[float, float]] = [
            (self.warmup_min, self.if_warmup),
            (self.protocol_duration_min, self.if_protocol),
            (self.cooldown_min, self.if_cooldown),
        ]
        if self.warmup_strides_count > 0:
            stride_min = (
                self.warmup_strides_count * self.warmup_strides_duration_seconds / 60.0
            )
            rest_min = (
                self.warmup_strides_count * self.warmup_strides_rest_seconds / 60.0
            )
            segments.append((stride_min, self.if_warmup_strides))
            segments.append((rest_min, self.if_warmup))
        return segments

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build the fixed protocol workout.

        If custom ``protocol_steps`` were provided at construction they are
        used verbatim; otherwise a generic warmup-protocol-cooldown structure
        is generated.
        """
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        if self.protocol_steps:  # pragma: no cover
            return StructuredWorkout(  # pragma: no cover
                workout_name=self.key, steps=list(self.protocol_steps)
            )
        steps: list[WorkoutStep] = [
            WorkoutStep(
                type="warmup",
                duration=_dur_seconds(self.warmup_min * 60.0),
                target=_rpe_target(3),
                description=_step_description("warmup", self.if_warmup),
            ),
        ]
        if self.warmup_strides_count > 0:
            stride_seconds = self.warmup_strides_duration_seconds
            rest_seconds = self.warmup_strides_rest_seconds
            steps.append(
                WorkoutStep(
                    type="repeat",
                    duration=_dur_seconds(
                        self.warmup_strides_count * (stride_seconds + rest_seconds)
                    ),
                    iterations=self.warmup_strides_count,
                    steps=[
                        WorkoutStep(
                            type="interval",
                            duration=_dur_seconds(stride_seconds),
                            target=_rpe_target(7),
                            description=self.warmup_strides_description,
                        ),
                        WorkoutStep(
                            type="recovery",
                            duration=_dur_seconds(rest_seconds),
                            target=_rpe_target(2),
                            description="easy",
                        ),
                    ],
                )
            )
        steps.extend(
            [
                WorkoutStep(
                    type="interval",
                    duration=_dur_seconds(self.protocol_duration_min * 60.0),
                    target=_rpe_target(9),
                    description=self.protocol_description,
                ),
                WorkoutStep(
                    type="cooldown",
                    duration=_dur_seconds(self.cooldown_min * 60.0),
                    target=_rpe_target(2),
                    description=_step_description("cooldown", self.if_cooldown),
                ),
            ]
        )
        return StructuredWorkout(workout_name=self.key, steps=steps)


__all__ = [
    "ActivityDefinition",
    "FixedProtocolActivity",
    "_athlete_zone_set",
    "_build_step_targets",
    "_dur_seconds",
    "_if_to_tid_fractions",
    "_if_to_zone",
    "_open_target",
    "_order_targets",
    "_rest_workout",
    "_rpe_target",
    "_step_description",
    "_tid_from_segments",
    "_zone_holding",
    "_zone_tag_target",
    "_zone_target",
]
