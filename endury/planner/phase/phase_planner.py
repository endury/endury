"""L1 Phase Planner — deterministic phase ordering and race placement.

Takes a ``PhaseRequest`` (goals + training start) and produces a ``PhasePlan``
(ordered ``WeekSpec``s + ``RaceEvent``s).

Algorithm (four steps):
1. Build A-goal skeleton — phase ratios, taper, race week.
2. Splice B/C race weeks — each tune-up race replaces only its own week.
3. Blend sport splits — priority-weighted at goal overlaps.
4. Stamp recovery weeks — every Nth consecutive LOAD week (cadence set by
   ambition).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    RaceEvent,
    RaceGoal,
    SportType,
    WeekMode,
    WeekSpec,
    is_maintenance_goal,
)
from endury.planner.phase.phase_config import (
    B_RACE_RECOVERY_MIN_HOURS,
    BC_EARLIEST_WEEK,
    BC_PROXIMITY_TO_A_RACE_WEEKS,
    BC_WINDOW_FRACTION,
    BLEND_WEIGHT_A,
    BLEND_WEIGHT_B,
    BLEND_WEIGHT_C,
    LONG_TAPER_WEEKS,
    MAX_HORIZON_WEEKS,
    SHORT_TAPER_SUPPRESSION_WEEKS,
    TAPER_SUPPRESSION_WEEKS,
)
from endury.planner.phase.phase_tables import (
    GOAL_PROFILES,
    GoalProfile,
    goal_sports,
    resolve_phase_ratios,
)
from endury.planner.phase.sport_split_tables import get_phase_sport_split


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of a phase planner validation check.

    Args:
        valid: True if all hard checks passed.
        errors: Hard rejection messages (plan cannot proceed).
        warnings: Soft concern messages (plan is valid but non-ideal).
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Goal-type helpers
# ---------------------------------------------------------------------------


def _resolve_sport_split(
    goal_type: GoalType, phase: PhaseType
) -> dict[SportType, float]:
    """Phase-aware sport split with fallback to goal default."""
    override = get_phase_sport_split(goal_type, phase)
    if override is not None:
        return override
    return GOAL_PROFILES[goal_type].default_sport_split


# ---------------------------------------------------------------------------
# Mutable intermediate
# ---------------------------------------------------------------------------


@dataclass
class _MutableWeek:
    """Mutable intermediate for building the week list.

    ``_underlying_phase`` (the A-goal phase a B/C race week replaced) is
    consumed by step 3 and not carried into the final ``WeekSpec``.
    """

    week: int
    phase: PhaseType
    mode: WeekMode
    sport_split: dict[SportType, float]
    effective_goal: GoalType
    owning_goal: RaceGoal | None
    _underlying_phase: PhaseType | None = field(default=None, repr=False)


def _freeze_week(mw: _MutableWeek) -> WeekSpec:
    return WeekSpec(
        week=mw.week,
        phase=mw.phase,
        mode=mw.mode,
        sport_split=dict(mw.sport_split),
        effective_goal=mw.effective_goal,
        owning_goal=mw.owning_goal,
    )


# ---------------------------------------------------------------------------
# Validation — input
# ---------------------------------------------------------------------------


def _day(day: date) -> str:
    """A date as a refusal shows it, with the year a mistyped race gets wrong."""
    return f"{day:%b} {day.day}, {day.year}"


def _tune_up_window(
    request: PhaseRequest, a_goal: RaceGoal, effective_start: date
) -> tuple[date, date]:
    """The first and last date a tune-up race fits this plan.

    An A-race build keeps its first ``BC_EARLIEST_WEEK`` weeks, its peak and
    taper and the ``BC_PROXIMITY_TO_A_RACE_WEEKS`` weeks up to the race free
    of tune-ups; a maintenance block takes one in any of its weeks
    (R-PHASE-019). The ends cross when a plan has no week free.
    """
    after_start = request.training_start + timedelta(days=1)
    if is_maintenance_goal(a_goal.goal_type):
        return after_start, a_goal.race_date - timedelta(days=1)
    total_weeks = (a_goal.race_date - effective_start).days // 7 + 1
    first_closed_week = min(
        total_weeks - BC_PROXIMITY_TO_A_RACE_WEEKS,
        _a_goal_protected_start(
            GOAL_PROFILES[a_goal.goal_type],
            a_goal.goal_type,
            total_weeks,
            request.ambition,
        ),
    )
    return (
        max(after_start, effective_start + timedelta(weeks=BC_EARLIEST_WEEK)),
        effective_start + timedelta(weeks=first_closed_week, days=-1),
    )


def _tune_up_refusal(
    race: RaceGoal,
    a_goal: RaceGoal,
    request: PhaseRequest,
    window: tuple[date, date],
) -> str:
    """Why a tune-up race's date does not fit this plan, and which dates do.

    The week is the one the athlete counts from 1: trained weeks are on the
    plan's timeline, the weeks a short-notice plan joined past are not.
    """
    first, last = window
    race_date = race.race_date
    plan_start = request.training_start - timedelta(weeks=request.prior_training_weeks)
    in_week = f"falls in week {(race_date - plan_start).days // 7 + 1} of this plan"
    if race_date <= request.training_start:
        reason = f"is not after this plan's start on {_day(request.training_start)}."
    elif is_maintenance_goal(a_goal.goal_type) and race_date >= a_goal.race_date:
        reason = f"is not before this block ends on {_day(a_goal.race_date)}."
    elif race_date >= a_goal.race_date:
        reason = (
            f"is not before your A race on {_day(a_goal.race_date)}, and a plan "
            "ends at its A race."
        )
    elif race_date > last:
        reason = (
            f"{in_week}, in the peak and taper before your A race on "
            f"{_day(a_goal.race_date)}, and those weeks stay free of other races."
        )
    else:
        reason = (
            f"{in_week}, too soon after it starts: the opening weeks are kept "
            "for training."
        )
    fits = (
        f"Any date from {_day(first)} to {_day(last)} fits."
        if first <= last
        else "This plan has no week free for a tune-up race, so take it out or "
        "pick a later A race."
    )
    return f"Your tune-up race on {_day(race_date)} {reason} {fits}"


def validate_phase_request(request: PhaseRequest) -> ValidationResult:
    """Validate a ``PhaseRequest`` before running the phase planner.

    Checks hard rejections and soft warnings per the L1 contract. The plan
    wizard and the coach show a refusal or a warning verbatim, so each is
    written for the athlete, and a race-date refusal says which dates would
    fit.

    Args:
        request: The phase planning request to validate.

    Returns:
        ``ValidationResult`` with errors (hard rejections) and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if request.prior_training_weeks < 0:
        return ValidationResult(
            valid=False,
            errors=[
                f"prior_training_weeks must be >= 0, got {request.prior_training_weeks}"
            ],
        )
    # Replanning builds the skeleton on a virtual timeline that starts
    # prior_training_weeks + horizon_join_weeks earlier; all week math below
    # uses that start so horizon/window checks apply to the full (virtual +
    # actual) horizon.
    effective_start = request.training_start - timedelta(
        weeks=request.prior_training_weeks + request.horizon_join_weeks
    )

    a_goals = [g for g in request.goals if g.priority == "A"]
    bc_goals = [g for g in request.goals if g.priority in ("B", "C")]
    # A replan's tune-ups behind its start are history: they keep their weeks
    # on the virtual timeline (R-PHASE-002b) and no check here can refuse them.
    if request.prior_training_weeks:
        bc_goals = [g for g in bc_goals if g.race_date > request.training_start]

    # --- Hard rejections ---

    # 1. Exactly one A-goal.
    if not a_goals:
        errors.append(
            "A plan builds toward one A race and this one has none. Make the race "
            "you are building toward your A race."
        )
    elif len(a_goals) > 1:
        errors.append(
            f"A plan builds toward one A race and this one has {len(a_goals)}, on "
            + " and ".join(_day(g.race_date) for g in a_goals)
            + ". Keep the race you are building toward as your A race and make "
            "the rest tune-up races."
        )
    else:
        a = a_goals[0]
        race_week = (a.race_date - effective_start).days // 7
        total_weeks = race_week + 1

        # 1b. A maintenance goal is the block itself: it can only be the
        # sole A-goal. Its tune-up races take any week — the rest of check 3
        # protects an A-race build the block does not have (R-PHASE-019).
        maintenance = is_maintenance_goal(a.goal_type)
        errors.extend(
            "A maintenance block is a plan of its own, so the one ending on "
            f"{_day(g.race_date)} cannot be a tune-up race. Take it out of this plan."
            for g in bc_goals
            if is_maintenance_goal(g.goal_type)
        )

        # 2. A-race date > training_start.
        if a.race_date <= request.training_start:
            errors.append(
                f"Your race on {_day(a.race_date)} is not after this plan's start "
                f"on {_day(request.training_start)}. Pick a later date."
            )

        # 3. Each B/C after training_start and before the A-race, clear of its
        # first weeks, its peak/taper zone and the weeks up to it: one reason
        # per race.
        window = _tune_up_window(request, a, effective_start)
        errors.extend(
            _tune_up_refusal(g, a, request, window)
            for g in bc_goals
            if not window[0] <= g.race_date <= window[1]
        )

        # 4. A-race >= min horizon. A race on or before the start is check 2's:
        # it has no horizon to count.
        profile = GOAL_PROFILES[a.goal_type]
        min_weeks = profile.min_horizon_weeks
        if total_weeks < min_weeks and a.race_date > request.training_start:
            preparation = (
                "A maintenance block" if maintenance else "A preparation for this race"
            )
            first_fit = effective_start + timedelta(weeks=min_weeks - 1)
            errors.append(
                f"{preparation} takes at least {min_weeks} weeks and this one has "
                f"{total_weeks}. Any date from {_day(first_fit)} on fits."
            )

        # 6. No two B/C in same week.
        by_week: dict[int, list[RaceGoal]] = {}
        for g in bc_goals:
            by_week.setdefault((g.race_date - effective_start).days // 7, []).append(g)
        errors.extend(
            "Your tune-up races on "
            + " and ".join(_day(g.race_date) for g in races)
            + " fall in the same week. A plan takes one race a week, so move one "
            "to another week."
            for races in by_week.values()
            if len(races) > 1
        )

        # 8. B/C race weeks <= 40% of pre-taper weeks. A tune-up race
        # replaces only its own week (R-PHASE-010), so its window is that
        # week; two in one week are check 6.
        pre_taper = total_weeks - profile.taper_weeks
        if pre_taper > 0 and len(bc_goals) > BC_WINDOW_FRACTION * pre_taper:
            errors.append(
                f"This plan has {len(bc_goals)} tune-up races and can take at most "
                f"{int(BC_WINDOW_FRACTION * pre_taper)}. Take some out or give the "
                "plan more weeks."
            )

        # Horizon cap.
        if total_weeks > MAX_HORIZON_WEEKS:
            last_fit = effective_start + timedelta(weeks=MAX_HORIZON_WEEKS, days=-1)
            errors.append(
                f"This plan would run {total_weeks} weeks and a plan runs at most "
                f"{MAX_HORIZON_WEEKS}. Any date up to {_day(last_fit)} fits, or start "
                "with a maintenance block and build the race plan later."
            )

        # --- Soft warnings ---

        # 1. Horizon beyond the goal's focused-prep ceiling. Fresh plans
        # only (no trained or joined weeks): "start with a maintenance
        # block" is actionable at creation, not mid-prep on a replan.
        max_rec = profile.max_recommended_weeks
        if (
            request.prior_training_weeks + request.horizon_join_weeks == 0
            and max_rec is not None
            and total_weeks > max_rec
        ):
            # A fresh plan starts at effective_start, so this start leaves
            # exactly max_rec weeks.
            race_plan_start = request.training_start + timedelta(
                weeks=total_weeks - max_rec
            )
            warnings.append(
                f"This plan runs {total_weeks} weeks, and a race preparation works "
                f"best within {max_rec}. Begin the race plan on "
                f"{_day(race_plan_start)}, with a maintenance block before it, or "
                "add a B race along the way."
            )

        # 2. B-goal sport != A-goal sport.
        a_sports = goal_sports(a.goal_type)
        warnings.extend(
            f"Your B race on {_day(g.race_date)} and your A race are not the same "
            "sport, so the B race does less to prepare you for your A race."
            for g in bc_goals
            if g.priority == "B" and goal_sports(g.goal_type) != a_sports
        )

        # 3. > 2 secondary goals.
        if len(bc_goals) > 2:
            warnings.append(
                f"This plan has {len(bc_goals)} tune-up races. Each one takes over "
                "its own week, and more than two break up the build, so keep the "
                "ones that matter most."
            )

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Validation — post-conditions
# ---------------------------------------------------------------------------


def validate_phase_plan(
    plan: PhasePlan,
    request: PhaseRequest,
    recovery_cadence: int | None = None,
) -> ValidationResult:
    """Verify post-conditions on a completed ``PhasePlan``.

    Args:
        plan: The completed phase plan.
        request: The original request (for cross-referencing goals).
        recovery_cadence: Override for the consecutive-LOAD limit. When
            ``None``, derived from ``request.ambition``.

    Returns:
        ``ValidationResult`` with any structural violations.
    """
    if recovery_cadence is None:
        recovery_cadence = get_ambition_profile(request.ambition).recovery_cadence

    if is_maintenance_goal(plan.a_goal.goal_type):
        return _validate_maintenance_plan(plan, recovery_cadence)

    errors: list[str] = []
    weeks = plan.weeks

    # 1. Gap-free week indices [0..N).
    indices = [w.week for w in weeks]
    expected = list(range(len(weeks)))
    if indices != expected:
        errors.append(f"Week indices not gap-free: got {indices[:10]}...")

    # 2. Phase ordering: no BASE after BUILD/PEAK in the A-goal skeleton.
    #    Only checked on A-goal weeks (owning_goal is None). A B race week
    #    is labelled TAPER inside the A-goal sequence and is not a
    #    regression.
    _seen_advanced: set[PhaseType] = set()
    for w in weeks:
        if w.owning_goal is not None:
            continue  # skip B/C race weeks
        if w.phase in (PhaseType.BUILD, PhaseType.PEAK, PhaseType.TAPER):
            _seen_advanced.add(w.phase)
        if w.phase == PhaseType.BASE and (
            PhaseType.BUILD in _seen_advanced or PhaseType.PEAK in _seen_advanced
        ):
            errors.append(f"Week {w.week}: A-goal BASE after BUILD/PEAK")

    # 3. Every goal has exactly one RaceEvent in a RACE-mode week.
    race_event_goals = {re.goal for re in plan.race_events}
    errors.extend(
        f"Missing RaceEvent for {g.priority}-goal {g.goal_type.value}"
        for g in request.goals
        if g not in race_event_goals
    )
    race_weeks = {w.week for w in weeks if w.mode == WeekMode.RACE}
    errors.extend(
        f"RaceEvent week {re.week} is not a RACE-mode week"
        for re in plan.race_events
        if re.week not in race_weeks
    )

    # 4. Phase minimums (A-goal phases only — B/C race weeks excluded).
    #    Base minimum relaxed for tight horizons where build+peak+taper
    #    already consume all available weeks.
    a_phases = [w.phase for w in weeks if w.owning_goal is None]
    if a_phases:
        total_a = len(a_phases)
        base_count = sum(1 for p in a_phases if p == PhaseType.BASE)
        build_count = sum(1 for p in a_phases if p == PhaseType.BUILD)
        peak_count = sum(1 for p in a_phases if p == PhaseType.PEAK)
        taper_count = sum(1 for p in a_phases if p == PhaseType.TAPER)
        # Build/peak/taper have hard minimums; base absorbs remainder.
        min_non_base = build_count + peak_count + taper_count
        base_floor = max(0, min(2, total_a - min_non_base))
        if base_count < base_floor:  # pragma: no cover
            errors.append(f"A-goal base phase has {base_count} wk (min {base_floor})")
        if build_count < 2:
            errors.append(f"A-goal build phase has {build_count} wk (min 2)")
        if peak_count < 1:
            errors.append(f"A-goal peak phase has {peak_count} wk (min 1)")
        if taper_count < 1:
            errors.append(f"A-goal taper phase has {taper_count} wk (min 1)")

    # 5. Sport split sums to 1.0 (+-0.01).
    for w in weeks:
        total = sum(w.sport_split.values())
        if abs(total - 1.0) > 0.01:  # pragma: no cover
            errors.append(  # pragma: no cover
                f"Week {w.week}: sport_split sums to {total:.4f}"
            )

    # 6. No more than recovery_cadence consecutive LOAD weeks without a break.
    #    The final LOAD run before taper/race is exempt from this limit
    #    because taper suppression (rule 7) intentionally prevents recovery
    #    placement near taper, and taper itself serves as recovery.
    consecutive_load_count = 0
    load_runs: list[tuple[int, int]] = []  # (start_week, length)
    run_start = -1
    for w in weeks:
        if w.mode == WeekMode.LOAD:
            if consecutive_load_count == 0:
                run_start = w.week
            consecutive_load_count += 1
        else:
            if consecutive_load_count > 0:
                load_runs.append((run_start, consecutive_load_count))
            consecutive_load_count = 0
    if consecutive_load_count > 0:  # pragma: no cover — last week is always RACE
        load_runs.append((run_start, consecutive_load_count))

    for start, length in load_runs:
        if length > recovery_cadence:
            # The final LOAD run before taper/race is exempt from the cadence
            # cap (rule 7 suppresses recovery placement within
            # TAPER_SUPPRESSION_WEEKS of taper, and taper itself serves as
            # recovery).  Two shapes of exemption:
            #   (a) LOAD run terminates directly at TAPER/RACE.
            #   (b) LOAD run terminates at a RECOVERY week that is itself
            #       immediately followed by TAPER/RACE — this is the PEAK-
            #       phase deload the stamper inserts on short plans where
            #       suppression prevented an earlier cadence break.
            end_week = start + length - 1
            next_week = end_week + 1
            terminates_at_taper_adjacent = False
            if next_week < len(weeks):
                nw_mode = weeks[next_week].mode
                if nw_mode in (WeekMode.TAPER, WeekMode.RACE):
                    terminates_at_taper_adjacent = True
                elif nw_mode == WeekMode.RECOVERY:
                    after_rec = next_week + 1
                    if after_rec < len(weeks) and weeks[after_rec].mode in (
                        WeekMode.TAPER,
                        WeekMode.RACE,
                    ):
                        terminates_at_taper_adjacent = True
            if not terminates_at_taper_adjacent:
                errors.append(
                    f"Week {start + length - 1}: {length} consecutive LOAD "
                    f"weeks (max {recovery_cadence})"
                )

    # 7. No RECOVERY within the suppression window of the A-goal taper
    #    (R-PHASE-015): three weeks with PEAK exempt before a three-week
    #    taper, two weeks with no exemption before a shorter one. Only the
    #    A-goal taper counts, not a B race week.
    first_a_taper, window, peak_exempt = _taper_suppression(weeks)
    if first_a_taper is not None:
        errors.extend(
            f"Week {w.week}: RECOVERY within {window} wk of taper "
            f"(week {first_a_taper})"
            for w in weeks
            if w.mode == WeekMode.RECOVERY
            and not (peak_exempt and w.phase == PhaseType.PEAK)
            and w.week < first_a_taper
            and first_a_taper - w.week <= window
        )

    # 8. RACE_RECOVERY directly follows a B/C race week (R-PHASE-010).
    errors.extend(
        f"Week {w.week}: RACE_RECOVERY not directly after a B/C race week"
        for i, w in enumerate(weeks)
        if w.mode == WeekMode.RACE_RECOVERY
        and (
            i == 0
            or weeks[i - 1].mode != WeekMode.RACE
            or weeks[i - 1].owning_goal is None
        )
    )

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=[])


