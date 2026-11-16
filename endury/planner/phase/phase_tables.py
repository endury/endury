"""Structured lookup tables for the L1 phase planner.

All constants are keyed by ``GoalType``. No imports from ``endury.planner``.
"""

from __future__ import annotations

from dataclasses import dataclass

from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PlanMaturity,
    SportType,
)


@dataclass(frozen=True, slots=True)
class GoalProfile:
    """Immutable reference data for a single goal type.

    Args:
        taper_weeks: Number of taper weeks before the A-race.
        min_horizon_weeks: Minimum training horizon to prepare.
        max_recommended_weeks: Ceiling for a single focused prep, or ``None``
            when no ceiling applies (maintenance blocks). Longer horizons
            still generate — validation emits a soft warning suggesting a
            maintenance block first. Beyond this ceiling a continuous
            macrocycle stops paying: aerobic base plateaus against the ramp
            caps and focus decays (see
            docs/research/triathlon_long_course.md, Half-Distance vs.
            Full-Distance — Summary Comparison).
        phase_ratios: ``(base, build, peak)`` fractions summing to 1.0.
            Used as-is for goals without horizon-aware overrides.
        min_build_weeks: Floor for build phase duration.
        min_peak_weeks: Floor for peak phase duration.
        default_sport_split: Fill-distribution target by sport (sums to 1.0).
        race_duration: Estimated race-day duration per sport in hours.
        maturity: How well-validated this goal's blueprint is; see
            :class:`~endury.planner.foundations.types.PlanMaturity` for what
            each level requires. A known evidence gap is noted inline on an
            entry where one exists.
    """

    taper_weeks: int
    min_horizon_weeks: int
    max_recommended_weeks: int | None
    phase_ratios: tuple[float, float, float]
    min_build_weeks: int
    min_peak_weeks: int
    default_sport_split: dict[SportType, float]
    race_duration: dict[SportType, float]
    maturity: PlanMaturity


# ---------------------------------------------------------------------------
# Per-goal-type profiles
# ---------------------------------------------------------------------------

