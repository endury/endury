"""Rules-based quality score for planner output.

Computes a composite score in [0, 1] (lower is better, 0 = every rule
satisfied) for the deterministic single-A-goal plan. Each rule emits a
bounded distance from a target band. The rules and their bands are data:
they live in the rulebook (``bench/rulebook.yaml``), each band with its
evidence. A benchmark rule reads the plan's workout cards through the
benchmark's own readings (:mod:`bench.readings`), the way it reads an AI
language model's plan; the rules that read Endury's own plan structure are
measured by the evaluators here.

By default the score is computed for COMPETITIVE across a sweep of week
horizons (``HORIZONS``) and aggregated as the per-rule mean across
horizons; ``--matrix`` sweeps every goal in ``GOAL_MATRIX_HORIZONS`` across
the ambition tiers its menu offers. This pushes changes toward plans that
improve at every plan length and offered tier, not just one.

``--replan`` adds a second axis: it scores mid-preparation *replans* (the
diff between a plan and its regeneration part-way through) against the
replan rules (:data:`REPLAN_RULES`), which the fresh sweep never sees.

Multi-goal scenarios are out of scope (sport-share targets aggregate
across the whole plan and skew under B/C races).

Usage::

    poetry run python -m devtools.quality.planner.score_quality
    poetry run python -m devtools.quality.planner.score_quality --json
    poetry run python -m devtools.quality.planner.score_quality --weeks 20  # single horizon
    poetry run python -m devtools.quality.planner.score_quality --matrix --json
    poetry run python -m devtools.quality.planner.score_quality --matrix --horizons 23,26,31
    poetry run python -m devtools.quality.planner.score_quality --replan  # replan-quality sweep
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from bench.readings import read
from bench.rules import Band, Rulebook, load_rulebook
from bench.scenarios import Athlete
from bench.scenarios import Scenario as CardScenario
from devtools.bench.endury_contestant import LONGEST_FAMILY, SPORT_LABEL, export
from devtools.planner._shared.metrics import (
    PipelineResult,
    PlanMetrics,
    _compute_metrics,
    run_full_pipeline,
)
from devtools.planner._shared.pipeline import DEFAULT_START, default_profile_for
from devtools.planner._shared.replay import (
    ReplanDiff,
    build_replan_diff,
    replay_replan,
)
from devtools.planner._shared.scenarios import Scenario
from devtools.planner._shared.sessions import build_synthetic_resolver
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.steps import coaching_notes
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    PlacedSession,
    RaceGoal,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.pipeline import PlanResult
from endury.planner.sessions.menus import BLUEPRINTS, get_session_menu
from endury.training.load import ResolverProtocol
from endury.training.workout import StructuredWorkout, WorkoutStep

# Rulebook and bands
#
# Every rule, the goals it covers, its bands and its weight live in the
# rulebook. A rule covers only the goals its evidence applies to: ``score()``
# skips the rules that do not cover the scenario's A-goal and reports them as
# ``uncovered``, so a goal is never mis-scored against another goal's bands.

RULEBOOK: Rulebook = load_rulebook()
"""Every rule the scorecard grades, from ``bench/rulebook.yaml``."""


def _goal(result: PipelineResult) -> GoalType:
    """Return the scored scenario's A-goal type (``goals[0]`` by convention)."""
    return result.scenario.goals[0].goal_type


def _band(result: PipelineResult, rule: str) -> Band:
    """Resolve *rule*'s band for the scored plan's A-goal and ambition tier."""
    goal, tier = _goal(result).value, result.scenario.ambition.value
    band = RULEBOOK.by_name(rule).band(goal, tier)
    if band is None:
        raise KeyError(f"no band for rule {rule!r} and goal {goal!r}")
    return band


# Per-rule evaluators


def _race_day_freshness_ratio(result: PipelineResult, m: PlanMetrics) -> float:
    """Race-day TSB as a fraction of CTL, vs the goal's band.

    Measured on ``race_day_s_tsb``: the COMBINED (unweighted-sum) PMC, in
    weekly-TSS units (Banister-internal), divided by 7 for the conventional
    daily TSB. That is the chart the athlete's trends view plots and the
    metric the literature's TSB zones are stated for. (The sport-WEIGHTED
    composite is an *average* of per-sport TSBs with no external referent,
    ~2.7x lower than real form for a 3-discipline athlete; a band fitted in
    that space locked in the mismatch.)

    Why a ratio and not an absolute daily-TSB zone:

    1. **Instant.** The classic +15..+25 zone is stated for the state
       *entering* race week; this reads the race-day instant, after ATL has
       collapsed across the taper week, where most plans read
       above that zone.
    2. **Shape.** TSB <= CTL, so a low-CTL athlete cannot reach +15 at all;
       any absolute band is unreachable for small goals.

    TSB/CTL is scale-free, so it survives goal parameterisation and needs
    no ambition scaling — the tier signal is already carried by CTL. The
    band is per-goal where plans were measured, with the cross-goal
    envelope as the rule's fallback band.
    """
    ctl = m.race_day_s_ctl
    if ctl <= 0.0:
        return 0.0
    return _band(result, "race_day_freshness_ratio").distance(m.race_day_s_tsb / ctl)


def _weekly_total(m: PlanMetrics) -> dict[int, float]:
    """Return per-week total hours across all sports."""
    return {wk: sum(h.values()) for wk, h in m.weekly_hours.items()}


def _recovery_week_test_frequency(result: PipelineResult, _m: PlanMetrics) -> float:
    """Share of test occasions carrying a fitness test, vs the band.

    The occasions are the RECOVERY weeks, plus an opening week that tests:
    a plan that sets its zones in week 1 has tested once more than its
    recovery weeks show (2026-10-11). Measures test frequency rather than
    a TSB-rebound floor or a one-test-per-recovery-week cap: recovery weeks mostly do
    not rebound, and the plans that test at all
    repeat the same weekday pattern regardless of a cap.

    **Thin evidence:** two readings, and a plan may
    legitimately prescribe no tests at all — so the rule is inert when a
    plan has no TEST-family sessions anywhere, rather than scoring that
    choice as a defect.
    """
    ensure_registry_loaded()
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    test_weeks = {
        s.week
        for s in result.sessions
        if get_activity(s.activity_key).family == ActivityFamily.TEST
    }
    if not test_weeks:
        return 0.0
    occasions = [wk for wk, ws in plan_weeks.items() if ws.mode == WeekMode.RECOVERY]
    first = min(plan_weeks)
    if first in test_weeks and first not in occasions:
        # A plan that opens with its tests (the test week that sets the
        # zones every card is written against) has tested once more than
        # its recovery weeks show.
        occasions.append(first)
    if not occasions:
        return 0.0
    share = sum(1 for wk in occasions if wk in test_weeks) / len(occasions)
    return _band(result, "recovery_week_test_frequency").distance(share)


def _quality_minutes(session: PlacedSession, resolver: ResolverProtocol) -> float:
    """Minutes at or above tempo (z_mid + z_high) in one placed session.

    Measured through the activity's own TID rather than its family, for
    two reasons that both cut against a family filter. Ironman taper
    menus carry their intensity in ``BIKE_OPENER`` — family ``EASY``,
    ``if_work=1.30`` — which a family filter scores as zero quality; and other plans
    carry theirs in whole THRESHOLD-family sessions that
    are mostly Z2 around the work interval, which a family filter counts
    in full. TID reads both the same way: the minutes actually spent above
    the sport's LT1 (``TID_CUTS``: IF 0.85 for a run, 0.75 for a ride, 0.90
    for a swim), whatever the session is named.
    """
    hours = session.target_hours
    if hours <= 0.0:
        return 0.0
    act = get_activity(session.activity_key)
    tss = assignment_target_tss(act, hours, resolver)
    tid = act.tid_fractions(tss, resolver)
    return (tid.z_mid + tid.z_high) * hours * 60.0