# ---------------------------------------------------------------------------
# Step 1 — A-goal skeleton
# ---------------------------------------------------------------------------


def _week_for_date(training_start: date, race_date: date) -> int:
    """Return 0-indexed week offset for a race date."""
    return (race_date - training_start).days // 7


def _a_goal_protected_start(
    profile: GoalProfile,
    goal_type: GoalType,
    total_weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.MODERATE,
) -> int:
    """Return the first week of the A-goal peak/taper zone.

    B/C goals at or beyond this week should be rejected / skipped.
    """
    taper = min(profile.taper_weeks, total_weeks)
    pre_taper = total_weeks - taper
    ratios = resolve_phase_ratios(goal_type, pre_taper, ambition)
    base, build, _peak = _allocate_phases(
        pre_taper, ratios, profile.min_build_weeks, profile.min_peak_weeks
    )
    return base + build


def _allocate_phases(
    total_pre_taper: int,
    ratios: tuple[float, float, float],
    min_build: int,
    min_peak: int,
) -> tuple[int, int, int]:
    """Distribute pre-taper weeks into (base, build, peak).

    Guarantees minimums for build and peak. Remainder goes to base.
    """
    base_r, build_r, peak_r = ratios
    base = round(total_pre_taper * base_r)
    build = round(total_pre_taper * build_r)
    peak = round(total_pre_taper * peak_r)

    # Reconcile rounding.
    while base + build + peak < total_pre_taper:
        build += 1
    while base + build + peak > total_pre_taper:
        if base > 0:
            base -= 1
        elif build > min_build:
            build -= 1
        else:
            peak -= 1

    # Enforce minimums.
    build = max(build, min_build)
    peak = max(peak, min_peak)
    base = total_pre_taper - build - peak

    # If base went negative, steal from build/peak.
    while base < 0:
        if build > min_build:
            build -= 1
            base += 1
        elif peak > min_peak:
            peak -= 1
            base += 1
        else:
            break

    base = max(base, 0)
    # When minimums exceed total, the sum will overshoot — validation
    # (min_horizon) prevents this in real plans.  Assert only when feasible.
    if total_pre_taper >= min_build + min_peak:
        assert base + build + peak == total_pre_taper, (
            f"Phase allocation broken: {base}+{build}+{peak} != {total_pre_taper}"
        )
    return base, build, peak