GOAL_PROFILES: dict[GoalType, GoalProfile] = {
    # -- Running ----------------------------------------------------------------
    GoalType.RUNNING_5K: GoalProfile(
        taper_weeks=1,
        min_horizon_weeks=4,
        max_recommended_weeks=16,
        phase_ratios=(0.35, 0.45, 0.20),
        min_build_weeks=2,
        min_peak_weeks=1,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 0.5},  # ~30 min
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.RUNNING_10K: GoalProfile(
        taper_weeks=2,
        min_horizon_weeks=6,
        max_recommended_weeks=20,
        phase_ratios=(0.35, 0.45, 0.20),
        min_build_weeks=2,
        min_peak_weeks=1,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 0.9},  # ~54 min
        # The scorecard matrix grades every tier; its one band of its own is
        # the race week (thin).
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.RUNNING_HALF_MARATHON: GoalProfile(
        taper_weeks=2,
        min_horizon_weeks=8,
        max_recommended_weeks=24,
        phase_ratios=(0.40, 0.42, 0.18),
        min_build_weeks=3,
        min_peak_weeks=2,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 2.2},  # ~2 h 12 min
        # Phase ratios and minimums mirror the marathon profile below; not
        # independently derived.
        # Alpha: the moderate tier, the one on offer, is
        # reviewed with the maintainer (docs/plan_maturity.md, Menu reviews)
        # and graded by the scorecard matrix.
        maturity=PlanMaturity.ALPHA,
    ),
    GoalType.RUNNING_MARATHON: GoalProfile(
        taper_weeks=3,
        min_horizon_weeks=12,
        max_recommended_weeks=28,
        phase_ratios=(0.40, 0.42, 0.18),
        min_build_weeks=3,
        min_peak_weeks=2,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 4.5},  # ~4 h 30 min
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    # Trail goals use fixed phase ratios, unlike the horizon-aware bands
    # below for Ironman/Half-Ironman. Per docs/research/trail_running.md,
    # Phase Ratios, these are the least evidence-backed numbers in this
    # table — avoid fine-tuning them without new evidence.
    GoalType.TRAIL_20K: GoalProfile(
        # Biased a week above the road half because a 20K trail race
        # carries far more eccentric (downhill) load.
        taper_weeks=2,
        # 12, not the category's 8-week floor: an 8-week plan held one base
        # week and a taper too small for its hill session.
        min_horizon_weeks=12,
        max_recommended_weeks=20,
        phase_ratios=(0.40, 0.40, 0.20),
        # Peak-block floors are only defined from 50K up; this row is
        # bridged from the half-marathon profile (3/2) below.
        min_build_weeks=3,
        min_peak_weeks=2,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 3.25},  # ~3 h 15 min
        # Basic, the one tier on offer: its scorecard cells, a race-day
        # freshness band of its own and a menu review (docs/plan_maturity.md).
        # Every research row for this category is itself an extrapolation
        # (docs/research/trail_running.md,
        # Per-Category Planning Parameters).
        maturity=PlanMaturity.ALPHA,
    ),
    GoalType.TRAIL_50K: GoalProfile(
        taper_weeks=2,  # 10-14 days
        min_horizon_weeks=12,  # needs 3+ months at 4-6 h/week of base
        max_recommended_weeks=24,
        phase_ratios=(0.45, 0.35, 0.20),
        min_build_weeks=3,  # a 4-6 week intro block
        # A 3-week peak-block floor applies once weekly volume reaches 6+
        # hours; below that, 2 weeks is enough.
        min_peak_weeks=2,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 7.5},  # ~7 h 30 min
        # The scorecard matrix grades every tier; race-day freshness is the maintainer's
        # judgement, the other trail bands are literature (thin).
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.TRAIL_100K: GoalProfile(
        taper_weeks=2,  # 2-3 wk; use 2 unless the peak block was heavy
        min_horizon_weeks=14,  # a 14-week skeleton
        max_recommended_weeks=28,
        phase_ratios=(0.48, 0.32, 0.20),
        min_build_weeks=4,  # late build carries the peak-block remainder
        # A 6-week peak-block floor applies once weekly volume reaches 9+
        # hours; the default here is 4.
        min_peak_weeks=4,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={SportType.RUNNING: 15.75},  # ~15 h 45 min
        # The scorecard matrix does
        # not grade it on every change.
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    # -- Triathlon ---------------------------------------------------------------
    GoalType.SPRINT_TRIATHLON: GoalProfile(
        taper_weeks=1,
        min_horizon_weeks=6,
        max_recommended_weeks=20,
        phase_ratios=(0.35, 0.45, 0.20),
        min_build_weeks=3,
        min_peak_weeks=2,
        default_sport_split={
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
        race_duration={
            SportType.SWIMMING: 0.25,  # ~15 min
            SportType.CYCLING: 0.75,  # ~45 min
            SportType.RUNNING: 0.35,  # ~21 min
        },
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.OLYMPIC_TRIATHLON: GoalProfile(
        taper_weeks=2,
        # The shortest prep in which every tier's default athlete, ramping
        # the long ride one step a week from a 90-min longest (R-SESS-013),
        # reaches the menu's PEAK ride: moderate books 125-126 min at 10-11
        # weeks against its 128-min PEAK minimum.
        min_horizon_weeks=12,
        max_recommended_weeks=24,
        phase_ratios=(0.38, 0.44, 0.18),
        min_build_weeks=3,
        min_peak_weeks=2,
        default_sport_split={
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
        race_duration={
            SportType.SWIMMING: 0.50,  # ~30 min
            SportType.CYCLING: 1.50,  # ~1 h 30 min
            SportType.RUNNING: 1.00,  # ~1 h
        },
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.HALF_IRONMAN: GoalProfile(
        taper_weeks=2,
        # As the olympic: basic books a 160-172 min PEAK ride at 12-15 weeks
        # against its 180-min PEAK minimum, 188 from 16.
        min_horizon_weeks=16,
        max_recommended_weeks=28,
        phase_ratios=(0.40, 0.42, 0.18),
        min_build_weeks=4,
        min_peak_weeks=3,
        default_sport_split={
            SportType.SWIMMING: 0.15,
            SportType.CYCLING: 0.50,
            SportType.RUNNING: 0.35,
        },
        race_duration={
            SportType.SWIMMING: 1.00,  # ~1 h
            SportType.CYCLING: 2.50,  # ~2 h 30 min
            SportType.RUNNING: 2.00,  # ~2 h
        },
        # Alpha: the moderate tier, the one on offer, is
        # reviewed with the maintainer (docs/plan_maturity.md, Menu reviews)
        # and graded by the scorecard matrix.
        maturity=PlanMaturity.ALPHA,
    ),
    GoalType.IRONMAN: GoalProfile(
        taper_weeks=3,
        # Matches the minimum preparation length for an athlete with an
        # existing aerobic base (docs/research/triathlon_long_course.md,
        # Preparation Length). Below this the 7-12%/wk ramp cannot build a
        # 5-6h long ride and 2.5-3h long run in time, regardless of
        # ambition. At 22 weeks the competitive PEAK race-pace ride
        # (320 min) is one long-ride step (R-SESS-013) from the BUILD ride's
        # 291-min top, which the default athlete reaches from 22 weeks.
        min_horizon_weeks=22,
        max_recommended_weeks=36,
        phase_ratios=(0.47, 0.35, 0.18),
        min_build_weeks=4,
        min_peak_weeks=3,
        default_sport_split={
            SportType.SWIMMING: 0.15,
            SportType.CYCLING: 0.55,
            SportType.RUNNING: 0.30,
        },
        race_duration={
            SportType.SWIMMING: 1.20,  # ~1 h 12 min
            SportType.CYCLING: 5.00,  # ~5 h
            SportType.RUNNING: 4.00,  # ~4 h
        },
        # Alpha: the moderate tier, the one on offer, is
        # reviewed with the maintainer (docs/plan_maturity.md, Menu reviews)
        # and graded by the scorecard matrix.
        maturity=PlanMaturity.ALPHA,
    ),
    # -- Maintenance (raceless) --------------------------------------------------
    # taper/build/peak fields are inert: the phase planner's maintenance
    # branch emits an all-MAINTAIN skeleton and no RaceEvent, so only
    # min_horizon_weeks and default_sport_split are read.
    GoalType.RUNNING_MAINTENANCE: GoalProfile(
        taper_weeks=0,
        min_horizon_weeks=4,
        max_recommended_weeks=None,
        phase_ratios=(1.0, 0.0, 0.0),
        min_build_weeks=0,
        min_peak_weeks=0,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={},
        # A committed scenario per tier; the block converges on the tier's
        # maintenance volume.
        maturity=PlanMaturity.EXPERIMENTAL,
    ),
    GoalType.TRAIL_MAINTENANCE: GoalProfile(
        taper_weeks=0,
        min_horizon_weeks=4,
        max_recommended_weeks=None,
        phase_ratios=(1.0, 0.0, 0.0),
        min_build_weeks=0,
        min_peak_weeks=0,
        default_sport_split={SportType.RUNNING: 1.0},
        race_duration={},
        # Basic, the one tier on offer: a committed scenario, the convergence
        # test and a menu review (docs/plan_maturity.md, Menu reviews).
        maturity=PlanMaturity.ALPHA,
    ),
    GoalType.TRIATHLON_MAINTENANCE: GoalProfile(
        taper_weeks=0,
        min_horizon_weeks=4,
        max_recommended_weeks=None,
        phase_ratios=(1.0, 0.0, 0.0),
        min_build_weeks=0,
        min_peak_weeks=0,
        default_sport_split={
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
        race_duration={},
        # Moderate, the one tier on offer: a committed
        # scenario, the convergence test and a menu review
        # (docs/plan_maturity.md, Menu reviews).
        maturity=PlanMaturity.ALPHA,
    ),
}


def goal_sports(goal_type: GoalType) -> frozenset[SportType]:
    """Return the sports a goal trains.

    The single canonical partition of running-only versus multi-sport goals:
    derived from the goal's own ``default_sport_split`` keys rather than a
    hand-maintained frozenset, so a new goal type cannot be added to the
    profile table and forgotten in a second list.

    Args:
        goal_type: The goal type to inspect.

    Returns:
        The sports the goal's default split distributes volume across.
    """
    return frozenset(GOAL_PROFILES[goal_type].default_sport_split)


# ---------------------------------------------------------------------------
# Horizon-aware phase ratio overrides (Ironman + Half Ironman)
#
# Longer horizons favour a proportionally longer aerobic BASE phase; see
# docs/research/triathlon_long_course.md, Phase Ratios.
# ---------------------------------------------------------------------------

_HorizonBand = tuple[int, tuple[float, float, float]]

_IRONMAN_HORIZON_BANDS: tuple[_HorizonBand, ...] = (
    #  (max_pre_taper_weeks, (base, build, peak))
    # Band 1's base/build split keeps the COMPETITIVE-shifted ratio inside
    # the scorecard's build-share band (0.27-0.45) at a 17-week pre-taper
    # horizon, where it must round to BASE=BUILD=7 rather than 6/8.
    (20, (0.42, 0.38, 0.20)),  # short prep — sharpen-dominant
    (30, (0.50, 0.32, 0.18)),  # standard IM prep
    # Standard's build, the peak held at five weeks: 0.18 rounds to six here
    # and the long band back to five one week later, so a longer plan's peak
    # shrank and a tune-up eight weeks out was refused at these two lengths
    # only (R-PHASE-006).
    (32, (0.52, 0.32, 0.16)),  # standard IM prep, long end
    (999, (0.58, 0.27, 0.15)),  # long prep — aerobic-foundation-dominant
)

_HALF_IRONMAN_HORIZON_BANDS: tuple[_HorizonBand, ...] = (
    (18, (0.40, 0.42, 0.18)),  # short prep
    (28, (0.48, 0.34, 0.18)),  # standard HIM prep
    (999, (0.55, 0.30, 0.15)),  # long prep
)

_AMBITION_BASE_SHIFT: dict[AmbitionLevel, float] = {
    AmbitionLevel.BASIC: +0.03,
    AmbitionLevel.MODERATE: 0.0,
    AmbitionLevel.COMPETITIVE: -0.03,
}

_HORIZON_AWARE_GOALS: dict[GoalType, tuple[_HorizonBand, ...]] = {
    GoalType.IRONMAN: _IRONMAN_HORIZON_BANDS,
    GoalType.HALF_IRONMAN: _HALF_IRONMAN_HORIZON_BANDS,
}


def resolve_phase_ratios(
    goal: GoalType,
    pre_taper_weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.MODERATE,
) -> tuple[float, float, float]:
    """Return ``(base, build, peak)`` phase ratios for the given context.

    For Ironman and Half Ironman, ratios are adjusted by training horizon
    length (longer horizons → proportionally longer BASE) and ambition
    level (BASIC gets slightly more BASE, COMPETITIVE slightly less).

    All other goal types return their fixed ``GoalProfile.phase_ratios``.

    Args:
        goal: Goal type.
        pre_taper_weeks: Number of pre-taper weeks in the plan.
        ambition: Ambition level for the base-shift adjustment.

    Returns:
        Tuple of three floats summing to 1.0.
    """
    bands = _HORIZON_AWARE_GOALS.get(goal)
    if bands is None:
        return GOAL_PROFILES[goal].phase_ratios

    base_r, build_r, peak_r = GOAL_PROFILES[goal].phase_ratios
    for max_weeks, ratios in bands:
        if pre_taper_weeks <= max_weeks:
            base_r, build_r, peak_r = ratios
            break

    shift = _AMBITION_BASE_SHIFT.get(ambition, 0.0)
    base_r = base_r + shift
    build_r = build_r - shift
    total = base_r + build_r + peak_r
    return (base_r / total, build_r / total, peak_r / total)


__all__ = [
    "GOAL_PROFILES",
    "GoalProfile",
    "goal_sports",
    "resolve_phase_ratios",
]
