"""Bridge, sized and card TSS pricing of every scorecard-matrix plan.

Three prices for the same session hours:

* **bridge** — one TSS/h per week from the TID tables
  (``tss_per_hour(get_tid_fractions(ws.phase, ws.mode, ws.effective_goal))``),
  what the macro QP priced every hour with until 2026-09-24 and still
  prices race hours with;
* **sized** — the QP's own load: the session's activity price frozen at its
  menu target (``PlanResult.session_tss_per_hour``) times the solved hours;
* **card** — the athlete's workout card, the activity price at the solved
  hours (``session_tss_per_hour``, the one home for session pricing).

The tool prints all three per week, per activity family and per activity
key on every ``(goal, ambition, weeks)`` cell the plan-quality scorecard
matrix sweeps (``devtools/quality/planner/score_quality.py``,
``GOAL_MATRIX_HORIZONS``), the per-plan ratios, and the race-day CTL/TSB
each pricing implies. ``card / sized`` is the residual the scorecard rule
``load_pricing_agreement`` bounds; ``card / bridge`` is the reference
reading the change to per-activity pricing was measured against.

Usage:
    make run-devtool TOOL=planner_load_pricing
    make run-devtool TOOL=planner_load_pricing ARGS='--goal ironman --ambition competitive'
    make run-devtool TOOL=planner_load_pricing ARGS='--weeks 20 --brief'
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from devtools.quality.planner.score_quality import (
    GOAL_MATRIX_HORIZONS,
    build_default_scenario,
)
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PlacedSession,
    SportType,
)
from endury.planner.macro.macro_config import ATL_DECAY_WEEKLY, CTL_DECAY_WEEKLY
from endury.planner.macro.macro_planner import (
    race_day_carryover,
    race_day_session_weights,
)
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour
from endury.planner.pipeline import (
    PlanResult,
    default_profile_for,
    generate_plan,
    initial_load_state,
)

_ALL_SPORTS: tuple[SportType, ...] = (
    SportType.RUNNING,
    SportType.CYCLING,
    SportType.SWIMMING,
)

_Variant = Literal["bridge", "sized", "card"]
_VARIANTS: tuple[_Variant, ...] = ("bridge", "sized", "card")


def _price(
    result: PlanResult, session: PlacedSession, bridge: float, variant: _Variant
) -> float:
    """Return the session's TSS under one pricing variant.

    Args:
        result: The plan the session belongs to (holds the QP's coefficients).
        session: A QP-sized session.
        bridge: The week's TID bridge in TSS/h.
        variant: ``"bridge"``, ``"sized"`` or ``"card"``.

    Returns:
        The session's TSS under that pricing.
    """
    hours = session.target_hours
    if variant == "bridge":
        return hours * bridge
    if variant == "sized":
        return hours * result.session_tss_per_hour[session.slot_id]
    return hours * session_tss_per_hour(session.activity_key, hours)


# ---------------------------------------------------------------------------
# Per-cell report
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Priced:
    """Hours priced three ways, for a week, a family, a key or a plan."""

    hours: float = 0.0
    bridge_tss: float = 0.0
    sized_tss: float = 0.0
    card_tss: float = 0.0

    def add(self, hours: float, bridge: float, sized: float, card: float) -> None:
        """Fold one session's hours and prices in."""
        self.hours += hours
        self.bridge_tss += bridge
        self.sized_tss += sized
        self.card_tss += card

    @property
    def card_tss_per_hour(self) -> float:
        """Return card TSS per hour, or 0.0 for no hours."""
        return self.card_tss / self.hours if self.hours > 0 else 0.0

    @property
    def card_over_bridge(self) -> float:
        """Return card / bridge TSS, or 0.0 for no bridge TSS."""
        return self.card_tss / self.bridge_tss if self.bridge_tss > 0 else 0.0

    @property
    def card_over_sized(self) -> float:
        """Return card / sized TSS, or 0.0 for no sized TSS."""
        return self.card_tss / self.sized_tss if self.sized_tss > 0 else 0.0


@dataclass(slots=True)
class WeeklyRow:
    """One plan week's hours priced three ways."""

    week: int
    phase: str
    mode: str
    priced: Priced


@dataclass(slots=True)
class RaceDayState:
    """Summed race-day CTL/TSB under one pricing variant."""

    ctl: float
    tsb: float

    @property
    def tsb_over_ctl(self) -> float:
        """Return TSB/CTL, or 0.0 when CTL is non-positive."""
        return self.tsb / self.ctl if self.ctl > 0 else 0.0


@dataclass(slots=True)
class CellReport:
    """Pricing measurement for one (goal, ambition, weeks) cell."""

    goal: GoalType
    ambition: AmbitionLevel
    weeks: int
    weekly_rows: list[WeeklyRow]
    family_rows: dict[str, Priced]
    key_rows: dict[str, Priced]
    plan: Priced
    race_day: dict[_Variant, RaceDayState]

    @property
    def name(self) -> str:
        """Return the cell's identifying name, e.g. ``ironman moderate 20wk``."""
        return f"{self.goal.value} {self.ambition.value} {self.weeks}wk"

    @property
    def min_weekly_ratio(self) -> float:
        """Return the smallest per-week card/bridge ratio (0.0 if no weeks)."""
        return min((r.priced.card_over_bridge for r in self.weekly_rows), default=0.0)

    @property
    def max_weekly_ratio(self) -> float:
        """Return the largest per-week card/bridge ratio (0.0 if no weeks)."""
        return max((r.priced.card_over_bridge for r in self.weekly_rows), default=0.0)

    @property
    def worst_sized_deviation(self) -> float:
        """Return the worst week's ``|card / sized - 1|`` (the rule's reading)."""
        return max(
            (abs(r.priced.card_over_sized - 1.0) for r in self.weekly_rows),
            default=0.0,
        )


# ---------------------------------------------------------------------------
# Race-day trajectory (mirrors devtools/planner/_shared/metrics.py)
# ---------------------------------------------------------------------------


def _week_bridge(result: PlanResult, week: int) -> float:
    """Return the TID bridge (TSS/h) of one plan week."""
    ws = result.phase_plan.weeks[week]
    return tss_per_hour(get_tid_fractions(ws.phase, ws.mode, ws.effective_goal))


def _race_day(
    result: PlanResult,
    ctl_0: dict[SportType, float],
    atl_0: dict[SportType, float],
    variant: _Variant,
) -> RaceDayState:
    """Seed, forward-simulate and step to the race-day instant under *variant*.

    Mirrors ``metrics._compute_trajectory`` (weekly Banister recursion, state
    entering each week) and ``metrics._race_day_state`` (``race_day_carryover``
    on the race-week entry state, ``race_day_session_weights`` on each
    race-week session). Race-event hours are priced by the bridge under every
    variant; only training sessions change price.

    Args:
        result: The plan to simulate.
        ctl_0: Per-sport CTL seed (the athlete's sports only).
        atl_0: Per-sport ATL seed.
        variant: Session pricing.

    Returns:
        Summed CTL/TSB at the race-day instant, in weekly-TSS units.
    """
    plan = result.phase_plan
    alpha, beta = CTL_DECAY_WEEKLY, ATL_DECAY_WEEKLY
    ctl = {sp: ctl_0.get(sp, 0.0) for sp in _ALL_SPORTS}
    atl = {sp: atl_0.get(sp, 0.0) for sp in _ALL_SPORTS}
    sessions_by_week: dict[int, list[PlacedSession]] = defaultdict(list)
    for s in result.sessions:
        sessions_by_week[s.week].append(s)

    races = sorted(plan.race_events, key=lambda re: re.week)
    event = races[-1] if races else None
    race_week = min(event.week if event else len(plan.weeks) - 1, len(plan.weeks) - 1)
    race_dow = event.day_of_week if event else 6

    for ws in plan.weeks:
        if ws.week == race_week:
            break
        bridge = _week_bridge(result, ws.week)
        sport_tss: dict[SportType, float] = defaultdict(float)
        for sess in sessions_by_week.get(ws.week, []):
            sport_tss[sess.sport] += _price(result, sess, bridge, variant)
        for re in plan.race_events:
            if re.week == ws.week:
                for sp, h in re.duration_by_sport.items():
                    sport_tss[sp] += h * bridge
        for sp in _ALL_SPORTS:
            ctl[sp] = alpha * ctl[sp] + (1 - alpha) * sport_tss[sp]
            atl[sp] = beta * atl[sp] + (1 - beta) * sport_tss[sp]

    ctl_carry, atl_carry = race_day_carryover(race_dow)
    ctl = {sp: ctl_carry * ctl[sp] for sp in _ALL_SPORTS}
    atl = {sp: atl_carry * atl[sp] for sp in _ALL_SPORTS}
    bridge = _week_bridge(result, race_week)
    for sess in sessions_by_week.get(race_week, []):
        w_ctl, w_atl = race_day_session_weights(sess.day_of_week, race_dow)
        tss = _price(result, sess, bridge, variant)
        ctl[sess.sport] += w_ctl * tss
        atl[sess.sport] += w_atl * tss
    summed_ctl = sum(ctl.values())
    return RaceDayState(ctl=summed_ctl, tsb=summed_ctl - sum(atl.values()))


# ---------------------------------------------------------------------------
# Cell measurement
# ---------------------------------------------------------------------------


def measure_cell(goal: GoalType, ambition: AmbitionLevel, weeks: int) -> CellReport:
    """Measure the three pricings for one scorecard matrix cell.

    Args:
        goal: Race goal type.
        ambition: Ambition tier.
        weeks: Plan length in weeks.

    Returns:
        A ``CellReport`` with per-week, per-family, per-activity-key and
        race-day breakdowns.
    """
    ensure_registry_loaded()
    result = generate_plan(build_default_scenario(weeks, ambition, goal).to_request())
    sessions_by_week: dict[int, list[PlacedSession]] = defaultdict(list)
    for s in result.sessions:
        sessions_by_week[s.week].append(s)

    weekly_rows: list[WeeklyRow] = []
    family_rows: dict[str, Priced] = {}
    key_rows: dict[str, Priced] = {}
    plan = Priced()
    for k in sorted(sessions_by_week):
        ws = result.phase_plan.weeks[k]
        bridge = _week_bridge(result, k)
        week = Priced()
        for s in sessions_by_week[k]:
            prices = tuple(_price(result, s, bridge, v) for v in _VARIANTS)
            family = get_activity(s.activity_key).family.value
            for acc in (
                week,
                plan,
                family_rows.setdefault(family, Priced()),
                key_rows.setdefault(s.activity_key.value, Priced()),
            ):
                acc.add(s.target_hours, *prices)
        weekly_rows.append(WeeklyRow(k, ws.phase.value, ws.mode.value, week))

    # The seed the scorecard derives for this plan (its solved opening week).
    ctl_0, atl_0 = initial_load_state(
        default_profile_for(goal, ambition),
        goal,
        [(s.activity_key, s.target_hours) for s in sessions_by_week.get(0, [])],
    )
    return CellReport(
        goal=goal,
        ambition=ambition,
        weeks=weeks,
        weekly_rows=weekly_rows,
        family_rows=dict(sorted(family_rows.items())),
        key_rows=dict(sorted(key_rows.items())),
        plan=plan,
        race_day={v: _race_day(result, ctl_0, atl_0, v) for v in _VARIANTS},
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_GROUP_HEADER = (
    "  {:<27}   hours  bridge_tss   sized_tss    card_tss  card_tss/h  "
    "card/bridge  card/sized"
)


def _group_line(name: str, p: Priced) -> str:
    """Render one family, key or matrix-total row."""
    return (
        f"  {name:<27}  {p.hours:>6.1f}  {p.bridge_tss:>10.1f}  {p.sized_tss:>10.1f}  "
        f"{p.card_tss:>10.1f}  {p.card_tss_per_hour:>10.1f}  "
        f"{p.card_over_bridge:>11.3f}  {p.card_over_sized:>10.3f}"
    )


def format_cell(report: CellReport, brief: bool = False) -> str:
    """Render one cell's report as plain, aligned text.

    Args:
        report: The cell's measurement.
        brief: Skip the per-week rows when True.

    Returns:
        Multi-line report text (no trailing newline).
    """
    lines: list[str] = [f"== {report.name} =="]
    if not brief:
        lines.append(
            "  wk  phase   mode         hours  bridge_tss   sized_tss    card_tss  "
            "card/bridge  card/sized"
        )
        lines.extend(
            f"  {row.week:>3}  {row.phase:<6}  {row.mode:<10}  "
            f"{row.priced.hours:>6.1f}  {row.priced.bridge_tss:>10.1f}  "
            f"{row.priced.sized_tss:>10.1f}  {row.priced.card_tss:>10.1f}  "
            f"{row.priced.card_over_bridge:>11.3f}  {row.priced.card_over_sized:>10.3f}"
            for row in report.weekly_rows
        )
    lines.append(_GROUP_HEADER.format("family"))
    lines.extend(_group_line(name, p) for name, p in report.family_rows.items())
    lines.append(_group_line("plan", report.plan))
    lines.append(
        f"  plan ratios: card/bridge={report.plan.card_over_bridge:.3f} "
        f"(weekly min {report.min_weekly_ratio:.3f}, max {report.max_weekly_ratio:.3f})  "
        f"card/sized={report.plan.card_over_sized:.3f} "
        f"(worst-week |card/sized-1| {report.worst_sized_deviation:.4f})"
    )
    for label, state in report.race_day.items():
        lines.append(
            f"  race day ({label:<6}): ctl={state.ctl:>7.1f}  tsb={state.tsb:>7.1f}  "
            f"tsb/ctl={state.tsb_over_ctl:>6.3f}"
        )
    return "\n".join(lines)


def _accumulate(totals: dict[str, Priced], rows: dict[str, Priced]) -> None:
    """Fold one cell's family or key rows into the matrix-wide totals."""
    for name, p in rows.items():
        totals.setdefault(name, Priced()).add(
            p.hours, p.bridge_tss, p.sized_tss, p.card_tss
        )


def format_matrix_summary(cells: list[CellReport]) -> str:
    """Render the matrix-wide per-family, per-key and per-plan summary.

    Args:
        cells: Every measured cell.

    Returns:
        Multi-line summary text (no trailing newline).
    """
    lines: list[str] = ["=== Matrix summary ==="]
    family_totals: dict[str, Priced] = {}
    key_totals: dict[str, Priced] = {}
    for cell in cells:
        _accumulate(family_totals, cell.family_rows)
        _accumulate(key_totals, cell.key_rows)
    for title, totals in (("family", family_totals), ("key", key_totals)):
        lines.append(f"Per {title}:")
        lines.append(_GROUP_HEADER.format(title))
        lines.extend(_group_line(name, totals[name]) for name in sorted(totals))
    if cells:
        for label, values in (
            ("card/bridge", [(c.plan.card_over_bridge, c.name) for c in cells]),
            ("card/sized", [(c.plan.card_over_sized, c.name) for c in cells]),
            (
                "worst-week |card/sized-1|",
                [(c.worst_sized_deviation, c.name) for c in cells],
            ),
        ):
            ordered = sorted(values)
            lines.append(
                f"Plan {label}: min={ordered[0][0]:.4f} ({ordered[0][1]})  "
                f"median={statistics.median(v for v, _ in values):.4f}  "
                f"max={ordered[-1][0]:.4f} ({ordered[-1][1]})"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _iter_cells(
    goal_filter: GoalType | None,
    ambition_filter: AmbitionLevel | None,
    weeks_filter: int | None,
) -> list[tuple[GoalType, AmbitionLevel, int]]:
    """Return the (goal, ambition, weeks) cells to measure, after CLI filters."""
    return [
        (goal, ambition, weeks)
        for goal, horizons in GOAL_MATRIX_HORIZONS.items()
        if goal_filter is None or goal == goal_filter
        for ambition in AmbitionLevel
        if ambition_filter is None or ambition == ambition_filter
        for weeks in horizons
        if weeks_filter is None or weeks == weeks_filter
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--goal",
        default=None,
        choices=[g.value for g in GoalType if g in GOAL_MATRIX_HORIZONS],
    )
    parser.add_argument(
        "--ambition", default=None, choices=[a.value for a in AmbitionLevel]
    )
    parser.add_argument(
        "--weeks", type=int, default=None, help="restrict to one horizon"
    )
    parser.add_argument("--brief", action="store_true", help="skip the per-week rows")
    args = parser.parse_args(argv)

    goal_filter = GoalType(args.goal) if args.goal else None
    ambition_filter = AmbitionLevel(args.ambition) if args.ambition else None
    cells = _iter_cells(goal_filter, ambition_filter, args.weeks)
    if not cells:
        print("No matrix cells match the given filters.")
        return 1

    t0 = time.monotonic()
    reports = [measure_cell(goal, ambition, weeks) for goal, ambition, weeks in cells]
    elapsed = time.monotonic() - t0
    for report in reports:
        print(format_cell(report, brief=args.brief))
        print()
    print(format_matrix_summary(reports))
    print(f"\n{len(reports)} cell(s) in {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
