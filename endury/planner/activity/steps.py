"""Flat-step decomposition and coaching notes for structured workouts.

Bridges a materialized ``StructuredWorkout`` (nested steps + repeat blocks)
to the flat, athlete-facing timeline other layers render: one ``FlatStep``
per non-zero-duration segment, annotated with a TID zone-band, formatted
target labels, and a display colour (:func:`build_flat_steps`,
:func:`build_table_steps`), plus the per-session coaching notes — fueling
cadence, brick context, race-rehearsal kit cues (:func:`coaching_notes`).

Consumed by the read-only planner reports/dossier
(``devtools.planner``, via a thin re-export shim in
``devtools/planner/_shared/sessions.py``) and by the ad-hoc coach
workout tool (``endury.coach.tools.workout_tools``).

Depends only on ``endury.training`` and ``planner`` internals — no
``endury.domain`` import — so it stays reachable from both the coach tool
layer and devtools without pulling in the host domain.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fallback critical-swim-speed (s/100 m) for duration-estimating
# distance-only steps when no athlete CSS is supplied.
from endury.planner.activity.config import (
    DEFAULT_SWIM_CSS_SEC_PER_100M as DEFAULT_SWIM_CSS_SEC_PER_100M,
)
from endury.planner.activity.materialize import workout_total_meters
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    PlacedSession,
    SessionOrigin,
    SportType,
)
from endury.training.load import ResolverProtocol
from endury.training.workout import StructuredWorkout, TargetDef, WorkoutStep

# Zone-band colour + mapping tables (consumed by FlatStep.display_color)

ZONE_COLORS: dict[str, str] = {
    "z_low": "#52B788",  # soft green  — aerobic
    "z_mid": "#F4A261",  # amber       — threshold
    "z_high": "#E63946",  # red         — high intensity
    "wu_cd": "#ADB5BD",  # light grey  — warmup / cooldown
    "recovery": "#C2E0CB",  # pale green — active recovery between intervals
    "rest": "#E9ECEF",  # near-white  — rest
}

# Maps HR / swim-pace / pace zone numbers to TID band keys on the planner's
# cut points (``TID_CUTS``, R-ACT-009): every sport's LT1 sits at the top of
# its zone 2 and LT2 at the top of its zone 4, so 1-2 → z_low, 3-4 → z_mid,
# 5+ → z_high. Exact for swim pace; a run's LT1 (IF 0.85) sits inside
# Friel's pace zone 2 (0.775-0.877), so a run at 0.85-0.877 reads z_low here
# and z_mid in ``tid_fractions``.
_HR_ZONE_TO_BAND: dict[int, str] = {
    1: "z_low",
    2: "z_low",
    3: "z_mid",
    4: "z_mid",
    5: "z_high",
}

# Coggan 7-zone → band, the same split: LT1 is the top of power zone 2
# (IF 0.75); LT2, FTP, falls inside zone 4, so IF 1.00-1.05 reads z_mid
# here and z_high in ``tid_fractions``.
_POWER_ZONE_TO_BAND: dict[int, str] = {
    1: "z_low",
    2: "z_low",
    3: "z_mid",
    4: "z_mid",
    5: "z_high",
    6: "z_high",
    7: "z_high",
}

# Per-zone-number colour palette (green → red gradient, up to 7 zones)
ZONE_NUMBER_COLORS: dict[int, str] = {
    1: "#52B788",
    2: "#7BC47F",
    3: "#F4D35E",
    4: "#F4A261",
    5: "#E63946",
    6: "#C1121F",
    7: "#780000",
}


# Flat-step data model


@dataclass
class FlatStep:
    """A single timeline segment after flattening repeat blocks."""

    step_type: str  # warmup / cooldown / interval / recovery / rest
    duration_s: float
    zone_band: str  # z_low / z_mid / z_high / wu_cd / recovery / rest
    primary_label: str
    secondary_label: str
    extra_labels: list[str]
    description: str
    distance_m: float | None = None
    zone_numbers: dict[str, int] | None = None  # model_key → zone number
    iterations: int = 1  # repeat-block multiplier for table display

    @property
    def display_color(self) -> str:
        """Return the display colour for this step.

        Uses the zone-number palette for training steps that have zone data,
        falling back to the TID-band colour for scaffold steps (warmup,
        cooldown, recovery, rest).
        """
        if self.step_type in {"warmup", "cooldown", "recovery", "rest"}:
            return ZONE_COLORS.get(self.zone_band, "#ADB5BD")
        if self.zone_numbers:
            zone_num = next(iter(self.zone_numbers.values()))
            return ZONE_NUMBER_COLORS.get(zone_num, "#ADB5BD")
        return ZONE_COLORS.get(self.zone_band, "#ADB5BD")


# Step-flattening and zone helpers


def _flatten_steps(steps: list[WorkoutStep]) -> list[WorkoutStep]:
    """Recursively expand repeat blocks into individual steps."""
    flat: list[WorkoutStep] = []
    for step in steps:
        if step.type == "repeat" and step.steps and step.iterations:
            for _ in range(step.iterations):
                flat.extend(_flatten_steps(step.steps))
        else:
            flat.append(step)
    return flat


def _step_duration_seconds(step: WorkoutStep, swim_css_sec_per_100m: float) -> float:
    """Return step duration in seconds, estimating distance steps via swim CSS.

    Args:
        step: Workout step to measure.
        swim_css_sec_per_100m: Critical swim speed used to estimate the
            duration of distance-only (meters) steps.
    """
    if step.duration is None:
        return 0.0
    if step.duration.value_seconds is not None:
        return step.duration.value_seconds
    if step.duration.value_meters is not None:
        return step.duration.value_meters * swim_css_sec_per_100m / 100.0
    return 0.0


def _zone_band_from_target(
    step_type: str,
    primary: TargetDef | None,
    additional: list[TargetDef] | None = None,
) -> str:
    """Map a WorkoutStep to a TID zone-band key.

    Checks primary target first, then falls through to additional targets
    (e.g. power_zone appended alongside a watts-based primary).
    """
    if step_type in {"warmup", "cooldown"}:
        return "wu_cd"
    if step_type == "recovery":
        return "recovery"
    if step_type == "rest":
        return "rest"

    all_targets = [t for t in ([primary] + (additional or [])) if t is not None]
    for target in all_targets:
        t = target.type
        zone = target.zone
        if t == "heart_rate_zone" and zone is not None:
            return _HR_ZONE_TO_BAND.get(zone, "z_mid")
        if t in {"pace_zone", "swim_pace_zone"} and zone is not None:
            return _HR_ZONE_TO_BAND.get(min(zone, 5), "z_mid")
        if t == "power_zone" and zone is not None:
            return _POWER_ZONE_TO_BAND.get(min(zone, 7), "z_mid")
        if t == "rpe" and target.rpe is not None:
            rpe = target.rpe
            if rpe <= 5:
                return "z_low"
            if rpe <= 7:
                return "z_mid"
            return "z_high"
    return "z_low"


def fmt_pace(min_per_km: float) -> str:
    """Format a decimal min/km value as ``M:SS/km``."""
    minutes = int(min_per_km)
    seconds = round((min_per_km - minutes) * 60)
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/km"


def fmt_swim_pace(low_sec_per_100m: float, high_sec_per_100m: float) -> str:
    """Format a swim pace band in seconds per 100 m as ``M:SS-M:SS/100m``."""
    low, high = round(low_sec_per_100m), round(high_sec_per_100m)
    return f"{low // 60}:{low % 60:02d}-{high // 60}:{high % 60:02d}/100m"


def _format_target(target: TargetDef | None) -> str:
    """Return a compact human-readable string for a TargetDef."""
    if target is None:
        return "—"
    t = target.type
    if t == "heart_rate_zone":
        if target.low_bpm and target.high_bpm:
            band = f"{target.low_bpm}-{target.high_bpm} bpm"
        elif target.high_bpm:
            # Zone 1 starts at 0 bpm, so its band has no floor: read the
            # ceiling alone rather than dropping the range altogether.
            band = f"under {target.high_bpm} bpm"
        else:
            return f"HR Z{target.zone}"
        # A band that crosses the athlete's own zones carries no zone number.
        if target.zone is None:
            return f"HR {band}"
        return f"HR Z{target.zone} ({band})"
    if t == "pace":
        if target.low_min_per_km and target.high_min_per_km:
            low = fmt_pace(target.low_min_per_km)
            high = fmt_pace(target.high_min_per_km)
            return f"{low}-{high}"
        return "Pace"
    if t == "pace_zone":
        return f"Pace Z{target.zone}"
    if t == "swim_pace_zone":
        if not (target.low_sec_per_100m and target.high_sec_per_100m):
            return f"Swim Z{target.zone}"
        band = fmt_swim_pace(target.low_sec_per_100m, target.high_sec_per_100m)
        # A band that crosses the athlete's own zones carries no zone number.
        if target.zone is None:
            return f"Swim {band}"
        return f"Swim Z{target.zone} ({band})"
    if t == "swim_pace":
        if target.low_sec_per_100m and target.high_sec_per_100m:
            return fmt_swim_pace(target.low_sec_per_100m, target.high_sec_per_100m)
        return "Swim pace"
    if t == "power":
        if target.low_watts and target.high_watts:
            return f"{target.low_watts}-{target.high_watts} W"
        return "Power"
    if t == "power_zone":
        return f"Power Z{target.zone}"
    if t == "rpe":
        return f"RPE {target.rpe}"
    if t == "cadence":
        return f"{target.low_rpm}-{target.high_rpm} rpm"
    if t == "open":
        return "Open effort"
    return t


def fmt_seconds(total_s: float) -> str:
    """Format seconds as m:ss or h:mm for display.

    Sub-minute durations render in whole seconds (e.g. ``45s``) so short
    intervals never appear as ``0m 45s`` or fractional minutes. Durations
    of 10 minutes or more round to whole minutes so the card never shows
    odd strings like ``36m 56s`` for a steady block.
    """
    total_s = max(0.0, total_s)
    total_min_round = round(total_s / 60)
    if total_min_round >= 10:
        if total_min_round >= 60:
            h = total_min_round // 60
            m = total_min_round % 60
            return f"{h}h {m:02d}m" if m else f"{h}h"
        return f"{total_min_round} min"
    total_min = int(total_s / 60)
    sec = int(total_s) % 60
    if total_min == 0:
        return f"{sec}s"
    if sec == 0:
        return f"{total_min} min"
    return f"{total_min}m {sec:02d}s"


def fmt_distance(meters: float) -> str:
    """Format meters for swim display.

    Always renders in meters, never kilometers, so a 3.8 km Ironman swim
    shows as ``3,800 m``.
    """
    m = round(meters)
    return f"{m:,d} m"


def _extract_zone_numbers(step: WorkoutStep) -> dict[str, int]:
    """Extract per-zone-model zone numbers from a step's targets.

    Scans primary, secondary, and additional targets for zone-typed targets
    and returns a dict mapping model keys to zone numbers.

    Args:
        step: Flattened workout step with resolved targets.

    Returns:
        Dict of model_key → zone_number for each zone model present.
    """
    zones: dict[str, int] = {}
    all_targets = [
        t
        for t in (
            [step.target, step.secondary_target] + (step.additional_targets or [])
        )
        if t is not None
    ]
    for target in all_targets:
        if target.type == "heart_rate_zone" and target.zone is not None:
            zones["hr"] = target.zone
        elif target.type == "pace_zone" and target.zone is not None:
            zones["pace"] = target.zone
        elif target.type == "power_zone" and target.zone is not None:
            zones["power"] = target.zone
        elif target.type == "swim_pace_zone" and target.zone is not None:
            zones["swim_pace"] = target.zone

    if step.type in {"warmup", "cooldown", "recovery"}:
        rpe_val: int | None = None
        for target in all_targets:
            if target.type == "rpe" and target.rpe is not None:
                rpe_val = target.rpe
                break
        if "hr" not in zones:
            zones["hr"] = 1
        if "pace" not in zones and any(
            t.type in {"pace", "pace_zone"} for t in all_targets
        ):
            zones["pace"] = 1
        if "power" not in zones and any(
            t.type in {"power", "power_zone"} for t in all_targets
        ):
            zones["power"] = 1
        if "swim_pace" not in zones and any(
            t.type in {"swim_pace", "swim_pace_zone"} for t in all_targets
        ):
            zones["swim_pace"] = 1
        _ = rpe_val

    return zones


def _flat_step_from_workout_step(
    step: WorkoutStep,
    *,
    iterations: int = 1,
    swim_css_sec_per_100m: float = DEFAULT_SWIM_CSS_SEC_PER_100M,
) -> FlatStep | None:
    """Build a ``FlatStep`` for a single non-repeat workout step.

    Returns ``None`` for zero-duration steps.
    """
    dur_s = _step_duration_seconds(step, swim_css_sec_per_100m)
    dist_m = (
        step.duration.value_meters
        if step.duration is not None and step.duration.value_meters is not None
        else None
    )
    if dur_s <= 0.0 and not (dist_m and dist_m > 0.0):
        return None
    band = _zone_band_from_target(step.type, step.target, step.additional_targets)
    return FlatStep(
        step_type=step.type,
        duration_s=dur_s,
        zone_band=band,
        primary_label=_format_target(step.target),
        secondary_label=_format_target(step.secondary_target),
        extra_labels=[_format_target(t) for t in (step.additional_targets or [])],
        description=step.description or "",
        distance_m=dist_m,
        zone_numbers=_extract_zone_numbers(step) or None,
        iterations=iterations,
    )


def build_flat_steps(
    workout_steps: list[WorkoutStep],
    *,
    swim_css_sec_per_100m: float = DEFAULT_SWIM_CSS_SEC_PER_100M,
) -> list[FlatStep]:
    """Flatten workout steps and annotate with zone and target labels.

    Zero-duration steps are skipped — they would otherwise appear as empty
    rows in a step table and as invisible (or 4 px minimum) segments in an
    SVG timeline.

    Args:
        workout_steps: Steps from a materialized ``StructuredWorkout``
            (may contain nested repeat blocks).
        swim_css_sec_per_100m: Critical swim speed used to estimate the
            duration of distance-only (meters) steps. Defaults to
            :data:`DEFAULT_SWIM_CSS_SEC_PER_100M`.

    Returns:
        One ``FlatStep`` per non-zero-duration segment, repeat blocks
        expanded into individual reps.
    """
    flat: list[FlatStep] = []
    for step in _flatten_steps(workout_steps):
        flat_step = _flat_step_from_workout_step(
            step, swim_css_sec_per_100m=swim_css_sec_per_100m
        )
        if flat_step is not None:
            flat.append(flat_step)
    return flat


def build_table_steps(
    workout_steps: list[WorkoutStep],
    *,
    swim_css_sec_per_100m: float = DEFAULT_SWIM_CSS_SEC_PER_100M,
) -> list[FlatStep]:
    """Build table rows that preserve repeat-block structure.

    Each repeat block contributes one row per sub-step, with the rep count
    applied as ``iterations`` so the table renders ``N x dur`` instead of
    listing every individual rep.

    Args:
        workout_steps: Steps from a materialized ``StructuredWorkout``
            (may contain nested repeat blocks).
        swim_css_sec_per_100m: Critical swim speed used to estimate the
            duration of distance-only (meters) steps. Defaults to
            :data:`DEFAULT_SWIM_CSS_SEC_PER_100M`.

    Returns:
        One ``FlatStep`` row per (sub-)step, with ``iterations`` carrying
        the repeat count for nested repeat blocks.
    """
    rows: list[FlatStep] = []
    for step in workout_steps:
        if step.type == "repeat" and step.steps and step.iterations:
            iters = step.iterations
            for sub in step.steps:
                if sub.type == "repeat" and sub.steps and sub.iterations:
                    flat_subs = build_table_steps(
                        [sub], swim_css_sec_per_100m=swim_css_sec_per_100m
                    )
                    for nested_sub in flat_subs:
                        nested_sub.iterations *= iters
                        rows.append(nested_sub)
                else:
                    flat_sub = _flat_step_from_workout_step(
                        sub,
                        iterations=iters,
                        swim_css_sec_per_100m=swim_css_sec_per_100m,
                    )
                    if flat_sub is not None:
                        rows.append(flat_sub)
        else:
            flat_step = _flat_step_from_workout_step(
                step, swim_css_sec_per_100m=swim_css_sec_per_100m
            )
            if flat_step is not None:
                rows.append(flat_step)
    return rows


# Coaching notes


#: Activity keys for IM race-pace / long-brick / sweet-spot rehearsal sessions.
#: These are dress rehearsals for race day, so cards render gear/kit cues
#: in addition to the standard step descriptions.
_IM_RACE_REHEARSAL_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.BIKE_LONG_IM_RACE_PACE,
        ActivityKey.BIKE_LONG_HIM_RACE_PACE,
        ActivityKey.BIKE_LONG_SWEET_SPOT,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_BRICK_HIM_TEMPO,
        ActivityKey.RUN_BRICK_IM_TEMPO,
    }
)

#: Activity keys that benefit from open-water-swim cues when long enough.
#: All freestyle-distance swim sessions qualify; drill / sprint micro-sets
#: do not.
_OWS_CAPABLE_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.SWIM_LONG,
        ActivityKey.SWIM_ENDURANCE,
        ActivityKey.SWIM_THRESHOLD,
        ActivityKey.SWIM_CSS,
        ActivityKey.SWIM_CSS_SHORT,
    }
)

#: Goals whose long runs are Ironman run-leg rehearsals rather than
#: standalone endurance runs. The note names "IM" and contrasts itself
#: with a marathon long run, so it must not fire on shorter goals — a
#: half-Ironman PEAK long run reaches 95-125 min and would otherwise pick
#: it up on the duration threshold alone.
_IM_RUN_LEG_GOALS: frozenset[GoalType] = frozenset({GoalType.IRONMAN})

#: Fluid on a card: the brief sheet's band (endury/planning/brief/fuelling.yaml,
#: which holds the evidence). tests/planning/test_card_fuel_cues.py holds the two equal.
_FLUID_CUE = "400-700 ml fluid / h, to thirst"


def _carb_cue(duration_min: float) -> str:
    """Return a session's carbs an hour: the brief sheet's bands by length."""
    if duration_min <= 150.0:
        return "30-60 g carbs / h"
    if duration_min <= 180.0:
        return "60-80 g carbs / h"
    return "60-90 g carbs / h (the top only if you have practised it)"


#: Trail long-run climb-density targets, in metres of gain per hour of
#: running — the weekly vertical-gain norm divided by peak weekly hours
#: per tier (docs/research/trail_running.md, Per-Category Planning
#: Parameters), so the band scales with race category without needing an
#: ambition input. The maintenance band sits below the 20K's, since that
#: block preserves climbing rather than building it. These are guidance
#: figures, not measured norms.
_TRAIL_LONG_RUN_VERT_M_PER_H: dict[GoalType, tuple[int, int]] = {
    GoalType.TRAIL_20K: (170, 260),
    GoalType.TRAIL_50K: (250, 330),
    GoalType.TRAIL_100K: (280, 420),
    GoalType.TRAIL_MAINTENANCE: (150, 250),
}

#: Run keys whose sessions carry the trail vert target (the runs where a
#: climb number is actionable — route choice does the prescribing).
_TRAIL_VERT_TARGET_KEYS: frozenset[ActivityKey] = frozenset(
    {ActivityKey.RUN_TRAIL_LONG, ActivityKey.RUN_TRAIL_B2B}
)


def _round_vert_m(meters: float) -> int:
    """Round a vert target to the nearest 50 m (floor 50)."""
    return max(50, round(meters / 50.0) * 50)


#: Long/brick run keys whose 75'+ renders carry a fueling cue.
_LONG_RUN_FUELING_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_LONG,
        ActivityKey.RUN_LONG_PROGRESSION,
        ActivityKey.RUN_LONG_WITH_MP,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_TRAIL_LONG,
        ActivityKey.RUN_TRAIL_B2B,
    }
)

#: Activity keys that always carry the brick context.
_BRICK_RUN_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_BRICK_SHORT,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_BRICK_HIM_TEMPO,
        ActivityKey.RUN_BRICK_IM_TEMPO,
    }
)


def coaching_notes(
    placed: PlacedSession,
    *,
    duration_min: float,
    total_meters: float,
    flat_steps: list[FlatStep],
    goal: GoalType | None = None,
) -> list[str]:
    """Build a list of athlete-facing coaching notes for a session.

    Notes cover concerns that span the whole session rather than a single
    step: fueling/hydration cadence on long bike and run sessions, brick
    context (including the "no separate warmup" case, when a brick's bike
    leg serves as the run's warmup), open-water-swim cues for long swims,
    and race-rehearsal gear/kit cues.

    Every other note is routed by sport, activity key, and duration alone;
    only the IM run-leg note names a race distance, so it is the only one
    gated on ``goal``.

    Args:
        placed: The placed session being rendered.
        duration_min: Materialised duration in minutes.
        total_meters: Materialised swim distance in metres.
        flat_steps: Flattened workout steps after zero-duration filtering.
        goal: The plan's A-goal, used to route goal-specific notes.
            ``None`` (the default, and the honest answer for an ad-hoc
            workout that belongs to no plan) suppresses every goal-gated
            note rather than guessing a distance.

    Returns:
        Ordered list of HTML-safe note strings.  Empty when no notes apply.
    """
    notes: list[str] = []
    sport = placed.sport
    key = placed.activity_key

    if key in _BRICK_RUN_KEYS:
        has_warmup = any(s.step_type == "warmup" for s in flat_steps)
        if has_warmup:
            notes.append(
                "<b>Brick:</b> start within 5 min of bike dismount — change "
                "shoes fast, get moving, settle cadence in the first km."
            )
        else:
            notes.append(
                "<b>Brick — no separate run warmup:</b> the bike legs are your "
                "warmup.  Step off the bike, change shoes, go straight into "
                "the prescribed effort within 5 min of dismount."
            )

    # intent: water alone covers a ride of up to 75 minutes on the brief
    # sheet, so the cue starts past that, in the sheet's own bands.
    if sport == SportType.CYCLING and duration_min > 75.0:
        if duration_min >= 180.0:
            notes.append(
                f"<b>Fueling:</b> {_carb_cue(duration_min)} from minute 30 (mix "
                f"bottles + bars / gels), {_FLUID_CUE}; rehearse exactly what you "
                "will use on race day."
            )
        else:
            notes.append(
                f"<b>Fueling:</b> {_carb_cue(duration_min)} from minute 30, "
                f"{_FLUID_CUE}."
            )

    vert_band = _TRAIL_LONG_RUN_VERT_M_PER_H.get(goal) if goal is not None else None
    if vert_band is not None and key in _TRAIL_VERT_TARGET_KEYS:
        lo = _round_vert_m(vert_band[0] * duration_min / 60.0)
        hi = max(_round_vert_m(vert_band[1] * duration_min / 60.0), lo + 50)
        notes.append(
            f"<b>Vert:</b> aim for ~{lo}-{hi} m of climbing — route choice "
            "does the prescribing here, so pick trails that get you there "
            "and hike the steep sections rather than forcing them."
        )

    if (
        sport == SportType.RUNNING
        and key in _LONG_RUN_FUELING_KEYS
        and duration_min >= 75.0
    ):
        if goal in _IM_RUN_LEG_GOALS and duration_min >= 90.0:
            notes.append(
                "<b>Long-run cadence:</b> consider 30-60 s walk breaks "
                f"every 20 min, {_carb_cue(duration_min)} via gels or chews, "
                f"{_FLUID_CUE} — this is run-leg nutrition practice, not a "
                "standalone marathon long run."
            )
        else:
            # Goal-independent: this note names no race distance, so it
            # applies to every long run regardless of the plan's goal.
            notes.append(
                f"<b>Fueling:</b> {_carb_cue(duration_min)} via gels or chews from "
                f"minute 30, {_FLUID_CUE} — the gut trains like a muscle, so "
                "practise your race-day fueling on every long run."
            )

    if (
        sport == SportType.SWIMMING
        and key in _OWS_CAPABLE_KEYS
        and total_meters >= 2000.0
    ):
        notes.append(
            "<b>OWS swap-in:</b> repeat as an open-water session when possible "
            "— sight every 6-8 strokes, breathe bilaterally, swim straight "
            "off feet rather than chasing the black line.  Use the same "
            "wetsuit / skinsuit you plan to race in."
        )

    if key in _IM_RACE_REHEARSAL_KEYS:
        if sport == SportType.CYCLING:
            notes.append(
                "<b>Race-day kit:</b> aero bars from minute 10 onward, race "
                "wheels / helmet if available, race-day nutrition and bottle "
                "rotation, race-day shoes and saddle position."
            )
        else:
            notes.append(
                "<b>Race-day kit:</b> race shoes, race-day nutrition, race-day "
                "kit and visor — this is a dress rehearsal, not just a "
                "training run."
            )

    return notes


def session_coaching_notes(
    activity: ActivityDefinition,
    workout: StructuredWorkout,
    *,
    duration_min: float,
    resolver: ResolverProtocol | None,
    goal: GoalType | None = None,
) -> list[str]:
    """Build the coaching notes for an already-materialized workout.

    The one-call form of :func:`coaching_notes` for callers that hold an
    ``ActivityDefinition`` plus its built workout rather than a placed
    session: it derives the flat steps, the swim distance, and the
    throwaway ``PlacedSession`` the note router needs.

    Args:
        activity: The materialized activity definition.
        workout: The structured workout built for the session.
        duration_min: Materialized duration in minutes.
        resolver: Athlete threshold resolver (supplies swim CSS).
        goal: The plan's A-goal, or ``None`` for a session that belongs
            to no plan (suppresses every goal-gated note).

    Returns:
        Ordered list of HTML-safe note strings.
    """
    swim_css = (
        resolver.swim_css_sec_per_100m
        if resolver is not None and resolver.swim_css_sec_per_100m
        else DEFAULT_SWIM_CSS_SEC_PER_100M
    )
    placed = PlacedSession(
        slot_id="",
        activity_key=activity.key,
        sport=activity.sport,
        origin=SessionOrigin.ANCHOR,
        week=0,
        day_of_week=None,
        target_hours=duration_min / 60.0,
    )
    return coaching_notes(
        placed,
        duration_min=duration_min,
        total_meters=workout_total_meters(workout.steps),
        flat_steps=build_flat_steps(workout.steps, swim_css_sec_per_100m=swim_css),
        goal=goal,
    )


__all__ = [
    "DEFAULT_SWIM_CSS_SEC_PER_100M",
    "ZONE_COLORS",
    "ZONE_NUMBER_COLORS",
    "FlatStep",
    "build_flat_steps",
    "build_table_steps",
    "coaching_notes",
    "fmt_distance",
    "fmt_pace",
    "fmt_seconds",
    "fmt_swim_pace",
    "session_coaching_notes",
]
