"""Planner scenario report engine and CLI.

Runs the session-menu pipeline for a ``Scenario``, computes metrics,
generates diagnostic charts, and produces a formatted console summary.
Works both as a CLI and as a library for the sweep site (``sweep_site``).

Usage::

    from devtools.planner.report_sweep.report_cli import generate_report, display_report
    from devtools.planner._shared.scenarios import SCENARIOS

    report = generate_report(SCENARIOS["ironman_moderate"])
    display_report(report)

CLI, including the deterministic replan-diff replay::

    make run-devtool TOOL=planner_scenario_report ARGS='--scenario ironman_moderate --replan-at-week 8 --no-plots'
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from devtools.planner._shared.metrics import (
    PipelineResult,
    PlanMetrics,
    _compute_metrics,
    format_athlete_state,
    run_full_pipeline,
)
from devtools.planner._shared.replay import replay_replan
from devtools.planner._shared.scenarios import (
    SCENARIOS,
    Scenario,
    list_scenarios,
    scenarios_by_tag,
)
from devtools.planner._shared.sessions import (
    build_synthetic_resolver,
    materialized_swim_meters,
)
from endury.planner.activity.registry import get_activity
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    PlacedSession,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour
from endury.planner.phase.phase_planner import validate_phase_plan
from endury.training.load import LoadMetricContext

# Style constants

_ALL_SPORTS = [SportType.RUNNING, SportType.CYCLING, SportType.SWIMMING]

_C: dict[SportType, str] = {
    SportType.RUNNING: "#E63946",
    SportType.CYCLING: "#457B9D",
    SportType.SWIMMING: "#2A9D8F",
}
_C_RACE = "#C0392B"

_PHASE_BG: dict[PhaseType, str] = {
    PhaseType.BASE: "#A8DADC",
    PhaseType.BUILD: "#F4A261",
    PhaseType.PEAK: "#E76F51",
    PhaseType.TAPER: "#B0BEC5",
    PhaseType.MAINTAIN: "#9CC5A1",
}
_PHASE_ALPHA = 0.16

# Abbreviation tables shared between the calendar grid and athlete state.
_SPORT_ABBR_3: dict[SportType, str] = {
    SportType.RUNNING: "RUN",
    SportType.CYCLING: "BIK",
    SportType.SWIMMING: "SWI",
}

_FAMILY_ABBR_2: dict[ActivityFamily, str] = {
    ActivityFamily.EASY: "EZ",
    ActivityFamily.LONG: "LG",
    ActivityFamily.RECOVERY: "RV",
    ActivityFamily.TEMPO: "TM",
    ActivityFamily.THRESHOLD: "TH",
    ActivityFamily.INTERVAL: "IV",
    ActivityFamily.TEST: "XT",
}

# Per-activity suffix to disambiguate activities that share a family code.
# Appended after the 2-letter family abbreviation (e.g. "EZ dr", "TH cs").
_ACTIVITY_SUFFIX: dict[ActivityKey, str] = {
    ActivityKey.SWIM_DRILL: "dr",
    ActivityKey.SWIM_CSS: "cs",
    ActivityKey.SWIM_CSS_SHORT: "cs",
    ActivityKey.RUN_LONG_PROGRESSION: "pg",
    ActivityKey.RUN_BRICK_SHORT: "bk",
    ActivityKey.RUN_BRICK_LONG: "bk",
    ActivityKey.RUN_BRICK_HIM_TEMPO: "bk",
    ActivityKey.RUN_BRICK_IM_TEMPO: "bk",
    ActivityKey.BIKE_LONG_SWEET_SPOT: "ss",
    ActivityKey.BIKE_LONG_HIM_RACE_PACE: "rp",
    ActivityKey.BIKE_LONG_IM_RACE_PACE: "rp",
    ActivityKey.RUN_HM_PACE: "mp",
    ActivityKey.RUN_FULL_MP: "mp",
    ActivityKey.RUN_LONG_WITH_MP: "pg",
    ActivityKey.RUN_5K_RACE_PACE: "rp",
    ActivityKey.RUN_10K_RACE_PACE: "rp",
    ActivityKey.RUN_VO2MAX_SHORT: "vo",
    ActivityKey.RUN_SPEED: "sp",
    # Rotation-variant disambiguation (same-family pairs).
    ActivityKey.RUN_PROGRESSION: "pg",
    ActivityKey.RUN_FARTLEK: "fk",
    ActivityKey.RUN_HILL_REPEATS: "hr",
    ActivityKey.BIKE_OVER_UNDER: "ou",
}


def _family_code(act: ActivityDefinition) -> str:
    """Return the display code for a calendar grid cell.

    Combines the 2-letter family abbreviation with an optional per-activity
    suffix so that activities sharing a family (e.g. SWIM_ENDURANCE vs
    SWIM_DRILL, both EASY) are visually distinct.
    """
    base = _FAMILY_ABBR_2.get(act.family, "??")
    suffix = _ACTIVITY_SUFFIX.get(act.key, "")
    if suffix:
        return f"{base} {suffix}"
    return base


def _setup_plot_style() -> None:
    """Apply the shared matplotlib rcParams."""
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "figure.facecolor": "white",
            "axes.facecolor": "#FAFAFA",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.35,
            "grid.linestyle": "--",
            "grid.linewidth": 0.7,
            "font.size": 11,
            "axes.labelsize": 12,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "legend.fontsize": 10,
            "legend.framealpha": 0.88,
            "lines.linewidth": 2.2,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        }
    )


# Data types


@dataclass
class ScenarioReport:
    """Complete report for one scenario."""

    scenario: Scenario
    pipeline: PipelineResult
    metrics: PlanMetrics
    figures: list[tuple[str, plt.Figure]]
    summary_text: str


# Validation


def _run_validation(result: PipelineResult) -> dict[str, list[str]]:
    """Run L1 validation and return a dict of errors and warnings."""
    errors: list[str] = []
    warnings: list[str] = []

    req = PhaseRequest(
        goals=list(result.scenario.goals),
        training_start=result.scenario.training_start,
        ambition=result.scenario.ambition,
    )
    v = validate_phase_plan(result.plan, req)
    errors.extend(v.errors)
    warnings.extend(v.warnings)

    # Basic session sanity.
    if not result.sessions:
        errors.append("No sessions generated.")

    # Race-DAY collisions: the day assigner evicts non-TEST sessions from
    # race days, so any assignment landing on one is a regression.
    race_dates = {
        result.scenario.training_start + timedelta(weeks=re.week, days=re.day_of_week)
        for re in result.plan.race_events
    }
    collisions = [a for a in result.assignments if a.date in race_dates]
    if collisions:
        warnings.append(f"{len(collisions)} assignment(s) on a race day - expected 0.")

    return {"errors": errors, "warnings": warnings}


# Plot helpers


def _add_phase_bands(ax: plt.Axes, plan: PhasePlan) -> None:
    """Shade contiguous phase runs as coloured background bands."""
    if not plan.weeks:
        return
    runs: list[tuple[int, int, PhaseType]] = []
    cur, start = plan.weeks[0].phase, 0
    for k, ws in enumerate(plan.weeks):
        if ws.phase != cur:
            runs.append((start, k - 1, cur))
            cur, start = ws.phase, k
    runs.append((start, len(plan.weeks) - 1, cur))
    for x0, x1, phase in runs:
        ax.axvspan(
            x0 - 0.5,
            x1 + 0.5,
            facecolor=_PHASE_BG.get(phase, "#DDD"),
            alpha=_PHASE_ALPHA,
            zorder=0,
            lw=0,
        )


def _add_race_vlines(ax: plt.Axes, plan: PhasePlan) -> None:
    for re in plan.race_events:
        ax.axvline(re.week + 0.5, color=_C_RACE, ls="--", lw=1.8, zorder=3)
        # xaxis_transform: x in data coords, y in axes coords (0-1)
        ax.text(
            re.week + 0.5,
            0.97,
            "RACE",
            rotation=90,
            ha="right",
            va="top",
            color=_C_RACE,
            fontsize=8,
            fontweight="bold",
            transform=ax.get_xaxis_transform(),
        )


def _phase_patches(plan: PhasePlan) -> list[mpatches.Patch]:
    seen: dict[PhaseType, mpatches.Patch] = {}
    for ws in plan.weeks:
        if ws.phase not in seen:
            seen[ws.phase] = mpatches.Patch(
                facecolor=_PHASE_BG.get(ws.phase, "#DDD"),
                alpha=_PHASE_ALPHA + 0.25,
                label=ws.phase.value.capitalize(),
            )
    return list(seen.values())


def _week_ticks(ax: plt.Axes, n: int) -> None:
    ax.set_xticks(range(0, n, max(1, n // 10)))


# Plot functions


def _plot_phase_timeline(plan: PhasePlan, title: str) -> plt.Figure:
    """Colour-coded horizontal bar showing phase progression."""
    fig, ax = plt.subplots(figsize=(14, 1.8), layout="constrained")
    ax.set_title(title, fontsize=13, fontweight="bold")
    n = len(plan.weeks)
    for ws in plan.weeks:
        color = _PHASE_BG.get(ws.phase, "#DDD")
        ax.barh(0, 1, left=ws.week, color=color, edgecolor="white", lw=0.5, height=0.6)
        if ws.mode == WeekMode.RACE:
            ax.axvline(ws.week + 0.5, color=_C_RACE, ls="--", lw=1.8, zorder=3)

    runs: list[tuple[int, int, PhaseType]] = []
    cur, start = plan.weeks[0].phase, 0
    for k, ws in enumerate(plan.weeks):
        if ws.phase != cur:
            runs.append((start, k - 1, cur))
            cur, start = ws.phase, k
    runs.append((start, len(plan.weeks) - 1, cur))
    for x0, x1, phase in runs:
        mid = (x0 + x1) / 2 + 0.5
        ax.text(
            mid,
            0,
            phase.value[:4].upper(),
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="#333",
        )

    ax.set_xlim(0, n)
    ax.set_ylim(-0.5, 0.5)
    ax.set_yticks([])
    ax.set_xlabel("Training week")
    _week_ticks(ax, n)
    return fig


def _plot_weekly_volume(
    result: PipelineResult,
    metrics: PlanMetrics,
    title: str,
) -> plt.Figure:
    """Stacked bar of weekly hours by sport + capacity ceiling."""
    from endury.planner.foundations.ambition_tables import get_ambition_profile
    from endury.planner.macro.capacity import build_capacity_projection

    plan = result.plan
    n = len(plan.weeks)
    xs = np.arange(n)

    # Capacity ceiling.
    amb = get_ambition_profile(result.scenario.ambition)
    recent = result.scenario.recent_hours or {
        SportType.RUNNING: 6.0,
        SportType.CYCLING: 10.0,
        SportType.SWIMMING: 3.0,
    }
    cap = build_capacity_projection(
        recent,
        plan.weeks,
        hours_ramp_limit=amb.hours_ramp_limit,
        max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
        recovery_ceiling_factor=amb.recovery_load_fraction,
    )
    ceilings = np.array([cap.weekly_ceiling.get(ws.week, 0.0) for ws in plan.weeks])

    weekly = metrics.weekly_hours
    sports_present = [
        s
        for s in _ALL_SPORTS
        if any(weekly.get(ws.week, {}).get(s.value, 0.0) > 0 for ws in plan.weeks)
    ]

    fig, ax = plt.subplots(figsize=(14, 4.5), layout="constrained")
    _add_phase_bands(ax, plan)

    bot = np.zeros(n)
    for sport in sports_present:
        vals = np.array(
            [weekly.get(ws.week, {}).get(sport.value, 0.0) for ws in plan.weeks]
        )
        ax.bar(
            xs,
            vals,
            bottom=bot,
            color=_C[sport],
            width=0.72,
            alpha=0.88,
            label=sport.value.capitalize(),
        )
        bot += vals

    ax.plot(
        xs,
        ceilings,
        color="#333",
        ls="--",
        lw=1.6,
        marker=".",
        markersize=4,
        label="Capacity ceiling",
        zorder=3,
    )

    for ws in plan.weeks:
        if ws.mode == WeekMode.RECOVERY:
            ax.axvspan(
                ws.week - 0.5, ws.week + 0.5, color="#27AE60", alpha=0.09, zorder=0
            )
        elif ws.mode == WeekMode.TAPER:
            ax.axvspan(
                ws.week - 0.5, ws.week + 0.5, color="#8E44AD", alpha=0.09, zorder=0
            )

    _add_race_vlines(ax, plan)
    ax.set_xlabel("Training week")
    ax.set_ylabel("Hours")
    ax.set_title(title)
    ax.set_xlim(-0.7, n - 0.3)
    _week_ticks(ax, n)
    ph = _phase_patches(plan)
    h, lbl = ax.get_legend_handles_labels()
    handles = h + ph
    labels = lbl + [p.get_label() for p in ph]
    ax.legend(handles, labels, loc="upper left", ncol=3, fontsize=9)
    return fig


def _plot_trajectory(
    result: PipelineResult,
    metrics: PlanMetrics,
    title: str,
) -> plt.Figure:
    """Weighted CTL/ATL, per-sport absolute CTL/ATL, and weighted TSB trajectories."""
    plan = result.plan
    n = len(plan.weeks)
    xs = np.arange(n)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True, layout="constrained"
    )
    _add_phase_bands(ax1, plan)
    _add_phase_bands(ax2, plan)
    _add_phase_bands(ax3, plan)

    w_ctl_d = metrics.w_ctl / 7.0
    w_atl_d = metrics.w_atl / 7.0
    w_tsb_d = metrics.w_tsb / 7.0
    s_ctl_d = metrics.s_ctl / 7.0
    s_atl_d = metrics.s_atl / 7.0

    # Row 1: weighted composite + absolute sum CTL/ATL.
    ax1.plot(xs, w_ctl_d, color="#2980B9", lw=2.2, label="wCTL (fitness)")
    ax1.plot(xs, w_atl_d, color="#E74C3C", lw=2.2, label="wATL (fatigue)")
    ax1.plot(
        xs,
        s_ctl_d,
        color="#1F4E79",
        lw=1.6,
        ls=":",
        label="sCTL (sum of sports)",
    )
    ax1.plot(
        xs,
        s_atl_d,
        color="#922B21",
        lw=1.6,
        ls=":",
        label="sATL (sum of sports)",
    )
    ax1.set_ylabel("TSS/day")
    ax1.set_title(title + " - weighted + absolute CTL/ATL")
    ax1.legend(loc="upper left", ncol=2, fontsize=9)
    _add_race_vlines(ax1, plan)

    # Row 2: per-sport absolute CTL/ATL (the un-weighted per-sport view).
    present_sports = [sp for sp in _ALL_SPORTS if metrics.a_ctl[sp.value].max() > 0.0]
    for sp in present_sports:
        color = _C[sp]
        ax2.plot(
            xs,
            metrics.a_ctl[sp.value] / 7.0,
            color=color,
            lw=2.0,
            ls="-",
            label=f"aCTL {sp.value}",
        )
        ax2.plot(
            xs,
            metrics.a_atl[sp.value] / 7.0,
            color=color,
            lw=1.4,
            ls="--",
            alpha=0.85,
            label=f"aATL {sp.value}",
        )
    ax2.set_ylabel("TSS/day (per sport, absolute)")
    ax2.set_title("Per-sport CTL (solid) / ATL (dashed)")
    ax2.legend(loc="upper left", ncol=len(present_sports) or 1, fontsize=9)
    _add_race_vlines(ax2, plan)

    # Row 3: weighted TSB (freshness / race readiness).
    ax3.axhline(0, color="#AAA", lw=0.8, ls="--")
    ax3.fill_between(
        xs,
        w_tsb_d,
        0,
        where=w_tsb_d >= 0,
        color="#27AE60",
        alpha=0.35,
        label="Fresh (TSB > 0)",
    )
    ax3.fill_between(
        xs,
        w_tsb_d,
        0,
        where=w_tsb_d < 0,
        color="#E74C3C",
        alpha=0.35,
        label="Fatigued (TSB < 0)",
    )
    ax3.plot(xs, w_tsb_d, color="#8E44AD", lw=1.8)
    ax3.set_ylabel("TSS/day (wTSB)")
    ax3.set_xlabel("Training week")
    ax3.legend(loc="upper left", fontsize=9)
    _add_race_vlines(ax3, plan)
    _week_ticks(ax3, n)

    return fig


def _plot_session_count(
    result: PipelineResult,
    metrics: PlanMetrics,
    title: str,
) -> plt.Figure:
    """Bar chart of weekly session counts coloured by sport."""
    plan = result.plan
    n = len(plan.weeks)
    xs = np.arange(n)

    # Group sessions by week and sport.
    sport_counts: dict[SportType, np.ndarray] = {
        s: np.zeros(n, dtype=float) for s in _ALL_SPORTS
    }
    week_to_idx = {ws.week: i for i, ws in enumerate(plan.weeks)}
    for sess in result.sessions:
        idx = week_to_idx.get(sess.week)
        if idx is not None:
            sport_counts[sess.sport][idx] += 1

    fig, ax = plt.subplots(figsize=(14, 3.5), layout="constrained")
    _add_phase_bands(ax, plan)

    bot = np.zeros(n)
    for sport in _ALL_SPORTS:
        if sport_counts[sport].sum() == 0:
            continue
        ax.bar(
            xs,
            sport_counts[sport],
            bottom=bot,
            color=_C[sport],
            width=0.72,
            alpha=0.88,
            label=sport.value.capitalize(),
        )
        bot += sport_counts[sport]

    _add_race_vlines(ax, plan)
    ax.set_xlabel("Training week")
    ax.set_ylabel("Sessions")
    ax.set_title(title)
    ax.set_xlim(-0.7, n - 0.3)
    _week_ticks(ax, n)
    ax.legend(loc="upper left", fontsize=9)
    ph = _phase_patches(plan)
    h, lbl = ax.get_legend_handles_labels()
    handles = h + ph
    labels = lbl + [p.get_label() for p in ph]
    ax.legend(handles, labels, loc="upper left", ncol=3, fontsize=9)
    return fig


# Summary text


def _build_summary(
    result: PipelineResult,
    metrics: PlanMetrics,
    validation: dict[str, list[str]],
) -> str:
    """Build a compact ASCII summary string."""
    sc = result.scenario
    plan = result.plan
    lines: list[str] = []
    sep = "─" * 78

    lines += [
        sep,
        f"  {sc.name}  -  {sc.description}",
        f"  Goal: {plan.a_goal.goal_type.value}  "
        f"|  {'Block ends' if is_maintenance_goal(plan.a_goal.goal_type) else 'Race'}: "
        f"{plan.a_goal.race_date}  "
        f"|  Ambition: {sc.ambition.value}  |  {metrics.n_weeks} weeks  "
        f"|  {len(result.sessions)} sessions  |  {metrics.total_hours:.1f}h total",
        sep,
    ]

    # Phase/mode breakdown.
    lines.append("\n  Phase distribution:")
    for p, n in sorted(metrics.phase_distribution.items()):
        lines.append(f"    {p:<12} {n:>3} wk")
    lines.append("\n  Week modes:")
    for m, n in sorted(metrics.mode_distribution.items()):
        lines.append(f"    {m:<14} {n:>3} wk")

    # Hours by sport.
    lines.append("\n  Hours by sport:")
    for sp, h in sorted(metrics.hours_by_sport.items()):
        pct = h / metrics.total_hours * 100 if metrics.total_hours else 0
        lines.append(f"    {sp:<10} {h:6.1f}h  ({pct:.0f}%)")

    # Session families.
    lines.append("\n  Session families:")
    for fam, cnt in metrics.family_distribution.most_common():
        lines.append(f"    {fam:<14} {cnt:>4}")

    # Race-day (block-end for maintenance) fitness/freshness.
    end_lbl = "Block-end" if is_maintenance_goal(plan.a_goal.goal_type) else "Race-day"
    lines += [
        "",
        f"  {end_lbl} wCTL  : {metrics.race_day_w_ctl:.1f} weekly-TSS  ({metrics.race_day_w_ctl / 7:.1f} TSS/d)",
        f"  {end_lbl} wTSB  : {metrics.race_day_w_tsb:.1f} weekly-TSS  ({metrics.race_day_w_tsb / 7:.1f} TSS/d)",
        f"  {end_lbl} sCTL  : {metrics.race_day_s_ctl:.1f} weekly-TSS  ({metrics.race_day_s_ctl / 7:.1f} TSS/d)",
        f"  {end_lbl} sTSB  : {metrics.race_day_s_tsb:.1f} weekly-TSS  ({metrics.race_day_s_tsb / 7:.1f} TSS/d)",
        f"  Pipeline time  : {result.elapsed_s:.2f}s",
    ]

    # Validation.
    if validation["errors"] or validation["warnings"]:
        lines.append("\n  Validation:")
        for e in validation["errors"]:
            lines.append(f"    [ERROR]   {e}")
        for w in validation["warnings"]:
            lines.append(f"    [WARNING] {w}")
    else:
        lines.append("\n  Validation: OK")

    lines.append(sep)
    return "\n".join(lines)


# Weekly table helper


def _print_weekly_table(result: PipelineResult, metrics: PlanMetrics) -> None:
    """Print a per-week hours table to stdout."""
    plan = result.plan
    sports_present = [
        s for s in _ALL_SPORTS if metrics.hours_by_sport.get(s.value, 0) > 0
    ]
    sp_labels = [s.value[:3].upper() for s in sports_present]
    header = (
        f"{'Wk':>3}  {'Phase':<6}  {'Mode':<14}  "
        + "  ".join(f"{lbl:>6}" for lbl in sp_labels)
        + f"  {'Total':>7}  Sessions"
    )
    print(f"\n{header}")
    print("─" * len(header))
    for ws in plan.weeks:
        wh = metrics.weekly_hours.get(ws.week, {})
        vals = "  ".join(f"{wh.get(s.value, 0.0):6.2f}" for s in sports_present)
        total = sum(wh.values())
        cnt = metrics.weekly_session_count.get(ws.week, 0)
        mode_marker = ""
        if ws.mode == WeekMode.RACE:
            mode_marker = " <<<RACE"
        elif ws.mode == WeekMode.RECOVERY:
            mode_marker = " [deload]"
        elif ws.mode == WeekMode.TAPER:
            mode_marker = " [taper]"
        print(
            f"{ws.week:3d}  {ws.phase.value:<6}  {ws.mode.value:<14}  "
            f"{vals}  {total:7.2f}  {cnt:>8}{mode_marker}"
        )


# Replan diff (scenario-world replay)


def _print_replan_diff(
    original: PipelineResult,
    replanned: PipelineResult,
    metrics_orig: PlanMetrics,
    metrics_replan: PlanMetrics,
    week: int,
) -> None:
    """Print a calendar-aligned diff of the original plan tail vs the replan.

    The replanned plan's week ``j`` (0-indexed) corresponds to the original
    plan's week ``week + j``; both fall on the same calendar date. Phases must
    match exactly (the replan suffix property) — a ``MISMATCH`` verdict
    means that invariant regressed. Hours legitimately differ, since the QP
    re-seeds capacity from the synthesized mid-preparation state.

    Args:
        original: The original full-horizon pipeline run.
        replanned: The replan (starts ``week`` weeks later).
        metrics_orig: Metrics for ``original``.
        metrics_replan: Metrics for ``replanned``.
        week: The week the athlete replanned from.
    """
    sep = "─" * 78
    print(f"\n{sep}")
    print(
        f"  REPLAN DIFF — replanning at week {week} "
        f"(weeks 0..{week - 1} assumed complete)"
    )
    print(sep)

    orig_tail = [(w.phase.value, w.mode.value) for w in original.plan.weeks[week:]]
    rep_seq = [(w.phase.value, w.mode.value) for w in replanned.plan.weeks]
    verdict = "MATCH" if orig_tail == rep_seq else "MISMATCH ***"
    print(f"  phase suffix vs original[{week}:] : {verdict}")

    header = (
        f"\n  {'Wk':>3}  {'Date':<10}  {'Phase':<6}  {'Mode':<12}  "
        f"{'Orig h':>7}  {'Replan h':>8}  {'Δh':>6}"
    )
    print(header)
    print("  " + "─" * (len(header) - 3))

    start = replanned.scenario.training_start
    orig_total = 0.0
    replan_total = 0.0
    for j, ws in enumerate(replanned.plan.weeks):
        orig_week = week + j
        the_date = start + timedelta(weeks=j)
        oh = sum(metrics_orig.weekly_hours.get(orig_week, {}).values())
        rh = sum(metrics_replan.weekly_hours.get(ws.week, {}).values())
        orig_total += oh
        replan_total += rh
        marker = ""
        if ws.mode == WeekMode.RACE:
            marker = " <<<RACE"
        elif ws.mode == WeekMode.RECOVERY:
            marker = " [deload]"
        elif ws.mode == WeekMode.TAPER:
            marker = " [taper]"
        print(
            f"  {orig_week:3d}  {the_date.isoformat():<10}  {ws.phase.value:<6}  "
            f"{ws.mode.value:<12}  {oh:7.2f}  {rh:8.2f}  {rh - oh:+6.2f}{marker}"
        )

    print("  " + "─" * (len(header) - 3))
    print(
        f"  {'':>3}  {'Totals':<10}  {'':<6}  {'':<12}  "
        f"{orig_total:7.2f}  {replan_total:8.2f}  {replan_total - orig_total:+6.2f}"
    )
    print(sep)


# Swim distance helpers: swim pills render in meters


def _swim_meters_for_assignment(
    activity: ActivityDefinition,
    target_hours: float,
    resolver: LoadMetricContext,
) -> int | None:
    """Return total swim distance for a calendar pill, in whole meters.

    Duration-first materialization (``materialized_swim_meters``): the pill
    shows the distance of the workout built for the solved hours, so it
    matches the session card and recovers the authored menu metres.

    Returns ``None`` for swim activities whose steps are time-based rather
    than distance-based (e.g. the SWIM_CSS_TEST fixed protocol), letting the
    caller fall back to an h:mm pill.
    """
    return materialized_swim_meters(activity, target_hours, resolver)


def _fmt_swim_distance(meters: int) -> str:
    """Format meters for the calendar pill (always meters, never km).

    No thousands separator: the text calendar reserves only 14 chars per
    pill, and a comma pushes longer distances (e.g. ``1,800m``) past the
    truncation point.
    """
    return f"{meters}m"


# Calendar grid helper


def _print_calendar_grid(result: PipelineResult, metrics: PlanMetrics) -> None:
    """Print a week x day session calendar with per-week load statistics.

    Rows represent training weeks; columns represent days Mon-Sun.  The
    right-most columns show weekly total hours, raw weekly TSS, and per-sport
    absolute CTL (TSS/day) entering that week.

    Args:
        result: Pipeline result (plan, sessions, assignments).
        metrics: Computed plan metrics (CTL trajectories, weekly hours).
    """
    plan = result.plan
    training_start = result.scenario.training_start
    goal_type = plan.a_goal.goal_type
    resolver = build_synthetic_resolver()

    # slot_id → PlacedSession for sport lookup.
    slot_map: dict[str, PlacedSession] = {s.slot_id: s for s in result.sessions}

    # week_idx → day-of-week → [(sp_abbr, fm_abbr, hours, swim_meters_or_None)].
    _CalEntry = tuple[str, str, float, int | None]
    cal: dict[int, dict[int, list[_CalEntry]]] = defaultdict(lambda: defaultdict(list))
    for aa in result.assignments:
        week_idx = (aa.date - training_start).days // 7
        dow = aa.date.weekday()
        sess = slot_map.get(aa.slot_id)
        if sess is None:
            continue
        act = get_activity(aa.activity_key)
        sp = _SPORT_ABBR_3.get(sess.sport, "???")
        fm = _family_code(act)
        meters = (
            _swim_meters_for_assignment(act, aa.target_hours, resolver)
            if sess.sport == SportType.SWIMMING
            else None
        )
        cal[week_idx][dow].append((sp, fm, aa.target_hours, meters))

    # Inject race events (family code "RC"). Race distance is fixed by
    # the goal type and not carried on RaceEvent, so race pills keep the
    # h:mm format for all sports (including swimming).
    for re in plan.race_events:
        for sport, hours in re.duration_by_sport.items():
            sp = _SPORT_ABBR_3.get(sport, "???")
            cal[re.week][re.day_of_week].append((sp, "RC", hours, None))

    # Sports present in this plan.
    present_sports = [
        s for s in _ALL_SPORTS if metrics.hours_by_sport.get(s.value, 0) > 0
    ]

    # Raw weekly TSS (not Banister-smoothed).
    raw_tss_by_week: dict[int, float] = {}
    for ws in plan.weeks:
        mult = tss_per_hour(get_tid_fractions(ws.phase, ws.mode, goal_type))
        wh = metrics.weekly_hours.get(ws.week, {})
        raw_tss_by_week[ws.week] = sum(h * mult for h in wh.values())

    # Layout
    DAY_W = 15  # content chars per day column (fits "SWI TH cs 1825m")
    _WDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _cell(sp: str, fm: str, hrs: float, meters: int | None) -> str:
        if meters is not None:
            return f"{sp} {fm} {_fmt_swim_distance(meters)}"
        h = int(hrs)
        m = round((hrs - h) * 60)
        return f"{sp} {fm} {h}:{m:02d}"

    ctl_hdrs = [f"{_SPORT_ABBR_3[s][0]}CTL/d" for s in present_sports]
    day_hdr_str = " ".join(f"{d:<{DAY_W}}" for d in _WDAY)
    stat_hdr_str = f"  {'Hrs':>5}  {'aTSS':>5}" + "".join(f"  {h:>5}" for h in ctl_hdrs)
    hdr = f"{'Wk':>3}  {'Phase':<6}  {'Mode':<9}  {day_hdr_str}{stat_hdr_str}"
    rule = "─" * len(hdr)

    print(f"\n{'─' * 78}")
    print("  WEEKLY CALENDAR  (best viewed at ≥160 columns)")
    print(f"{'─' * 78}")
    print(hdr)
    print(rule)

    _MODE_LBL: dict[WeekMode, str] = {
        WeekMode.LOAD: "load",
        WeekMode.RECOVERY: "recovery",
        WeekMode.TAPER: "taper",
        WeekMode.RACE: "RACE <<<",
        WeekMode.RACE_RECOVERY: "race-rec",
    }

    prev_phase: PhaseType | None = None

    for idx, ws in enumerate(plan.weeks):
        k = ws.week

        # Blank separator at phase transitions.
        if prev_phase is not None and ws.phase != prev_phase:
            print()
        prev_phase = ws.phase

        day_entries: list[list[_CalEntry]] = [cal[k].get(d, []) for d in range(7)]
        n_rows = max((len(e) for e in day_entries), default=0)
        n_rows = max(n_rows, 1)

        # Weekly stat values.
        wh = metrics.weekly_hours.get(k, {})
        total_h = sum(wh.values())
        raw_tss = raw_tss_by_week.get(k, 0.0)
        h_int, m_int = divmod(round(total_h * 60), 60)
        ctl_vals = [f"{metrics.a_ctl[sp.value][idx] / 7:.1f}" for sp in present_sports]
        stat_str = f"  {h_int}:{m_int:02d}  {raw_tss:5.0f}" + "".join(
            f"  {c:>5}" for c in ctl_vals
        )

        mode_lbl = _MODE_LBL.get(ws.mode, ws.mode.value)
        prefix = f"{k:3d}  {ws.phase.value:<6}  {mode_lbl:<9}  "
        blank_pfx = " " * len(prefix)

        for row in range(n_rows):
            cells = []
            for d in range(7):
                ents = day_entries[d]
                if row < len(ents):
                    cell = _cell(*ents[row])[:DAY_W]
                    cells.append(f"{cell:<{DAY_W}}")
                else:
                    cells.append(" " * DAY_W)
            cells_str = " ".join(cells)
            if row == 0:
                print(f"{prefix}{cells_str}{stat_str}")
            else:
                print(f"{blank_pfx}{cells_str}")

    print(rule)


# HTML calendar grid (Jupyter)

_PHASE_CSS: dict[PhaseType, str] = {
    PhaseType.BASE: "ph-base",
    PhaseType.BUILD: "ph-build",
    PhaseType.PEAK: "ph-peak",
    PhaseType.TAPER: "ph-taper",
}
_MODE_CSS: dict[WeekMode, str] = {
    WeekMode.RACE: "mode-race",
    WeekMode.RECOVERY: "mode-recovery",
    WeekMode.TAPER: "mode-taper",
}
_SP_CSS: dict[str, str] = {"RUN": "s-run", "BIK": "s-bik", "SWI": "s-swi"}
_MODE_DISPLAY: dict[WeekMode, str] = {
    WeekMode.LOAD: "load",
    WeekMode.RECOVERY: "recovery",
    WeekMode.TAPER: "taper",
    WeekMode.RACE: "RACE ◀",
    WeekMode.RACE_RECOVERY: "race-rec",
}

_CALENDAR_CSS = """\
<style>
.cal-wrap{font-family:ui-monospace,'Cascadia Code',monospace;
  background:#fafafa;color:#222;padding:8px;border-radius:4px;
  display:inline-block}
