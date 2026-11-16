"""Shared types, enums, and dataclasses for the planner.

All pipeline layers depend on this module.
No imports from endury.planner (the host-free kernel endury.training is
the one allowed dependency — see the ``planner-boundary`` architecture rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from endury.training.zones import HR_BAND_SPECS, POWER_BAND_SPECS, SWIM_PACE_BAND_SPECS

# IF thresholds for the 3-band Training Intensity Distribution (TID) model.
# Derived from the canonical band specs rather than re-declared: the TID
# cuts ARE the zone edges — Z1 Recovery's upper bound is LT1, Z4 Threshold's
# upper bound is LT2 — so a change to `endury/training/zones.py` must carry
# the TID model with it.

_HR_BAND_UPPER: dict[int, float] = {
    band.zone_number: band.high_factor for band in HR_BAND_SPECS
}
_POWER_BAND_UPPER: dict[int, float] = {
    band.zone_number: band.high_factor for band in POWER_BAND_SPECS
}
_SWIM_BAND_UPPER: dict[int, float] = {
    band.zone_number: band.high_factor for band in SWIM_PACE_BAND_SPECS
}

TID_LOW_IF_THRESHOLD: float = _HR_BAND_UPPER[1]  # below → Z-low  (Z1, below LT1)
TID_HIGH_IF_THRESHOLD: float = _HR_BAND_UPPER[4]  # at/above → Z-high (Z5, above LT2)


# Core enums


class SportType(StrEnum):
    """Supported sports."""

    RUNNING = "running"
    CYCLING = "cycling"
    SWIMMING = "swimming"


#: Each sport's TID cut points ``(LT1, LT2)`` on its own intensity factor.
#: A run reads the heart-rate table's (its IF is a fraction of threshold
#: speed; Friel's aerobic-zone top, 0.877, counted the trail climb day as
#: easy when measured), a ride power's (Coggan's endurance zone
#: tops at LT1, FTP is LT2), a swim the swim table's (zone 2 tops at 90 % of
#: CSS speed).
TID_CUTS: dict[SportType, tuple[float, float]] = {
    SportType.RUNNING: (TID_LOW_IF_THRESHOLD, TID_HIGH_IF_THRESHOLD),
    SportType.CYCLING: (_POWER_BAND_UPPER[2], 1.0),
    SportType.SWIMMING: (_SWIM_BAND_UPPER[2], _SWIM_BAND_UPPER[4]),
}


class GoalType(StrEnum):
    """Supported goal types (race preparations plus raceless maintenance).

    The ``TRAIL_*`` members are UTMB-category mountain-trail goals, keyed by
    km-effort (``distance_km + gain_m / 100``) rather than distance
    (docs/research/trail_running.md, Race Categories and the km-effort
    Metric): >20 and <50 km-effort for the 20K band, 50-100 for the 50K
    band, 100-170 for the 100K band; Endury plans no race of 170 or more
    (the 100-mile band: too little evidence for its menus). A 45 km race
    with 4,000 m of gain is a 50K-category problem, not a marathon-plus.
    """

    RUNNING_5K = "running_5k"
    RUNNING_10K = "running_10k"
    RUNNING_HALF_MARATHON = "running_half_marathon"
    RUNNING_MARATHON = "running_marathon"
    TRAIL_20K = "trail_20k"
    TRAIL_50K = "trail_50k"
    TRAIL_100K = "trail_100k"
    SPRINT_TRIATHLON = "sprint_triathlon"
    OLYMPIC_TRIATHLON = "olympic_triathlon"
    HALF_IRONMAN = "half_ironman"
    IRONMAN = "ironman"
    RUNNING_MAINTENANCE = "running_maintenance"
    TRAIL_MAINTENANCE = "trail_maintenance"
    TRIATHLON_MAINTENANCE = "triathlon_maintenance"


#: Raceless goal types: a fixed-length block that converges on the
#: ambition tier's maintenance volume instead of building toward a race.
#: The goal's ``race_date`` marks the end of the block, produces no
#: ``RaceEvent``, and the phase skeleton is all-MAINTAIN (no taper/race).
MAINTENANCE_GOALS: frozenset[GoalType] = frozenset(
    {
        GoalType.RUNNING_MAINTENANCE,
        GoalType.TRAIL_MAINTENANCE,
        GoalType.TRIATHLON_MAINTENANCE,
    }
)


def is_maintenance_goal(goal_type: GoalType) -> bool:
    """Return True if *goal_type* is a raceless maintenance goal."""
    return goal_type in MAINTENANCE_GOALS


class PlanMaturity(StrEnum):
    """How well-checked one goal's plan blueprint is, by its evidence.

    The criteria are mechanical, so a goal's tier is an audit outcome
    rather than a matter of taste (``tests/planner/test_maturity_audit.py``
    fails a tier that claims more, or less, than its evidence):

    * ``EXPERIMENTAL`` — any of the Alpha evidence is missing.
    * ``ALPHA`` — the rulebook (``bench/rulebook.yaml``) holds at least one
      band of the race goal's own, and a scorecard matrix
      cell (``GOAL_MATRIX_HORIZONS``) for every tier its menu offers. A
      maintenance block, which has no race, has a committed scenario for every offered
      tier and a test
      that its weekly volume converges on the tier's target. Either way,
      every offered tier's menus have been reviewed session by session,
      recorded as a dated line in ``docs/plan_maturity.md``. A goal that
      offers no tier is not audited; its label waits with its menu.
    * ``BETA`` — Alpha, plus at least one athlete has completed a full
      preparation (or maintenance block) with it, recorded as a dated line
      in ``docs/plan_maturity.md``.
    """

    EXPERIMENTAL = "experimental"
    ALPHA = "alpha"
    BETA = "beta"


class PhaseType(StrEnum):
    """High-level training phase labels."""

    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    TAPER = "taper"
    MAINTAIN = "maintain"


class TargetPreference(StrEnum):
    """Preferred prescription metric for an activity definition."""

    PACE_FIRST = "pace_first"
    HR_FIRST = "hr_first"
    POWER_FIRST = "power_first"
    RPE_FIRST = "rpe_first"


class AmbitionLevel(StrEnum):
    """Training ambition selector controlling solver aggressiveness.

    Controls ramp rate, recovery cadence, fatigue tolerance, and race-day
    freshness targets across all pipeline layers.  See ``AmbitionProfile``
    and ``ambition_tables.py`` for per-level knobs.
    """

    BASIC = "basic"
    MODERATE = "moderate"
    COMPETITIVE = "competitive"


@dataclass(frozen=True, slots=True)
class AmbitionProfile:
    """Ambition-dependent policy knobs applied across all pipeline layers.

    Each ``AmbitionLevel`` maps to exactly one profile via the lookup table
    in ``ambition_tables.py``.  Layers read these values instead of the
    module-level defaults in ``macro_config.py``, so the config file
    serves as a moderate-level reference only.

    The planner plans in **training hours** as its primary currency.
    TSS is derived internally (each session priced by its own activity) for
    the Banister CTL/ATL dynamics only.

    Args:
        hours_ramp_limit: Weekly controllable-hours growth factor for
            LOAD → LOAD transitions (capacity projection, QP ramp constraint).
        recovery_cadence: Insert a RECOVERY week every *N*-th consecutive LOAD
            week (L1 phase planner).
        max_weekly_hours_ceiling: Hard ceiling applied to the compound ramp
            in ``build_capacity_projection`` to prevent physiologically
            absurd long-plan projections.
        recovery_load_fraction: Recovery-week volume as a fraction of the
            preceding LOAD week.
    """

    hours_ramp_limit: float
    recovery_cadence: int
    max_weekly_hours_ceiling: float
    recovery_load_fraction: float


class WeekMode(StrEnum):
    """First-class week classification. Every downstream layer keys on this."""

    LOAD = "load"
    RECOVERY = "recovery"
    TAPER = "taper"
    RACE = "race"
    RACE_RECOVERY = "race_recovery"


class SessionOrigin(StrEnum):
    """How a PlacedSession was introduced into the plan."""

    ANCHOR = "anchor"  # key session from the session menu
    TEST = "test"  # fixed-protocol fitness test


class ActivityFamily(StrEnum):
    """Workout family — coarser than ActivityKey."""

    EASY = "easy"
    LONG = "long"
    RECOVERY = "recovery"
    TEMPO = "tempo"
    THRESHOLD = "threshold"
    INTERVAL = "interval"
    TEST = "test"


class ActivityKey(StrEnum):
    """Typed keys matching ``ACTIVITY_REGISTRY`` entries.

    Grouped by sport below: running (incl. trail), cycling, swimming, and
    the three fitness-test protocols.
    """

    # Running
    RUN_EASY = "run_easy"
    RUN_STRIDES = "run_strides"
    RUN_LONG = "run_long"
    RUN_RECOVERY = "run_recovery"
    RUN_TEMPO = "run_tempo"
    RUN_THRESHOLD = "run_threshold"
    RUN_VO2MAX = "run_vo2max"
    RUN_VO2MAX_SHORT = "run_vo2max_short"
    RUN_PROGRESSION = "run_progression"
    RUN_FARTLEK = "run_fartlek"
    RUN_HILL_REPEATS = "run_hill_repeats"
    RUN_HM_PACE = "run_hm_pace"
    RUN_FULL_MP = "run_full_mp"
    RUN_LONG_WITH_MP = "run_long_with_mp"
    RUN_5K_RACE_PACE = "run_5k_race_pace"
    RUN_10K_RACE_PACE = "run_10k_race_pace"
    RUN_SPEED = "run_speed"
    RUN_LONG_PROGRESSION = "run_long_progression"
    RUN_AEROBIC_TEMPO = "run_aerobic_tempo"
    RUN_BRICK_SHORT = "run_brick_short"
    RUN_BRICK_LONG = "run_brick_long"
    RUN_BRICK_HIM_TEMPO = "run_brick_him_tempo"
    RUN_BRICK_IM_TEMPO = "run_brick_im_tempo"

    # Trail running
    RUN_TRAIL_EASY = "run_trail_easy"
    RUN_TRAIL_LONG = "run_trail_long"
    RUN_TRAIL_B2B = "run_trail_b2b"
    RUN_VERT_HIKE = "run_vert_hike"
    RUN_DOWNHILL_REPEATS = "run_downhill_repeats"

    # Cycling
    BIKE_ENDURANCE = "bike_endurance"
    BIKE_LONG = "bike_long"
    BIKE_LONG_SWEET_SPOT = "bike_long_sweet_spot"
    BIKE_LONG_HIM_RACE_PACE = "bike_long_him_race_pace"
    BIKE_LONG_IM_RACE_PACE = "bike_long_im_race_pace"
    BIKE_RECOVERY = "bike_recovery"
    BIKE_SWEET_SPOT = "bike_sweet_spot"
    BIKE_THRESHOLD = "bike_threshold"
    BIKE_OVER_UNDER = "bike_over_under"
    BIKE_VO2MAX = "bike_vo2max"
    BIKE_VO2MAX_SHORT = "bike_vo2max_short"
    BIKE_TEMPO = "bike_tempo"
    BIKE_CADENCE = "bike_cadence"
    BIKE_ANAEROBIC = "bike_anaerobic"
    BIKE_SPRINT = "bike_sprint"
    BIKE_HILL_CLIMB = "bike_hill_climb"
    BIKE_AEROBIC = "bike_aerobic"
    BIKE_OPENER = "bike_opener"

    # Swimming
    SWIM_ENDURANCE = "swim_endurance"
    SWIM_LONG = "swim_long"
    SWIM_RECOVERY = "swim_recovery"
    SWIM_CSS = "swim_css"
    SWIM_CSS_SHORT = "swim_css_short"
    SWIM_VO2MAX = "swim_vo2max"
    SWIM_VO2MAX_SHORT = "swim_vo2max_short"
    SWIM_THRESHOLD = "swim_threshold"
    SWIM_DRILL = "swim_drill"
    SWIM_PULL = "swim_pull"
    SWIM_SPRINT = "swim_sprint"
    SWIM_KICK = "swim_kick"

    # Fitness tests
    RUN_THRESHOLD_TEST = "run_threshold_test"
    BIKE_FTP_TEST = "bike_ftp_test"
    SWIM_CSS_TEST = "swim_css_test"


# Value objects


@dataclass(frozen=True, slots=True)
class TIDFractions:
    """Fraction of training time spent in each intensity zone band."""

    z_low: float
    z_mid: float
    z_high: float


@dataclass(frozen=True, slots=True)
class ActivityHardness:
    """Hardness classifiers for a workout activity."""

    is_hard_overall: bool
    is_hard_physiologically: bool
    is_hard_mentally: bool


@dataclass(frozen=True, slots=True)
class RaceGoal:
    """A single race goal with priority."""

    goal_type: GoalType
    race_date: date
    priority: Literal["A", "B", "C"]


@dataclass(frozen=True, slots=True)
class WeekSpec:
    """Canonical per-week row — the plan skeleton's source of truth."""

    week: int  # 0-indexed from training_start
    phase: PhaseType
    mode: WeekMode
    sport_split: dict[SportType, float]  # distribution target, sums to 1.0
    effective_goal: GoalType  # resolved: owning B/C goal or A-goal
    owning_goal: RaceGoal | None  # None for shared base / A-goal phases

    def __post_init__(self) -> None:
        """Validate sport_split sums to 1.0 (±0.01 tolerance)."""
        total = sum(self.sport_split.values())
        if abs(total - 1.0) > 0.01:
            msg = f"sport_split must sum to 1.0, got {total:.4f}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RaceEvent:
    """Day-level fixture — not a trainable session.

    Produces known load (r_k), and its day evicts colliding training
    sessions in the day assigner.
    """

    event_id: str
    goal: RaceGoal
    week: int
    day_of_week: int
    duration_by_sport: dict[SportType, float]

    @property
    def total_hours(self) -> float:
        """Sum of per-sport race duration in hours."""
        return sum(self.duration_by_sport.values())

    def leg_id(self, sport: SportType) -> str:
        """Stable ID for a single-sport materialization leg."""
        return f"{self.event_id}:{sport.value}"


