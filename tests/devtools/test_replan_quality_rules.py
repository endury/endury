"""Behavioral checks for the replan-quality rules in ``score_quality``.

These grade a mid-preparation *replan* (the diff between a plan and its
regeneration part-way through), a class the fresh ``score`` sweep never sees.
The Ironman sweep reads clean, so each rule is exercised two ways: it must
read 0.0 on a healthy diff (and on the real deterministic replay) and *fire*
on a hand-built diff that violates its property — otherwise the rule would
silently rubber-stamp a regression.
"""

from __future__ import annotations

from devtools.planner._shared.replay import (
    ReplanDiff,
    ReplanWeekDelta,
    build_replan_diff,
    replay_replan,
)
from devtools.planner._shared.scenarios import SCENARIOS
from devtools.quality.planner.score_quality import (
    REPLAN_RULES,
    _replan_fixed_bound_stability,
    _replan_phase_suffix,
    _replan_seam_convergence,
    build_default_scenario,
    score_replan,
    score_replan_sweep,
)
from endury.planner.foundations.types import (
    AmbitionLevel,
    PhaseType,
    WeekMode,
)


def _row(
    phase: PhaseType, mode: WeekMode, orig: float, replan: float
) -> ReplanWeekDelta:
    return ReplanWeekDelta(phase=phase, mode=mode, orig_hours=orig, replan_hours=replan)


def _diff(rows: list[ReplanWeekDelta], *, suffix_match: bool = True) -> ReplanDiff:
    return ReplanDiff(week=8, phase_suffix_match=suffix_match, rows=tuple(rows))


# replan_phase_suffix (R-PHASE-002b hard invariant)


def test_phase_suffix_rule_discriminates() -> None:
    assert _replan_phase_suffix(_diff([], suffix_match=True)) == 0.0
    assert _replan_phase_suffix(_diff([], suffix_match=False)) == 1.0


# replan_fixed_bound_stability


def test_fixed_bound_stability_tolerates_small_drift() -> None:
    # The healthy sweep drifted deload/taper/race weeks by up to 0.35 h — well
    # inside the 0.5 h band — so a plan whose only fixed-bound moves are ~0.3 h
    # reads 0.0. LOAD-week deltas (which legitimately move) are ignored.
    rows = [
        _row(PhaseType.BUILD, WeekMode.LOAD, 12.0, 9.5),  # ignored
        _row(PhaseType.BUILD, WeekMode.RECOVERY, 6.15, 6.15),
        _row(PhaseType.PEAK, WeekMode.RECOVERY, 6.50, 6.80),  # +0.30
        _row(PhaseType.TAPER, WeekMode.TAPER, 4.58, 4.58),
        _row(PhaseType.TAPER, WeekMode.RACE, 3.67, 3.67),
    ]
    assert _replan_fixed_bound_stability(_diff(rows)) == 0.0


def test_fixed_bound_stability_fires_on_moved_taper() -> None:
    # A taper week shifted 2.5 h is well past even the widened TAPER band —
    # the re-seed is perturbing a fixed-bound week far beyond what tuning
    # race-day freshness from the measured state could justify.
    rows = [
        _row(PhaseType.TAPER, WeekMode.TAPER, 4.58, 7.08),  # +2.5
        _row(PhaseType.TAPER, WeekMode.RACE, 3.67, 3.67),
    ]
    assert _replan_fixed_bound_stability(_diff(rows)) == 1.0


def test_fixed_bound_tolerance_is_per_mode() -> None:
    # The race-day freshness band makes TAPER volume state-responsive, so a
    # ~0.9 h taper shift is legitimate re-tuning (observed on the Ironman
    # COMPETITIVE wk-12 replay). A RECOVERY week is a pure menu-authored
    # deload fraction, so the same shift there is still a defect.
    taper = [_row(PhaseType.TAPER, WeekMode.TAPER, 9.30, 8.42)]  # -0.88
    assert _replan_fixed_bound_stability(_diff(taper)) == 0.0

    recovery = [_row(PhaseType.BUILD, WeekMode.RECOVERY, 9.30, 8.42)]  # -0.88
    assert _replan_fixed_bound_stability(_diff(recovery)) > 0.0