.cal-title{font-size:13px;font-weight:600;margin:8px 0 2px;color:#222}
.cal-title span{color:#555!important}
.cal-legend{font-size:10px;color:#555;margin:0 0 6px}
.cal-table{border-collapse:collapse;font-size:11px;white-space:nowrap;
  background:#fff;color:#222}
.cal-table th{padding:4px 8px;background:#444;color:#eee;text-align:center;
  font-weight:600;border:1px solid #555}
.cal-table td{padding:3px 5px;border:1px solid #ddd;vertical-align:top;
  background:#fff;color:#222}
.ph-base  td{background:#e7f3f3}
.ph-build td{background:#fbe3cf}
.ph-peak  td{background:#f7d3c9}
.ph-taper td{background:#e4e8ea}
.phase-sep td{border-top:2px solid #888!important}
td.mode-race    {color:#c0392b;font-weight:bold}
td.mode-recovery{color:#1e8449}
td.mode-taper   {color:#7d3c98}
.sess{display:block;border-radius:3px;padding:1px 5px;margin:1px 0;
  font-size:10px;white-space:nowrap;line-height:1.6}
.s-run {background:#fde8e8;color:#922b21}
.s-bik {background:#d6eaf8;color:#1a5276}
.s-swi {background:#d0ece7;color:#0e6655}
.s-race{background:#f9ebea;color:#922b21;font-weight:bold;border:1px solid #e74c3c}
.cal-table td.cal-stat{text-align:right;color:#222;font-variant-numeric:tabular-nums;
  white-space:nowrap}
</style>"""


def _calendar_grid_html(result: PipelineResult, metrics: PlanMetrics) -> str:
    """Return a self-contained HTML string for the session calendar.

    Designed for ``IPython.display.HTML`` in Jupyter notebooks.  Each
    training week is one table row; sessions appear as coloured pills;
    the right-side stat columns show weekly hours, raw aTSS, and per-sport
    CTL/day entering that week.

    Args:
        result: Pipeline result (plan, sessions, assignments).
        metrics: Computed plan metrics.

    Returns:
        HTML string with embedded ``<style>`` block.
    """
    plan = result.plan
    training_start = result.scenario.training_start
    goal_type = plan.a_goal.goal_type
    resolver = build_synthetic_resolver()

    slot_map: dict[str, PlacedSession] = {s.slot_id: s for s in result.sessions}

    _CalEntry = tuple[str, str, float, int | None]
    cal: dict[int, dict[int, list[_CalEntry]]] = defaultdict(lambda: defaultdict(list))
    for aa in result.assignments:
        week_idx = (aa.date - training_start).days // 7
        dow = aa.date.weekday()
        sess = slot_map.get(aa.slot_id)
        if sess is None:
            continue
        act = get_activity(aa.activity_key)
        sp = _SPORT_ABBR_3.get(sess.sport, "???")
        fm = _family_code(act)
        meters = (
            _swim_meters_for_assignment(act, aa.target_hours, resolver)
            if sess.sport == SportType.SWIMMING
            else None
        )
        cal[week_idx][dow].append((sp, fm, aa.target_hours, meters))

    for re in plan.race_events:
        for sport, hours in re.duration_by_sport.items():
            sp = _SPORT_ABBR_3.get(sport, "???")
            cal[re.week][re.day_of_week].append((sp, "RC", hours, None))

    present_sports = [
        s for s in _ALL_SPORTS if metrics.hours_by_sport.get(s.value, 0) > 0
    ]

    raw_tss_by_week: dict[int, float] = {}
    for ws in plan.weeks:
        mult = tss_per_hour(get_tid_fractions(ws.phase, ws.mode, goal_type))
        wh = metrics.weekly_hours.get(ws.week, {})
        raw_tss_by_week[ws.week] = sum(h * mult for h in wh.values())

    def _pill(sp: str, fm: str, hrs: float, meters: int | None) -> str:
        css = "s-race" if fm == "RC" else _SP_CSS.get(sp, "s-run")
        if fm == "RC":
            h = int(hrs)
            m = round((hrs - h) * 60)
            label = f"RACE {h}:{m:02d}"
        elif meters is not None:
            label = f"{sp} {fm} {_fmt_swim_distance(meters)}"
        else:
            h = int(hrs)
            m = round((hrs - h) * 60)
            label = f"{sp} {fm} {h}:{m:02d}"
        return f'<span class="sess {css}">{label}</span>'

    def _day_td(entries: list[_CalEntry]) -> str:
        return "<td>" + "".join(_pill(*e) for e in entries) + "</td>"

    _WDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ctl_hdrs = [f"{_SPORT_ABBR_3[s][0]}CTL/d" for s in present_sports]

    day_ths = "".join(f"<th>{d}</th>" for d in _WDAY)
    stat_ths = "<th>Hrs</th><th>aTSS</th>" + "".join(f"<th>{h}</th>" for h in ctl_hdrs)
    thead = (
        f"<thead><tr><th>Wk</th><th>Phase</th><th>Mode</th>"
        f"{day_ths}{stat_ths}</tr></thead>"
    )

    rows: list[str] = []
    prev_phase: PhaseType | None = None
    for idx, ws in enumerate(plan.weeks):
        k = ws.week
        sep = prev_phase is not None and ws.phase != prev_phase
        prev_phase = ws.phase

        day_tds = "".join(_day_td(cal[k].get(d, [])) for d in range(7))

        wh = metrics.weekly_hours.get(k, {})
        total_h = sum(wh.values())
        raw_tss = raw_tss_by_week.get(k, 0.0)
        h_int, m_int = divmod(round(total_h * 60), 60)
        ctl_tds = "".join(
            f'<td class="cal-stat">{metrics.a_ctl[sp.value][idx] / 7:.1f}</td>'
            for sp in present_sports
        )
        stat_tds = (
            f'<td class="cal-stat">{h_int}:{m_int:02d}</td>'
            f'<td class="cal-stat">{raw_tss:.0f}</td>' + ctl_tds
        )

        ph_css = _PHASE_CSS.get(ws.phase, "")
        sep_cls = " phase-sep" if sep else ""
        mode_css = _MODE_CSS.get(ws.mode, "")
        mode_val = _MODE_DISPLAY.get(ws.mode, ws.mode.value)
        mode_td = (
            f'<td class="{mode_css}">{mode_val}</td>'
            if mode_css
            else f"<td>{mode_val}</td>"
        )

        rows.append(
            f'<tr class="{ph_css}{sep_cls}">'
            f"<td>{k}</td><td>{ws.phase.value}</td>{mode_td}"
            f"{day_tds}{stat_tds}</tr>"
        )

    sc = result.scenario
    title = (
        f'<p class="cal-title">Weekly Calendar - {sc.name}'
        f" &nbsp;<span style='font-weight:400;font-size:11px;color:#666'>"
        f"{sc.description}</span></p>"
        f'<p class="cal-legend">'
        f"Session codes: &nbsp;"
        f'<span class="sess s-run" style="display:inline-block">RUN</span> running &nbsp;'
        f'<span class="sess s-bik" style="display:inline-block">BIK</span> cycling &nbsp;'
        f'<span class="sess s-swi" style="display:inline-block">SWI</span> swimming &nbsp;'
        f"&nbsp;· families: EZ easy · LG long · TM tempo · TH threshold "
        f"· IV interval · RV recovery · XT test · RC race"
        f"<br/>suffixes: dr drill · cs CSS · pg progression · "
        f"bk brick · ss sweet-spot · rp race-pace · mp marathon-pace · sp speed"
        f"</p>"
    )
    tbody = "<tbody>" + "\n".join(rows) + "</tbody>"
    table = (
        f'<div class="cal-wrap">{title}'
        f'<table class="cal-table">{thead}{tbody}</table></div>'
    )
    return _CALENDAR_CSS + table


def show_calendar_grid(result: PipelineResult, metrics: PlanMetrics) -> None:
    """Display the session calendar - HTML in Jupyter, ASCII in a terminal.

    Automatically detects the execution context.  In a Jupyter kernel the
    calendar is rendered as a styled HTML table via ``IPython.display``;
    in a plain terminal the ASCII grid is printed instead.

    Args:
        result: Pipeline result (plan, sessions, assignments).
        metrics: Computed plan metrics.
    """
    try:
        from IPython import get_ipython

        if get_ipython() is not None:
            from IPython.display import HTML
            from IPython.display import display as ipy_display

            ipy_display(HTML(_calendar_grid_html(result, metrics)))
            return
    except ImportError:
        pass
    _print_calendar_grid(result, metrics)


# Public API


def generate_report(
    scenario: Scenario,
    show_plots: bool = False,
    use_macro_solver: bool = True,
) -> ScenarioReport:
    """Run pipeline and generate a complete report for a scenario.

    Args:
        scenario: The scenario to evaluate.
        show_plots: Whether to call plt.show() after building figures.
        use_macro_solver: If True (default), run the cvxpy QP macro
            planner. If False, use session menu target hours directly.

    Returns:
        ScenarioReport with metrics, figures, and summary text.
    """
    _setup_plot_style()
    pipeline = run_full_pipeline(scenario, use_macro_solver=use_macro_solver)
    metrics = _compute_metrics(pipeline)
    validation = _run_validation(pipeline)
    summary = _build_summary(pipeline, metrics, validation)

    label = f"{scenario.name} - {scenario.description}"
    figures = [
        ("Phase timeline", _plot_phase_timeline(pipeline.plan, label)),
        ("Weekly volume", _plot_weekly_volume(pipeline, metrics, label)),
        ("CTL/ATL trajectory", _plot_trajectory(pipeline, metrics, label)),
        ("Session count", _plot_session_count(pipeline, metrics, label)),
    ]

    if show_plots:
        plt.show(block=False)

    return ScenarioReport(
        scenario=scenario,
        pipeline=pipeline,
        metrics=metrics,
        figures=figures,
        summary_text=summary,
    )


def display_report(report: ScenarioReport) -> None:
    """Print the report summary, weekly table, calendar grid, and display figures.

    Args:
        report: Output of ``generate_report``.
    """
    print(report.summary_text)
    _print_weekly_table(report.pipeline, report.metrics)
    show_calendar_grid(report.pipeline, report.metrics)
    plt.show(block=False)


# CLI


def _run_replan_report(
    scenario: Scenario,
    week: int,
    *,
    use_macro_solver: bool = True,
) -> None:
    """Replay a scenario, replan it at ``week``, and print the state + diff.

    Args:
        scenario: The scenario to run and replan.
        week: Week to replan from (``>= 1``; ``0`` would mean no replan).
        use_macro_solver: Passed through to both pipeline runs.
    """
    if week < 1:
        print(
            "--replan-at-week must be >= 1 (0 = no replan / fresh plan).",
            file=sys.stderr,
        )
        return
    try:
        original, replanned = replay_replan(
            scenario, week, use_macro_solver=use_macro_solver
        )
    except ValueError as exc:
        print(f"\n[FAILED] replan at week {week}: {exc}", file=sys.stderr)
        return

    metrics_orig = _compute_metrics(original)
    metrics_replan = _compute_metrics(replanned)
    print(f"\nSynthesized athlete state at week {week} (perfect compliance):")
    print(format_athlete_state(replanned.scenario))
    _print_replan_diff(original, replanned, metrics_orig, metrics_replan, week)


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m devtools.planner.report_sweep.report_cli``."""
    parser = argparse.ArgumentParser(
        prog="devtools.planner.report_sweep.report_cli",
        description="Run and display a planner scenario report.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", metavar="NAME", help="Scenario name.")
    group.add_argument("--all", action="store_true", help="Run all scenarios.")
    group.add_argument("--tag", metavar="TAG", help="Run scenarios matching tag.")
    group.add_argument("--list", action="store_true", help="List available scenarios.")
    parser.add_argument(
        "--no-plots", action="store_true", help="Skip matplotlib figures."
    )
    parser.add_argument(
        "--no-macro-solver",
        action="store_true",
        help="Skip cvxpy QP macro planner; use session menu target hours.",
    )
    parser.add_argument(
        "--replan-at-week",
        type=int,
        default=None,
        metavar="K",
        help="Replay mode: generate the plan, then replan from week K under "
        "perfect compliance and print an original-vs-replan diff. "
        "Requires --scenario.",
    )
    args = parser.parse_args(argv)

    if args.list:
        list_scenarios()
        return

    if args.replan_at_week is not None:
        if not args.scenario:
            parser.error("--replan-at-week requires --scenario (not --all/--tag).")
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario: {args.scenario!r}")
            print("Available:", ", ".join(sorted(SCENARIOS)))
            sys.exit(1)
        _run_replan_report(
            SCENARIOS[args.scenario],
            args.replan_at_week,
            use_macro_solver=not args.no_macro_solver,
        )
        return

    if args.scenario:
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario: {args.scenario!r}")
            print("Available:", ", ".join(sorted(SCENARIOS)))
            sys.exit(1)
        targets = [SCENARIOS[args.scenario]]
    elif args.tag:
        targets = scenarios_by_tag(args.tag)
        if not targets:
            print(f"No scenarios with tag: {args.tag!r}")
            sys.exit(1)
    else:
        targets = list(SCENARIOS.values())

    for sc in targets:
        try:
            print(format_athlete_state(sc))
            report = generate_report(
                sc,
                show_plots=False,
                use_macro_solver=not args.no_macro_solver,
            )
            print(report.summary_text)
            _print_weekly_table(report.pipeline, report.metrics)
            _print_calendar_grid(report.pipeline, report.metrics)
            if not args.no_plots:
                plt.show(block=False)
        except Exception as exc:
            print(f"\n[FAILED] {sc.name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