@dataclass(frozen=True, slots=True)
class PlacedSession:
    """Single-sport training session. No RACE origin — races are RaceEvents."""

    slot_id: str
    activity_key: ActivityKey
    sport: SportType
    origin: SessionOrigin
    week: int  # 0-indexed week offset
    day_of_week: int | None  # menu-authored day (None → assigner's Monday fallback)
    target_hours: float
    min_hours: float = 0.0  # QP lower bound (0 = fixed at target_hours)
    max_hours: float = 0.0  # QP upper bound (0 = fixed at target_hours)
    brick_partner: str | None = None  # slot_id of the bike session to co-locate with
    slot_name: str | None = (
        None  # authoring menu Slot.name (provenance; None = non-menu)
    )
    # Why the athlete may skip it (R-SESS-037); None = a regular session.
    optional_reason: str | None = None
    # Booked at its card over the week's capacity ceiling (R-SESS-037).
    over_ceiling: bool = False


# L1 Phase Planner contracts


@dataclass(frozen=True, slots=True)
class PhaseRequest:
    """Input to the L1 phase planner.

    ``prior_training_weeks`` supports replanning: the weeks already trained
    before ``training_start``. ``horizon_join_weeks`` are the weeks a
    too-short race horizon lacks, never trained. The skeleton is built as
    if training had started both counts before ``training_start``, then
    those leading weeks are truncated — so a mid-prep replan resumes in
    BUILD/PEAK instead of restarting BASE. The trained weeks are kept as
    ``PhasePlan.prior_weeks``; the joined weeks are discarded. ``0`` for
    both (the default) is the fresh-plan path, with no truncation applied.
    """

    goals: list[RaceGoal]
    training_start: date
    ambition: AmbitionLevel = AmbitionLevel.MODERATE
    prior_training_weeks: int = 0
    horizon_join_weeks: int = 0


