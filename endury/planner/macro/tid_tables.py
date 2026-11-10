"""Training Intensity Distribution (TID) target tables for the planner.

Provides per-(phase, mode, goal) TID fraction lookup used by the L3 QP
to compute the TSS-per-hour multiplier for each sport.
"""

from __future__ import annotations

from endury.planner.foundations.types import (
    GoalType,
    PhaseType,
    TIDFractions,
    WeekMode,
)

# ---------------------------------------------------------------------------
# Band names
# ---------------------------------------------------------------------------

TID_BANDS: tuple[str, ...] = ("z_low", "z_mid", "z_high")

# ---------------------------------------------------------------------------
# Goal intensity index
#
# Maps each GoalType to a 0.0-1.0 position on the aerobic-anaerobic spectrum.
# 0.0 = marathon-like (highly aerobic, polarized low intensity)
# 1.0 = 5K-like (VO2max-dominant, more high-intensity fraction)
# ---------------------------------------------------------------------------

GOAL_INTENSITY_INDEX: dict[GoalType, float] = {
    GoalType.RUNNING_MARATHON: 0.00,
    # docs/research/trail_running.md, Intensity Distribution, puts ultra TID
    # at or below marathon intensity fractions, and 0.00 is the
    # interpolation floor — the trail goals pin the marathon endpoint
    # rather than extending past it.
    GoalType.TRAIL_50K: 0.00,
    GoalType.TRAIL_100K: 0.00,
    GoalType.IRONMAN: 0.05,
    GoalType.HALF_IRONMAN: 0.15,
    GoalType.RUNNING_HALF_MARATHON: 0.25,
    GoalType.TRIATHLON_MAINTENANCE: 0.35,
    # Trail 20K is not an ultra: docs/research/trail_running.md, Race
    # Demands by Category, puts a ~3 h mountain race between the 10K's and
    # the half's intensity demands — hilly and sustained, but far short of
    # the ultras' all-Z1 shape — so it sits between RUNNING_HALF_MARATHON
    # (0.25) and RUNNING_10K (0.60). The 20K rows in that document's
    # Per-Category Planning Parameters are downward extrapolation with no
    # direct corroboration, so this is the coarsest number in the trail
    # family.
    GoalType.TRAIL_20K: 0.40,
    GoalType.OLYMPIC_TRIATHLON: 0.40,
    # Trail maintenance sits with trail 20K rather than with running
    # maintenance: its quality slots are hill/vert flavoured, so the
    # week's intensity mix leans slightly more aerobic than the road
    # block's threshold/VO2max rotation.
    GoalType.TRAIL_MAINTENANCE: 0.40,
    GoalType.RUNNING_MAINTENANCE: 0.45,
    GoalType.RUNNING_10K: 0.60,
    GoalType.SPRINT_TRIATHLON: 0.70,
    GoalType.RUNNING_5K: 1.00,
}

# ---------------------------------------------------------------------------
# Raw TID table
# ---------------------------------------------------------------------------