def _build_a_skeleton(
    a_goal: RaceGoal,
    training_start: date,
    total_weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.MODERATE,
) -> list[_MutableWeek]:
    """Build the initial week skeleton for the A-goal."""
    profile = GOAL_PROFILES[a_goal.goal_type]
    taper = min(profile.taper_weeks, total_weeks)
    pre_taper = total_weeks - taper

    ratios = resolve_phase_ratios(a_goal.goal_type, pre_taper, ambition)
    base, build, peak = _allocate_phases(
        pre_taper, ratios, profile.min_build_weeks, profile.min_peak_weeks
    )

    phases: list[PhaseType] = []
    phases.extend([PhaseType.BASE] * base)
    phases.extend([PhaseType.BUILD] * build)
    phases.extend([PhaseType.PEAK] * peak)
    phases.extend([PhaseType.TAPER] * taper)
    phases = phases[:total_weeks]

    skeleton: list[_MutableWeek] = []
    for i, phase in enumerate(phases):
        if i == total_weeks - 1:
            mode = WeekMode.RACE
        elif phase == PhaseType.TAPER:
            mode = WeekMode.TAPER
        else:
            mode = WeekMode.LOAD
        skeleton.append(
            _MutableWeek(
                week=i,
                phase=phase,
                mode=mode,
                sport_split=dict(_resolve_sport_split(a_goal.goal_type, phase)),
                effective_goal=a_goal.goal_type,
                owning_goal=None,
            )
        )

    return skeleton