def test_fixed_bound_stability_no_fixed_weeks_is_zero() -> None:
    rows = [_row(PhaseType.BUILD, WeekMode.LOAD, 12.0, 9.0)]
    assert _replan_fixed_bound_stability(_diff(rows)) == 0.0


# replan_seam_convergence


def test_seam_convergence_passes_when_dip_decayed() -> None:
    # Large cutover-seam dip in BUILD, but the PEAK LOAD weeks have re-converged
    # (mean |Δ| ~0.2 h, inside the 0.75 h band): the dip decayed by PEAK.
    rows = [
        _row(PhaseType.BUILD, WeekMode.LOAD, 11.43, 9.09),  # seam -2.34
        _row(PhaseType.BUILD, WeekMode.LOAD, 13.77, 12.15),
        _row(PhaseType.PEAK, WeekMode.LOAD, 12.12, 11.93),  # -0.19
        _row(PhaseType.PEAK, WeekMode.LOAD, 11.33, 11.07),  # -0.26
    ]
    assert _replan_seam_convergence(_diff(rows)) == 0.0


def test_seam_convergence_fires_on_systematic_drag() -> None:
    # The dip never converged: PEAK LOAD weeks stay ~2 h depressed — the
    # "athlete who trained perfectly gets a much easier remainder" defect.
    rows = [
        _row(PhaseType.BUILD, WeekMode.LOAD, 11.43, 9.09),
        _row(PhaseType.PEAK, WeekMode.LOAD, 12.12, 10.12),  # -2.0
        _row(PhaseType.PEAK, WeekMode.LOAD, 11.33, 9.33),  # -2.0
    ]
    assert _replan_seam_convergence(_diff(rows)) == 1.0


def test_seam_convergence_no_peak_load_is_zero() -> None:
    # A replan that starts inside TAPER has no PEAK LOAD week to converge.
    rows = [
        _row(PhaseType.TAPER, WeekMode.TAPER, 4.58, 4.58),
        _row(PhaseType.TAPER, WeekMode.RACE, 3.67, 3.67),
    ]
    assert _replan_seam_convergence(_diff(rows)) == 0.0


# build_replan_diff (alignment + suffix verdict)


def test_build_replan_diff_aligns_and_matches_suffix() -> None:
    original, replanned = replay_replan(SCENARIOS["ironman_moderate"], week=8)
    diff = build_replan_diff(original, replanned, 8)
    # One row per replan week, and the R-PHASE-002b suffix holds.
    assert len(diff.rows) == len(replanned.plan.weeks)
    assert diff.phase_suffix_match is True
    # Row phases/modes track the replan's own week sequence.
    for row, ws in zip(diff.rows, replanned.plan.weeks, strict=True):
        assert row.phase == ws.phase
        assert row.mode == ws.mode


# integration: the deterministic replay reads clean


def test_replan_rules_registered() -> None:
    assert {r.name for r in REPLAN_RULES} == {
        "replan_phase_suffix",
        "replan_fixed_bound_stability",
        "replan_seam_convergence",
    }


def test_score_replan_healthy_ironman_reads_zero() -> None:
    scenario = build_default_scenario(28, AmbitionLevel.COMPETITIVE)
    report = score_replan(scenario, 12)
    assert report.phase_suffix_match is True
    assert report.composite == 0.0
    assert all(d == 0.0 for d in report.per_rule.values())


def test_score_replan_sweep_healthy_reads_zero() -> None:
    sweep = score_replan_sweep()
    assert sweep.composite == 0.0
    assert [r.week for r in sweep.per_week] == [8, 12, 16]
    assert all(r.phase_suffix_match for r in sweep.per_week)