def _taper_quality_share(result: PipelineResult, m: PlanMetrics) -> float:
    """Taper quality minutes / peak-week quality minutes, vs the goal's band.

    The third of Bosquet et al. 2007's factors, and the one
    :func:`_taper_session_frequency` explicitly does not measure.
    Maintaining intensity carried its own effect (0.33 +/- 0.14, p<0.001)
    independent of the 41-60 % volume cut; docs/research/triathlon_long_course.md,
    Taper Protocol puts it as "intensity maintained in short touches".

    The upper bound is where the band stops rather than a penalty
    for training hard: a plan that holds all of its quality minutes through
    the taper scores 0. The 0.50 ramp keeps the rule from saturating
    anywhere in the goal catalogue, so a bad taper still ranks against a
    worse one.

    **The marathon stays deliberately uncalibrated.** Marathon-pace running
    sits exactly on ``TID_LOW_IF_THRESHOLD`` (IF 0.85) and reads zero
    quality minutes with this evaluator, so one reading is unmeasurable here and the
    other is low-confidence; one
    unmeasurable + one low-confidence reading is not a band, so the
    marathon falls back to the rule's fallback band.
    """
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    totals = _weekly_total(m)
    load_weeks = [
        k for k, ws in plan_weeks.items() if ws.mode == WeekMode.LOAD and k in totals
    ]
    taper_weeks = [
        k for k, ws in plan_weeks.items() if ws.mode == WeekMode.TAPER and k in totals
    ]
    if not load_weeks or not taper_weeks:
        return 0.0

    ensure_registry_loaded()
    resolver = build_synthetic_resolver()
    by_week: dict[int, float] = {}
    for s in result.sessions:
        by_week[s.week] = by_week.get(s.week, 0.0) + _quality_minutes(s, resolver)

    # Peak by hours, as _taper_session_frequency does.  Menu caps routinely
    # tie several weeks at the identical peak volume, so resolve ties in the
    # plan's favour: the tied week with the fewest quality minutes.
    peak_hours = max(totals[k] for k in load_weeks)
    tied = [k for k in load_weeks if totals[k] >= peak_hours - 1e-9]
    peak_quality = min(by_week.get(k, 0.0) for k in tied)
    if peak_quality < 1.0:
        # A peak week with no quality at all is a different defect, owned
        # by the intensity-distribution rules; the ratio is meaningless.
        return 0.0
    mean_taper = sum(by_week.get(k, 0.0) for k in taper_weeks) / len(taper_weeks)
    return _band(result, "taper_quality_share").distance(mean_taper / peak_quality)


_RACE_PACE_RIDE_KEYS: dict[GoalType, ActivityKey] = {
    GoalType.IRONMAN: ActivityKey.BIKE_LONG_IM_RACE_PACE,
    GoalType.HALF_IRONMAN: ActivityKey.BIKE_LONG_HIM_RACE_PACE,
}
"""Per-goal race-pace rehearsal ride: the activity key that carries it.

Its duration band is ``race_pace_long_bike_peak`` in the rulebook. A half
plan with no ``BIKE_LONG_HIM_RACE_PACE`` session scores the
full penalty rather than falling back to the Ironman key.
"""


def _race_pace_long_bike_peak(result: PipelineResult, _m: PlanMetrics) -> float:
    """Longest race-pace long ride vs the goal's key + band.

    Distinct from ``long_ride_peak``, which credits the single longest ride of
    ANY type — so a higher-intensity sweet-spot ride can satisfy it while the
    race-PACE durability rehearsal stays too short (e.g. a 4.1 h sweet-spot
    ride masking a race-pace ride that tops out at 3.8 h and then shrinks in
    PEAK). Zero race-pace rides scores the full 1.0 (no race-pace rehearsal
    at all).
    """
    key = _RACE_PACE_RIDE_KEYS[_goal(result)]
    rides = [s.target_hours for s in result.sessions if s.activity_key == key]
    return _band(result, "race_pace_long_bike_peak").distance(max(rides, default=0.0))


def _load_pricing_agreement(result: PipelineResult, _m: PlanMetrics) -> float:
    """Worst week's gap between the card TSS and the TSS the QP sized, vs the band.

    Sizing and cards must speak the same load, or the freshness the QP
    targets is not the freshness the athlete sees. The card side prices each
    session by its own activity at its solved hours; the sized side is the
    QP's frozen coefficient (``PlanResult.session_tss_per_hour``, evaluated
    at the menu target) times those same hours. A healthy plan's residual is
    only the linearisation at the menu target plus the 5-minute rounding of
    solved hours. A large reading means a session was sized outside its
    activity's catalog TSS window, so the card silently resized it (the
    "bounds pushed past the catalog TSS window" trap in the menus'
    AUTHORING.md).
    """
    card: dict[int, float] = {}
    sized: dict[int, float] = {}
    for s in result.sessions:
        card[s.week] = card.get(s.week, 0.0) + (
            session_tss_per_hour(s.activity_key, s.target_hours) * s.target_hours
        )
        sized[s.week] = (
            sized.get(s.week, 0.0)
            + result.session_tss_per_hour[s.slot_id] * s.target_hours
        )
    gaps = [abs(card[k] / sized[k] - 1.0) for k in card if sized[k] > 0]
    if not gaps:
        return 0.0
    return _band(result, "load_pricing_agreement").distance(max(gaps))


def _vo2max_peak_taper(result: PipelineResult, _m: PlanMetrics) -> float:
    """INTERVAL sessions in PEAK/TAPER, vs a band of zero (see docs/research/triathlon_long_course.md, The VO2max Window).

    Long-course VO2max work belongs in early BUILD; PEAK and TAPER want
    only brief race-pace openers (3-6 x 30 s), never structured intervals.
    The rulebook's ``vo2max_in_base`` rule scans only BASE, so it is
    structurally blind to intervals that survive into PEAK/TAPER. Distance
    ramps to 1.0 by two such sessions.
    """
    ensure_registry_loaded()
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    count = sum(
        1
        for s in result.sessions
        if s.week in plan_weeks
        and plan_weeks[s.week].phase in (PhaseType.PEAK, PhaseType.TAPER)
        and get_activity(s.activity_key).family == ActivityFamily.INTERVAL
    )
    return _band(result, "vo2max_peak_taper").distance(count)


def _longest_long_session_in_peak(result: PipelineResult, _m: PlanMetrics) -> float:
    """Phase of the longest long bike/run/swim. Target: PEAK (`docs/specs/planner/behavioral.md`, R-PP-002; docs/research/triathlon_long_course.md, Long Session Benchmarks).

    R-PP-002 requires each sport's longest long session to land
    in PEAK, ~4-6 weeks before the race. The sibling ``peak_long_timing``
    rule measures only weeks-to-race and is blind to WHICH phase the max lives
    in — it reads 0.0 on ironman_moderate where the longest bike/run peak in
    mid-BUILD yet still fall ~6 weeks out. This rule measures the phase offset
    of the single longest LONG-family session per sport (0 in PEAK/TAPER, 0.5
    in BUILD, 1.0 in BASE), worst of cycling, running and swimming. No long
    sessions scores the full 1.0.

    Ties are resolved in the plan's favour: menu caps routinely make several
    instances of a sport's long session share the exact peak duration (e.g.
    three 1.75 h swims across BUILD and PEAK), and R-PP-002 is satisfied when
    a session OF that peak duration occurs in PEAK. Picking the first maximal
    element instead made the verdict depend on list order, so an unrelated
    QP nudge could flip a compliant plan to a 0.5 violation.
    """
    ensure_registry_loaded()
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    phase_offset = {
        PhaseType.PEAK: 0,
        PhaseType.TAPER: 0,
        PhaseType.BUILD: 1,
        PhaseType.BASE: 2,
    }
    band = _band(result, "longest_long_session_in_peak")
    distances: list[float] = []
    for sport in (SportType.CYCLING, SportType.RUNNING, SportType.SWIMMING):
        sport_longs = [
            s
            for s in result.sessions
            if s.sport == sport
            and get_activity(s.activity_key).family == ActivityFamily.LONG
            and s.week in plan_weeks
        ]
        if not sport_longs:
            continue
        peak_hours = max(s.target_hours for s in sport_longs)
        offset = min(
            phase_offset.get(plan_weeks[s.week].phase, 2)
            for s in sport_longs
            if s.target_hours >= peak_hours - 1e-6
        )
        distances.append(band.distance(offset))
    if not distances:
        return 1.0
    return max(distances)


_BRICK_RUN_KEYS = frozenset(
    {
        ActivityKey.RUN_BRICK_SHORT,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_BRICK_IM_TEMPO,
        ActivityKey.RUN_BRICK_HIM_TEMPO,
    }
)


def _build_brick_run_duration(result: PipelineResult, _m: PlanMetrics) -> float:
    """Longest BUILD brick-run vs the band (see docs/research/triathlon.md, Brick Sessions: BUILD bricks 20-40 min).

    Off-the-bike durability running is the specific race skill BUILD should
    rehearse, so this rule measures the longest BUILD-phase brick-run (total
    minutes) against the top of the 20-40 min band: a flat 25-min BUILD
    brick reads ~0.53; a plan whose BUILD brick reaches ~35-40 min reads 0.
    Complements ``longest_long_session_in_peak``, which ignores bricks.
    """
    ensure_registry_loaded()
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    build_brick = [
        s.target_hours * 60.0
        for s in result.sessions
        if s.activity_key in _BRICK_RUN_KEYS
        and s.week in plan_weeks
        and plan_weeks[s.week].phase == PhaseType.BUILD
    ]
    if not build_brick:
        return 1.0
    return _band(result, "build_brick_run_duration").distance(max(build_brick))


def _flatten_leaf_types(steps: list[WorkoutStep]) -> list[str]:
    """Flatten a workout step tree into an ordered list of leaf step types.

    Repeat groups are expanded ``iterations`` times so the returned order
    reflects the sequence an athlete actually executes.

    Args:
        steps: Workout steps, possibly containing nested ``repeat`` groups.

    Returns:
        Ordered leaf step types (``warmup`` / ``interval`` / ``recovery`` /
        ``cooldown`` / ...).
    """
    out: list[str] = []
    for step in steps:
        if step.type == "repeat" and step.steps:
            for _ in range(step.iterations or 1):
                out.extend(_flatten_leaf_types(step.steps))
        else:
            out.append(step.type)
    return out