def _build_maintenance_skeleton(
    a_goal: RaceGoal,
    total_weeks: int,
) -> list[_MutableWeek]:
    """Build the all-MAINTAIN skeleton for a raceless maintenance block.

    Every week is ``MAINTAIN``/``LOAD`` (recovery stamping runs afterwards);
    there is no taper, no race week, and no ``RaceEvent``. The goal's
    ``race_date`` only fixes the horizon (end of block).
    """
    return [
        _MutableWeek(
            week=i,
            phase=PhaseType.MAINTAIN,
            mode=WeekMode.LOAD,
            sport_split=dict(
                _resolve_sport_split(a_goal.goal_type, PhaseType.MAINTAIN)
            ),
            effective_goal=a_goal.goal_type,
            owning_goal=None,
        )
        for i in range(total_weeks)
    ]


def _avoid_terminal_recovery(skeleton: list[_MutableWeek]) -> None:
    """Ensure a maintenance block never ends on a RECOVERY week (in-place).

    ``_stamp_recovery_weeks`` places RECOVERY every ``recovery_cadence``-th
    consecutive LOAD week regardless of where the block ends; when the
    block length is an exact multiple of the cadence, that lands RECOVERY
    on the final week, ending the block on its lightest week. Reverting
    that one week to LOAD leaves every earlier recovery week untouched.
    Maintenance-only — race-goal skeletons never call this helper.

    Args:
        skeleton: Mutable week list, modified in-place. Must be non-empty.
    """
    if skeleton[-1].mode == WeekMode.RECOVERY:
        skeleton[-1].mode = WeekMode.LOAD