@dataclass(frozen=True, slots=True)
class PhasePlan:
    """Output of the L1 phase planner.

    ``prior_weeks`` are the trained weeks a replan truncated, indexed
    ``-k..-1`` before ``weeks[0]``: the plan's timeline opens on them, so
    cadences and opening-week windows count from the plan's own start.
    """

    weeks: list[WeekSpec]
    race_events: list[RaceEvent]
    a_goal: RaceGoal
    prior_weeks: tuple[WeekSpec, ...] = ()


# Capacity projection


@dataclass(frozen=True, slots=True)
class TestProtocol:
    """User-configurable fitness-test schedule.

    Tests (``bike_ftp_test``, ``run_threshold_test``, ``swim_css_test``)
    are inserted as fixed-duration ``PlacedSession`` entries with
    ``origin=SessionOrigin.TEST`` and a pre-assigned ``day_of_week``.
    The QP treats them as locked (``min_hours == target_hours ==
    max_hours``) and the day assigner honors the pre-set day.

    Two scheduling modes are available; use one or the other.

    * **Explicit weeks** — ``weeks`` is a tuple of 0-indexed week
      offsets. Any valid week is allowed, including LOAD weeks, so a
      week-0 baseline test is supported.
    * **Cadence** — ``every_n_recovery_weeks`` picks every Nth RECOVERY
      week from the phase plan. With ``include_baseline=True`` the first
      week of the plan (week 0) is also selected even if it's a LOAD
      week.

    If both ``weeks`` and ``every_n_recovery_weeks`` are set, ``weeks``
    wins. If neither is set, no tests are emitted.

    Args:
        sports: Sports to test. Empty tuple disables testing entirely.
        weeks: Explicit 0-indexed week offsets. Overrides cadence.
        every_n_recovery_weeks: Cadence (1=every recovery, 2=every 2nd).
        include_baseline: If using cadence, also emit a test at week 0.
        day_by_sport: Day-of-week per sport (0=Mon..6=Sun), encoded as a
            tuple of ``(sport, day)`` pairs (hashable so the dataclass
            can remain frozen and slotted). Defaults to Tue/Thu/Fri for
            cycling/running/swimming.
    """

    # Prevent pytest from trying to collect this class as a test case
    # just because the name begins with "Test".
    __test__ = False

    sports: tuple[SportType, ...] = ()
    weeks: tuple[int, ...] | None = None
    every_n_recovery_weeks: int | None = None
    include_baseline: bool = False
    day_by_sport: tuple[tuple[SportType, int], ...] = ()