def _ends_on_easy_block(workout: StructuredWorkout) -> bool:
    """Return True if the last non-cooldown leaf of a workout is a recovery block.

    Args:
        workout: Built structured workout.

    Returns:
        True when the final work-bearing block is a recovery/easy step (the
        session fades to its easiest effort), False when it ends on work.
    """
    leaves = _flatten_leaf_types(workout.steps)
    while leaves and leaves[-1] == "cooldown":
        leaves.pop()
    return bool(leaves) and leaves[-1] == "recovery"


def _race_pace_ride_ends_at_effort(result: PipelineResult, _m: PlanMetrics) -> float:
    """The goal's race-pace long ride finishes AT race effort, not on easy Z2.

    Progressive long sessions — finishing the last 30-60 minutes of a long
    ride or run at race pace — train the specific ability to produce quality
    output in a fatigued state (docs/research/triathlon_long_course.md,
    Training Durability). Guards against ``BIKE_LONG_IM_RACE_PACE``
    interleaving each race-pace block with a longer Z2 recovery and ending
    every rep on that recovery, which would finish the whole ride on its
    easiest 55-min block before cooldown — the opposite of a race-day dress
    rehearsal (``end_on_work=True`` keeps it ending on work). This rule
    builds the plan's longest race-pace ride for the goal's key
    (``_RACE_PACE_RIDE_KEYS``) and returns 1.0 if it ends on a recovery
    block, 0.0 if it ends at race pace — a yes/no property, so it reads no
    band. Reads 0.0 when the plan carries no such ride — the
    missing-rehearsal defect is ``race_pace_long_bike_peak``'s to flag, not
    this rule's.
    """
    ensure_registry_loaded()
    key = _RACE_PACE_RIDE_KEYS[_goal(result)]
    rides = [s for s in result.sessions if s.activity_key == key]
    if not rides:
        return 0.0
    peak = max(rides, key=lambda s: s.target_hours)
    activity = get_activity(peak.activity_key)
    workout = activity.build(activity.tss_max)
    return 1.0 if _ends_on_easy_block(workout) else 0.0


#: Long/brick run keys whose 75'+ renders must carry a fueling cue —
#: ``steps._LONG_RUN_FUELING_KEYS`` read from the outside. Kept as a local
#: literal rather than an import of the private name so the rule fails when
#: production narrows the set, instead of silently narrowing with it.
_LONG_RUN_FUEL_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_LONG,
        ActivityKey.RUN_LONG_PROGRESSION,
        ActivityKey.RUN_LONG_WITH_MP,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_TRAIL_LONG,
        ActivityKey.RUN_TRAIL_B2B,
    }
)

#: Note prefixes that count as a fueling cue: the goal-agnostic long-run
#: cue and the Ironman run-leg cue that replaces it on IM plans.
_FUELING_CUE_PREFIXES: tuple[str, ...] = (
    "<b>Fueling:",
    "<b>Long-run cadence:",
)


def _run_long_fueling_cue_coverage(result: PipelineResult, _m: PlanMetrics) -> float:
    """Share of 75'+ long/brick runs whose rendered notes carry a fueling cue.

    Target: 1.0 — every qualifying run. Distance is ``1 - coverage`` on a
    unit ramp, so one uncued run in four reads 0.25.

    Not a measured band: training plans encode session shape and volume,
    never coaching-cue text. The target is the codebase's own contract
    (fueling / hydration cadence on long bike AND run sessions,
    ``endury/planner/activity/steps.py``; `docs/specs/planner/behavioral.md`,
    R-SC-014) plus the 60-90 g/h race-intensity fueling standard.

    Guards against ``_IM_RUN_LEG_GOALS`` gating the run-nutrition note to
    ``GoalType.IRONMAN`` only: a half-Ironman's structurally identical
    90-112 min PEAK long run would then render no fueling cue at all,
    while every 75'+ *bike* on the same plan carries one. Reads 0.0 on
    every current matrix goal.

    Read through ``coaching_notes`` itself rather than re-deriving the
    condition, so the rule cannot drift from the production text: the
    call mirrors the serializer's (``flat_steps=[]``, the plan's A-goal),
    and only the *notes* are inspected. ``flat_steps`` is empty because it
    routes brick warmup wording only, never the fueling branch.

    Vacuous case: a plan with no qualifying run (every running goal below
    the marathon, and any triathlon plan whose long runs stay under
    75 min) has nothing to cover and returns 0.0 rather than a
    manufactured defect.
    """
    goal = _goal(result)
    qualifying = [
        s
        for s in result.sessions
        if s.sport == SportType.RUNNING
        and s.activity_key in _LONG_RUN_FUEL_KEYS
        and s.target_hours * 60.0 >= 75.0
    ]
    if not qualifying:
        return 0.0
    covered = 0
    for session in qualifying:
        notes = coaching_notes(
            session,
            duration_min=session.target_hours * 60.0,
            total_meters=0.0,
            flat_steps=[],
            goal=goal,
        )
        if any(n.startswith(_FUELING_CUE_PREFIXES) for n in notes):
            covered += 1
    return _band(result, "run_long_fueling_cue_coverage").distance(
        covered / len(qualifying)
    )


#: The zone-band phrase ``shapes/base.py`` renders for VO2max-band work.
_VO2MAX_STEP_PHRASE = "VO₂max"

#: Reps at or below this length are sprints/strides, not VO2max work —
#: already routed to sprint wording elsewhere.
_VO2MAX_MIN_WORK_S = 30.0


def _expand_leaf_steps(steps: list[WorkoutStep]) -> list[WorkoutStep]:
    """Flatten a workout step tree into its ordered leaf steps.

    Repeat groups are expanded ONCE (not ``iterations`` times): the
    work/rest ratio of a rep is identical in every repetition, and one
    pass keeps the returned list proportional to the authored structure.

    Args:
        steps: Workout steps, possibly containing nested ``repeat`` groups.

    Returns:
        Ordered leaf steps, repeat wrappers removed.
    """
    out: list[WorkoutStep] = []
    for step in steps:
        if step.type == "repeat" and step.steps:
            out.extend(_expand_leaf_steps(step.steps))
        else:
            out.append(step)
    return out


def _step_seconds(step: WorkoutStep) -> float:
    """Return a step's time duration in seconds (0.0 when not time-based)."""
    if step.duration is None or step.duration.value_seconds is None:
        return 0.0
    return float(step.duration.value_seconds)


def _vo2max_session_work_rest_ratio(result: PipelineResult, _m: PlanMetrics) -> float:
    """Worst rest:work ratio among the plan's rendered VO2max reps.

    Classical VO2max work is 3-5 min at 95-105 % with roughly equal
    recovery, and the codebase's own correctly-built siblings set the
    standard (training plans encode session shape, never rep-level
    interval structure). Every VO2max-labelled rep the matrix actually
    renders, measured through this rule's own code path (each key built
    at the median session TSS of the plan that prescribes it):

    ================ ====== ========= ========= ====== ======================
    Activity            TSS  Work (s)  Rest (s)  Ratio  Prescribed by
    ================ ====== ========= ========= ====== ======================
    ``BIKE_VO2MAX``    60.4     264.0     180.0  0.682  ironman COMPETITIVE
    ``BIKE_VO2MAX``    55.3     300.0     180.0  0.600  ironman MODERATE
    ``RUN_FARTLEK``    56.8     180.0     120.0  0.667  half-marathon COMP
    ``RUN_VO2MAX``     44.9     180.0     180.0  1.000  half-marathon BASIC
    ================ ====== ========= ========= ====== ======================

    Worst reading anywhere in the matrix: 1.000, two thirds of a band
    width inside the edge. Swim VO2max reps are distance-based (no
    ``value_seconds``) and carry no time ratio to read, so the rule is
    silent on them by construction.

    Regression guard for the 30-90 s short-rep carve-out
    (`docs/specs/planner/behavioral.md`, R-SC-015).
    ``RUN_SPEED`` carries 0.5-1.0 min reps behind a fixed 3 min recovery —
    rest:work 3.0-6.0 — which would render the "VO₂max effort — hard, near
    maximal sustainable" cue on a protocol physiologically closer to a
    speed/SIT session, if the short-rep carve-out did not also cover
    30-90 s reps: those read "Speed rep" instead, so the rule finds no
    VO2max-labelled rep with a 3:1 recovery and reads 0.0. Should a menu
    reintroduce that mismatch, the ratio — not the wording — is what
    fires here, which is why this rule reads the built steps rather than
    the catalog fields alone. (Half-Ironman, sprint and olympic prescribe
    ``RUN_SPEED`` as their only interval slot, so those cells stay vacuous
    only while the wording carve-out holds; the "VO2max effort" wording on
    these reps would make the rule read 1.0 on every one of them.)

    Each DISTINCT interval-family key in the plan is built once, at that
    key's median session TSS (the same duration-first materialization the
    session cards render), and its rendered steps inspected: an interval
    step whose description carries the VO2max band phrase and whose work
    rep exceeds ``_VO2MAX_MIN_WORK_S`` is scored against the recovery step
    that follows it. Plans with no such rep read 0.0 — the missing-VO2max
    defect belongs to ``vo2max_in_base``, not here.
    """
    ensure_registry_loaded()
    resolver = build_synthetic_resolver()
    by_key: dict[ActivityKey, list[float]] = {}
    for s in result.sessions:
        act = get_activity(s.activity_key)
        if act.family == ActivityFamily.INTERVAL:
            by_key.setdefault(s.activity_key, []).append(s.target_hours)
    worst = 0.0
    found = False
    for key, hours in by_key.items():
        act = get_activity(key)
        median_hours = sorted(hours)[len(hours) // 2]
        workout = act.build(
            assignment_target_tss(act, median_hours, resolver), resolver
        )
        leaves = _expand_leaf_steps(workout.steps)
        for i, step in enumerate(leaves[:-1]):
            work_s = _step_seconds(step)
            if (
                step.type != "interval"
                or _VO2MAX_STEP_PHRASE not in (step.description or "")
                or work_s <= _VO2MAX_MIN_WORK_S
            ):
                continue
            following = leaves[i + 1]
            if following.type not in ("recovery", "rest"):
                continue
            found = True
            worst = max(worst, _step_seconds(following) / work_s)
    if not found:
        return 0.0
    return _band(result, "vo2max_session_work_rest_ratio").distance(worst)


#: Race-tempo brick runs: the ones doctrine chains to the key ride.
_TEMPO_BRICK_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_BRICK_HIM_TEMPO,
        ActivityKey.RUN_BRICK_IM_TEMPO,
    }
)