def _validate_maintenance_plan(
    plan: PhasePlan, recovery_cadence: int
) -> ValidationResult:
    """Post-conditions for a maintenance ``PhasePlan``.

    Structural rules: gap-free week indices, MAINTAIN phases except a B
    race week's TAPER, LOAD/RECOVERY modes except a tune-up's RACE week
    and the RACE_RECOVERY week directly after a long B race, one race
    event per tune-up in its own RACE week and none for the block's end,
    sport splits summing to 1.0, and no LOAD run longer than
    *recovery_cadence*.
    """
    errors: list[str] = []
    weeks = plan.weeks

    indices = [w.week for w in weeks]
    if indices != list(range(len(weeks))):
        errors.append(f"Week indices not gap-free: got {indices[:10]}...")

    errors.extend(
        f"Week {w.week}: maintenance plan has phase {w.phase.value}"
        for w in weeks
        if w.phase != PhaseType.MAINTAIN
        and not (w.owning_goal is not None and w.phase == PhaseType.TAPER)
    )
    errors.extend(
        f"Week {w.week}: maintenance plan has mode {w.mode.value}"
        for w in weeks
        if w.mode not in (WeekMode.LOAD, WeekMode.RECOVERY, WeekMode.RACE_RECOVERY)
        and not (w.mode == WeekMode.RACE and w.owning_goal is not None)
    )
    errors.extend(
        f"Week {w.week}: RACE_RECOVERY not directly after a B/C race week"
        for i, w in enumerate(weeks)
        if w.mode == WeekMode.RACE_RECOVERY
        and (
            i == 0
            or weeks[i - 1].mode != WeekMode.RACE
            or weeks[i - 1].owning_goal is None
        )
    )

    race_weeks = {w.week for w in weeks if w.mode == WeekMode.RACE}
    event_goals = {event.goal for event in plan.race_events}
    errors.extend(
        f"Missing RaceEvent for {w.owning_goal.priority}-goal "
        f"{w.owning_goal.goal_type.value}"
        for w in weeks
        if w.owning_goal is not None and w.owning_goal not in event_goals
    )
    errors.extend(
        f"RaceEvent week {event.week} is not a B/C race week"
        for event in plan.race_events
        if event.week not in race_weeks or event.goal.priority == "A"
    )

    for w in weeks:
        total = sum(w.sport_split.values())
        if abs(total - 1.0) > 0.01:  # pragma: no cover — WeekSpec validates
            errors.append(  # pragma: no cover
                f"Week {w.week}: sport_split sums to {total:.4f}"
            )

    consecutive_load = 0
    for w in weeks:
        if w.mode == WeekMode.LOAD:
            consecutive_load += 1
            if consecutive_load > recovery_cadence:
                errors.append(
                    f"Week {w.week}: {consecutive_load} consecutive LOAD "
                    f"weeks (max {recovery_cadence})"
                )
        else:
            consecutive_load = 0

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=[])


