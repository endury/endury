"""Swim activity classes and helpers for the planner."""

from __future__ import annotations

from dataclasses import dataclass

from endury.planner.activity.config import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    PACE_DISTANCE_TO_MINUTES,
    SWIM_PACE_ZONE_THRESHOLDS,
    SWIM_STEP_METERS,
    TARGET_BAND_FRACTION,
)
from endury.planner.activity.shapes.base import (
    ActivityDefinition,
    _athlete_zone_set,
    _dur_seconds,
    _if_to_zone,
    _open_target,
    _rest_workout,
    _rpe_target,
    _zone_holding,
)
from endury.planner.foundations.types import SportType
from endury.training.load import (
    DEFAULT_THRESHOLD_TSS_PER_HOUR,
    SWIM_LOAD_EXPONENT,
    ResolverProtocol,
    pace_sec_per_100m_from_css,
    positive_float_or_none,
    tss_from_duration_minutes,
)
from endury.training.workout import (
    DurationDef,
    StructuredWorkout,
    TargetDef,
    WorkoutStep,
)

# ---------------------------------------------------------------------------
# Swim helpers
# ---------------------------------------------------------------------------


def _dur_meters(meters: float, *, seconds: float | None = None) -> DurationDef:
    """Construct a distance duration."""
    return DurationDef(
        type="distance",
        value_meters=float(meters),
        value_seconds=(float(seconds) if seconds is not None else None),
    )


def _round_to_swim_step(meters: float) -> int:
    """Round to the nearest ``SWIM_STEP_METERS`` (whole 50 m lengths)."""
    rounded = int(round(meters / SWIM_STEP_METERS) * SWIM_STEP_METERS)
    return max(SWIM_STEP_METERS, rounded)


#: Display granularity for total swim distance: every materialised swim
#: session must sum to a whole number of hundred metres.
SWIM_TOTAL_GRANULARITY_METERS: int = 100


def _round_to_100m(meters: float) -> int:
    """Round to nearest hundred metres, with a 100 m floor."""
    rounded = int(
        round(meters / SWIM_TOTAL_GRANULARITY_METERS) * SWIM_TOTAL_GRANULARITY_METERS
    )
    return max(SWIM_TOTAL_GRANULARITY_METERS, rounded)


def _round_cooldown_for_100m_total(
    warmup_meters: int, content_meters: int, cooldown_meters: int
) -> int:
    """Shift cooldown so warmup+content+cooldown lands on a 100 m boundary.

    Total swim distance must round to a whole number of hundred metres
    while individual reps keep their 50 m granularity.  The
    cooldown absorbs the slop (≤ 50 m); a floor of one 50 m step keeps
    the cooldown step renderable.
    """
    raw_total = warmup_meters + content_meters + cooldown_meters
    rounded_total = _round_to_100m(raw_total)
    return max(rounded_total - warmup_meters - content_meters, SWIM_STEP_METERS)


def _select_lattice_target(candidates: list[float], target_tss: float) -> float:
    """Pick a lattice point, preferring not to overshoot target load."""
    non_overshoot = [value for value in candidates if value <= target_tss + 1e-9]
    if non_overshoot:
        return max(non_overshoot)
    return min(candidates)


def _swim_zone_target(
    if_value: float, rpe: int, resolver: ResolverProtocol | None = None
) -> tuple[TargetDef, TargetDef]:
    """Create swim pace zone target with RPE secondary.

    When the resolver carries the athlete's CSS the target also gets a
    concrete ``low/high_sec_per_100m`` pace band (CSS / IF ± band fraction)
    — without it a swim step renders as a bare zone number while bike and
    run steps show real watts and paces. With the athlete's own swim zones
    the number is the zone holding that whole band, and none when the band
    crosses their zones.

    Args:
        if_value: Swim intensity factor (fraction of CSS).
        rpe: RPE value for secondary target.
        resolver: Optional threshold resolver providing athlete CSS.

    Returns:
        Tuple of (swim pace zone target, RPE target).
    """
    zone: int | None = _if_to_zone(if_value, SWIM_PACE_ZONE_THRESHOLDS)
    low_sec: float | None = None
    high_sec: float | None = None
    css = positive_float_or_none(
        resolver.swim_css_sec_per_100m if resolver is not None else None
    )
    if css is not None:
        center = pace_sec_per_100m_from_css(css, if_value)
        low_sec = round(center * (1 - TARGET_BAND_FRACTION), 1)
        high_sec = round(center * (1 + TARGET_BAND_FRACTION), 1)
        athlete = _athlete_zone_set(resolver, SportType.SWIMMING, "swim_pace")
        if athlete is not None:
            zone = _zone_holding(athlete, low_sec, high_sec)
    return (
        TargetDef(
            type="swim_pace_zone",
            zone=zone,
            low_sec_per_100m=low_sec,
            high_sec_per_100m=high_sec,
        ),
        _rpe_target(rpe),
    )


#: Athlete-facing cues for swim steps whose primary target is ``open``.
#: Without an explicit cue these steps would render with an empty
#: description even though they carry meaningful coaching intent
#: (loosen-up warmup, recovery between reps, easy cooldown).
SWIM_OPEN_WARMUP_CUE: str = (
    "Easy swim warm-up — choice of stroke, gradually loosen up; build pace "
    "and rotation in the last 100 m"
)
SWIM_OPEN_COOLDOWN_CUE: str = (
    "Easy swim cool-down — relaxed freestyle or backstroke, focus on "
    "technique, bring HR down"
)
SWIM_OPEN_REST_CUE: str = (
    "Wall rest between reps — stay on pace clock, keep breathing easy"
)
#: Fallback cues for swim WU/CD whose target is a zoned swim pace (rather
#: than ``open``), e.g. SWIM_ENDURANCE, SWIM_LONG, SWIM_PULL.  Without
#: these the card renders a blank description even though there is
#: meaningful coaching content to convey.
SWIM_PACED_WARMUP_CUE: str = (
    "Swim warm-up — easy freestyle building rotation and feel for the water"
)
SWIM_PACED_COOLDOWN_CUE: str = (
    "Swim cool-down — relaxed freestyle, focus on long strokes and easy breathing"
)
#: Wall rest after each rep when a continuous swim's main set is broken
#: into reps: a timed ``rest`` step the watch counts, so the card's clock
#: includes it.  It covers no distance and carries no load.
# intent: 15 s, the middle of the 10-20 s an easy aerobic set rests at
# the wall between reps.
SWIM_SPLIT_REST_SECONDS: float = 15.0