def _brick_pairs_with_key_ride(result: PipelineResult, _m: PlanMetrics) -> float:
    """Share of race-tempo bricks chained to their week's longest ride.

    Target: 1.0 — every race-tempo brick's ``brick_partner`` resolves to
    the longest cycling session of its own week. Distance is
    ``1 - coverage``.

    **Doctrine-asserted, not measurable on a plan**: plans encode
    which sessions exist on which day, never which ride a brick was
    authored behind. The band is therefore doctrine ("PEAK bricks =
    race-duration bike + race-pace run", `docs/research/triathlon.md`,
    Brick Sessions, and `docs/research/triathlon_long_course.md`, Key
    Session Types by Phase), the same citations the half-Ironman menus'
    own rationale text already carries. A rule with an asserted band is
    held to the *stricter* standard of naming its doctrine.

    The defect it guards: ``half_ironman.py``'s rationale for
    ``him_peak_sun_brick`` states "this brick follows Sunday's endurance
    ride, not Saturday's race-pace long ride" — so without the chaining
    check, the plan's marquee race-pace ride can go without a run
    attached, while the brick sits behind an easy spin instead.
    ``build_brick_run_duration`` and ``race_pace_ride_ends_at_effort``
    each grade one session in isolation; neither can see the chaining.

    Scored over LOAD/RECOVERY weeks only. A TAPER-week brick is
    deliberately a short race-simulation touch behind a short spin
    (`docs/research/triathlon_long_course.md`, Taper Protocol), not a
    chaining defect, and the cited doctrine is a BUILD/PEAK statement.
    Plans with no race-tempo brick in a training
    week (every non-long-course goal, and the BASIC/MODERATE Ironman
    tiers, whose PEAK menus prescribe none) read 0.0 rather than a
    manufactured defect.
    """
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    by_slot = {s.slot_id: s for s in result.sessions}
    bricks = [
        s
        for s in result.sessions
        if s.activity_key in _TEMPO_BRICK_KEYS
        and s.week in plan_weeks
        and plan_weeks[s.week].mode in (WeekMode.LOAD, WeekMode.RECOVERY)
    ]
    if not bricks:
        return 0.0
    hits = 0
    for brick in bricks:
        rides = [
            s.target_hours
            for s in result.sessions
            if s.week == brick.week and s.sport == SportType.CYCLING
        ]
        if not rides:
            continue
        partner = by_slot.get(brick.brick_partner) if brick.brick_partner else None
        if (
            partner is not None
            and partner.sport == SportType.CYCLING
            and partner.target_hours >= max(rides) - 1e-6
        ):
            hits += 1
    return _band(result, "brick_pairs_with_key_ride").distance(hits / len(bricks))


def _cross_sport_hard_day_stacking(result: PipelineResult, _m: PlanMetrics) -> float:
    """Share of weeks with a day carrying hard sessions from two sports.

    Hardness is the activity's own ``hardness.is_hard_physiologically``
    flag — the same predicate the ``hard_adjacent_days`` menu lint keys on
    (`docs/specs/planner/session_generator.md`, R-SESS-025). That lint
    groups hard days BY SPORT before
    checking adjacency, so it is structurally blind to one day carrying two
    hard sessions from two DIFFERENT sports: ``day_capacity=2`` permits it
    outright as long as it is not the same sport twice.

    Goal-scoped, because full-distance programs carry more sessions across
    the same seven days: full-distance plans stack more often than
    the half-distance band allows, so condemning them against half evidence
    would be a goal-direction failure, and widening one shared band would
    have loosened the half band the finding rests on.

    Scored over every week of the plan, the population the bands were
    measured over. Unlike a volume property, day-level stacking does not
    become legitimate in a taper — two hard venues on one day is the same
    scheduling fact in week 2 and week 15 — so no week mode is excluded.
    """
    ensure_registry_loaded()
    plan_weeks = {ws.week: ws for ws in result.plan.weeks}
    if not plan_weeks:
        return 0.0
    hard_sports: dict[tuple[int, int], set[SportType]] = {}
    for s in result.sessions:
        if s.week not in plan_weeks or s.day_of_week is None:
            continue
        if get_activity(s.activity_key).hardness.is_hard_physiologically:
            hard_sports.setdefault((s.week, s.day_of_week), set()).add(s.sport)
    stacked = {wk for (wk, _day), sports in hard_sports.items() if len(sports) > 1}
    return _band(result, "cross_sport_hard_day_stacking").distance(
        len(stacked) / len(plan_weeks)
    )


#: The sessions that carry a trail week's vertical work: sustained climbing,
#: uphill intervals and descent repeats (docs/research/trail_running.md §7, §9).
_VERT_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_VERT_HIKE,
        ActivityKey.RUN_HILL_REPEATS,
        ActivityKey.RUN_DOWNHILL_REPEATS,
    }
)

#: The final weeks before race week that concentrate vertical work
#: (docs/research/trail_running.md §7.3: the final 8 weeks are its window).
_VERT_WINDOW_WEEKS = 8


def _vert_session_coverage(result: PipelineResult, _m: PlanMetrics) -> float:
    """Share of the last eight weeks' load and taper weeks with a vertical session.

    Vertical adaptation is "easy-come-easy-go": the final eight weeks are its
    window (docs/research/trail_running.md §7.3), and terrain and vertical
    specificity are kept unchanged through the taper (§11.3). So every LOAD
    and TAPER week among the eight before race week carries a climbing or
    descent session. Recovery weeks shed their climbing by design and the
    race week is not graded. Distance is ``1 - coverage``.
    """
    race_week = max(
        (re.week for re in result.plan.race_events),
        default=len(result.plan.weeks) - 1,
    )
    graded = {
        ws.week
        for ws in result.plan.weeks
        if race_week - _VERT_WINDOW_WEEKS <= ws.week < race_week
        and ws.mode in (WeekMode.LOAD, WeekMode.TAPER)
    }
    if not graded:
        return 0.0
    covered = graded & {s.week for s in result.sessions if s.activity_key in _VERT_KEYS}
    return _band(result, "vert_session_coverage").distance(len(covered) / len(graded))


def _easier_week_long_run(result: PipelineResult, _m: PlanMetrics) -> float:
    """Each easier week's long run against the longest long run before it.

    Only one or two long runs a month sit at the athlete's ceiling; the rest
    sit at 50-75 % of it (docs/research/trail_running.md §5.3, §13.5). A
    RECOVERY or TAPER week's long run is one of the rest, so its ratio to the
    longest run the plan has booked before it should fall in that band:
    above it the easier week is not easier, below it the long day is starved.
    Scored as the mean band distance over the easier weeks that follow a
    long run, so one off-band week counts for its share. Reads 0.0 when no
    easier week holds a long run after one.
    """
    ensure_registry_loaded()
    modes = {ws.week: ws.mode for ws in result.plan.weeks}
    longest: dict[int, float] = {}
    for s in result.sessions:
        if (
            s.sport == SportType.RUNNING
            and get_activity(s.activity_key).family == ActivityFamily.LONG
        ):
            longest[s.week] = max(longest.get(s.week, 0.0), s.target_hours)
    band = _band(result, "easier_week_long_run")
    distances = [
        band.distance(hours / max(h for w, h in longest.items() if w < week))
        for week, hours in longest.items()
        if modes.get(week) in (WeekMode.RECOVERY, WeekMode.TAPER)
        and any(w < week for w in longest)
    ]
    if not distances:
        return 0.0
    return sum(distances) / len(distances)