# ---------------------------------------------------------------------------
# Step 2 — Splice B/C race weeks
# ---------------------------------------------------------------------------


def _splice_secondary_goals(
    skeleton: list[_MutableWeek],
    bc_goals: list[RaceGoal],
    a_goal: RaceGoal,
    training_start: date,
) -> list[RaceEvent]:
    """Turn each B/C goal's week into its race week (in place).

    A tune-up race costs the A-goal preparation only its own week
    (``docs/specs/planner/phase_planner.md``, R-PHASE-010): no sharpening
    block and no separate taper week, so the weeks around it keep their
    A-goal phase and mode. A B race week is labelled ``TAPER`` so it draws
    the B goal's race-week menu — the few-day mini-taper inside the race
    week; a C race week keeps its A-goal phase. A B race longer than
    ``B_RACE_RECOVERY_MIN_HOURS`` also turns the next A-goal week into a
    ``RACE_RECOVERY`` week on its own phase and menus; on the first A-goal
    peak week it stays a build week, so the peak starts a week later. In a
    maintenance block a C race week keeps the block's goal as
    ``effective_goal``: its recovery menu is the lighter week with the race in
    it, since a race goal's menu has no ``MAINTAIN`` phase to serve.

    Returns the list of ``RaceEvent`` objects for the secondary goals.
    """
    race_events: list[RaceEvent] = []
    total_weeks = len(skeleton)

    # Identify A-goal peak/taper zone (no splicing here). A B/C race falling
    # in A-goal PEAK or TAPER is skipped to protect A-goal specificity, so
    # the guard starts at the first A-goal PEAK week.
    a_protected_start = next(
        (
            w.week
            for w in skeleton
            if w.phase in (PhaseType.PEAK, PhaseType.TAPER) and w.owning_goal is None
        ),
        total_weeks,
    )

    for goal in sorted(bc_goals, key=lambda g: g.race_date):
        race_week = _week_for_date(training_start, goal.race_date)
        if race_week < 0 or race_week >= total_weeks:
            continue
        # Skip if race falls in A-goal peak/taper zone.
        if race_week >= a_protected_start:
            continue

        week = skeleton[race_week]
        week._underlying_phase = week.phase
        if goal.priority == "B":
            week.phase = PhaseType.TAPER
        week.mode = WeekMode.RACE
        week.effective_goal = goal.goal_type
        if goal.priority == "C" and is_maintenance_goal(a_goal.goal_type):
            week.effective_goal = a_goal.goal_type
        week.owning_goal = goal

        race_events.append(
            RaceEvent(
                event_id=f"race:{goal.priority.lower()}_{goal.goal_type.value}_w{race_week}",
                goal=goal,
                week=race_week,
                day_of_week=goal.race_date.weekday(),
                duration_by_sport=dict(GOAL_PROFILES[goal.goal_type].race_duration),
            )
        )

    # After a long B race the athlete needs an easier week before loading
    # again; a later race that week keeps its own week. On the first A-goal
    # peak week the easier week stays a build week: the peak starts a week
    # later and its session windows, which count the phase's weeks, keep their
    # places. A one-week peak keeps its label.
    for event in race_events:
        after = event.week + 1
        if (
            event.goal.priority == "B"
            and sum(event.duration_by_sport.values()) > B_RACE_RECOVERY_MIN_HOURS
            and after < total_weeks
            and skeleton[after].owning_goal is None
        ):
            skeleton[after].mode = WeekMode.RACE_RECOVERY
            if (
                after == a_protected_start
                and skeleton[after + 1].phase == PhaseType.PEAK
            ):
                skeleton[after].phase = PhaseType.BUILD
                skeleton[after].sport_split = dict(
                    _resolve_sport_split(a_goal.goal_type, PhaseType.BUILD)
                )

    return race_events