def _resolve_css(resolver: ResolverProtocol | None) -> float:
    """Return CSS threshold pace in sec/100m, preferring resolver data.

    Shared by every swim shape. Without an athlete CSS the card swims at
    ``DEFAULT_SWIM_CSS_SEC_PER_100M``, the pace the session generator
    budgeted the menu metres at, so the card shows the metres the plan sized.

    Args:
        resolver: Optional threshold resolver providing athlete-specific CSS.

    Returns:
        Athlete-specific CSS from resolver, or ``DEFAULT_SWIM_CSS_SEC_PER_100M``.
    """
    css = positive_float_or_none(
        resolver.swim_css_sec_per_100m if resolver is not None else None
    )
    return css or DEFAULT_SWIM_CSS_SEC_PER_100M


def _swim_window(
    activity: ActivityDefinition, resolver: ResolverProtocol | None
) -> tuple[float, float]:
    """Return the activity's TSS window at the athlete's CSS.

    A swim's ``tss_min`` / ``tss_max`` are authored at
    ``DEFAULT_SWIM_CSS_SEC_PER_100M``. The same metres carry load in
    proportion to CSS pace, so the window scales with it: it bounds the
    metres a card swims, never its hours, and a slower swimmer's card
    holds the distance the menu books.

    Args:
        activity: The swim activity.
        resolver: Optional threshold resolver providing athlete CSS.

    Returns:
        ``(tss_min, tss_max)`` at the athlete's CSS.
    """
    scale = _resolve_css(resolver) / DEFAULT_SWIM_CSS_SEC_PER_100M
    return activity.tss_min * scale, activity.tss_max * scale


def _clamp_swim_target(
    activity: ActivityDefinition,
    target_tss: float,
    resolver: ResolverProtocol | None,
) -> float:
    """Clamp a swim's target TSS into its window at the athlete's CSS."""
    return min(max(target_tss, 0.0), _swim_window(activity, resolver)[1])


def _swim_meters_tss(
    meters: float,
    if_value: float,
    css_sec_per_100m: float,
    *,
    load_exponent: float,
    threshold_tss_per_hour: float,
) -> float:
    """Return TSS for swimming *meters* at *if_value* relative to CSS.

    The single swim load law shared by the linear, interval and drill shapes:
    pace follows from CSS and intensity (``css / IF``), duration from
    metres at that pace, and TSS from duration at the swim load exponent.

    Args:
        meters: Distance swum in meters.
        if_value: Intensity factor (fraction of CSS).
        css_sec_per_100m: CSS threshold pace in sec/100m.
        load_exponent: TSS load model exponent.
        threshold_tss_per_hour: TSS at threshold intensity per hour.

    Returns:
        TSS accrued over the given distance.
    """
    pace = pace_sec_per_100m_from_css(css_sec_per_100m, if_value)
    return tss_from_duration_minutes(
        meters * pace / PACE_DISTANCE_TO_MINUTES,
        if_value,
        exponent=load_exponent,
        threshold_tss_per_hour=threshold_tss_per_hour,
    )


