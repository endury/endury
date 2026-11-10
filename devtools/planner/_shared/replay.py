"""Deterministic replan replay for planner scenarios (no DB).

Simulates the "athlete followed the plan perfectly for K weeks, then replans"
loop entirely in memory, isolating the ``prior_training_weeks`` truncation and
capacity re-seeding from database/derivation noise.

``state_at_week`` synthesizes the mid-preparation athlete state from a plan's
own Banister trajectory (the CTL/ATL the plan itself would have produced under
perfect compliance). ``replay_replan`` then regenerates the remainder of the
plan from that state, exactly as :func:`run_replan` does against a real DB,
and returns both the original and the replanned run for diffing.

The synthesized CTL/ATL come straight from :class:`PlanMetrics` and are already
in weekly-TSS units, so — unlike the DB deriver in
``endury.planning.athlete_state`` — there is no daily-to-weekly
(``x7``) bridge here.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, replace
from datetime import timedelta

from devtools.planner._shared.metrics import (
    PipelineResult,
    PlanMetrics,
    _compute_metrics,
    run_full_pipeline,
)
from devtools.planner._shared.pipeline import (
    SolverOptions,
    default_profile_for,
    generate_plan,
)
from devtools.planner._shared.scenarios import Scenario
from endury.planner.activity.registry import ensure_registry_loaded
from endury.planner.foundations.types import (
    ActivityFamily,
    PhaseType,
    SportType,
    WeekMode,
)

# Window (in weeks) over which recent volume and longest sessions are measured,
# matching the 42-day window in ``athlete_state``.
_RECENT_WEEKS = 6

# Long-session family per sport, mirroring ``athlete_state._LONGEST_FAMILY``.
_LONGEST_FAMILY: dict[SportType, ActivityFamily] = {
    SportType.RUNNING: ActivityFamily.LONG,
    SportType.CYCLING: ActivityFamily.LONG,
    SportType.SWIMMING: ActivityFamily.EASY,
}


def _sport_set(scenario: Scenario) -> list[SportType]:
    """Return the effective sport set of a scenario (recent hours or defaults)."""
    if scenario.recent_hours:
        return list(scenario.recent_hours.keys())
    base = default_profile_for(scenario.goals[0].goal_type)
    return list(base.sports.keys())


def state_at_week(
    result: PipelineResult,
    metrics: PlanMetrics,
    week: int,
) -> dict[str, object]:
    """Synthesize the mid-preparation athlete state at the start of ``week``.

    Reads the plan's own perfect-compliance trajectory: CTL/ATL entering
    ``week`` become the seeds, and the trailing :data:`_RECENT_WEEKS` weeks of
    placed sessions give recent volume and longest-session baselines.

    Args:
        result: The original pipeline run.
        metrics: Metrics computed from ``result`` (supplies the CTL/ATL arrays).
        week: Week index the athlete is replanning from (``1 <= week < n_weeks``).

    Returns:
        A dict of :class:`Scenario` field overrides — ``ctl_0``, ``atl_0``,
        ``recent_hours`` (all ``dict[SportType, float]``) and ``longest_recent``
        (``dict[tuple[SportType, ActivityFamily], int] | None``) — ready to
        splat into :func:`dataclasses.replace`.
    """
    lo = max(0, week - _RECENT_WEEKS)
    ctl_0: dict[SportType, float] = {}
    atl_0: dict[SportType, float] = {}
    recent_hours: dict[SportType, float] = {}
    longest_recent: dict[tuple[SportType, ActivityFamily], int] = {}

    for sport in _sport_set(result.scenario):
        sv = sport.value
        ctl_arr = metrics.a_ctl.get(sv)
        atl_arr = metrics.a_atl.get(sv)
        if ctl_arr is None or atl_arr is None or len(ctl_arr) == 0:
            continue
        idx = min(week, len(ctl_arr) - 1)
        ctl_0[sport] = float(ctl_arr[idx])
        atl_0[sport] = float(atl_arr[idx])

        window_hours = [
            metrics.weekly_hours.get(w, {}).get(sv, 0.0) for w in range(lo, week)
        ]
        recent_hours[sport] = (
            float(statistics.median(window_hours)) if window_hours else 0.0
        )

        longest_h = max(
            (
                s.target_hours
                for s in result.sessions
                if lo <= s.week < week and s.sport == sport
            ),
            default=0.0,
        )
        if longest_h > 0.0:
            longest_recent[(sport, _LONGEST_FAMILY[sport])] = round(longest_h * 60)

    return {
        "ctl_0": ctl_0,
        "atl_0": atl_0,
        "recent_hours": recent_hours,
        "longest_recent": longest_recent or None,
    }


def replay_replan(
    scenario: Scenario,
    week: int,
    *,
    use_macro_solver: bool = True,
) -> tuple[PipelineResult, PipelineResult]:
    """Run a scenario, then replan it from ``week`` under perfect compliance.

    Generates the original plan, synthesizes the athlete state at ``week`` from
    its trajectory, and regenerates the remainder via ``prior_training_weeks``
    (the same mechanism :func:`run_replan` uses). Because the goals are
    unchanged, the replanned phase/mode sequence is the original plan's weeks
    ``[week:]`` re-indexed to zero (the replan suffix property; see
    :class:`ReplanDiff`); only the QP-sized hours differ, since capacity
    re-seeds from the mid-prep state.

    Args:
        scenario: The scenario to run and replan.
        week: Week to replan from (``1 <= week < n_weeks`` of the original plan).
        use_macro_solver: Passed through to both pipeline runs.

    Returns:
        ``(original, replanned)`` pipeline results. ``replanned`` starts
        ``week`` weeks later and spans the original plan's remaining weeks.
    """
    original = run_full_pipeline(scenario, use_macro_solver)
    metrics = _compute_metrics(original)
    overrides = state_at_week(original, metrics, week)

    replanned_sc = replace(
        scenario,
        name=f"{scenario.name}@wk{week}",
        training_start=scenario.training_start + timedelta(weeks=week),
        # Accumulate: a scenario that already resumes mid-preparation (a
        # partial reference) has trained its own prior weeks *plus* these.
        prior_training_weeks=scenario.prior_training_weeks + week,
        **overrides,  # type: ignore[arg-type]
    )
    request = replanned_sc.to_request(SolverOptions(use_macro_solver=use_macro_solver))

    ensure_registry_loaded()
    t0 = time.monotonic()
    plan_result = generate_plan(request)
    elapsed = time.monotonic() - t0

    replanned = PipelineResult(
        scenario=replanned_sc,
        plan=plan_result.phase_plan,
        sessions=plan_result.sessions,
        assignments=plan_result.assignments,
        elapsed_s=elapsed,
        session_tss_per_hour=plan_result.session_tss_per_hour,
    )
    return original, replanned


# Replan diff (aligned original tail vs replan, for scoring)


@dataclass(frozen=True, slots=True)
class ReplanWeekDelta:
    """One calendar-aligned week of a replan diff.

    Attributes:
        phase: The (shared) phase of this week in both plans.
        mode: The (shared) week mode.
        orig_hours: Total planned hours this week in the original plan.
        replan_hours: Total planned hours this week in the replan.
    """

    phase: PhaseType
    mode: WeekMode
    orig_hours: float
    replan_hours: float

    @property
    def delta(self) -> float:
        """Signed hour change, replan minus original."""
        return self.replan_hours - self.orig_hours


@dataclass(frozen=True, slots=True)
class ReplanDiff:
    """A calendar-aligned diff of an original plan tail vs its replan.

    Attributes:
        week: The cutover week the athlete replanned from.
        phase_suffix_match: Whether the replan's ``(phase, mode)`` sequence
            equals the original plan's ``weeks[week:]`` — the replan suffix
            property (`docs/specs/planner/phase_planner.md`, R-PHASE-002b).
            False is a hard contract regression.
        rows: One :class:`ReplanWeekDelta` per replan week, index-aligned so
            row ``j`` compares the original's ``week + j`` with the replan's
            ``j`` (same calendar date).
    """

    week: int
    phase_suffix_match: bool
    rows: tuple[ReplanWeekDelta, ...]


def build_replan_diff(
    original: PipelineResult,
    replanned: PipelineResult,
    week: int,
) -> ReplanDiff:
    """Align an original plan tail against its replan into a per-week diff.

    Mirrors the alignment ``report_cli._print_replan_diff`` renders: the
    replan's week ``j`` (0-indexed) corresponds to the original's ``week + j``,
    on the same calendar date. Hours legitimately differ (capacity re-seeds
    from the mid-preparation state); phases must not (the replan suffix
    property; see :class:`ReplanDiff`).

    Args:
        original: The original full-horizon pipeline run.
        replanned: The replan (starts ``week`` weeks later).
        week: The week the athlete replanned from.

    Returns:
        A :class:`ReplanDiff` carrying the suffix verdict and per-week deltas.
    """
    metrics_orig = _compute_metrics(original)
    metrics_replan = _compute_metrics(replanned)

    orig_tail = [(w.phase, w.mode) for w in original.plan.weeks[week:]]
    rep_seq = [(w.phase, w.mode) for w in replanned.plan.weeks]

    rows = tuple(
        ReplanWeekDelta(
            phase=ws.phase,
            mode=ws.mode,
            orig_hours=sum(metrics_orig.weekly_hours.get(week + j, {}).values()),
            replan_hours=sum(metrics_replan.weekly_hours.get(ws.week, {}).values()),
        )
        for j, ws in enumerate(replanned.plan.weeks)
    )
    return ReplanDiff(
        week=week,
        phase_suffix_match=orig_tail == rep_seq,
        rows=rows,
    )