def _race_rehearsal_duration(result: PipelineResult, _m: PlanMetrics) -> float:
    """The shortest race-rehearsal long run, vs the four-hour floor.

    Four hours is the least a long run can be and still rehearse race
    fuelling (docs/research/trail_running.md §5.4, §12.3): under it the
    session trains durability, not the gut. The rehearsal is the PEAK LOAD
    menu's ``race_sim`` long run, found by its slot name. A plan that holds
    none reads the rule's ``missing`` distance, a rehearsal of three hours
    or less: the rehearsal gate (session_generator R-SESS-023) skips it when
    the week or the long run cannot hold it, and a plan without a rehearsal
    is no better than one whose rehearsal is short.
    """
    ensure_registry_loaded()
    names = {
        spec.slot_name
        for spec in get_session_menu(
            _goal(result), PhaseType.PEAK, "load", result.scenario.ambition
        )
        if spec.race_sim
        and get_activity(spec.activity_key).family == ActivityFamily.LONG
    }
    rehearsals = [s.target_hours for s in result.sessions if s.slot_name in names]
    if not rehearsals:
        return RULEBOOK.by_name("race_rehearsal_duration").missing
    return _band(result, "race_rehearsal_duration").distance(min(rehearsals))


# Rule registry

Evaluator = Callable[[PipelineResult, PlanMetrics], float]

EVALUATORS: dict[str, Evaluator] = {
    "race_day_freshness_ratio": _race_day_freshness_ratio,
    "recovery_week_test_frequency": _recovery_week_test_frequency,
    "taper_quality_share": _taper_quality_share,
    "race_pace_long_bike_peak": _race_pace_long_bike_peak,
    "load_pricing_agreement": _load_pricing_agreement,
    "vo2max_peak_taper": _vo2max_peak_taper,
    "longest_long_session_in_peak": _longest_long_session_in_peak,
    "build_brick_run_duration": _build_brick_run_duration,
    "race_pace_ride_ends_at_effort": _race_pace_ride_ends_at_effort,
    "run_long_fueling_cue_coverage": _run_long_fueling_cue_coverage,
    "vo2max_session_work_rest_ratio": _vo2max_session_work_rest_ratio,
    "brick_pairs_with_key_ride": _brick_pairs_with_key_ride,
    "cross_sport_hard_day_stacking": _cross_sport_hard_day_stacking,
    "vert_session_coverage": _vert_session_coverage,
    "easier_week_long_run": _easier_week_long_run,
    "race_rehearsal_duration": _race_rehearsal_duration,
}
"""The rules that read Endury's own plan structure, by rulebook name.

Each returns a distance in [0, 1] for the current ``PipelineResult`` and
``PlanMetrics``. Every other rule of the rulebook reads the workout cards
(:func:`_card_distances`).
"""


def check_rulebook(rulebook: Rulebook) -> None:
    """Assert the rulebook and :data:`EVALUATORS` agree exactly.

    Every rule the benchmark cannot read (``benchmark: false``) needs an
    evaluator here, and every evaluator a rule: one without the other would
    be a rule that is never measured or a measurement no band grades. Run at
    import, so drift between the YAML and the registry is impossible. That
    every rule finds a band for every goal it covers is the rulebook
    loader's own check.

    Args:
        rulebook: The loaded rulebook.

    Raises:
        AssertionError: On a disagreement.
    """
    own = {rule.name for rule in rulebook.rules if not rule.benchmark}
    assert own == set(EVALUATORS), (
        f"rules without an evaluator {sorted(own - set(EVALUATORS))}, "
        f"evaluators without a rule {sorted(set(EVALUATORS) - own)}"
    )


check_rulebook(RULEBOOK)
assert {goal.value for goal in _RACE_PACE_RIDE_KEYS} == set(
    RULEBOOK.by_name("race_pace_ride_ends_at_effort").goals
), "the race-pace ride keys and that rule's goals disagree"


def _card_distances(result: PipelineResult) -> dict[str, float]:
    """Distance per benchmark rule, read off the plan's workout cards.

    The plan is exported into the benchmark's plan table and read by the
    benchmark's own readings, so a rule measures Endury's plan the way it
    measures any contestant's. A reading the plan does not carry (no taper
    week in a sprint plan, no VO2max set) scores 0: the benchmark's
    ``missing`` distance is for a contestant's plan that lacks the feature
    altogether, which the phase planner does not produce.

    Args:
        result: The scored pipeline run.

    Returns:
        A distance for every benchmark rule that covers the plan's goal.
    """
    scenario = result.scenario
    goal, tier = _goal(result), scenario.ambition
    athlete = scenario.to_request().athlete or default_profile_for(goal, tier)
    sports = {
        SPORT_LABEL[sport]: state
        for sport, state in athlete.sports.items()
        if sport in SPORT_LABEL
    }
    recent = Athlete(
        "scorecard",
        {label: state.recent_weekly_hours for label, state in sports.items()},
        {
            label: state.longest_recent_min.get(LONGEST_FAMILY[label], 0)
            for label, state in sports.items()
        },
    )
    plan = export(
        PlanResult(result.plan, result.sessions, result.assignments),
        CardScenario(
            scenario.name, goal.value, tier.value, len(result.plan.weeks), recent
        ),
    )
    readings, _ = read(plan, recent.hours, recent.longest_min)
    distances: dict[str, float] = {}
    for rule, band in RULEBOOK.applicable(goal.value, tier.value):
        value = readings.get(rule.reading or "")
        distances[rule.name] = band.distance(value) if value is not None else 0.0
    return distances


@dataclass(slots=True)
class QualityReport:
    """One scoring run.

    Attributes:
        composite: Weighted mean of per-rule distances, in [0, 1].
        per_rule: Distance per rule, keyed by its rulebook name, covered
            rules only.
        raw: Underlying ``PlanMetrics`` for ad-hoc inspection.
        uncovered: Rules skipped because they do not cover the scenario's
            goal type (reported so a goal is never silently mis-scored).
    """

    composite: float
    per_rule: dict[str, float]
    raw: PlanMetrics
    uncovered: tuple[str, ...] = ()


def _weighted_composite(per_rule: dict[str, float]) -> float:
    """Weighted-mean composite over the rules present in *per_rule*."""
    weights = {r.name: r.weight for r in RULEBOOK.rules if r.name in per_rule}
    weight_sum = sum(weights.values())
    return sum(w * per_rule[name] for name, w in weights.items()) / weight_sum


def score(scenario: Scenario) -> QualityReport:
    """Run the pipeline for *scenario* and compute the composite score.

    Args:
        scenario: The pipeline scenario to evaluate.

    Returns:
        ``QualityReport`` with composite, per-rule distances, and raw metrics.

    Raises:
        ValueError: Propagated from the macro QP when the scenario is
            infeasible. The loop interprets this as a hard rejection.
            Also raised for maintenance scenarios: every rule band is
            calibrated to race preparation, so scoring a raceless
            maintenance block would emit meaningless numbers.
    """
    goal = scenario.goals[0].goal_type
    if is_maintenance_goal(goal):
        raise ValueError(
            f"score_quality is race-prep-only; scenario {scenario.name!r} "
            "is a maintenance block (no taper/race to grade)."
        )
    result = run_full_pipeline(scenario, use_macro_solver=True)
    metrics = _compute_metrics(result)
    cards = _card_distances(result)
    per_rule = {
        rule.name: (
            cards[rule.name]
            if rule.benchmark
            else EVALUATORS[rule.name](result, metrics)
        )
        for rule in RULEBOOK.rules
        if goal.value in rule.goals
    }
    return QualityReport(
        composite=_weighted_composite(per_rule),
        per_rule=per_rule,
        raw=metrics,
        uncovered=tuple(r.name for r in RULEBOOK.rules if r.name not in per_rule),
    )


# Horizon sweep

HORIZONS: tuple[int, ...] = (22, 24, 28, 32)
"""Default plan lengths in the Ironman + COMPETITIVE sweep.

Matches the Ironman row of ``report_sweep.sweep_site.WEEK_SWEEPS``: short
prep (22 wk, the minimum) through standard prep (32 wk). A change is only accepted
when it does not worsen any rule's mean distance by more than the
per-rule tolerance, so improvements generalise across plan lengths.
"""