# ---------------------------------------------------------------------------
# Step 3 — Blend sport splits
# ---------------------------------------------------------------------------


def _normalize_split(split: dict[SportType, float]) -> dict[SportType, float]:
    """Normalize a sport split so values sum to 1.0."""
    total = sum(split.values())
    if total <= 0:
        return split
    return {s: v / total for s, v in split.items()}


def _blend_sport_splits(skeleton: list[_MutableWeek], a_goal: RaceGoal) -> None:
    """Blend sport splits at B/C race weeks (in-place).

    The race week's sessions should match the race sport. Both the A-side
    and B/C-side lookups use phase-aware splits.
    """
    for week in skeleton:
        bc_goal = week.owning_goal
        if bc_goal is None:
            continue

        # A-side: use the underlying A-goal phase (before the race week replaced it).
        a_phase = week._underlying_phase or week.phase
        a_split = _resolve_sport_split(a_goal.goal_type, a_phase)

        bc_split = _resolve_sport_split(bc_goal.goal_type, week.phase)

        w_bc = BLEND_WEIGHT_B if bc_goal.priority == "B" else BLEND_WEIGHT_C

        # Merge all sports from both splits.
        all_sports = set(a_split) | set(bc_split)
        blended = {
            s: BLEND_WEIGHT_A * a_split.get(s, 0.0) + w_bc * bc_split.get(s, 0.0)
            for s in all_sports
        }
        week.sport_split = _normalize_split(blended)


# ---------------------------------------------------------------------------
# Step 4 — Stamp recovery weeks
# ---------------------------------------------------------------------------


def _taper_suppression(
    weeks: list[_MutableWeek] | list[WeekSpec],
) -> tuple[int | None, int, bool]:
    """The A-goal taper's first week, its recovery-free window and PEAK's exemption.

    R-PHASE-015: a taper of ``LONG_TAPER_WEEKS`` or more keeps
    ``TAPER_SUPPRESSION_WEEKS`` and exempts PEAK, whose load demands a
    deload the taper then adds to; a shorter taper takes
    ``SHORT_TAPER_SUPPRESSION_WEEKS`` with no exemption, so the last load
    block holds two or three weeks and no recovery week sits beside the one
    taper week.

    Args:
        weeks: The plan's weeks, mutable or final.

    Returns:
        ``(first_a_taper, window, peak_exempt)``; ``first_a_taper`` is
        ``None`` for a plan without an A-goal taper (a maintenance block).
    """
    taper = [
        w.week
        for w in weeks
        if w.mode in (WeekMode.TAPER, WeekMode.RACE) and w.owning_goal is None
    ]
    if not taper:
        return None, TAPER_SUPPRESSION_WEEKS, True
    if len(taper) >= LONG_TAPER_WEEKS:
        return min(taper), TAPER_SUPPRESSION_WEEKS, True
    return min(taper), SHORT_TAPER_SUPPRESSION_WEEKS, False


def _stamp_recovery_weeks(
    skeleton: list[_MutableWeek],
    recovery_cadence: int,
) -> None:
    """Insert recovery weeks every *recovery_cadence*-th consecutive LOAD week.

    Suppresses recovery in the window before the first A-goal taper week
    that :func:`_taper_suppression` sets (R-PHASE-015). Any non-LOAD week
    (a taper or race week, a B/C race week included) resets the count, and
    the week before a B/C race week stays LOAD: the race week's own
    mini-taper is that block's deload (R-PHASE-014).

    Args:
        skeleton: Mutable week list (modified in-place).
        recovery_cadence: Insert RECOVERY every *N*-th consecutive LOAD week.
            Driven by ``AmbitionProfile.recovery_cadence``: 4 (3:1) at
            every tier.
    """
    first_a_taper, window, peak_exempt = _taper_suppression(skeleton)

    consecutive_load = 0
    for w in skeleton:
        if w.mode != WeekMode.LOAD:
            consecutive_load = 0
            continue

        consecutive_load += 1
        # Before a short taper the last week the window allows takes the
        # recovery early when the block would otherwise run past the cadence
        # into the window (five load weeks before a two-week taper).
        pulled_forward = (
            first_a_taper is not None
            and not peak_exempt
            and first_a_taper - w.week == window + 1
            and consecutive_load + window > recovery_cadence
        )
        if consecutive_load >= recovery_cadence or pulled_forward:
            # Suppress near taper. Before a three-week taper PEAK is exempt:
            # its load demands a deload and the taper adds to it. Before a
            # shorter taper it is not: a recovery week there sat beside the
            # one taper week, three light weeks before a three-hour race.
            if (
                first_a_taper is not None
                and first_a_taper - w.week <= window
                and not (peak_exempt and w.phase == PhaseType.PEAK)
            ):
                continue
            # A B/C race week next is this block's deload; a recovery week
            # here would put two light weeks back to back.
            if w.week + 1 < len(skeleton) and skeleton[w.week + 1].owning_goal:
                continue
            w.mode = WeekMode.RECOVERY
            consecutive_load = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _truncate_prior_weeks(plan: PhasePlan, trained: int, joined: int) -> PhasePlan:
    """Drop the leading virtual weeks of a virtual-horizon plan and re-index.

    The first *joined* weeks fill a too-short horizon and are discarded;
    the *trained* weeks after them are kept as ``prior_weeks``, indexed
    ``-trained..-1``. Week indices become 0-based relative to the actual
    ``training_start``; race events shift with them. A replan's tune-ups
    behind the truncation point keep their weeks among ``prior_weeks``
    (R-PHASE-002b); their events go with those weeks.
    """
    cut = trained + joined
    weeks = [replace(w, week=w.week - cut) for w in plan.weeks[cut:]]
    prior_weeks = tuple(replace(w, week=w.week - cut) for w in plan.weeks[joined:cut])
    events = [replace(e, week=e.week - cut) for e in plan.race_events if e.week >= cut]
    return PhasePlan(
        weeks=weeks, race_events=events, a_goal=plan.a_goal, prior_weeks=prior_weeks
    )


