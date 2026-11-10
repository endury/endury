"""Concrete land-sport activity classes (run and bike) for the planner."""

from __future__ import annotations

from dataclasses import dataclass

from endury.planner.activity.shapes.base import (
    ActivityDefinition,
    _build_step_targets,
    _dur_seconds,
    _hr_zone,
    _rest_workout,
    _step_description,
)
from endury.planner.foundations.types import TargetPreference
from endury.training.load import (
    DEFAULT_THRESHOLD_TSS_PER_HOUR,
    RUN_BIKE_LOAD_EXPONENT,
    ResolverProtocol,
    tss_from_duration_minutes,
    tss_per_minute,
)
from endury.training.workout import (
    StructuredWorkout,
    WorkoutStep,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _warmup_cooldown_tss(
    warmup_min: float,
    if_warmup: float,
    cooldown_min: float,
    if_cooldown: float,
    load_exponent: float,
    threshold_tss_per_hour: float,
) -> float:
    """Compute combined TSS for warmup and cooldown phases.

    Shared by FixedBlockActivity, ProgressiveBlockActivity, and
    IntervalPiecewiseActivity.

    Args:
        warmup_min: Warmup duration in minutes.
        if_warmup: Warmup intensity factor.
        cooldown_min: Cooldown duration in minutes.
        if_cooldown: Cooldown intensity factor.
        load_exponent: TSS load model exponent.
        threshold_tss_per_hour: TSS at threshold intensity per hour.

    Returns:
        Sum of warmup and cooldown TSS at their respective intensity factors.
    """
    return tss_from_duration_minutes(
        warmup_min,
        if_warmup,
        exponent=load_exponent,
        threshold_tss_per_hour=threshold_tss_per_hour,
    ) + tss_from_duration_minutes(
        cooldown_min,
        if_cooldown,
        exponent=load_exponent,
        threshold_tss_per_hour=threshold_tss_per_hour,
    )


def _block_duration_tss(
    duration_minutes: float,
    *,
    warmup_min: float,
    cooldown_min: float,
    fixed_tss: float,
    main_rate: float,
) -> float:
    """Derive TSS for a warm-up + scalable-main + cool-down block shape.

    The shared duration-first inversion for LinearDurationActivity,
    FixedBlockActivity, and ProgressiveBlockActivity: carve the main block
    out of the requested total duration (1-minute floor, mirroring
    ``_main_minutes``) and price it at the shape's main-block TSS rate.

    Args:
        duration_minutes: Desired total session duration in minutes.
        warmup_min: Warmup duration in minutes.
        cooldown_min: Cooldown duration in minutes.
        fixed_tss: Combined warmup + cooldown TSS.
        main_rate: Main-block TSS accrued per minute.

    Returns:
        Derived TSS for the given duration (0.0 for non-positive input).
    """
    if duration_minutes <= 0.0:
        return 0.0
    main_min = max(duration_minutes - warmup_min - cooldown_min, 1.0)
    return fixed_tss + main_min * main_rate


# ---------------------------------------------------------------------------
# LinearDurationActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinearDurationActivity(ActivityDefinition):
    """Type-A activity with duration directly proportional to TSS.

    The materialised workout always carves an explicit warm-up and
    cool-down out of the total duration, so a long endurance session never
    renders as a single multi-hour interval on the athlete's card.
    ``warmup_min`` / ``cooldown_min`` set the carved durations and
    ``if_warmup`` / ``if_cooldown`` set their intensities (defaulting to
    one and two zone-bands easier than the main interval, clamped so very
    short / very easy activities like recovery runs still produce
    structurally sensible warm-ups).
    """

    intensity_factor: float
    rpe: int
    step_type: str = "interval"
    load_exponent: float = RUN_BIKE_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    cadence_low_rpm: int | None = None
    cadence_high_rpm: int | None = None
    #: Optional override for the main interval's athlete-facing description.
    #: Used by brick runs to carry race-specific cues that the generic
    #: IF-derived phrasing cannot express.
    step_description: str | None = None
    warmup_min: float = 8.0
    cooldown_min: float = 5.0
    if_warmup: float | None = None
    if_cooldown: float | None = None
    #: HR band (fractions of LTHR) for an easy main step; see
    #: ``LONG_EASY_HR_BAND``.
    easy_hr_band: tuple[float, float] | None = None

    @property
    def _resolved_if_warmup(self) -> float:
        if self.if_warmup is not None:
            return self.if_warmup
        return max(0.50, self.intensity_factor - 0.10)

    @property
    def _resolved_if_cooldown(self) -> float:
        if self.if_cooldown is not None:
            return self.if_cooldown
        return max(0.45, self.intensity_factor - 0.20)

    def _scaffold_tss(self) -> float:
        """Return combined TSS contributed by the warm-up and cool-down."""
        return _warmup_cooldown_tss(
            self.warmup_min,
            self._resolved_if_warmup,
            self.cooldown_min,
            self._resolved_if_cooldown,
            self.load_exponent,
            self.threshold_tss_per_hour,
        )

    def _main_minutes(self, target_tss: float) -> float:
        """Carve main-interval minutes out of the clamped target TSS."""
        tss = self._clamp_target(target_tss)
        rate = tss_per_minute(
            self.intensity_factor,
            exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )
        return max((tss - self._scaffold_tss()) / rate, 1.0)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact total duration including warm-up and cool-down."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        return self.warmup_min + self._main_minutes(target_tss) + self.cooldown_min

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Return achieved TSS summed across warm-up, main, and cool-down."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        main_min = self._main_minutes(target_tss)
        return self._scaffold_tss() + tss_from_duration_minutes(
            main_min,
            self.intensity_factor,
            exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build warm-up + main + cool-down workout steps."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        main_min = self._main_minutes(target_tss)
        wu_primary, wu_secondary, wu_additional = _build_step_targets(
            self._resolved_if_warmup,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        primary, secondary, additional = _build_step_targets(
            self.intensity_factor,
            self.sport,
            self.rpe,
            self.target_preference,
            resolver,
            self.easy_hr_band,
        )
        cadence = self._cadence_target()
        if cadence is not None:
            additional.append(cadence)
        cd_primary, cd_secondary, cd_additional = _build_step_targets(
            self._resolved_if_cooldown,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_seconds(self.warmup_min * 60.0),
                target=wu_primary,
                secondary_target=wu_secondary,
                additional_targets=wu_additional or None,
                description=_step_description("warmup", self._resolved_if_warmup),
            ),
            WorkoutStep(
                type=self.step_type,
                duration=_dur_seconds(main_min * 60.0),
                target=primary,
                secondary_target=secondary,
                additional_targets=additional or None,
                description=self.step_description
                or _step_description(
                    self.step_type,
                    self.intensity_factor,
                    sport=self.sport,
                    target_preference=self.target_preference,
                ),
            ),
            WorkoutStep(
                type="cooldown",
                duration=_dur_seconds(self.cooldown_min * 60.0),
                target=cd_primary,
                secondary_target=cd_secondary,
                additional_targets=cd_additional or None,
                description=_step_description("cooldown", self._resolved_if_cooldown),
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return []
        main_min = self._main_minutes(target_tss)
        return [
            (self.warmup_min, self._resolved_if_warmup),
            (main_min, self.intensity_factor),
            (self.cooldown_min, self._resolved_if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration for this linear activity.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Unused; present for interface compatibility.

        Returns:
            Derived TSS for the given duration including the carved
            warm-up and cool-down.
        """
        _ = resolver
        return _block_duration_tss(
            duration_minutes,
            warmup_min=self.warmup_min,
            cooldown_min=self.cooldown_min,
            fixed_tss=self._scaffold_tss(),
            main_rate=tss_per_minute(
                self.intensity_factor,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            ),
        )


# ---------------------------------------------------------------------------
# FixedBlockActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixedBlockActivity(ActivityDefinition):
    """Type-B activity with fixed warmup/cooldown and scalable main block."""

    warmup_min: float
    cooldown_min: float
    if_warmup: float
    if_main: float
    if_cooldown: float
    main_rpe: int
    load_exponent: float = RUN_BIKE_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    cadence_low_rpm: int | None = None
    cadence_high_rpm: int | None = None
    #: Optional override for the main block's athlete-facing description.
    #: Used by brick tempo activities to carry race-specific cues that the
    #: generic IF-derived phrasing cannot express.
    main_description: str | None = None
    #: Metric the main step leads with when it differs from the rest of the
    #: session: a brick's race-pace step leads with pace, its cool-down with
    #: heart rate.
    work_target_preference: TargetPreference | None = None

    def _fixed_tss(self) -> float:
        """Compute combined TSS for warmup and cooldown phases."""
        return _warmup_cooldown_tss(
            self.warmup_min,
            self.if_warmup,
            self.cooldown_min,
            self.if_cooldown,
            self.load_exponent,
            self.threshold_tss_per_hour,
        )

    def _main_minutes(self, target_tss: float) -> float:
        """Compute main block duration to absorb the remaining TSS budget.

        Args:
            target_tss: Desired total session TSS.

        Returns:
            Main block duration in minutes (minimum 1.0).
        """
        tss = self._clamp_target(target_tss)
        rate = tss_per_minute(
            self.if_main,
            exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )
        return max((tss - self._fixed_tss()) / rate, 1.0)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact duration for the fixed-block structure."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        return self.warmup_min + self._main_minutes(target_tss) + self.cooldown_min

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact achieved TSS for the fixed-block structure."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        main_minutes = self._main_minutes(target_tss)
        return (
            tss_from_duration_minutes(
                self.warmup_min,
                self.if_warmup,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
            + tss_from_duration_minutes(
                main_minutes,
                self.if_main,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
            + tss_from_duration_minutes(
                self.cooldown_min,
                self.if_cooldown,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
        )

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build warmup-main-cooldown workout steps."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        main_minutes = self._main_minutes(target_tss)
        wu_primary, wu_secondary, wu_additional = _build_step_targets(
            self.if_warmup,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        main_preference = self.work_target_preference or self.target_preference
        main_primary, main_secondary, main_additional = _build_step_targets(
            self.if_main,
            self.sport,
            self.main_rpe,
            main_preference,
            resolver,
        )
        cadence = self._cadence_target()
        if cadence is not None:
            main_additional.append(cadence)
        cd_primary, cd_secondary, cd_additional = _build_step_targets(
            self.if_cooldown,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_seconds(self.warmup_min * 60.0),
                target=wu_primary,
                secondary_target=wu_secondary,
                additional_targets=wu_additional or None,
                description=_step_description("warmup", self.if_warmup),
            ),
            WorkoutStep(
                type="interval",
                duration=_dur_seconds(main_minutes * 60.0),
                target=main_primary,
                secondary_target=main_secondary,
                additional_targets=main_additional or None,
                description=self.main_description
                or _step_description(
                    "interval",
                    self.if_main,
                    sport=self.sport,
                    target_preference=main_preference,
                ),
            ),
            WorkoutStep(
                type="cooldown",
                duration=_dur_seconds(self.cooldown_min * 60.0),
                target=cd_primary,
                secondary_target=cd_secondary,
                additional_targets=cd_additional or None,
                description=_step_description("cooldown", self.if_cooldown),
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return []
        main_min = self._main_minutes(target_tss)
        return [
            (self.warmup_min, self.if_warmup),
            (main_min, self.if_main),
            (self.cooldown_min, self.if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration for this fixed-block activity.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Unused; present for interface compatibility.

        Returns:
            Derived TSS for the given duration.
        """
        _ = resolver
        return _block_duration_tss(
            duration_minutes,
            warmup_min=self.warmup_min,
            cooldown_min=self.cooldown_min,
            fixed_tss=self._fixed_tss(),
            main_rate=tss_per_minute(
                self.if_main,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            ),
        )


# ---------------------------------------------------------------------------
# ProgressiveBlockActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProgressiveBlockActivity(ActivityDefinition):
    """Type-D activity with fixed warmup/cooldown and progressive main block.

    The main phase is split into ``progression_steps`` equal-duration segments
    whose intensity factors linearly interpolate from ``if_main_start`` to
    ``if_main_end``.
    """

    warmup_min: float
    cooldown_min: float
    if_warmup: float
    if_main_start: float
    if_main_end: float
    if_cooldown: float
    rpe_start: int
    rpe_end: int
    progression_steps: int = 3
    load_exponent: float = RUN_BIKE_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    cadence_low_rpm: int | None = None
    cadence_high_rpm: int | None = None
    #: HR band (fractions of LTHR) for the easy blocks of the progression;
    #: see ``LONG_EASY_HR_BAND``.
    easy_hr_band: tuple[float, float] | None = None
    #: Metric the blocks above the easy zone lead with: a progression's
    #: rising blocks lead with pace, its easy block with heart rate.
    work_target_preference: TargetPreference | None = None

    def _step_if(self, index: int) -> float:
        """Return the intensity factor for progression step *index*.

        Args:
            index: Zero-based step index.

        Returns:
            Linearly interpolated IF value.
        """
        if self.progression_steps <= 1:
            return (self.if_main_start + self.if_main_end) / 2.0
        return self.if_main_start + (self.if_main_end - self.if_main_start) * index / (
            self.progression_steps - 1
        )

    def _step_rpe(self, index: int) -> int:
        """Return the RPE for progression step *index*.

        Args:
            index: Zero-based step index.

        Returns:
            Linearly interpolated RPE (rounded to nearest int).
        """
        if self.progression_steps <= 1:
            return round((self.rpe_start + self.rpe_end) / 2.0)
        raw = self.rpe_start + (self.rpe_end - self.rpe_start) * index / (
            self.progression_steps - 1
        )
        return round(raw)

    def _effective_main_rate(self) -> float:
        """Average TSS-per-minute rate across all progression segments."""
        total = sum(
            tss_per_minute(
                self._step_if(i),
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
            for i in range(self.progression_steps)
        )
        return total / self.progression_steps

    def _fixed_tss(self) -> float:
        """Compute combined TSS for warmup and cooldown phases."""
        return _warmup_cooldown_tss(
            self.warmup_min,
            self.if_warmup,
            self.cooldown_min,
            self.if_cooldown,
            self.load_exponent,
            self.threshold_tss_per_hour,
        )

    def _main_minutes(self, target_tss: float) -> float:
        """Compute total main block duration across all progression steps.

        Args:
            target_tss: Desired total session TSS.

        Returns:
            Total main block duration in minutes (minimum 1.0).
        """
        tss = self._clamp_target(target_tss)
        return max((tss - self._fixed_tss()) / self._effective_main_rate(), 1.0)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact duration for the progressive-block structure.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver.

        Returns:
            Total workout duration in minutes.
        """
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        return self.warmup_min + self._main_minutes(target_tss) + self.cooldown_min

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute achieved TSS by summing per-segment contributions.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver.

        Returns:
            Achieved TSS after quantization.
        """
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        seg_minutes = self._main_minutes(target_tss) / self.progression_steps
        main_tss = sum(
            tss_from_duration_minutes(
                seg_minutes,
                self._step_if(i),
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
            for i in range(self.progression_steps)
        )
        return self._fixed_tss() + main_tss

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build warmup + progressive main steps + cooldown.

        Args:
            target_tss: Desired session TSS.
            resolver: Optional threshold resolver for pace/power targets.

        Returns:
            Structured workout with escalating main-phase targets.
        """
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        total_main = self._main_minutes(target_tss)
        seg_minutes = total_main / self.progression_steps

        wu_primary, wu_secondary, wu_additional = _build_step_targets(
            self.if_warmup,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        steps: list[WorkoutStep] = [
            WorkoutStep(
                type="warmup",
                duration=_dur_seconds(self.warmup_min * 60.0),
                target=wu_primary,
                secondary_target=wu_secondary,
                additional_targets=wu_additional or None,
                description=_step_description("warmup", self.if_warmup),
            ),
        ]

        for i in range(self.progression_steps):
            if_i = self._step_if(i)
            rpe_i = self._step_rpe(i)
            block_preference = self.target_preference
            above_easy = _hr_zone(if_i, self.sport) > 1
            if self.work_target_preference is not None and above_easy:
                block_preference = self.work_target_preference
            primary, secondary, additional = _build_step_targets(
                if_i,
                self.sport,
                rpe_i,
                block_preference,
                resolver,
                self.easy_hr_band,
            )
            cadence = self._cadence_target()
            if cadence is not None:
                additional.append(cadence)
            base_desc = _step_description(
                "interval",
                if_i,
                sport=self.sport,
                target_preference=block_preference,
            )
            # Prefix each progression block with its position so two blocks
            # that share a zone band do not render with identical copy.
            block_desc = f"Block {i + 1}/{self.progression_steps} — {base_desc}"
            steps.append(
                WorkoutStep(
                    type="interval",
                    duration=_dur_seconds(seg_minutes * 60.0),
                    target=primary,
                    secondary_target=secondary,
                    additional_targets=additional or None,
                    description=block_desc,
                ),
            )

        cd_primary, cd_secondary, cd_additional = _build_step_targets(
            self.if_cooldown,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        steps.append(
            WorkoutStep(
                type="cooldown",
                duration=_dur_seconds(self.cooldown_min * 60.0),
                target=cd_primary,
                secondary_target=cd_secondary,
                additional_targets=cd_additional or None,
                description=_step_description("cooldown", self.if_cooldown),
            ),
        )
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration for this progressive-block activity.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Unused; present for interface compatibility.

        Returns:
            Derived TSS for the given duration.
        """
        _ = resolver
        return _block_duration_tss(
            duration_minutes,
            warmup_min=self.warmup_min,
            cooldown_min=self.cooldown_min,
            fixed_tss=self._fixed_tss(),
            main_rate=self._effective_main_rate(),
        )

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return []
        total_main = self._main_minutes(target_tss)
        step_min = total_main / self.progression_steps
        segments: list[tuple[float, float]] = [(self.warmup_min, self.if_warmup)]
        segments.extend(
            (step_min, self._step_if(i)) for i in range(self.progression_steps)
        )
        segments.append((self.cooldown_min, self.if_cooldown))
        return segments


# ---------------------------------------------------------------------------
# IntervalPiecewiseActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntervalPiecewiseActivity(ActivityDefinition):
    """Type-C activity with repeat count switches and continuous TSS transitions."""

    warmup_min: float
    cooldown_min: float
    if_warmup: float
    if_cooldown: float
    if_work: float
    if_rest: float
    rep_count_min: int
    rep_count_max: int
    rep_work_min: float
    rep_work_max: float
    rep_rest_min: float
    work_rpe: int
    rest_rpe: int
    load_exponent: float = RUN_BIKE_LOAD_EXPONENT
    threshold_tss_per_hour: float = DEFAULT_THRESHOLD_TSS_PER_HOUR
    cadence_low_rpm: int | None = None
    cadence_high_rpm: int | None = None
    #: Upper end of the recovery band. When set, the recovery paired with a
    #: work rep interpolates linearly from ``rep_rest_min`` at
    #: ``rep_work_min`` to ``rep_rest_max`` at ``rep_work_max`` — longer reps
    #: buy longer recoveries. When ``None``, recovery stays fixed at
    #: ``rep_rest_min`` regardless of work duration.
    rep_rest_max: float | None = None
    #: Override the work-interval description. Falls back to the zone-band
    #: phrase from ``_step_description`` when ``None``.
    work_description: str | None = None
    #: Override the recovery-step description. Falls back to the
    #: duration-aware default in ``_step_description`` when ``None``.
    rest_description: str | None = None
    #: Order the recovery block *before* the work block within each rep so
    #: the session finishes on a work interval instead of an easy recovery
    #: block. For race-pace long rides this makes the ride end at race
    #: effort — the durability-rehearsal skill of holding pace into fatigue
    #: (see docs/research/triathlon_long_course.md, Training Durability) —
    #: rather than fading to its easiest Z2 block. TSS, duration and TID are
    #: order-independent, so this is a pure structural reorder.
    end_on_work: bool = False

    def _fixed_tss(self) -> float:
        """Compute combined TSS for warmup and cooldown phases."""
        return _warmup_cooldown_tss(
            self.warmup_min,
            self.if_warmup,
            self.cooldown_min,
            self.if_cooldown,
            self.load_exponent,
            self.threshold_tss_per_hour,
        )

    def _work_rate(self) -> float:
        """Return TSS per minute at the work interval intensity.

        Returns:
            TSS accrued per minute of work at ``if_work``.
        """
        return tss_per_minute(
            self.if_work,
            exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _rest_rate(self) -> float:
        """Return TSS per minute at the recovery interval intensity.

        Returns:
            TSS accrued per minute of recovery at ``if_rest``.
        """
        return tss_per_minute(
            self.if_rest,
            exponent=self.load_exponent,
            threshold_tss_per_hour=self.threshold_tss_per_hour,
        )

    def _rest_slope(self) -> float:
        """Return recovery minutes gained per extra minute of work in a rep.

        Zero unless ``rep_rest_max`` is declared, so a fixed-recovery
        activity's recovery duration never varies with work duration.

        Returns:
            Slope of the recovery band over the work band.
        """
        if self.rep_rest_max is None or self.rep_work_max <= self.rep_work_min:
            return 0.0
        return (self.rep_rest_max - self.rep_rest_min) / (
            self.rep_work_max - self.rep_work_min
        )

    def _rest_minutes(self, work_minutes: float) -> float:
        """Return the recovery minutes paired with one work rep.

        Args:
            work_minutes: Solved work duration for a single rep.

        Returns:
            Recovery minutes for that rep.
        """
        clamped = min(max(work_minutes, self.rep_work_min), self.rep_work_max)
        return self.rep_rest_min + self._rest_slope() * (clamped - self.rep_work_min)

    def _rep_work_tss_rate(self) -> float:
        """Return per-rep TSS accrued per minute of *work*.

        Includes the recovery that scales with the work rep, so a rep's total
        TSS stays affine in its work minutes: ``w * rate + offset``.

        Returns:
            TSS per work-minute, recovery scaling folded in.
        """
        return self._work_rate() + self._rest_slope() * self._rest_rate()

    def _rep_rest_tss_offset(self) -> float:
        """Return the per-rep recovery TSS that does not scale with work.

        Returns:
            The constant term of a rep's affine TSS model.
        """
        return (
            self.rep_rest_min - self._rest_slope() * self.rep_work_min
        ) * self._rest_rate()

    def _work_minutes_for_rep_minutes(self, rep_minutes: float) -> float:
        """Invert work + recovery back to the work minutes of one rep.

        Args:
            rep_minutes: Total wall-clock minutes for one work + recovery rep.

        Returns:
            Work minutes whose paired recovery fills out ``rep_minutes``.
        """
        slope = self._rest_slope()
        return (rep_minutes - self.rep_rest_min + slope * self.rep_work_min) / (
            1.0 + slope
        )

    def _rep_duration_range(self, reps: int) -> tuple[float, float]:
        """Return (lower, upper) work-duration bounds for a given rep count.

        Args:
            reps: Number of work-rest repetitions being evaluated.

        Returns:
            Tuple of (lower_minutes, upper_minutes) for work duration per rep.
        """
        work_rate = self._rep_work_tss_rate()
        rest_tss = self._rep_rest_tss_offset()
        if reps <= self.rep_count_min:
            lower = self.rep_work_min
        else:
            prev = reps - 1
            lower = (prev * self.rep_work_max * work_rate - rest_tss) / (
                reps * work_rate
            )
            lower = max(lower, self.rep_work_min)
        return lower, self.rep_work_max

    def _solve_pattern(self, target_tss: float) -> tuple[int, float]:
        """Find (rep_count, work_minutes_per_rep) to hit target TSS.

        Args:
            target_tss: Desired total session TSS.

        Returns:
            Tuple of (rep_count, work_minutes_per_rep).
        """
        tss = self._clamp_target(target_tss)
        avail = max(tss - self._fixed_tss(), 0.0)
        work_rate = self._rep_work_tss_rate()
        rest_tss = self._rep_rest_tss_offset()

        chosen_reps = self.rep_count_min
        chosen_work_min = self.rep_work_min

        for reps in range(self.rep_count_min, self.rep_count_max + 1):
            lower, upper = self._rep_duration_range(reps)
            tss_high = reps * (upper * work_rate + rest_tss)
            if avail <= tss_high + 1e-9:
                raw = (avail / reps - rest_tss) / work_rate
                chosen_reps = reps
                chosen_work_min = min(max(raw, lower), upper)
                break
        else:  # pragma: no cover
            chosen_reps = self.rep_count_max  # pragma: no cover
            _, upper = self._rep_duration_range(chosen_reps)  # pragma: no cover
            chosen_work_min = upper  # pragma: no cover

        return chosen_reps, chosen_work_min

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Project a request onto the feasible achieved-TSS bands."""
        _ = resolver
        lower = self.tss_min if lower_bound_tss is None else lower_bound_tss
        upper = self.tss_max if upper_bound_tss is None else upper_bound_tss
        if upper < lower:
            upper = lower
        requested = min(max(target_tss, lower), upper)

        fixed_tss = self._fixed_tss()
        work_rate = self._rep_work_tss_rate()
        rest_tss = self._rep_rest_tss_offset()

        def _intervals(
            clip_lower: float,
            clip_upper: float,
        ) -> list[tuple[float, float]]:
            intervals: list[tuple[float, float]] = []
            for reps in range(self.rep_count_min, self.rep_count_max + 1):
                work_low, work_high = self._rep_duration_range(reps)
                interval_low = fixed_tss + reps * (work_low * work_rate + rest_tss)
                interval_high = fixed_tss + reps * (work_high * work_rate + rest_tss)
                clipped_low = max(interval_low, clip_lower)
                clipped_high = min(interval_high, clip_upper)
                if clipped_low <= clipped_high + 1e-9:
                    intervals.append((clipped_low, clipped_high))
            return intervals

        intervals = _intervals(lower, upper)
        if not intervals:
            intervals = _intervals(self.tss_min, self.tss_max)
        if not intervals:  # pragma: no cover
            return requested  # pragma: no cover

        for interval_low, interval_high in intervals:
            if interval_low - 1e-9 <= requested <= interval_high + 1e-9:
                return requested

        non_overshoot = [
            interval_high for _, interval_high in intervals if interval_high < requested
        ]
        if non_overshoot:  # pragma: no cover
            return max(non_overshoot)  # pragma: no cover

        return min(interval_low for interval_low, _ in intervals)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute exact duration after rep-count selection."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        reps, work_minutes = self._solve_pattern(target_tss)
        return (
            self.warmup_min
            + self.cooldown_min
            + reps * work_minutes
            + reps * self._rest_minutes(work_minutes)
        )

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute achieved TSS from selected repeat pattern."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        reps, work_minutes = self._solve_pattern(target_tss)
        return (
            self._fixed_tss()
            + reps * work_minutes * self._work_rate()
            + reps * self._rest_minutes(work_minutes) * self._rest_rate()
        )

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build interval repeat workout steps."""
        if self._is_rest_target(target_tss):
            return _rest_workout(self.key)
        reps, work_minutes = self._solve_pattern(target_tss)
        rest_minutes = self._rest_minutes(work_minutes)
        repeat_seconds = (work_minutes + rest_minutes) * reps * 60.0
        wu_primary, wu_secondary, wu_additional = _build_step_targets(
            self.if_warmup,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        work_primary, work_secondary, work_additional = _build_step_targets(
            self.if_work,
            self.sport,
            self.work_rpe,
            self.target_preference,
            resolver,
        )
        cadence = self._cadence_target()
        if cadence is not None:
            work_additional.append(cadence)
        rest_primary, rest_secondary, rest_additional = _build_step_targets(
            self.if_rest,
            self.sport,
            self.rest_rpe,
            self.target_preference,
            resolver,
        )
        cd_primary, cd_secondary, cd_additional = _build_step_targets(
            self.if_cooldown,
            self.sport,
            2,
            self.target_preference,
            resolver,
        )
        work_duration_s = work_minutes * 60.0
        rest_duration_s = rest_minutes * 60.0
        interval_step = WorkoutStep(
            type="interval",
            duration=_dur_seconds(work_duration_s),
            target=work_primary,
            secondary_target=work_secondary,
            additional_targets=work_additional or None,
            description=self.work_description
            or _step_description(
                "interval",
                self.if_work,
                sport=self.sport,
                target_preference=self.target_preference,
                work_duration_s=work_duration_s,
            ),
        )
        recovery_step = WorkoutStep(
            type="recovery",
            duration=_dur_seconds(rest_duration_s),
            target=rest_primary,
            secondary_target=rest_secondary,
            additional_targets=rest_additional or None,
            description=self.rest_description
            or _step_description(
                "recovery",
                self.if_rest,
                work_duration_s=work_duration_s,
                rest_duration_s=rest_duration_s,
            ),
        )
        inner_steps = (
            [recovery_step, interval_step]
            if self.end_on_work
            else [interval_step, recovery_step]
        )
        repeat_step = WorkoutStep(
            type="repeat",
            duration=_dur_seconds(repeat_seconds),
            iterations=reps,
            steps=inner_steps,
        )
        steps = [
            WorkoutStep(
                type="warmup",
                duration=_dur_seconds(self.warmup_min * 60.0),
                target=wu_primary,
                secondary_target=wu_secondary,
                additional_targets=wu_additional or None,
                description=_step_description("warmup", self.if_warmup),
            ),
            repeat_step,
            WorkoutStep(
                type="cooldown",
                duration=_dur_seconds(self.cooldown_min * 60.0),
                target=cd_primary,
                secondary_target=cd_secondary,
                additional_targets=cd_additional or None,
                description=_step_description("cooldown", self.if_cooldown),
            ),
        ]
        return StructuredWorkout(workout_name=self.key, steps=steps)

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return []
        reps, work_minutes = self._solve_pattern(target_tss)
        return [
            (self.warmup_min, self.if_warmup),
            (reps * work_minutes, self.if_work),
            (reps * self._rest_minutes(work_minutes), self.if_rest),
            (self.cooldown_min, self.if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Derive TSS from target duration by inverting the rep lattice.

        Enumerates one candidate per rep count — work minutes carved from
        the remaining duration and clamped to that rep count's bounds —
        and returns the lattice TSS whose re-solved duration lands closest
        to the request, so the rendered card tracks the solved session
        duration to within one rep's wall-clock.

        Args:
            duration_minutes: Desired total session duration in minutes.
            resolver: Unused; present for interface compatibility.

        Returns:
            Derived TSS for the given duration.
        """
        if duration_minutes <= 0.0:
            return 0.0
        avail_min = max(duration_minutes - self.warmup_min - self.cooldown_min, 0.0)
        work_rate = self._rep_work_tss_rate()
        rest_tss = self._rep_rest_tss_offset()
        fixed_tss = self._fixed_tss()
        candidates: list[float] = []
        for reps in range(self.rep_count_min, self.rep_count_max + 1):
            lower, upper = self._rep_duration_range(reps)
            raw = self._work_minutes_for_rep_minutes(avail_min / reps)
            work_min = min(max(raw, lower), upper)
            candidates.append(fixed_tss + reps * (work_min * work_rate + rest_tss))
        return self._closest_duration_tss(
            duration_minutes, candidates, resolver=resolver
        )


# ---------------------------------------------------------------------------
# OverUnderActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverUnderActivity(IntervalPiecewiseActivity):
    """Interval reps that alternate just under and just over threshold.

    Each work rep is a whole number of cycles, ``under_min`` at ``if_under``
    then ``over_min`` at ``if_over``, so a rep is fixed at a multiple of the
    cycle (``rep_work_min == rep_work_max``) and the session grows by reps.
    ``if_work`` is unused: the work minutes are priced and zoned at their
    two intensities.
    """

    under_min: float = 2.0
    over_min: float = 1.0
    if_under: float = 0.93
    if_over: float = 1.05

    def _cycles(self) -> int:
        """Return the under/over cycles in one work rep."""
        return round(self.rep_work_min / (self.under_min + self.over_min))

    def _work_rate(self) -> float:
        """Return TSS per work minute, time-weighted over under and over.

        Returns:
            TSS accrued per minute of a work rep.
        """
        cycle = self.under_min + self.over_min
        return (
            self.under_min
            * tss_per_minute(
                self.if_under,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
            + self.over_min
            * tss_per_minute(
                self.if_over,
                exponent=self.load_exponent,
                threshold_tss_per_hour=self.threshold_tss_per_hour,
            )
        ) / cycle

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build the reps with each work block split into under/over cycles."""
        workout = IntervalPiecewiseActivity.build(self, target_tss, resolver)
        if self._is_rest_target(target_tss):
            return workout
        cycle_steps = [
            self._block_step("Under", self.under_min, self.if_under, resolver),
            self._block_step("Over", self.over_min, self.if_over, resolver),
        ]
        cycles = WorkoutStep(
            type="repeat",
            duration=_dur_seconds(
                (self.under_min + self.over_min) * self._cycles() * 60.0
            ),
            iterations=self._cycles(),
            steps=cycle_steps,
        )
        repeat = workout.steps[1]
        workout.steps[1] = repeat.model_copy(
            update={
                "steps": [
                    cycles if step.type == "interval" else step
                    for step in repeat.steps or []
                ]
            }
        )
        return workout

    def _block_step(
        self,
        label: str,
        minutes: float,
        if_value: float,
        resolver: ResolverProtocol | None,
    ) -> WorkoutStep:
        """Return one under or over block of a cycle, its label leading the cue."""
        primary, secondary, additional = _build_step_targets(
            if_value, self.sport, self.work_rpe, self.target_preference, resolver
        )
        return WorkoutStep(
            type="interval",
            duration=_dur_seconds(minutes * 60.0),
            target=primary,
            secondary_target=secondary,
            additional_targets=additional or None,
            description=f"{label}: "
            + _step_description(
                "interval",
                if_value,
                sport=self.sport,
                target_preference=self.target_preference,
                work_duration_s=minutes * 60.0,
            ),
        )

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        if self._is_rest_target(target_tss):
            return []
        reps, work_minutes = self._solve_pattern(target_tss)
        cycle = self.under_min + self.over_min
        work = reps * work_minutes
        return [
            (self.warmup_min, self.if_warmup),
            (work * self.under_min / cycle, self.if_under),
            (work * self.over_min / cycle, self.if_over),
            (reps * self._rest_minutes(work_minutes), self.if_rest),
            (self.cooldown_min, self.if_cooldown),
        ]


# ---------------------------------------------------------------------------
# IntervalSetActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntervalSetActivity(IntervalPiecewiseActivity):
    """Short fixed reps in sets, ``set_rest_min`` of active recovery between sets.

    Every set holds the same number of reps (``rep_count_min`` to
    ``rep_count_max``) of ``rep_work_max`` minutes, each followed by its
    recovery; the session grows by reps within a set, then by sets, up to
    ``set_count_max``. One set builds exactly as the fixed-rep parent. The
    lattice is discrete: a target between two of its points builds the one
    above and projects onto the one below, as the parent does.
    """

    set_count_max: int = 1
    set_rest_min: float = 3.0

    def _point_tss(self, sets: int, reps: int) -> float:
        """Return the session TSS of ``sets`` sets of ``reps`` reps."""
        rep_tss = (
            self.rep_work_max * self._work_rate()
            + self._rest_minutes(self.rep_work_max) * self._rest_rate()
        )
        return (
            self._fixed_tss()
            + sets * reps * rep_tss
            + (sets - 1) * self.set_rest_min * self._rest_rate()
        )

    def _lattice(self) -> list[tuple[int, int]]:
        """Return every ``(sets, reps per set)`` the session builds, by TSS."""
        return sorted(
            (
                (sets, reps)
                for sets in range(1, self.set_count_max + 1)
                for reps in range(self.rep_count_min, self.rep_count_max + 1)
            ),
            key=lambda point: self._point_tss(*point),
        )

    def _solve_sets(self, target_tss: float) -> tuple[int, int]:
        """Return the smallest ``(sets, reps)`` reaching the target, else the top."""
        tss = self._clamp_target(target_tss)
        lattice = self._lattice()
        return next(
            (point for point in lattice if tss <= self._point_tss(*point) + 1e-9),
            lattice[-1],
        )

    def _solve_pattern(self, target_tss: float) -> tuple[int, float]:
        """Return one set's reps and rep length, so the parent builds one set."""
        return self._solve_sets(target_tss)[1], self.rep_work_max

    def project_target_tss(
        self,
        target_tss: float,
        *,
        lower_bound_tss: float | None = None,
        upper_bound_tss: float | None = None,
        resolver: ResolverProtocol | None = None,
    ) -> float:
        """Project a request onto the highest lattice point at or below it."""
        _ = resolver
        lower = self.tss_min if lower_bound_tss is None else lower_bound_tss
        upper = self.tss_max if upper_bound_tss is None else upper_bound_tss
        upper = max(upper, lower)
        requested = min(max(target_tss, lower), upper)
        points = [self._point_tss(*point) for point in self._lattice()]
        inside = [tss for tss in points if lower - 1e-9 <= tss <= upper + 1e-9]
        candidates = inside or points
        below = [tss for tss in candidates if tss <= requested + 1e-9]
        return max(below) if below else min(candidates)

    def duration_minutes_exact(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute the duration of the solved sets."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        sets, reps = self._solve_sets(target_tss)
        rep_minutes = self.rep_work_max + self._rest_minutes(self.rep_work_max)
        return (
            self.warmup_min
            + self.cooldown_min
            + sets * reps * rep_minutes
            + (sets - 1) * self.set_rest_min
        )

    def achieved_tss(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> float:
        """Compute the TSS of the solved sets."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return 0.0
        return self._point_tss(*self._solve_sets(target_tss))

    def build(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> StructuredWorkout:
        """Build one set, then repeat it with active recovery between sets."""
        workout = IntervalPiecewiseActivity.build(self, target_tss, resolver)
        if self._is_rest_target(target_tss):
            return workout
        sets, _ = self._solve_sets(target_tss)
        warmup, one_set, cooldown = workout.steps
        primary, secondary, additional = _build_step_targets(
            self.if_rest, self.sport, self.rest_rpe, self.target_preference, resolver
        )
        between = WorkoutStep(
            type="recovery",
            duration=_dur_seconds(self.set_rest_min * 60.0),
            target=primary,
            secondary_target=secondary,
            additional_targets=additional or None,
            description=_step_description("recovery", self.if_rest),
        )
        steps = [warmup, one_set]
        for _ in range(sets - 1):
            steps += [between, one_set]
        return StructuredWorkout(workout_name=self.key, steps=[*steps, cooldown])

    def segments(
        self, target_tss: float, resolver: ResolverProtocol | None = None
    ) -> list[tuple[float, float]]:
        """Return ``(minutes, intensity factor)`` per segment, in order."""
        _ = resolver
        if self._is_rest_target(target_tss):
            return []
        sets, reps = self._solve_sets(target_tss)
        return [
            (self.warmup_min, self.if_warmup),
            (sets * reps * self.rep_work_max, self.if_work),
            (
                sets * reps * self._rest_minutes(self.rep_work_max)
                + (sets - 1) * self.set_rest_min,
                self.if_rest,
            ),
            (self.cooldown_min, self.if_cooldown),
        ]

    def tss_from_duration(
        self, duration_minutes: float, resolver: ResolverProtocol | None = None
    ) -> float | None:
        """Return the lattice TSS whose duration lands closest to the request."""
        if duration_minutes <= 0.0:
            return 0.0
        return self._closest_duration_tss(
            duration_minutes,
            [self._point_tss(*point) for point in self._lattice()],
            resolver=resolver,
        )


__all__ = [
    "FixedBlockActivity",
    "IntervalPiecewiseActivity",
    "IntervalSetActivity",
    "LinearDurationActivity",
    "OverUnderActivity",
    "ProgressiveBlockActivity",
    "_block_duration_tss",
    "_warmup_cooldown_tss",
]