GOAL_MATRIX_HORIZONS: dict[GoalType, tuple[int, ...]] = {
    GoalType.IRONMAN: HORIZONS,
    GoalType.HALF_IRONMAN: (16, 20, 24, 28),
    GoalType.RUNNING_MARATHON: (12, 16, 18),
    GoalType.RUNNING_HALF_MARATHON: (10, 12, 16),
    GoalType.SPRINT_TRIATHLON: (8, 10, 12),
    GoalType.OLYMPIC_TRIATHLON: (12, 14, 16),
    GoalType.RUNNING_10K: (8, 12, 16),
    GoalType.TRAIL_50K: (12, 16, 20),
    GoalType.TRAIL_20K: (12, 16, 20),
}
"""Per-goal horizon sets for the ``--matrix`` accept check.

Ironman keeps its 22-32 wk sweep; half-Ironman sweeps 16-28 wk
(docs/research/triathlon_long_course.md, Half-Distance vs. Full-Distance —
Summary Comparison, puts the half macrocycle at 16-28 weeks). Running
marathon's 12-18 wk covers the usual range (18-week programs, with
12-week variants). Half-marathon, sprint and olympic each bracket their
typical range (sprint 8-12 wk, olympic 12-16 wk from its 12-wk minimum,
half-marathon 10-16 wk).
Every (goal, tier, horizon) cell must stay QP-feasible.

Known defect held by the baseline rather than hidden: the sprint 8-wk
cells arrive under-fresh (race_day_freshness_ratio up to 0.77 at BASIC) —
the compressed horizon leaves no room to shed load; the fix is a
short-horizon freshness/step-down question for the macro layer, not a band
change (a no-taper shape is normal at this distance).

The 10K is mostly a regression guard: beyond its race-week and race-day
freshness bands it grades against the rules' fallback bands. Known defect held by the
baseline: the 8-wk BASIC cell arrives under-fresh (race-day TSB/CTL 0.03
against the 0.16 floor), the sprint's short-horizon question again.

The trail 50K sweeps its 12-wk minimum preparation up to 20 wk
(docs/research/trail_running.md §13.2: typical prep 16-20 wk). One band is
measured (race-day freshness); its other trail bands
are literature from that document, most of it single-coach opinion.

The trail 20K sweeps its whole 12-20 wk range here, so it has no
``_wide`` family. It is graded on the rules that cover every goal; its one
band of its own is race-day freshness.
"""

GOAL_MATRIX_HORIZONS_WIDE: dict[GoalType, tuple[int, ...]] = {
    GoalType.RUNNING_MARATHON: (22, 28),
    GoalType.RUNNING_HALF_MARATHON: (8, 20, 24),
    GoalType.SPRINT_TRIATHLON: (6, 16, 20),
    GoalType.OLYMPIC_TRIATHLON: (20, 24),
    GoalType.IRONMAN: (36,),
    GoalType.RUNNING_10K: (6, 20),
    GoalType.TRAIL_50K: (24,),
}
"""The ``<goal>_<tier>_wide`` family: the horizons that complete the range
the planner actually offers for each goal (``min_horizon_weeks`` to
``max_recommended_weeks`` in ``phase_tables.GOAL_PROFILES``), beyond
``GOAL_MATRIX_HORIZONS``' own sweep.

``GOAL_MATRIX_HORIZONS`` grades a much narrower band than the planner
recommends — a marathon 22-28 weeks out is inside the planner's own
12-28-week range but was never checked. These are the missing horizons
(endpoints plus enough intermediate points that no two neighbouring
graded horizons — this table's and ``GOAL_MATRIX_HORIZONS``' together —
sit more than 6 weeks apart), scored at the tier's *default* athlete, the
same as the base cell. Union the two tables per goal and the result spans
exactly ``min_horizon_weeks``..``max_recommended_weeks``.

Every horizon here must stay QP-feasible, as in the base sweep: the plan
label claims each one as checked.
"""

_LOW_VOLUME_FRACTION = 0.85
"""The ``<goal>_<tier>_low`` family's recent-volume scale.

The plan's volume floor: the trail 20K basic at 0.85 of its default is
2.1 h a week, the smallest week that holds its climb, long run and easy
run; below it the plan says so (plan maturity) rather than the gate
grading what it was not written for. Copied into ``endury.planning.plan_maturity`` as the
plan-maturity volume floor; ``tests/planning/test_plan_maturity.py`` pins
the two equal.
"""


def build_low_volume_scenario(
    weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
    goal: GoalType = GoalType.IRONMAN,
) -> Scenario:
    """Build the ``_low`` scenario: the tier's default athlete at 0.75x volume.

    Args:
        weeks: Plan length in weeks (race date = ``DEFAULT_START + weeks - 1``).
        ambition: Ambition tier (default COMPETITIVE, the loop's baseline).
        goal: Goal type (default Ironman, the loop's baseline).

    Returns:
        ``Scenario`` whose recent weekly hours and longest recent sessions
        are the goal's default profile scaled by :data:`_LOW_VOLUME_FRACTION`.
    """
    return _scaled_scenario(weeks, ambition, goal, _LOW_VOLUME_FRACTION, "low")


_HIGH_VOLUME_FRACTION = 1.5
"""The ``<goal>_<tier>_high`` family's recent-volume scale.

The mirror of :data:`_LOW_VOLUME_FRACTION`: the tier's default athlete at
one and a half times the default volume and longest session, the athlete
the menu's caps bind on (the trail 20K basic at 4 h/wk opened its base on
a flat 90-minute long run and trained less than before, 2026-10-06).
"""


def build_high_volume_scenario(
    weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
    goal: GoalType = GoalType.IRONMAN,
) -> Scenario:
    """Build the ``_high`` scenario: the tier's default athlete at 1.5x volume.

    Args:
        weeks: Plan length in weeks (race date = ``DEFAULT_START + weeks - 1``).
        ambition: Ambition tier (default COMPETITIVE, the loop's baseline).
        goal: Goal type (default Ironman, the loop's baseline).

    Returns:
        ``Scenario`` whose recent weekly hours and longest recent sessions
        are the goal's default profile scaled by :data:`_HIGH_VOLUME_FRACTION`.
    """
    return _scaled_scenario(weeks, ambition, goal, _HIGH_VOLUME_FRACTION, "high")


def _scaled_scenario(
    weeks: int, ambition: AmbitionLevel, goal: GoalType, fraction: float, suffix: str
) -> Scenario:
    """The tier's default athlete with volume and longest sessions scaled by *fraction*."""
    base = default_profile_for(goal, ambition)
    a_date = DEFAULT_START + timedelta(weeks=weeks, days=-1)
    return Scenario(
        name=f"{goal.value}_{ambition.value}_{weeks}wk_{suffix}",
        description=(
            f"{goal.value} {weeks}-week, {ambition.value} ambition, "
            f"{fraction}x recent volume."
        ),
        tags=(goal.value, ambition.value, f"{suffix}_volume"),
        goals=(RaceGoal(goal, a_date, "A"),),
        training_start=DEFAULT_START,
        ambition=ambition,
        recent_hours={
            sport: st.recent_weekly_hours * fraction
            for sport, st in base.sports.items()
        },
        longest_recent={
            (sport, family): round(minutes * fraction)
            for sport, st in base.sports.items()
            for family, minutes in st.longest_recent_min.items()
        },
    )


@dataclass(slots=True)
class SweepReport:
    """Aggregated score across multiple plan-length horizons.

    Attributes:
        composite: Weighted mean of ``per_rule``, in [0, 1].
        per_rule: Mean per-rule distance across all sampled horizons.
        per_horizon: ``(weeks, QualityReport)`` for every horizon.
        uncovered: Rules the swept goal is not scored on.
    """

    composite: float
    per_rule: dict[str, float]
    per_horizon: list[tuple[int, QualityReport]]
    uncovered: tuple[str, ...] = ()


def score_sweep(
    horizons: tuple[int, ...] = HORIZONS,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
    goal: GoalType = GoalType.IRONMAN,
    scenario_builder: Callable[[int, AmbitionLevel, GoalType], Scenario] | None = None,
) -> SweepReport:
    """Score a scenario at each horizon and aggregate per-rule.

    Args:
        horizons: Plan lengths (in weeks) to evaluate.
        ambition: Ambition tier of the swept scenario.
        goal: Goal type of the swept scenario (default Ironman, the
            loop's baseline).
        scenario_builder: Builds the scenario for one ``(weeks, ambition,
            goal)``. ``None`` (the default) uses :func:`build_default_scenario`.

    Returns:
        ``SweepReport`` with the mean per-rule distance across horizons,
        the composite over those means, and the raw per-horizon reports.

    Raises:
        ValueError: If the QP is infeasible at any horizon. The loop
            treats this as a hard rejection.
    """
    build = scenario_builder or build_default_scenario
    per_horizon: list[tuple[int, QualityReport]] = [
        (w, score(build(w, ambition, goal))) for w in horizons
    ]
    n = len(per_horizon)
    # Coverage is a function of the goal, so it is identical at every horizon.
    rule_names = list(per_horizon[0][1].per_rule)
    per_rule = {
        name: sum(rep.per_rule[name] for _, rep in per_horizon) / n
        for name in rule_names
    }
    return SweepReport(
        composite=_weighted_composite(per_rule),
        per_rule=per_rule,
        per_horizon=per_horizon,
        uncovered=per_horizon[0][1].uncovered,
    )


