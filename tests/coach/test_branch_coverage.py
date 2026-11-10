"""Branch-coverage tests for coach support modules (context)."""

from __future__ import annotations

from endury.coach.context import build_system_context
from endury.domain.models.athlete import AthleteProfile
from endury.wiring.config import CoachConfig

# System context


def test_build_system_context_includes_active_and_macro_plan_summaries() -> None:
    profile = AthleteProfile(name="Sam", primary_sport="running")
    context = build_system_context(
        CoachConfig(),
        profile,
        "",
        active_plan_summary="Plan A",
        macro_plan_summary="Macro B",
    )
    assert "## Active Training Plan" in context
    assert "Plan A" in context
    assert "## Macro Training Plan Overview" in context
    assert "Macro B" in context