def plan_phases(request: PhaseRequest) -> PhasePlan:
    """Run the L1 phase planner.

    Validates input, builds the A-goal skeleton, splices B/C race weeks,
    blends sport splits, and stamps recovery weeks. Maintenance
    goals start from an all-MAINTAIN skeleton with no taper and no race
    event of their own, take the same tune-up splicing, blending and
    recovery stamping, and add a terminal-recovery guard
    (`docs/specs/planner/phase_planner.md`, R-PHASE-020) so the block
    never ends on a RECOVERY week.

    When ``request.prior_training_weeks`` or ``request.horizon_join_weeks``
    is positive the four steps run on a virtual timeline that starts that
    many weeks earlier, post-conditions are checked on the full virtual
    plan, and the leading weeks are then truncated — so a replan resumes
    mid-prep instead of restarting BASE. The trained weeks stay on the
    plan's timeline as ``prior_weeks``.

    Args:
        request: Phase planning request.

    Returns:
        A complete ``PhasePlan`` with ordered ``WeekSpec``s and ``RaceEvent``s.

    Raises:
        ValueError: If the request fails hard validation.
    """
    validation = validate_phase_request(request)
    if not validation.valid:
        raise ValueError("Invalid PhaseRequest: " + " ".join(validation.errors))

    a_goal = next(g for g in request.goals if g.priority == "A")
    bc_goals = [g for g in request.goals if g.priority in ("B", "C")]
    trained, joined = request.prior_training_weeks, request.horizon_join_weeks
    effective_start = request.training_start - timedelta(weeks=trained + joined)
    race_week = _week_for_date(effective_start, a_goal.race_date)
    total_weeks = race_week + 1  # plan spans [0, race_week] inclusive

    profile = get_ambition_profile(request.ambition)

    # Maintenance branch — all-MAINTAIN skeleton, the block's end is not a
    # race; its tune-ups are spliced, blended and deloaded around like an
    # A-race build's (R-PHASE-019).
    if is_maintenance_goal(a_goal.goal_type):
        m_skeleton = _build_maintenance_skeleton(a_goal, total_weeks)
        m_race_events = _splice_secondary_goals(
            m_skeleton, bc_goals, a_goal, effective_start
        )
        _blend_sport_splits(m_skeleton, a_goal)
        _stamp_recovery_weeks(m_skeleton, recovery_cadence=profile.recovery_cadence)
        _avoid_terminal_recovery(m_skeleton)
        plan = PhasePlan(
            weeks=[_freeze_week(w) for w in m_skeleton],
            race_events=m_race_events,
            a_goal=a_goal,
        )
        post = validate_phase_plan(plan, request)
        assert post.valid, f"Post-condition failure: {'; '.join(post.errors)}"
        if trained or joined:
            plan = _truncate_prior_weeks(plan, trained, joined)
        return plan

    # Step 1 — A-goal skeleton.
    skeleton = _build_a_skeleton(a_goal, effective_start, total_weeks, request.ambition)

    # Step 2 — Splice B/C race weeks.
    bc_race_events = _splice_secondary_goals(
        skeleton, bc_goals, a_goal, effective_start
    )

    # Step 3 — Blend sport splits at B/C race weeks.
    _blend_sport_splits(skeleton, a_goal)

    # Step 4 — Stamp recovery weeks (ambition-dependent cadence).
    _stamp_recovery_weeks(skeleton, recovery_cadence=profile.recovery_cadence)

    # Freeze.
    frozen_weeks = [_freeze_week(w) for w in skeleton]

    # A-goal race event.
    a_profile = GOAL_PROFILES[a_goal.goal_type]
    a_race_event = RaceEvent(
        event_id=f"race:a_{a_goal.goal_type.value}",
        goal=a_goal,
        week=race_week,
        day_of_week=a_goal.race_date.weekday(),
        duration_by_sport=dict(a_profile.race_duration),
    )
    all_race_events = [a_race_event, *bc_race_events]

    plan = PhasePlan(weeks=frozen_weeks, race_events=all_race_events, a_goal=a_goal)

    # Debug assertion — post-conditions hold on the full virtual plan; a
    # truncated suffix may legitimately lack e.g. two BUILD weeks.
    post = validate_phase_plan(plan, request)
    assert post.valid, f"Post-condition failure: {'; '.join(post.errors)}"

    if trained or joined:
        plan = _truncate_prior_weeks(plan, trained, joined)

    return plan


__all__ = [
    "plan_phases",
    "validate_phase_plan",
    "validate_phase_request",
]