def score_matrix(
    horizons: tuple[int, ...] | None = None,
) -> dict[str, SweepReport]:
    """Sweep every goal x ambition tier on offer — the one-shot accept check.

    A plan its menu does not offer (``offered:``) is not graded: nobody can
    build it, and its cells would hold every shared planner change to
    plans no athlete trains with.

    Args:
        horizons: Plan lengths (in weeks) to evaluate per cell. ``None``
            (the default) uses each goal's own horizon set from
            ``GOAL_MATRIX_HORIZONS``, plus the ``_wide``, ``_low`` and
            ``_high`` evidence families (below); an explicit tuple applies
            to every goal (the odd-geometry spot check) and skips the
            families.

    Returns:
        A sweep report per ``"<goal>_<tier>"`` key, plus one per
        ``"<goal>_<tier>_wide"``, ``"<goal>_<tier>_low"`` and
        ``"<goal>_<tier>_high"`` key when *horizons* is ``None``.

    Raises:
        ValueError: If the QP is infeasible at any goal x tier x horizon.
    """
    base = {
        f"{goal.value}_{ambition.value}": score_sweep(
            horizons if horizons is not None else goal_horizons,
            ambition,
            goal=goal,
        )
        for goal, goal_horizons in GOAL_MATRIX_HORIZONS.items()
        for ambition in AmbitionLevel
        if ambition in BLUEPRINTS[goal].offered
    }
    if horizons is not None:
        return base
    wide = {
        f"{goal.value}_{ambition.value}_wide": score_sweep(
            goal_horizons, ambition, goal
        )
        for goal, goal_horizons in GOAL_MATRIX_HORIZONS_WIDE.items()
        for ambition in AmbitionLevel
        if ambition in BLUEPRINTS[goal].offered
    }
    scaled = {
        f"{goal.value}_{ambition.value}_{suffix}": score_sweep(
            goal_horizons, ambition, goal, builder
        )
        for suffix, builder in (
            ("low", build_low_volume_scenario),
            ("high", build_high_volume_scenario),
        )
        for goal, goal_horizons in GOAL_MATRIX_HORIZONS.items()
        for ambition in AmbitionLevel
        if ambition in BLUEPRINTS[goal].offered
    }
    return {**base, **wide, **scaled}


# Default scenario builder


def build_default_scenario(
    weeks: int,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
    goal: GoalType = GoalType.IRONMAN,
) -> Scenario:
    """Build the single-A-goal scenario for one goal x ambition cell.

    Args:
        weeks: Plan length in weeks (race date = ``DEFAULT_START + weeks - 1``).
        ambition: Ambition tier (default COMPETITIVE, the loop's baseline).
        goal: Goal type (default Ironman, the loop's baseline).

    Returns:
        ``Scenario`` with no athlete-state overrides; the pipeline falls
        back to its goal-type defaults.
    """
    a_date = DEFAULT_START + timedelta(weeks=weeks, days=-1)
    return Scenario(
        name=f"{goal.value}_{ambition.value}_{weeks}wk",
        description=f"{goal.value} {weeks}-week, {ambition.value} ambition.",
        tags=(goal.value, ambition.value),
        goals=(RaceGoal(goal, a_date, "A"),),
        ambition=ambition,
    )


# Replan-quality rules
#
# The rules above grade a single FRESH plan. These grade a REPLAN: the diff
# between the original plan's tail and the plan regenerated mid-preparation
# (``replay_replan``). The fresh sweep never sees a replan, yet mid-prep
# regeneration has its own failure modes — a broken phase/mode suffix,
# fixed-bound weeks drifting, or the capacity re-seed leaving a systematic
# volume drag that never converges. Bands are calibrated on a healthy
# 18-run Ironman replan sweep (largest fixed-bound shift 0.35 h; largest
# mean PEAK-load drift 0.55 h), with the TAPER/RACE tolerance derived once
# the race-day freshness band made taper volume state-responsive (see
# _FIXED_BOUND_TOLERANCE_H).


_FIXED_BOUND_TOLERANCE_H: dict[WeekMode, float] = {
    WeekMode.RECOVERY: 0.5,
    WeekMode.TAPER: 1.0,
    WeekMode.RACE: 1.0,
}
"""Per-mode replan drift tolerance (hours) for weeks the capacity QP should
not be freely re-sizing. LOAD weeks are excluded — they legitimately re-size
when capacity re-seeds from the mid-prep state.

RECOVERY keeps a tight 0.5 h tolerance: a deload is a pure menu-authored
fraction of the preceding LOAD week, so nothing should move it.

TAPER/RACE get 1.0 h because taper volume is deliberately state-responsive:
the QP sizes the taper so the athlete lands inside the combined-PMC
race-ready band, and an athlete who arrives at the cutover with different
fitness/fatigue legitimately needs a different taper to get there (observed
0.88 h on the Ironman COMPETITIVE wk-12 replay).
"""


def _replan_phase_suffix(diff: ReplanDiff) -> float:
    """Hard invariant: replan phases MUST equal the original ``weeks[K:]`` (`docs/specs/planner/phase_planner.md`, R-PHASE-002b).

    The replan's ``(phase, mode)`` sequence must be the original plan's
    suffix, re-indexed to zero. 0.0 on MATCH, 1.0 on any MISMATCH — an
    unambiguous, deterministic check.
    """
    return 0.0 if diff.phase_suffix_match else 1.0


def _replan_fixed_bound_stability(diff: ReplanDiff) -> float:
    """Fixed-bound (deload/taper/race) weeks barely move across a replan.

    Their hours come from near-fixed menu bounds rather than the capacity QP,
    so a replan should leave them close to where they were; a larger shift
    means the re-seed is perturbing weeks it should not touch. Tolerances are
    per-mode (:data:`_FIXED_BOUND_TOLERANCE_H`) — tight for RECOVERY, looser
    for the now freshness-responsive TAPER/RACE weeks — and each distance
    ramps to 1.0 one hour past its band edge. Scored as the worst mode.
    Returns 0.0 when the replan tail carries no fixed-bound weeks.
    """
    distances = [
        Band(0.0, tolerance, 1.0).distance(abs(r.delta))
        for r in diff.rows
        if (tolerance := _FIXED_BOUND_TOLERANCE_H.get(r.mode)) is not None
    ]
    if not distances:
        return 0.0
    return max(distances)


def _replan_seam_convergence(diff: ReplanDiff) -> float:
    """The capacity re-seed dip has converged to ~0 by the first PEAK week.

    A mid-preparation replan re-seeds capacity from the measured state, which
    dips the LOAD weeks at the cutover seam; that dip SHOULD decay as the new
    ramp re-converges on the shared ceiling. Measured across PEAK-phase LOAD
    weeks — if the dip decayed, these are ~unchanged; a systematic residual
    drag (the "athlete who trained perfectly gets a much easier remainder"
    defect) leaves them depressed. Distance ramps from a 0.75 h band edge to
    1.0 by a 2.0 h mean drag (healthy Ironman replans stayed within 0.55 h).
    Returns 0.0 when the replan has no PEAK LOAD weeks (nothing left to
    converge).
    """
    peak_load = [
        abs(r.delta)
        for r in diff.rows
        if r.phase == PhaseType.PEAK and r.mode == WeekMode.LOAD
    ]
    if not peak_load:
        return 0.0
    return Band(0.0, 0.75, 1.25).distance(sum(peak_load) / len(peak_load))


@dataclass(frozen=True, slots=True)
class ReplanRule:
    """One replan-scoring rule.

    Attributes:
        name: Stable identifier used in JSON output.
        weight: Relative weight in the replan composite (the hard suffix
            invariant outweighs the soft re-seed bands).
        evaluate: Function returning a distance in [0, 1] for a
            :class:`ReplanDiff`.
    """

    name: str
    weight: float
    evaluate: Callable[[ReplanDiff], float]


REPLAN_RULES: tuple[ReplanRule, ...] = (
    ReplanRule("replan_phase_suffix", 2.0, _replan_phase_suffix),
    ReplanRule("replan_fixed_bound_stability", 1.0, _replan_fixed_bound_stability),
    ReplanRule("replan_seam_convergence", 1.0, _replan_seam_convergence),
)


@dataclass(slots=True)
class ReplanQualityReport:
    """One replan scoring run.

    Attributes:
        week: The cutover week replanned from.
        composite: Weighted mean of per-rule distances, in [0, 1].
        per_rule: Distance per replan rule, keyed by ``ReplanRule.name``.
        phase_suffix_match: The phase-suffix verdict for this replan.
    """

    week: int
    composite: float
    per_rule: dict[str, float]
    phase_suffix_match: bool


def _replan_weighted_composite(per_rule: dict[str, float]) -> float:
    """Compute the weighted-mean composite from a replan per-rule map."""
    weight_sum = sum(r.weight for r in REPLAN_RULES)
    return sum(r.weight * per_rule[r.name] for r in REPLAN_RULES) / weight_sum