_TID_TABLE: dict[tuple[PhaseType | None, WeekMode, str], TIDFractions] = {
    # Base phase
    (PhaseType.BASE, WeekMode.LOAD, "marathon"): TIDFractions(0.75, 0.20, 0.05),
    (PhaseType.BASE, WeekMode.LOAD, "5k"): TIDFractions(0.65, 0.25, 0.10),
    # BASE recovery — calibrated to ≈63 TSS/h.
    # Active recovery weeks retain mid-zone work; they aren't pure easy.
    (PhaseType.BASE, WeekMode.RECOVERY, "any"): TIDFractions(0.55, 0.35, 0.10),
    # Build phase
    (PhaseType.BUILD, WeekMode.LOAD, "marathon"): TIDFractions(0.61, 0.25, 0.14),
    (PhaseType.BUILD, WeekMode.LOAD, "5k"): TIDFractions(0.40, 0.35, 0.25),
    # BUILD recovery — calibrated to ≈64 TSS/h.
    (PhaseType.BUILD, WeekMode.RECOVERY, "any"): TIDFractions(0.52, 0.38, 0.10),
    # Peak phase
    (PhaseType.PEAK, WeekMode.LOAD, "marathon"): TIDFractions(0.62, 0.30, 0.08),
    (PhaseType.PEAK, WeekMode.LOAD, "5k"): TIDFractions(0.35, 0.30, 0.35),
    # PEAK recovery keeps its own row rather than falling back to BASE's
    # (see the RECOVERY branch in get_tid_fractions).
    (PhaseType.PEAK, WeekMode.RECOVERY, "any"): TIDFractions(0.52, 0.38, 0.10),
    # Taper phase
    (PhaseType.TAPER, WeekMode.TAPER, "marathon"): TIDFractions(0.65, 0.25, 0.10),
    (PhaseType.TAPER, WeekMode.TAPER, "5k"): TIDFractions(0.50, 0.30, 0.20),
    # Maintain phase — retain-intensity/cut-volume: mostly easy with a
    # preserved weekly quality touch (docs/research/endurance_methodology.md,
    # Taper and Peaking).
    (PhaseType.MAINTAIN, WeekMode.LOAD, "marathon"): TIDFractions(0.72, 0.18, 0.10),
    (PhaseType.MAINTAIN, WeekMode.LOAD, "5k"): TIDFractions(0.62, 0.23, 0.15),
    (PhaseType.MAINTAIN, WeekMode.RECOVERY, "any"): TIDFractions(0.55, 0.35, 0.10),
    # Mode overrides (goal-independent).
    # RACE week — calibrated to ≈69 TSS/h.
    # Race week retains short quality work pre-race, not an easy week.
    (None, WeekMode.RACE, "any"): TIDFractions(0.40, 0.48, 0.12),
    (None, WeekMode.RACE_RECOVERY, "any"): TIDFractions(0.85, 0.10, 0.05),
}

_MODE_OVERRIDES: frozenset[WeekMode] = frozenset(
    {WeekMode.RECOVERY, WeekMode.RACE, WeekMode.RACE_RECOVERY}
)


def get_tid_fractions(
    phase: PhaseType,
    mode: WeekMode,
    goal: GoalType,
) -> TIDFractions:
    """Return target TID fractions for a given (phase, mode, goal) combination.

    Mode overrides (RECOVERY, RACE, RACE_RECOVERY) are goal-independent and
    take precedence over phase-specific rows.  For all other modes the result
    is linearly interpolated between the marathon endpoint (index 0.0) and the
    5K endpoint (index 1.0) using the goal's position in GOAL_INTENSITY_INDEX.

    Args:
        phase: Training phase (BASE, BUILD, PEAK, TAPER).
        mode: Week mode (LOAD, RECOVERY, TAPER, RACE, RACE_RECOVERY).
        goal: Race goal type driving the intensity mix.

    Returns:
        TIDFractions with z_low + z_mid + z_high == 1.0.
    """
    if mode in _MODE_OVERRIDES:
        if mode == WeekMode.RECOVERY:
            row = _TID_TABLE.get((phase, mode, "any"))
            if row is None:
                row = _TID_TABLE[(PhaseType.BASE, WeekMode.RECOVERY, "any")]
            return row
        return _TID_TABLE[(None, mode, "any")]

    marathon_row = _TID_TABLE[(phase, mode, "marathon")]
    fivek_row = _TID_TABLE[(phase, mode, "5k")]
    t = GOAL_INTENSITY_INDEX[goal]

    return TIDFractions(
        z_low=marathon_row.z_low + t * (fivek_row.z_low - marathon_row.z_low),
        z_mid=marathon_row.z_mid + t * (fivek_row.z_mid - marathon_row.z_mid),
        z_high=marathon_row.z_high + t * (fivek_row.z_high - marathon_row.z_high),
    )


__all__ = [
    "GOAL_INTENSITY_INDEX",
    "TID_BANDS",
    "get_tid_fractions",
]