# ---------------------------------------------------------------------------
# SwimLinearDistanceActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SwimLinearDistanceActivity(ActivityDefinition):
    """Swim activity with distance materialization at 25m granularity.

    ``if_warmup`` and ``if_cooldown`` default to the main ``intensity_factor``
    (so existing sessions show the warm-up / cool-down at the same zone as
    the main set). For sessions that want a true easy lead-in / lead-out
    (e.g. SWIM_ENDURANCE at main IF 0.88 with warm-up at IF 0.78), explicitly
    set lower values; the athlete-facing workout view will show a distinct
    Z1 Recovery zone for those steps.
    """

    intensity_factor: float
    rpe: int
    warmup_meters: int = 400
    cooldown_meters: int = 200
    if_warmup: float | None = None
    if_cooldown: float | None = None
    load_exponent: float = SWIM_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    #: Athlete-facing stroke note rendered on the main interval.
    step_description: str | None = None
    #: Longest rep the main set may hand the athlete, in metres — must be a
    #: whole hundred.  ``None`` keeps the main block as one continuous
    #: swim; a value breaks it into whole-hundred reps of even length
    #: (±100 m), each followed by a wall rest, without changing the
    #: session's distance or load.
    max_rep_meters: int | None = None

    @property
    def _resolved_if_warmup(self) -> float:
        return self.intensity_factor if self.if_warmup is None else self.if_warmup

    @property
    def _resolved_if_cooldown(self) -> float:
        return self.intensity_factor if self.if_cooldown is None else self.if_cooldown

    def _resolved_threshold_pace(self, resolver: ResolverProtocol | None) -> float:
        """Return CSS threshold pace in sec/100m, using resolver data when available."""
        return _resolve_css(resolver)

    def _pace_sec_per_100m(self, resolver: ResolverProtocol | None) -> float:
        """Return target swim pace in sec/100m at this activity's intensity factor.

        Args:
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Pace in sec/100m derived from the resolved CSS and ``intensity_factor``.
        """
        return pace_sec_per_100m_from_css(
            self._resolved_threshold_pace(resolver),
            self.intensity_factor,
        )

    def _pace_at_if(self, if_value: float, resolver: ResolverProtocol | None) -> float:
        """Return swim pace in sec/100m at an arbitrary IF."""
        return pace_sec_per_100m_from_css(
            self._resolved_threshold_pace(resolver), if_value
        )

    def _blocks(self, main_meters: float) -> list[tuple[float, float]]:
        """Return ``(metres, intensity factor)`` for warm-up, main set and cool-down."""
        return [
            (float(self.warmup_meters), self._resolved_if_warmup),
            (main_meters, self.intensity_factor),
            (float(self.cooldown_meters), self._resolved_if_cooldown),
        ]

    def _block_tss(
        self, meters: float, if_value: float, resolver: ResolverProtocol | None
    ) -> float:
        """Return TSS for *meters* of this swim at *if_value*."""
        return _swim_meters_tss(
            meters,
            if_value,
            self._resolved_threshold_pace(resolver),
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _card_tss(self, main_meters: float, resolver: ResolverProtocol | None) -> float:
        """Return the card's TSS: every block at its own intensity."""
        return sum(
            self._block_tss(meters, if_value, resolver)
            for meters, if_value in self._blocks(main_meters)
        )

    def _card_segments(
        self, main_meters: float, resolver: ResolverProtocol | None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per block, each at its own pace."""
        return [
            (
                meters
                * self._pace_at_if(if_value, resolver)
                / PACE_DISTANCE_TO_MINUTES,
                if_value,
            )
            for meters, if_value in self._blocks(main_meters)
        ]

    def _rep_count(self, main_meters: int) -> int:
        """Return the reps the main set breaks into; one is a continuous swim."""
        rep_cap = self.max_rep_meters or main_meters
        return (main_meters + rep_cap - 1) // rep_cap

    def _rest_minutes(self, main_meters: int) -> float:
        """Return the wall rest a main set takes: one after each rep of a split set."""
        reps = self._rep_count(main_meters)
        return (reps if reps > 1 else 0) * SWIM_SPLIT_REST_SECONDS / 60.0

    def _card_minutes(
        self, main_meters: int, resolver: ResolverProtocol | None
    ) -> float:
        """Return the card's wall clock: every block at its pace, and the wall rests."""
        return sum(
            minutes for minutes, _ in self._card_segments(float(main_meters), resolver)
        ) + self._rest_minutes(main_meters)

    def minutes_for_meters(
        self, meters: int, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return the wall clock of a card that swims *meters* in total.

        The inverse of :meth:`tss_from_duration`, so the hours the session
        generator books for a metre-authored swim build a card of those
        metres (R-SESS-B-006).

        Args:
            meters: Total session distance, warm-up and cool-down included.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Session minutes, every block at its own pace, wall rests included.
        """
        return self._card_minutes(
            meters - self.warmup_meters - self.cooldown_meters, resolver
        )

    def _quantize_main(self, raw_meters: float) -> int:
        """Round a main set so the session totals a whole 100 m; it keeps 100 m."""
        fixed = self.warmup_meters + self.cooldown_meters
        return max(
            _round_to_100m(fixed + raw_meters) - fixed, SWIM_TOTAL_GRANULARITY_METERS
        )

    def _main_meters(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> int:
        """Return the main-set distance whose card lands on *target_tss*.

        TSS is linear in metres at a fixed intensity, so the main set swims
        the load the target leaves after the warm-up and cool-down at the
        main set's TSS per metre.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Main-set distance in metres; the session totals a whole 100 m.
        """
        tss = _clamp_swim_target(self, target_tss, resolver)
        per_meter = self._block_tss(1.0, self.intensity_factor, resolver)
        return self._quantize_main((tss - self._card_tss(0.0, resolver)) / per_meter)

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Snap target TSS to nearest reachable 100 m-quantized swim load."""
        window_min, window_max = _swim_window(self, resolver)
        lower = window_min if lower_bound_tss is None else lower_bound_tss
        upper = window_max if upper_bound_tss is None else upper_bound_tss
        if upper < lower:
            upper = lower

        fixed_tss = self._card_tss(0.0, resolver)
        per_meter = self._block_tss(1.0, self.intensity_factor, resolver)
        step = SWIM_TOTAL_GRANULARITY_METERS
        # Walk the main-set lattice in whole 100 m.
        meters_start = max(
            step, int((lower - fixed_tss) / per_meter // step) * step - step
        )
        meters_end = max(
            meters_start, int((upper - fixed_tss) / per_meter // step) * step + step
        )
        candidates = [
            tss
            for tss in (
                self._card_tss(float(meters), resolver)
                for meters in range(meters_start, meters_end + 1, step)
            )
            if lower <= tss <= upper
        ]

        if not candidates:
            return min(max(target_tss, lower), upper)

        return _select_lattice_target(candidates, target_tss)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute the card's wall clock, wall rests included."""
        if self._is_rest_target(target_tss):
            return 0.0
        return self._card_minutes(self._main_meters(target_tss, resolver), resolver)

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute the card's TSS after 100 m distance quantization."""
        if self._is_rest_target(target_tss):
            return 0.0
        return self._card_tss(float(self._main_meters(target_tss, resolver)), resolver)

    def _main_steps(
        self,
        main_meters: int,
        pace_main: float,
        main_zone: TargetDef,
        main_rpe: TargetDef,
    ) -> list[WorkoutStep]:
        """Return the main set, broken into capped reps when configured.

        With ``max_rep_meters`` unset the block stays one continuous swim.
        Otherwise it is split into the fewest reps that respect the cap,
        all the same whole-hundred length give or take 100 m: a 3,300 m
        endurance block reads ``6 x 400 m`` + ``3 x 300 m``, a 5,500 m
        long-swim block reads ``1 x 1,000 m`` + ``5 x 900 m``.  The reps
        sum to *main_meters* exactly, so the session keeps its distance and
        its load; each rep of a split set is followed by a timed wall rest
        (``SWIM_SPLIT_REST_SECONDS``) the watch counts, as the interval
        swims rest after every rep, the last one included.

        Args:
            main_meters: Main-block distance, a whole number of hundred
                metres.
            pace_main: Main-set pace in sec/100 m.
            main_zone: Primary target for the main set.
            main_rpe: Secondary RPE target for the main set.

        Returns:
            One step per segment: a ``repeat`` block of rep and wall rest for
            a repeated segment, a bare ``interval`` (and its wall rest, when
            the set is split) for a single swim.
        """
        reps = self._rep_count(main_meters)
        short_meters = (
            main_meters
            // reps
            // SWIM_TOTAL_GRANULARITY_METERS
            * SWIM_TOTAL_GRANULARITY_METERS
        )
        long_reps = (main_meters - reps * short_meters) // SWIM_TOTAL_GRANULARITY_METERS
        segments = [
            (count, meters)
            for count, meters in (
                (long_reps, short_meters + SWIM_TOTAL_GRANULARITY_METERS),
                (reps - long_reps, short_meters),
            )
            if count
        ]
        rest_seconds = SWIM_SPLIT_REST_SECONDS if reps > 1 else 0.0
        steps: list[WorkoutStep] = []
        for count, meters in segments:
            seconds = float(meters) * pace_main / 100.0
            rep = [
                WorkoutStep(
                    type="interval",
                    duration=_dur_meters(float(meters), seconds=seconds),
                    target=main_zone,
                    secondary_target=main_rpe,
                    description=self.step_description,
                )
            ]
            if rest_seconds:
                rep.append(
                    WorkoutStep(
                        type="rest",
                        duration=_dur_seconds(rest_seconds),
                        target=_open_target(),
                        description=SWIM_OPEN_REST_CUE,
                    )
                )
            if count == 1:
                steps.extend(rep)
            else:
                steps.append(
                    WorkoutStep(
                        type="repeat",
                        duration=_dur_seconds(count * (seconds + rest_seconds)),
                        iterations=count,
                        steps=rep,
                    )
                )
        return steps

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build swim workout with meter-based steps."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        main_meters = self._main_meters(target_tss, resolver)
        pace_main = self._pace_sec_per_100m(resolver)
        wu_pace = self._pace_at_if(self._resolved_if_warmup, resolver)
        cd_pace = self._pace_at_if(self._resolved_if_cooldown, resolver)
        main_zone, main_rpe = _swim_zone_target(
            self.intensity_factor, self.rpe, resolver
        )
        wu_target = (
            _open_target()
            if self.if_warmup is None
            else _swim_zone_target(self._resolved_if_warmup, 2, resolver)[0]
        )
        cd_target = (
            _open_target()
            if self.if_cooldown is None
            else _swim_zone_target(self._resolved_if_cooldown, 2, resolver)[0]
        )
        wu_description = (
            SWIM_OPEN_WARMUP_CUE if wu_target.type == "open" else SWIM_PACED_WARMUP_CUE
        )
        cd_description = (
            SWIM_OPEN_COOLDOWN_CUE
            if cd_target.type == "open"
            else SWIM_PACED_COOLDOWN_CUE
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_meters(
                    float(self.warmup_meters),
                    seconds=float(self.warmup_meters) * wu_pace / 100.0,
                ),
                target=wu_target,
                description=wu_description,
            ),
            *self._main_steps(main_meters, pace_main, main_zone, main_rpe),
            WorkoutStep(
                type="cooldown",
                duration=_dur_meters(
                    float(self.cooldown_meters),
                    seconds=float(self.cooldown_meters) * cd_pace / 100.0,
                ),
                target=cd_target,
                description=cd_description,
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        if self._is_rest_target(target_tss):
            return []
        return self._card_segments(
            float(self._main_meters(target_tss, resolver)), resolver
        )

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration for a linear-distance swim.

        The main set swims what the duration leaves after the warm-up and
        cool-down, each at its own pace, and after the wall rests its reps
        take.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Derived TSS for the given duration.
        """
        if duration_minutes <= 0.0:
            return 0.0
        meters_per_minute = PACE_DISTANCE_TO_MINUTES / self._pace_sec_per_100m(resolver)
        fixed_minutes = sum(
            minutes for minutes, _ in self._card_segments(0.0, resolver)
        )
        raw_main = (duration_minutes - fixed_minutes) * meters_per_minute
        # The wall rests take time but cover no distance.
        raw_main -= (
            self._rest_minutes(self._quantize_main(raw_main)) * meters_per_minute
        )
        return self._card_tss(float(self._quantize_main(raw_main)), resolver)


# ---------------------------------------------------------------------------
# SwimCssIntervalActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SwimCssIntervalActivity(ActivityDefinition):
    """Swim CSS interval activity with quantized rep distances and static wall rest.

    ``rep_rest_seconds`` is the canonical rest value; ``rep_rest_seconds_max``
    (defaulting to ``rep_rest_seconds``) gives the solver headroom to flex
    rest density for sessions like SWIM_SPRINT (60-90 s span) or
    SWIM_VO2MAX_SHORT (10-20 s microset rest).

    ``work_rpe`` and ``rest_rpe`` are per-session athlete-facing perceived-
    effort values shown on the work / recovery steps, defaulting to
    work 7 and rest 2.
    """

    if_work: float
    rep_count_min: int
    rep_count_max: int
    rep_meters_min: int
    rep_meters_max: int
    rep_rest_seconds: int
    rep_rest_seconds_max: int | None = None
    work_rpe: int = 7
    rest_rpe: int = 2
    warmup_meters: int = 400
    cooldown_meters: int = 200
    if_warmup: float = 0.72
    if_cooldown: float = 0.68
    load_exponent: float = SWIM_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    #: Athlete-facing stroke note rendered on the work interval.
    step_description: str | None = None

    def _resolved_threshold_pace(self, resolver: ResolverProtocol | None) -> float:
        """Return CSS threshold pace in sec/100m, using resolver data when available."""
        return _resolve_css(resolver)

    def _pace_for_intensity(
        self, intensity_factor: float, resolver: ResolverProtocol | None
    ) -> float:
        """Return swim pace in sec/100m for a given intensity factor.

        Args:
            intensity_factor: Fraction of CSS threshold pace.
            resolver: Optional threshold resolver for CSS personalization.
        """
        return pace_sec_per_100m_from_css(
            self._resolved_threshold_pace(resolver), intensity_factor
        )

    def _work_pace_sec_per_100m(self, resolver: ResolverProtocol | None) -> float:
        """Return work-interval swim pace in sec/100m at ``if_work``."""
        return self._pace_for_intensity(self.if_work, resolver)

    def _warmup_pace_sec_per_100m(self, resolver: ResolverProtocol | None) -> float:
        """Return warmup swim pace in sec/100m at ``if_warmup``."""
        return self._pace_for_intensity(self.if_warmup, resolver)

    def _cooldown_pace_sec_per_100m(self, resolver: ResolverProtocol | None) -> float:
        """Return cooldown swim pace in sec/100m at ``if_cooldown``."""
        return self._pace_for_intensity(self.if_cooldown, resolver)

    def _work_tss_per_meter(self, resolver: ResolverProtocol | None) -> float:
        """Return TSS per meter swum at work-interval intensity."""
        return _swim_meters_tss(
            1.0,
            self.if_work,
            self._resolved_threshold_pace(resolver),
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _rest_tss(self, resolver: ResolverProtocol | None) -> float:
        """Return TSS contribution per rest segment.

        Wall rest contributes no training stimulus; the swimmer is stationary at
        the pool wall. Kept as a named extension point for future rest modes
        (e.g. send-off / leave-on with continuous easy-swim recovery).
        """
        del resolver
        return 0.0

    def _fixed_tss(
        self,
        resolver: ResolverProtocol | None,
        *,
        cooldown_meters: int | None = None,
    ) -> float:
        """Compute combined TSS for warmup and cooldown phases.

        Args:
            resolver: Optional threshold resolver for CSS personalization.
            cooldown_meters: Override for the cooldown distance.  When
                ``None`` the natural ``self.cooldown_meters`` is used.
                Pass the 100 m-rounded cooldown to keep post-solve
                outputs (duration, achieved TSS, TID, materialised
                workout) consistent with each other.

        Returns:
            Sum of warmup and cooldown TSS at their respective CSS-relative paces.
        """
        cd_m = self.cooldown_meters if cooldown_meters is None else cooldown_meters
        css = self._resolved_threshold_pace(resolver)
        return _swim_meters_tss(
            float(self.warmup_meters),
            self.if_warmup,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        ) + _swim_meters_tss(
            float(cd_m),
            self.if_cooldown,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _effective_cooldown_meters(self, reps: int, work_meters: int) -> int:
        """Cooldown rounded so total swim distance is a multiple of 100 m.

        Every materialised swim session must sum to a whole number of
        hundred metres.  The solver picks ``(reps, work_meters)`` against
        the natural cooldown; the cooldown is then shifted by at most
        ±50 m so the displayed grand total lands on a 100 m boundary.
        """
        return _round_cooldown_for_100m_total(
            self.warmup_meters,
            reps * work_meters,
            self.cooldown_meters,
        )

    def _solve_pattern(
        self,
        target_tss: float,
        resolver: ResolverProtocol | None = None,
    ) -> tuple[int, int]:
        """Find (rep_count, work_meters_per_rep) for target TSS.

        Picks the pattern on the 50 m rep grid whose work distance lands
        nearest the distance the target's load leaves after warm-up and
        cool-down, fewest reps on a tie, so every 50 m of work is reachable.

        Args:
            target_tss: Desired total session TSS.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Tuple of (rep_count, work_meters_per_rep) both quantized.
        """
        tss = _clamp_swim_target(self, target_tss, resolver)
        avail = max(tss - self._fixed_tss(resolver), 0.0)
        work_meters = avail / self._work_tss_per_meter(resolver)
        return min(
            (
                (reps, meters)
                for reps in range(self.rep_count_min, self.rep_count_max + 1)
                for meters in range(
                    self.rep_meters_min, self.rep_meters_max + 1, SWIM_STEP_METERS
                )
            ),
            key=lambda p: (abs(p[0] * p[1] - work_meters), p[0]),
        )

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Snap target TSS to nearest reachable (reps, meters) lattice value."""
        window_min, window_max = _swim_window(self, resolver)
        lower = window_min if lower_bound_tss is None else lower_bound_tss
        upper = window_max if upper_bound_tss is None else upper_bound_tss
        if upper < lower:
            upper = lower

        candidates = [t for t in self._lattice_tss(resolver) if lower <= t <= upper]
        if not candidates:
            return min(max(target_tss, lower), upper)

        return _select_lattice_target(candidates, target_tss)

    def _lattice_tss(self, resolver: ResolverProtocol | None) -> list[float]:
        """Return every reachable card TSS, ascending.

        One point per (reps, rep metres) seed, re-solved twice through
        ``achieved_tss``: the cool-down's 100 m rounding moves a seed's load,
        and the moved load can re-solve to a neighbouring pattern.
        """
        fixed_tss = self._fixed_tss(resolver)
        work_tss_per_meter = self._work_tss_per_meter(resolver)
        points: set[float] = set()
        for reps in range(self.rep_count_min, self.rep_count_max + 1):
            for meters in range(
                self.rep_meters_min, self.rep_meters_max + 1, SWIM_STEP_METERS
            ):
                seed_tss = fixed_tss + reps * meters * work_tss_per_meter
                achieved = self.achieved_tss(seed_tss, resolver=resolver)
                points.add(self.achieved_tss(achieved, resolver=resolver))
        return sorted(points)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute swim CSS workout duration in minutes."""
        if self._is_rest_target(target_tss):
            return 0.0
        reps, work_meters = self._solve_pattern(target_tss, resolver=resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, work_meters)
        work_min = (
            reps
            * work_meters
            * self._work_pace_sec_per_100m(resolver)
            / PACE_DISTANCE_TO_MINUTES
        )
        rest_min = reps * self.rep_rest_seconds / 60.0
        fixed_min = (
            self.warmup_meters * self._warmup_pace_sec_per_100m(resolver)
            + eff_cooldown * self._cooldown_pace_sec_per_100m(resolver)
        ) / PACE_DISTANCE_TO_MINUTES
        return fixed_min + work_min + rest_min

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute achieved TSS for quantized CSS repeats."""
        if self._is_rest_target(target_tss):
            return 0.0
        reps, work_meters = self._solve_pattern(target_tss, resolver=resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, work_meters)
        work_tss = reps * work_meters * self._work_tss_per_meter(resolver)
        rest_tss = reps * self._rest_tss(resolver)
        return (
            self._fixed_tss(resolver, cooldown_meters=eff_cooldown)
            + work_tss
            + rest_tss
        )

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build meter-based CSS repeat workout steps."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        reps, work_meters = self._solve_pattern(target_tss, resolver=resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, work_meters)
        work_sec_per_100m = self._work_pace_sec_per_100m(resolver)
        warmup_sec_per_100m = self._warmup_pace_sec_per_100m(resolver)
        cooldown_sec_per_100m = self._cooldown_pace_sec_per_100m(resolver)
        repeat_seconds = reps * (
            work_meters * work_sec_per_100m / 100.0 + float(self.rep_rest_seconds)
        )
        work_zone, work_rpe_target = _swim_zone_target(
            self.if_work, self.work_rpe, resolver
        )
        rest_rpe_target = _rpe_target(self.rest_rpe)
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_meters(
                    float(self.warmup_meters),
                    seconds=float(self.warmup_meters) * warmup_sec_per_100m / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_WARMUP_CUE,
            ),
            WorkoutStep(
                type="repeat",
                duration=_dur_seconds(repeat_seconds),
                iterations=reps,
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_dur_meters(
                            float(work_meters),
                            seconds=float(work_meters) * work_sec_per_100m / 100.0,
                        ),
                        target=work_zone,
                        secondary_target=work_rpe_target,
                        description=self.step_description,
                    ),
                    WorkoutStep(
                        type="recovery",
                        duration=_dur_seconds(float(self.rep_rest_seconds)),
                        target=_open_target(),
                        secondary_target=rest_rpe_target,
                        description=SWIM_OPEN_REST_CUE,
                    ),
                ],
            ),
            WorkoutStep(
                type="cooldown",
                duration=_dur_meters(
                    float(eff_cooldown),
                    seconds=float(eff_cooldown) * cooldown_sec_per_100m / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_COOLDOWN_CUE,
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        if self._is_rest_target(target_tss):
            return []
        reps, work_meters = self._solve_pattern(target_tss, resolver=resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, work_meters)
        wu_pace = self._warmup_pace_sec_per_100m(resolver)
        cd_pace = self._cooldown_pace_sec_per_100m(resolver)
        work_pace = self._work_pace_sec_per_100m(resolver)
        wu_min = self.warmup_meters * wu_pace / PACE_DISTANCE_TO_MINUTES
        cd_min = eff_cooldown * cd_pace / PACE_DISTANCE_TO_MINUTES
        work_min = reps * work_meters * work_pace / PACE_DISTANCE_TO_MINUTES
        return [
            (wu_min, self.if_warmup),
            (work_min, self.if_work),
            (cd_min, self.if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration by inverting the rep lattice.

        Picks, among the same reachable lattice points ``project_target_tss``
        snaps to, the one whose card duration lands closest to the request,
        so projection keeps it and the card is the session the plan sized.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Derived TSS for the given duration.
        """
        if duration_minutes <= 0.0:
            return 0.0
        return self._closest_duration_tss(
            duration_minutes, self._lattice_tss(resolver), resolver=resolver
        )


# ---------------------------------------------------------------------------
# SwimDrillSwimActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SwimDrillSwimActivity(ActivityDefinition):
    """Swim drill / whole-stroke alternation activity for technique sets.

    Each rep is ``drill_meters`` of focused-form drill swimming followed by
    ``swim_meters`` of whole-stroke moderate-aerobic swimming, e.g. a
    ``8 x (50 m drill / 50 m swim)`` set. Rep count scales with target TSS
    within ``[rep_count_min, rep_count_max]``; rep length is fixed. Past the
    last rep an easy whole-stroke block follows the set, growing in 100 m
    up to ``aerobic_max_meters``, so a longer booking swims its distance
    without more drilling than one set holds.
    """

    if_drill: float
    if_swim: float
    drill_meters: int
    swim_meters: int
    rep_count_min: int
    rep_count_max: int
    drill_rpe: int = 3
    swim_rpe: int = 3
    warmup_meters: int = 200
    cooldown_meters: int = 100
    if_warmup: float = 0.72
    if_cooldown: float = 0.68
    load_exponent: float = SWIM_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    #: Athlete-facing drill explanation rendered on the drill step.
    #: Should name the drill family or rotation, the stroke, and the
    #: focus cue (e.g. "freestyle catch-up — lead-hand stays extended
    #: until trailing hand touches").
    drill_description: str | None = None
    #: Athlete-facing stroke note rendered on the whole-stroke swim step.
    swim_description: str | None = None
    #: Longest easy whole-stroke block after the drill set, in whole
    #: hundreds of metres; 0 keeps the card to the set alone.
    aerobic_max_meters: int = 0
    #: Athlete-facing note rendered on the aerobic block.
    aerobic_description: str | None = None

    @property
    def _drill_leg_meters(self) -> int:
        """Drill leg length snapped to the pool grid.

        A 25 m leg is not swimmable in a 50 m pool.
        """
        return _round_to_swim_step(float(self.drill_meters))

    @property
    def _swim_leg_meters(self) -> int:
        """Whole-stroke leg length snapped to the pool grid."""
        return _round_to_swim_step(float(self.swim_meters))

    def _resolved_threshold_pace(self, resolver: ResolverProtocol | None) -> float:
        """Return CSS threshold pace in sec/100m, using resolver data when available."""
        return _resolve_css(resolver)

    def _pace_at_if(self, if_value: float, resolver: ResolverProtocol | None) -> float:
        """Return swim pace in sec/100m at an arbitrary IF."""
        return pace_sec_per_100m_from_css(
            self._resolved_threshold_pace(resolver), if_value
        )

    def _fixed_tss(
        self,
        resolver: ResolverProtocol | None,
        *,
        cooldown_meters: int | None = None,
    ) -> float:
        """Combined warmup + cooldown TSS at their respective IFs.

        Pass ``cooldown_meters`` to substitute the 100 m-rounded cooldown
        when reporting post-solve outputs.
        """
        cd_m = self.cooldown_meters if cooldown_meters is None else cooldown_meters
        css = self._resolved_threshold_pace(resolver)
        return _swim_meters_tss(
            float(self.warmup_meters),
            self.if_warmup,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        ) + _swim_meters_tss(
            float(cd_m),
            self.if_cooldown,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _effective_cooldown_meters(self, reps: int, aerobic_meters: int = 0) -> int:
        """Cooldown rounded so total swim distance is a multiple of 100 m."""
        content = reps * (self._drill_leg_meters + self._swim_leg_meters)
        return _round_cooldown_for_100m_total(
            self.warmup_meters, content + aerobic_meters, self.cooldown_meters
        )

    def _aerobic_tss(self, meters: float, resolver: ResolverProtocol | None) -> float:
        """TSS of *meters* of the easy whole-stroke block."""
        return _swim_meters_tss(
            meters,
            self.if_swim,
            self._resolved_threshold_pace(resolver),
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _lattice(self) -> list[tuple[int, int]]:
        """Return every ``(reps, aerobic metres)`` the card builds, by load."""
        step = SWIM_TOTAL_GRANULARITY_METERS
        return [
            (reps, 0) for reps in range(self.rep_count_min, self.rep_count_max + 1)
        ] + [
            (self.rep_count_max, meters)
            for meters in range(step, self.aerobic_max_meters + 1, step)
        ]

    def _point_tss(
        self, reps: int, aerobic_meters: int, resolver: ResolverProtocol | None
    ) -> float:
        """Return the card TSS of one lattice point, cooldown rounded."""
        cooldown = self._effective_cooldown_meters(reps, aerobic_meters)
        return (
            self._fixed_tss(resolver, cooldown_meters=cooldown)
            + reps * self._rep_tss(resolver)
            + self._aerobic_tss(float(aerobic_meters), resolver)
        )

    def _rep_tss(self, resolver: ResolverProtocol | None) -> float:
        """TSS contribution from one drill+swim rep."""
        css = self._resolved_threshold_pace(resolver)
        return _swim_meters_tss(
            float(self._drill_leg_meters),
            self.if_drill,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        ) + _swim_meters_tss(
            float(self._swim_leg_meters),
            self.if_swim,
            css,
            load_exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _solve(
        self, target_tss: float, resolver: ResolverProtocol | None
    ) -> tuple[int, int]:
        """Return ``(reps, aerobic metres)`` for the target: reps, then the block.

        Reps fill first within ``[rep_count_min, rep_count_max]``; only the
        load the full set leaves goes to the aerobic block, in whole 100 m.
        """
        tss = _clamp_swim_target(self, target_tss, resolver)
        avail = max(tss - self._fixed_tss(resolver), 0.0)
        rep_tss = self._rep_tss(resolver)
        if rep_tss <= 0.0:  # pragma: no cover
            return self.rep_count_min, 0
        raw_reps = round(avail / rep_tss)
        reps = max(self.rep_count_min, min(raw_reps, self.rep_count_max))
        if raw_reps <= self.rep_count_max:
            return reps, 0
        step = SWIM_TOTAL_GRANULARITY_METERS
        steps = round((avail - reps * rep_tss) / self._aerobic_tss(step, resolver))
        return reps, min(max(steps, 0) * step, self.aerobic_max_meters)

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Snap target TSS to nearest reachable lattice value.

        The lattice uses each point's 100 m-rounded cooldown so the
        projected TSS matches what :meth:`achieved_tss` later reports.
        """
        window_min, window_max = _swim_window(self, resolver)
        lower = window_min if lower_bound_tss is None else lower_bound_tss
        upper = window_max if upper_bound_tss is None else upper_bound_tss
        if upper < lower:
            upper = lower
        candidates = [
            tss
            for tss in (
                self._point_tss(reps, aerobic, resolver)
                for reps, aerobic in self._lattice()
            )
            if lower <= tss <= upper
        ]
        if not candidates:
            return min(max(target_tss, lower), upper)
        return _select_lattice_target(candidates, target_tss)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute total session duration in minutes."""
        if self._is_rest_target(target_tss):
            return 0.0
        reps, aerobic = self._solve(target_tss, resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, aerobic)
        wu_pace = self._pace_at_if(self.if_warmup, resolver)
        cd_pace = self._pace_at_if(self.if_cooldown, resolver)
        drill_pace = self._pace_at_if(self.if_drill, resolver)
        swim_pace = self._pace_at_if(self.if_swim, resolver)
        fixed_min = (
            self.warmup_meters * wu_pace + eff_cooldown * cd_pace
        ) / PACE_DISTANCE_TO_MINUTES
        rep_min = (
            reps
            * (self._drill_leg_meters * drill_pace + self._swim_leg_meters * swim_pace)
            / PACE_DISTANCE_TO_MINUTES
        )
        return fixed_min + rep_min + aerobic * swim_pace / PACE_DISTANCE_TO_MINUTES

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute achieved TSS for the chosen reps and aerobic block."""
        if self._is_rest_target(target_tss):
            return 0.0
        reps, aerobic = self._solve(target_tss, resolver)
        return self._point_tss(reps, aerobic, resolver)

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build the drill / swim alternation workout."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        reps, aerobic = self._solve(target_tss, resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, aerobic)
        wu_pace = self._pace_at_if(self.if_warmup, resolver)
        cd_pace = self._pace_at_if(self.if_cooldown, resolver)
        drill_pace = self._pace_at_if(self.if_drill, resolver)
        swim_pace = self._pace_at_if(self.if_swim, resolver)
        drill_zone, drill_rpe_target = _swim_zone_target(
            self.if_drill, self.drill_rpe, resolver
        )
        swim_zone, swim_rpe_target = _swim_zone_target(
            self.if_swim, self.swim_rpe, resolver
        )
        repeat_seconds = reps * (
            self._drill_leg_meters * drill_pace / 100.0
            + self._swim_leg_meters * swim_pace / 100.0
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_meters(
                    float(self.warmup_meters),
                    seconds=float(self.warmup_meters) * wu_pace / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_WARMUP_CUE,
            ),
            WorkoutStep(
                type="repeat",
                duration=_dur_seconds(repeat_seconds),
                iterations=reps,
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_dur_meters(
                            float(self._drill_leg_meters),
                            seconds=float(self._drill_leg_meters) * drill_pace / 100.0,
                        ),
                        target=drill_zone,
                        secondary_target=drill_rpe_target,
                        description=self.drill_description or "drill",
                    ),
                    WorkoutStep(
                        type="interval",
                        duration=_dur_meters(
                            float(self._swim_leg_meters),
                            seconds=float(self._swim_leg_meters) * swim_pace / 100.0,
                        ),
                        target=swim_zone,
                        secondary_target=swim_rpe_target,
                        description=self.swim_description or "swim",
                    ),
                ],
            ),
        ]
        if aerobic:
            steps.append(
                WorkoutStep(
                    type="interval",
                    duration=_dur_meters(
                        float(aerobic), seconds=float(aerobic) * swim_pace / 100.0
                    ),
                    target=swim_zone,
                    secondary_target=swim_rpe_target,
                    description=self.aerobic_description or "easy swim",
                )
            )
        steps.append(
            WorkoutStep(
                type="cooldown",
                duration=_dur_meters(
                    float(eff_cooldown),
                    seconds=float(eff_cooldown) * cd_pace / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_COOLDOWN_CUE,
            )
        )
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        if self._is_rest_target(target_tss):
            return []
        reps, aerobic = self._solve(target_tss, resolver)
        eff_cooldown = self._effective_cooldown_meters(reps, aerobic)
        wu_pace = self._pace_at_if(self.if_warmup, resolver)
        cd_pace = self._pace_at_if(self.if_cooldown, resolver)
        drill_pace = self._pace_at_if(self.if_drill, resolver)
        swim_pace = self._pace_at_if(self.if_swim, resolver)
        wu_min = self.warmup_meters * wu_pace / PACE_DISTANCE_TO_MINUTES
        cd_min = eff_cooldown * cd_pace / PACE_DISTANCE_TO_MINUTES
        drill_min = (
            reps * self._drill_leg_meters * drill_pace / PACE_DISTANCE_TO_MINUTES
        )
        swim_min = reps * self._swim_leg_meters * swim_pace / PACE_DISTANCE_TO_MINUTES
        aerobic_min = aerobic * swim_pace / PACE_DISTANCE_TO_MINUTES
        return [
            (wu_min, self.if_warmup),
            (drill_min, self.if_drill),
            (swim_min, self.if_swim),
            *([(aerobic_min, self.if_swim)] if aerobic else []),
            (cd_min, self.if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration by inverting the lattice.

        Rep length is fixed, so the lattice is one candidate per rep count,
        then one per 100 m of aerobic block; returns the candidate whose
        re-solved duration lands closest to the request, so the rendered
        card tracks the solved session duration to within one drill+swim
        rep's (or 100 m's) wall-clock.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Optional threshold resolver for CSS personalization.

        Returns:
            Derived TSS for the given duration.
        """
        if duration_minutes <= 0.0:
            return 0.0
        candidates = [
            self._point_tss(reps, aerobic, resolver)
            for reps, aerobic in self._lattice()
        ]
        return self._closest_duration_tss(
            duration_minutes, candidates, resolver=resolver
        )


# ---------------------------------------------------------------------------
# SwimCssTestActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SwimCssTestActivity(ActivityDefinition):
    """Distance-based CSS swim test protocol.

    Standard CSS (Critical Swim Speed) test: structured warm-up, a few
    50 m build strides to prime race pace, a maximal 400 m time trial,
    a fixed wall rest, a maximal 200 m time trial, and a structured
    cool-down.  CSS is then derived from the two times.

    Unlike most activities the structure is fixed: rep counts and
    distances do not flex with ``target_tss``.  ``tss_min`` / ``tss_max``
    bracket the expected load for planning, and ``achieved_tss`` returns
    the midpoint of that range.  The protocol is duration-independent by
    design: ``tss_from_duration`` stays ``None``, so the card never
    resizes with solved hours.

    The warm-up, strides, time trials, and cool-down all render as
    distance-based steps (e.g. ``400 m``, ``4 x 50 m``); only the wall
    rests between strides and between time trials remain time-based.
    """

    warmup_meters: int
    strides_count: int
    strides_meters: int
    strides_rest_seconds: float
    tt1_meters: int
    tt_rest_seconds: float
    tt2_meters: int
    cooldown_meters: int
    if_warmup: float
    if_strides: float
    if_tt: float
    if_cooldown: float
    #: Headline copy rendered on the first time-trial step — describes the
    #: overall protocol intent (e.g. ``"400 m TT + 200 m TT — establish
    #: critical swim speed"``).
    protocol_description: str
    #: Cue rendered on each strides rep (e.g. ``"50 m build to race pace"``).
    strides_description: str = "build to race pace"

    def _pace(
        self, intensity_factor: float, resolver: ResolverProtocol | None
    ) -> float:
        return pace_sec_per_100m_from_css(
            _resolve_css(resolver),
            intensity_factor,
        )

    def _segment_minutes(
        self, resolver: ResolverProtocol | None
    ) -> tuple[float, float, float, float, float, float]:
        """Return (wu, strides_swim, strides_rest, tt_swim, tt_rest, cd) minutes."""
        wu_pace = self._pace(self.if_warmup, resolver)
        cd_pace = self._pace(self.if_cooldown, resolver)
        st_pace = self._pace(self.if_strides, resolver)
        tt_pace = self._pace(self.if_tt, resolver)
        wu_min = self.warmup_meters * wu_pace / PACE_DISTANCE_TO_MINUTES
        cd_min = self.cooldown_meters * cd_pace / PACE_DISTANCE_TO_MINUTES
        strides_swim_min = (
            self.strides_count * self.strides_meters * st_pace
        ) / PACE_DISTANCE_TO_MINUTES
        strides_rest_min = (self.strides_count * self.strides_rest_seconds) / 60.0
        tt_swim_min = (
            (self.tt1_meters + self.tt2_meters) * tt_pace
        ) / PACE_DISTANCE_TO_MINUTES
        tt_rest_min = self.tt_rest_seconds / 60.0
        return (
            wu_min,
            strides_swim_min,
            strides_rest_min,
            tt_swim_min,
            tt_rest_min,
            cd_min,
        )

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return total session duration in minutes (fixed structure)."""
        if self._is_rest_target(target_tss):
            return 0.0
        wu, st_swim, st_rest, tt_swim, tt_rest, cd = self._segment_minutes(resolver)
        return wu + st_swim + st_rest + tt_swim + tt_rest + cd

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return midpoint of the TSS range (protocol load is fixed)."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        return (self.tss_min + self.tss_max) / 2.0

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        if self._is_rest_target(target_tss):
            return []
        wu_min, st_swim_min, _, tt_swim_min, _, cd_min = self._segment_minutes(resolver)
        return [
            (wu_min, self.if_warmup),
            (st_swim_min, self.if_strides),
            (tt_swim_min, self.if_tt),
            (cd_min, self.if_cooldown),
        ]

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build the distance-based CSS test workout."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        wu_pace = self._pace(self.if_warmup, resolver)
        cd_pace = self._pace(self.if_cooldown, resolver)
        st_pace = self._pace(self.if_strides, resolver)
        tt_pace = self._pace(self.if_tt, resolver)
        strides_swim_sec = self.strides_meters * st_pace / 100.0
        strides_total_sec = self.strides_count * (
            strides_swim_sec + self.strides_rest_seconds
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_meters(
                    float(self.warmup_meters),
                    seconds=float(self.warmup_meters) * wu_pace / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_WARMUP_CUE,
            ),
            WorkoutStep(
                type="repeat",
                duration=_dur_seconds(strides_total_sec),
                iterations=self.strides_count,
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_dur_meters(
                            float(self.strides_meters),
                            seconds=strides_swim_sec,
                        ),
                        target=_rpe_target(7),
                        description=self.strides_description,
                    ),
                    WorkoutStep(
                        type="recovery",
                        duration=_dur_seconds(float(self.strides_rest_seconds)),
                        target=_rpe_target(2),
                        description="easy",
                    ),
                ],
            ),
            WorkoutStep(
                type="interval",
                duration=_dur_meters(
                    float(self.tt1_meters),
                    seconds=float(self.tt1_meters) * tt_pace / 100.0,
                ),
                target=_rpe_target(9),
                description=self.protocol_description,
            ),
            WorkoutStep(
                type="recovery",
                duration=_dur_seconds(float(self.tt_rest_seconds)),
                target=_rpe_target(2),
                description=(
                    "Wall rest between time trials — easy breathing, stay on "
                    "the pace clock"
                ),
            ),
            WorkoutStep(
                type="interval",
                duration=_dur_meters(
                    float(self.tt2_meters),
                    seconds=float(self.tt2_meters) * tt_pace / 100.0,
                ),
                target=_rpe_target(9),
                description=(
                    f"{self.tt2_meters} m time trial — go all-out; CSS is "
                    f"derived from this and the {self.tt1_meters} m time"
                ),
            ),
            WorkoutStep(
                type="cooldown",
                duration=_dur_meters(
                    float(self.cooldown_meters),
                    seconds=float(self.cooldown_meters) * cd_pace / 100.0,
                ),
                target=_open_target(),
                description=SWIM_OPEN_COOLDOWN_CUE,
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)


__all__ = [
    "PACE_DISTANCE_TO_MINUTES",
    "SWIM_OPEN_COOLDOWN_CUE",
    "SWIM_OPEN_REST_CUE",
    "SWIM_OPEN_WARMUP_CUE",
    "SWIM_PACE_ZONE_THRESHOLDS",
    "SWIM_SPLIT_REST_SECONDS",
    "SWIM_STEP_METERS",
    "SWIM_TOTAL_GRANULARITY_METERS",
    "SwimCssIntervalActivity",
    "SwimCssTestActivity",
    "SwimDrillSwimActivity",
    "SwimLinearDistanceActivity",
    "_dur_meters",
    "_resolve_css",
    "_round_cooldown_for_100m_total",
    "_round_to_100m",
    "_round_to_swim_step",
    "_select_lattice_target",
    "_swim_meters_tss",
    "_swim_zone_target",
]