def score_replan(
    scenario: Scenario, week: int, *, use_macro_solver: bool = True
) -> ReplanQualityReport:
    """Replay a mid-preparation replan of *scenario* and score its diff.

    Args:
        scenario: The scenario to run and replan.
        week: Cutover week to replan from (``1 <= week < n_weeks``).
        use_macro_solver: Passed through to both pipeline runs.

    Returns:
        A :class:`ReplanQualityReport` over :data:`REPLAN_RULES`.

    Raises:
        ValueError: Propagated from the macro QP if either run is infeasible.
    """
    original, replanned = replay_replan(
        scenario, week, use_macro_solver=use_macro_solver
    )
    diff = build_replan_diff(original, replanned, week)
    per_rule = {r.name: r.evaluate(diff) for r in REPLAN_RULES}
    return ReplanQualityReport(
        week=week,
        composite=_replan_weighted_composite(per_rule),
        per_rule=per_rule,
        phase_suffix_match=diff.phase_suffix_match,
    )


REPLAN_HORIZON: int = 28
"""Plan length (weeks) for the replan sweep — a standard-prep Ironman."""

REPLAN_WEEKS: tuple[int, ...] = (8, 12, 16)
"""Cutover weeks probed by the replan sweep (early/mid/late BUILD)."""


@dataclass(slots=True)
class ReplanSweepReport:
    """Aggregated replan score across cutover weeks.

    Attributes:
        composite: Weighted mean of ``per_rule``, in [0, 1].
        per_rule: Mean per-rule distance across all cutover weeks.
        per_week: The per-week :class:`ReplanQualityReport` list.
    """

    composite: float
    per_rule: dict[str, float]
    per_week: list[ReplanQualityReport]


def score_replan_sweep(
    horizon: int = REPLAN_HORIZON,
    weeks: tuple[int, ...] = REPLAN_WEEKS,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
) -> ReplanSweepReport:
    """Score replans of the default Ironman scenario across cutover weeks.

    Complements :func:`score_sweep` (which only grades fresh plans) with the
    replan axis: one deterministic replan per cutover week, aggregated as the
    per-rule mean. Ironman-only and single-horizon by design — the sweep
    isolates the capacity re-seed seam, not plan-length sensitivity.

    Args:
        horizon: Plan length in weeks (default standard-prep Ironman).
        weeks: Cutover weeks to replan from.
        ambition: Ambition tier of the swept scenario.

    Returns:
        A :class:`ReplanSweepReport` with the mean per-rule distance across
        cutover weeks, the composite over those means, and the per-week runs.

    Raises:
        ValueError: If the QP is infeasible at any cutover week.
    """
    scenario = build_default_scenario(horizon, ambition)
    per_week = [score_replan(scenario, w) for w in weeks]
    n = len(per_week)
    per_rule = {
        r.name: sum(rep.per_rule[r.name] for rep in per_week) / n for r in REPLAN_RULES
    }
    return ReplanSweepReport(
        composite=_replan_weighted_composite(per_rule),
        per_rule=per_rule,
        per_week=per_week,
    )


# CLI


def _format_per_rule_table(per_rule: dict[str, float]) -> list[str]:
    """Format a per-rule distance map as sorted text lines."""
    lines: list[str] = []
    for name, dist in sorted(per_rule.items(), key=lambda kv: -kv[1]):
        marker = "*" if dist > 0.05 else " "
        lines.append(f" {marker} {name:<24} {dist:.4f}")
    return lines


def _format_sweep_text(report: SweepReport) -> str:
    """Format a sweep report as a human-readable summary."""
    horizons = ", ".join(f"{w}wk" for w, _ in report.per_horizon)
    lines = [
        f"composite (mean over {horizons}): {report.composite:.4f}",
        "",
        "per-rule (mean across horizons):",
    ]
    lines.extend(_format_per_rule_table(report.per_rule))
    lines.append("")
    lines.append("per-horizon composite:")
    for w, rep in report.per_horizon:
        lines.append(f"  {w}wk  {rep.composite:.4f}")
    return "\n".join(lines)


def _format_single_text(weeks: int, report: QualityReport) -> str:
    """Format a single-horizon report as a human-readable summary."""
    lines = [f"composite ({weeks}wk): {report.composite:.4f}", ""]
    lines.extend(_format_per_rule_table(report.per_rule))
    return "\n".join(lines)


def _format_replan_text(report: ReplanSweepReport, horizon: int) -> str:
    """Format a replan sweep report as a human-readable summary."""
    weeks = ", ".join(f"wk{r.week}" for r in report.per_week)
    lines = [
        f"replan composite (mean over {weeks}, {horizon}wk Ironman): "
        f"{report.composite:.4f}",
        "",
        "per-rule (mean across cutover weeks):",
    ]
    lines.extend(_format_per_rule_table(report.per_rule))
    lines.append("")
    lines.append("per-week composite:")
    for rep in report.per_week:
        verdict = "MATCH" if rep.phase_suffix_match else "MISMATCH ***"
        lines.append(f"  wk{rep.week:<3} {rep.composite:.4f}  suffix={verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point. Scores the default Ironman scenario.

    By default sweeps COMPETITIVE over ``HORIZONS`` and reports the
    per-rule mean. Use ``--weeks N`` for a single horizon, or
    ``--matrix`` for the one-shot accept check (every matrix goal x
    ambition tier x horizons).

    Args:
        argv: Optional argv override; defaults to ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(
        prog="devtools.quality.planner.score_quality",
        description="Rules-based quality score for planner output.",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=None,
        help="Score a single horizon instead of the default sweep.",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help=(
            "Sweep all goals (ironman/half_ironman) x ambition tiers "
            "(BASIC/MODERATE/COMPETITIVE) x horizons."
        ),
    )
    parser.add_argument(
        "--replan",
        action="store_true",
        help="Score mid-preparation replans (cutover-week sweep) not fresh plans.",
    )
    parser.add_argument(
        "--horizons",
        type=lambda s: tuple(int(w) for w in s.split(",")),
        default=None,
        help=(
            "Comma-separated week horizons (default: per-goal sets — "
            "IM 20,24,28,32 / half-IM 16,20,24,28)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of the text summary.",
    )
    args = parser.parse_args(argv)

    try:
        if args.replan:
            replan = score_replan_sweep()
            if args.as_json:
                print(
                    json.dumps(
                        {
                            "horizon": REPLAN_HORIZON,
                            "weeks": list(REPLAN_WEEKS),
                            "composite": replan.composite,
                            "per_rule": replan.per_rule,
                            "per_week": [
                                {
                                    "week": r.week,
                                    "composite": r.composite,
                                    "per_rule": r.per_rule,
                                    "phase_suffix_match": r.phase_suffix_match,
                                }
                                for r in replan.per_week
                            ],
                        },
                        indent=2,
                    )
                )
            else:
                print(_format_replan_text(replan, REPLAN_HORIZON))
            return

        if args.matrix:
            per_cell = score_matrix(args.horizons)
            if args.as_json:
                print(
                    json.dumps(
                        {
                            "horizons": (
                                list(args.horizons)
                                if args.horizons is not None
                                else {
                                    g.value: list(h)
                                    for g, h in GOAL_MATRIX_HORIZONS.items()
                                }
                            ),
                            "cells": {
                                cell: {
                                    "composite": rep.composite,
                                    "per_rule": rep.per_rule,
                                    "uncovered": list(rep.uncovered),
                                    "per_horizon": [
                                        {"weeks": w, "composite": r.composite}
                                        for w, r in rep.per_horizon
                                    ],
                                }
                                for cell, rep in per_cell.items()
                            },
                        },
                        indent=2,
                    )
                )
            else:
                if args.horizons is not None:
                    horizons = ", ".join(f"{w}wk" for w in args.horizons)
                else:
                    horizons = "; ".join(
                        f"{g.value} {','.join(str(w) for w in h)}wk"
                        for g, h in GOAL_MATRIX_HORIZONS.items()
                    )
                print(f"matrix (horizons: {horizons})")
                for cell, rep in per_cell.items():
                    worst_name, worst = max(rep.per_rule.items(), key=lambda kv: kv[1])
                    worst_txt = f"worst: {worst_name} {worst:.4f}" if worst > 0 else ""
                    print(f"  {cell:<28} composite {rep.composite:.4f}  {worst_txt}")
            return

        if args.weeks is not None:
            single = score(build_default_scenario(args.weeks))
            if args.as_json:
                print(
                    json.dumps(
                        {
                            "weeks": args.weeks,
                            "composite": single.composite,
                            "per_rule": single.per_rule,
                        },
                        indent=2,
                    )
                )
            else:
                print(_format_single_text(args.weeks, single))
            return

        sweep = score_sweep(args.horizons if args.horizons is not None else HORIZONS)
    except ValueError as exc:
        print(f"[QP infeasible] {exc}", file=sys.stderr)
        sys.exit(2)

    if args.as_json:
        print(
            json.dumps(
                {
                    "composite": sweep.composite,
                    "per_rule": sweep.per_rule,
                    "horizons": [w for w, _ in sweep.per_horizon],
                    "per_horizon": [
                        {
                            "weeks": w,
                            "composite": rep.composite,
                            "per_rule": rep.per_rule,
                        }
                        for w, rep in sweep.per_horizon
                    ],
                },
                indent=2,
            )
        )
    else:
        print(_format_sweep_text(sweep))


if __name__ == "__main__":
    main()
