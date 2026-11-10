"""Capacity projection for the planner session-menu pipeline.

Projects per-week controllable-hours ceilings from the athlete's current
training load and the week-mode skeleton produced by the L1 phase planner.
Used by both the session generator (to cap session bounds) and the L3 QP
(as hard ceiling constraints).
"""

from __future__ import annotations

import math

from endury.planner.foundations.types import (
    CapacityProjection,
    PhaseType,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.macro.macro_config import POST_RECOVERY_BOUNCE_BACK

# ---------------------------------------------------------------------------
# Capacity policy constants
# ---------------------------------------------------------------------------

RECOVERY_CEILING_FACTOR: float = 0.60
"""RECOVERY week ceiling as a fraction of the preceding LOAD week's ceiling."""

TAPER_DECAY_FACTOR: float = 0.85
"""Per-week hours reduction factor during taper."""

RACE_SHAKEOUT_HOURS: float = 0.5
"""Shakeout allowance on RACE weeks (~30 min)."""

RACE_RECOVERY_CEILING_FACTOR: float = 0.60
"""Base factor for graduated race-recovery ramp."""

MIN_CAPACITY_FLOOR_HOURS: float = 1.0
"""Minimum seed when athlete's recent load is zero or very low (~1 h/wk)."""

MAX_CAPACITY_CEILING_HOURS: float = 18.0
"""Hard ceiling preventing exponential blowout on long plans."""

PEAK_SHED_FACTORS: tuple[float, ...] = (0.90, 0.80)
"""Volume shed applied to the final peak/load weeks before TAPER.

Index 0 is the week furthest from taper; the last entry is the week
immediately before taper. The two-step ~10%-then-~20% shed is the
maintainer's run-in to the full taper decay in the TAPER phase.

Applies only to LOAD weeks whose phase is ``PEAK`` and which sit in the
contiguous run immediately before the first TAPER week, and only before a
taper of ``PEAK_SHED_MIN_TAPER_WEEKS`` or more. Earlier LOAD weeks,
non-peak phases, and recovery weeks are unaffected. A run of a single PEAK
LOAD week has no run-down to shape: ``build_capacity_projection`` floors it
at the LOAD week issued before it instead (R-PP-007, see
``_lone_peak_load_week``), never shedding it below that week.
"""

PEAK_SHED_MIN_TAPER_WEEKS: int = 3
"""The A-goal taper length from which the peak shed applies (R-PP-007).

Before a three-week taper the shed starts the run-down early. Before a
shorter taper it made the PEAK weeks lighter than late BUILD, so the plan's
longest long run fell in BUILD (the trail 20K at 0.75 of the default
volume, 2026-10-06); such a plan sheds in its taper weeks alone.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_capacity_projection(
    recent_weekly_hours: dict[SportType, float],
    week_specs: list[WeekSpec],
    hours_ramp_limit: float = 1.10,
    max_weekly_hours_ceiling: float = MAX_CAPACITY_CEILING_HOURS,
    recovery_ceiling_factor: float = RECOVERY_CEILING_FACTOR,
    post_recovery_bounce_back: float = POST_RECOVERY_BOUNCE_BACK,
) -> CapacityProjection:
    """Build per-week hours ceiling from athlete state and week modes.

    The ramp rate is adaptive: on long plans the ceiling grows slowly so it
    reaches ``max_weekly_hours_ceiling`` in the last third of LOAD weeks
    (late BUILD / early PEAK).  On short plans the ambition-level
    ``hours_ramp_limit`` is used directly.

    After a RECOVERY week the next LOAD ceiling is capped at
    ``pre_recovery_ceiling * post_recovery_bounce_back`` so that the
    athlete re-enters load at roughly the level they left off, not at the
    unconstrained ramp projection.

    Args:
        recent_weekly_hours: Recent average weekly training hours per sport.
        week_specs: Ordered WeekSpec list from PhasePlan.
        hours_ramp_limit: Weekly ceiling growth factor for LOAD weeks.
        max_weekly_hours_ceiling: Absolute ceiling cap preventing
            exponential blowout on long plans.
        recovery_ceiling_factor: Recovery-week ceiling as a fraction of
            the preceding LOAD week's ceiling.
        post_recovery_bounce_back: Maximum rebound factor after a RECOVERY
            week.  The first LOAD ceiling after recovery is capped at
            ``pre_recovery_ceiling * post_recovery_bounce_back``.

    Returns:
        CapacityProjection with per-week total and per-sport hours ceilings.
    """
    total_recent = max(sum(recent_weekly_hours.values()), MIN_CAPACITY_FLOOR_HOURS)

    # Adaptive ramp: calibrate growth so the ceiling reaches
    # max_weekly_hours_ceiling around 75% of LOAD weeks (late BUILD).
    n_load = sum(1 for ws in week_specs if ws.mode == WeekMode.LOAD)
    target_load_week = max(1, int(n_load * 0.75))
    _MIN_ADAPTIVE_RAMP = 1.02  # floor: even 36-week plans grow >= 2%/wk
    if total_recent < max_weekly_hours_ceiling and target_load_week > 1:
        adaptive_ramp = math.exp(
            math.log(max_weekly_hours_ceiling / total_recent) / target_load_week
        )
        effective_ramp = max(_MIN_ADAPTIVE_RAMP, min(hours_ramp_limit, adaptive_ramp))
    else:
        effective_ramp = hours_ramp_limit

    weekly_ceiling: dict[int, float] = {}
    weekly_ceiling_by_sport: dict[int, dict[SportType, float]] = {}

    # Seed: the athlete's current load level.  The first LOAD week in the
    # loop applies one ramp step (x effective_ramp), so week 0 ceiling =
    # total_recent x effective_ramp -- a single, correct ramp step up.
    ceiling = total_recent
    last_load_ceiling = ceiling
    # The last LOAD ceiling as actually *issued* (post peak-shed).  The
    # taper chains off this when a RECOVERY week intervenes, so a recovery
    # deload and the taper decay don't compound.
    last_issued_load_ceiling = ceiling
    # Whether a LOAD week has actually issued a ceiling yet (as opposed to
    # ``last_issued_load_ceiling`` still holding the initial seed) -- a lone
    # PEAK LOAD week with nothing before it has nothing to floor against.
    prior_load_issued = False
    # Track the last LOAD ceiling before entering a non-LOAD gap so we
    # can cap the bounce-back when LOAD resumes.
    pre_gap_load_ceiling: float | None = None
    in_gap = False
    recovery_index = 0
    prev_mode: WeekMode | None = None

    rr_block_len: dict[int, int] = _race_recovery_block_lengths(week_specs)
    a_taper = sum(
        1
        for ws in week_specs
        if ws.mode in (WeekMode.TAPER, WeekMode.RACE) and ws.owning_goal is None
    )
    peak_shed_scale: dict[int, float] = (
        _peak_load_shed_window(week_specs, PEAK_SHED_FACTORS)
        if a_taper >= PEAK_SHED_MIN_TAPER_WEEKS
        else {}
    )
    # The PEAK LOAD week to floor at the LOAD week before it, when the PEAK
    # run before taper holds only one (R-PP-007). None when it holds none
    # or two-or-more (the two-step shed shapes a run-down across those).
    lone_peak_week = _lone_peak_load_week(week_specs)

    for ws in week_specs:
        mode = ws.mode
        if mode == WeekMode.LOAD:
            unshed = min(last_load_ceiling * effective_ramp, max_weekly_hours_ceiling)
            # After a non-LOAD gap (recovery, race-recovery, etc.) cap
            # the re-entry ceiling so volume doesn't spike.
            if in_gap and pre_gap_load_ceiling is not None:
                bounce_cap = pre_gap_load_ceiling * post_recovery_bounce_back
                unshed = min(unshed, bounce_cap)
            # last_load_ceiling ramps from the un-shed baseline so that
            # recovery weeks and subsequent LOAD weeks scale from the true
            # progression, not the shed value.
            last_load_ceiling = unshed
            ceiling = unshed * peak_shed_scale.get(ws.week, 1.0)
            if ws.week == lone_peak_week and prior_load_issued:
                # The PEAK run before taper holds a single LOAD week
                # (R-PP-007): floor it at the LOAD week issued before it
                # rather than shedding it below that week.
                ceiling = max(ceiling, last_issued_load_ceiling)
            last_issued_load_ceiling = ceiling
            prior_load_issued = True
            recovery_index = 0
            in_gap = False
            pre_gap_load_ceiling = None
        elif mode == WeekMode.RECOVERY:
            if not in_gap:
                pre_gap_load_ceiling = last_load_ceiling
                in_gap = True
            ceiling = last_load_ceiling * recovery_ceiling_factor
        elif mode in (WeekMode.TAPER, WeekMode.RACE):
            if not in_gap:
                pre_gap_load_ceiling = last_load_ceiling
                in_gap = True
            # A RECOVERY week immediately before the taper would otherwise
            # compound two deloads (recovery factor x taper decay).  Chain
            # off the last *issued* LOAD ceiling instead, which keeps the
            # peak shed in the chain and holds the first taper week below
            # the LOAD week preceding it.
            base = (
                last_issued_load_ceiling if prev_mode == WeekMode.RECOVERY else ceiling
            )
            ceiling = base * TAPER_DECAY_FACTOR
        elif mode == WeekMode.RACE_RECOVERY:
            if not in_gap:
                pre_gap_load_ceiling = last_load_ceiling
                in_gap = True
            recovery_index += 1
            n = rr_block_len.get(ws.week, 1)
            ceiling = (
                (recovery_index / n) * RACE_RECOVERY_CEILING_FACTOR * last_load_ceiling
            )

        weekly_ceiling[ws.week] = ceiling
        prev_mode = mode

        sport_ceil: dict[SportType, float] = {}
        for sport, frac in ws.sport_split.items():
            sport_ceil[sport] = ceiling * frac
        weekly_ceiling_by_sport[ws.week] = sport_ceil

    return CapacityProjection(
        weekly_ceiling=weekly_ceiling,
        weekly_ceiling_by_sport=weekly_ceiling_by_sport,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _peak_load_shed_window(
    week_specs: list[WeekSpec],
    shed_factors: tuple[float, ...],
) -> dict[int, float]:
    """Map the final ``len(shed_factors)`` peak/load weeks to shed factors.

    Finds the first TAPER week in ``week_specs`` and walks backwards through
    the contiguous peak/load run immediately preceding it, assigning shed
    factors from right to left (the week closest to TAPER gets the last
    entry in ``shed_factors``, i.e. the strongest shed).

    Non-peak weeks, recovery weeks inside the peak run, and plans without a
    TAPER week break the contiguous run — anything beyond that break is
    left at factor 1.0 (no shed).

    Args:
        week_specs: Ordered WeekSpec list.
        shed_factors: Multipliers to apply to the trailing peak/load weeks.

    Returns:
        Dict mapping week index to shed factor for peak/load weeks that sit
        in the shed window. Weeks not in the window are absent from the dict.
    """
    if not shed_factors:
        return {}

    peak_load_weeks = _peak_load_run(week_specs)[: len(shed_factors)]

    # peak_load_weeks is ordered newest-first (closest to taper first).
    # Assign shed factors from the end of the tuple backwards.
    result: dict[int, float] = {}
    for offset, week in enumerate(peak_load_weeks):
        factor = shed_factors[-1 - offset]
        result[week] = factor
    return result


def _peak_load_run(week_specs: list[WeekSpec]) -> list[int]:
    """The contiguous PEAK LOAD weeks immediately before the first TAPER week.

    Walks backwards from the first TAPER week through the PEAK phase,
    skipping recovery (or any non-LOAD) weeks inside the run so a mid-peak
    recovery week doesn't break it, and stopping as soon as the walk
    leaves PEAK phase.

    Args:
        week_specs: Ordered WeekSpec list.

    Returns:
        PEAK LOAD week indices, newest-first (closest to taper first).
        Empty when there is no TAPER week or no PEAK LOAD week before it.
    """
    taper_pos: int | None = None
    for i, ws in enumerate(week_specs):
        if ws.mode == WeekMode.TAPER:
            taper_pos = i
            break
    if taper_pos is None:
        return []

    peak_load_weeks: list[int] = []
    for j in range(taper_pos - 1, -1, -1):
        ws = week_specs[j]
        if ws.phase != PhaseType.PEAK:
            break
        if ws.mode == WeekMode.LOAD:
            peak_load_weeks.append(ws.week)
    return peak_load_weeks


def _lone_peak_load_week(week_specs: list[WeekSpec]) -> int | None:
    """The PEAK LOAD week's index, when the PEAK run before TAPER holds only one.

    R-PP-007: the two-step peak shed (``PEAK_SHED_FACTORS``) shapes a
    run-down across two or more PEAK LOAD weeks; a lone week has no
    run-down to shape and is floored at the LOAD week before it instead
    (``build_capacity_projection``).

    Args:
        week_specs: Ordered WeekSpec list.

    Returns:
        The lone PEAK LOAD week's index, or ``None`` when the run holds
        none or two-or-more.
    """
    peak_load_weeks = _peak_load_run(week_specs)
    return peak_load_weeks[0] if len(peak_load_weeks) == 1 else None


def _race_recovery_block_lengths(
    week_specs: list[WeekSpec],
) -> dict[int, int]:
    """Map each RACE_RECOVERY week to its block length.

    Args:
        week_specs: Ordered WeekSpec list.

    Returns:
        Dict mapping week index to total length of its RACE_RECOVERY block.
    """
    result: dict[int, int] = {}
    block_weeks: list[int] = []

    for ws in week_specs:
        if ws.mode == WeekMode.RACE_RECOVERY:
            block_weeks.append(ws.week)
        else:
            if block_weeks:
                n = len(block_weeks)
                for w in block_weeks:
                    result[w] = n
                block_weeks = []

    if block_weeks:
        n = len(block_weeks)
        for w in block_weeks:
            result[w] = n

    return result


__all__ = [
    "MAX_CAPACITY_CEILING_HOURS",
    "MIN_CAPACITY_FLOOR_HOURS",
    "PEAK_SHED_FACTORS",
    "PEAK_SHED_MIN_TAPER_WEEKS",
    "RACE_RECOVERY_CEILING_FACTOR",
    "RACE_SHAKEOUT_HOURS",
    "RECOVERY_CEILING_FACTOR",
    "TAPER_DECAY_FACTOR",
    "build_capacity_projection",
]
