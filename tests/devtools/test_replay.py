"""Tests for the scenario-world replan replay harness (``_shared/replay.py``)."""

from __future__ import annotations

from dataclasses import replace

from devtools.planner._shared.metrics import _compute_metrics
from devtools.planner._shared.replay import replay_replan, state_at_week
from devtools.planner._shared.scenarios import SCENARIOS
from endury.planner.foundations.types import SportType


def _phase_mode_seq(plan) -> list[tuple[str, str]]:
    return [(w.phase.value, w.mode.value) for w in plan.weeks]


class TestSuffixProperty:
    """The replan is the original plan's weeks[k:], re-indexed (R-PHASE-002b)."""

    def test_marathon_suffix_matches_full_pipeline(self):
        original, replanned = replay_replan(SCENARIOS["marathon_competitive"], week=6)
        assert _phase_mode_seq(replanned.plan) == _phase_mode_seq(original.plan)[6:]
        assert replanned.plan.weeks[0].week == 0  # re-indexed to zero

    def test_triathlon_suffix_matches(self):
        # Multi-sport: exercises the three-sport trajectory + capacity re-seed.
        original, replanned = replay_replan(SCENARIOS["ironman_moderate"], week=8)
        assert _phase_mode_seq(replanned.plan) == _phase_mode_seq(original.plan)[8:]

    def test_replan_starts_week_weeks_later(self):
        original, replanned = replay_replan(SCENARIOS["marathon_competitive"], week=6)
        expected = original.scenario.training_start.toordinal() + 6 * 7
        assert replanned.scenario.training_start.toordinal() == expected

    def test_replan_has_sessions(self):
        _, replanned = replay_replan(SCENARIOS["marathon_competitive"], week=6)
        assert len(replanned.sessions) > 0

    def test_suffix_holds_for_a_scenario_that_already_resumes_mid_prep(self):
        # Partial scenarios set prior_training_weeks themselves. Replaying
        # one must ADD the replan week to that, not replace it — overwriting it
        # silently rebuilds a shorter skeleton and breaks the suffix property.
        partial = replace(SCENARIOS["marathon_competitive"], prior_training_weeks=4)
        original, replanned = replay_replan(partial, week=6)
        assert _phase_mode_seq(replanned.plan) == _phase_mode_seq(original.plan)[6:]


class TestSynthesizedState:
    """``state_at_week`` reads the plan's own perfect-compliance trajectory."""

    def test_ctl_seed_matches_trajectory(self):
        original = replay_replan(SCENARIOS["marathon_competitive"], week=6)[0]
        metrics = _compute_metrics(original)
        overrides = state_at_week(original, metrics, 6)
        assert overrides["ctl_0"][SportType.RUNNING] == float(
            metrics.a_ctl["running"][6]
        )
        assert overrides["atl_0"][SportType.RUNNING] == float(
            metrics.a_atl["running"][6]
        )

    def test_recent_hours_is_window_median(self):
        import statistics

        original = replay_replan(SCENARIOS["marathon_competitive"], week=8)[0]
        metrics = _compute_metrics(original)
        overrides = state_at_week(original, metrics, 8)
        window = [
            metrics.weekly_hours.get(w, {}).get("running", 0.0) for w in range(2, 8)
        ]
        assert overrides["recent_hours"][SportType.RUNNING] == float(
            statistics.median(window)
        )

    def test_longest_recent_populated(self):
        original = replay_replan(SCENARIOS["ironman_moderate"], week=8)[0]
        metrics = _compute_metrics(original)
        overrides = state_at_week(original, metrics, 8)
        assert overrides["longest_recent"]  # non-empty for a mid-plan replan


class TestDeterminism:
    """The pipeline is deterministic post-CP-SAT: replay is reproducible."""

    def test_two_runs_identical(self):
        _, rep_a = replay_replan(SCENARIOS["marathon_competitive"], week=6)
        _, rep_b = replay_replan(SCENARIOS["marathon_competitive"], week=6)
        sig_a = [
            (s.slot_id, s.week, s.sport.value, s.target_hours) for s in rep_a.sessions
        ]
        sig_b = [
            (s.slot_id, s.week, s.sport.value, s.target_hours) for s in rep_b.sessions
        ]
        assert sig_a == sig_b