@dataclass(frozen=True, slots=True)
class CapacityProjection:
    """Per-week projected controllable hours ceiling, accounting for week mode.

    Args:
        weekly_ceiling: Total hours ceiling per week index.
        weekly_ceiling_by_sport: Per-sport hours ceiling per week index.
    """

    weekly_ceiling: dict[int, float]
    weekly_ceiling_by_sport: dict[int, dict[SportType, float]]


# Day assignment output


@dataclass(frozen=True, slots=True)
class ActivityAssignment:
    """Resolved day-level activity placement from the day assigner.

    Args:
        slot_id: Stable identity matching ``PlacedSession.slot_id``.
        date: Calendar date of the assignment.
        activity_key: Activity registry key.
        target_hours: Target training hours.
    """

    slot_id: str
    date: date
    activity_key: ActivityKey
    target_hours: float


__all__ = [
    "MAINTENANCE_GOALS",
    "TID_CUTS",
    "TID_HIGH_IF_THRESHOLD",
    "TID_LOW_IF_THRESHOLD",
    "ActivityAssignment",
    "ActivityFamily",
    "ActivityHardness",
    "ActivityKey",
    "AmbitionLevel",
    "AmbitionProfile",
    "CapacityProjection",
    "GoalType",
    "PhasePlan",
    "PhaseRequest",
    "PhaseType",
    "PlacedSession",
    "PlanMaturity",
    "RaceEvent",
    "RaceGoal",
    "SessionOrigin",
    "SportType",
    "TIDFractions",
    "TargetPreference",
    "TestProtocol",
    "WeekMode",
    "WeekSpec",
    "is_maintenance_goal",
]
