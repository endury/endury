"""Guardrail for the planner evaluation scenario registry.

A scenario's ``recent_hours`` is the athlete's volume *before* the plan
starts. If a scenario seeds more volume than its own tier ever prescribes,
the plan detrains its own athlete: the CTL trace in the dossier falls from
week 0, and the LLM review panels read that as a plan defect when it is a
scenario-seed mistake.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
from devtools.planner._shared.scenarios import SCENARIOS, Scenario
from endury.planner.foundations.types import is_maintenance_goal

#: Scenarios whose seed is deliberately left above what the plan reaches,
#: because the athlete is plausible and the *plan* is the thing that is
#: wrong. Removing an entry here should follow a menu-volume fix, not a
#: scenario edit.
_UNDER_PRESCRIBED_BY_MENU = frozenset({"10k_competitive", "marathon_competitive"})


def _race_prep_scenarios() -> list[Scenario]:
    """Every scenario whose plan ramps toward a race.

    Maintenance goals are excluded: they converge on a fixed per-tier volume
    rather than ramping, so their scenarios seed the athlete *at* that
    volume on purpose and a flat CTL trace is the intended outcome.
    """
    return [
        s
        for s in SCENARIOS.values()
        if not is_maintenance_goal(s.goals[0].goal_type)
        and s.name not in _UNDER_PRESCRIBED_BY_MENU
    ]


@pytest.mark.parametrize("scenario", _race_prep_scenarios(), ids=lambda s: s.name)
def test_plan_peak_exceeds_seeded_volume(scenario: Scenario) -> None:
    """The plan must build the athlete past the volume they arrive with.

    A healthy plan seeds 1.4-2.2x below its own peak week; the registry is held to the
    weaker "strictly above" bar so
    menu-volume changes do not churn the seeds.
    """
    result = run_full_pipeline(scenario)
    weekly: dict[int, float] = defaultdict(float)
    for session in result.sessions:
        weekly[session.week] += session.target_hours

    seeded = sum((scenario.recent_hours or {}).values())
    assert max(weekly.values()) > seeded


@pytest.mark.parametrize("scenario", _race_prep_scenarios(), ids=lambda s: s.name)
def test_ctl_trace_does_not_peak_at_the_seed(scenario: Scenario) -> None:
    """Fitness must end up above where the athlete started, not below."""
    metrics = _compute_metrics(run_full_pipeline(scenario))
    assert int(metrics.s_ctl.argmax()) > 0
