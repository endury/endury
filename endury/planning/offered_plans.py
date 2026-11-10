"""Which plans Endury builds for a new request.

Each menu names the ambition tiers it offers (``offered:`` in its YAML).
The wizard and the coach read that set here, and
:func:`~endury.planning.generate_plan.run_generate` refuses a new plan
outside it. A stored plan replans and a tune-up race keeps its goal type
whatever is offered: the planner itself builds every tier.
"""

from __future__ import annotations

from endury.planner.foundations.types import AmbitionLevel, GoalType
from endury.planner.sessions.menus import BLUEPRINTS
from endury.planning.coach_brief import _goal_label

#: Goal -> the tiers a new plan may be built at (empty: not offered).
OFFERED_TIERS: dict[GoalType, frozenset[AmbitionLevel]] = {
    goal: blueprint.offered for goal, blueprint in BLUEPRINTS.items()
}


def is_offered(goal: GoalType, ambition: AmbitionLevel) -> bool:
    """Whether a new plan may be built for *goal* at *ambition*."""
    return ambition in OFFERED_TIERS[goal]


def offered_names() -> str:
    """The plans on offer as the athlete reads them, e.g. ``Trail 20K (basic)``."""
    return ", ".join(
        f"{_goal_label(goal.value)} ({tier.value})"
        for goal, tiers in OFFERED_TIERS.items()
        for tier in AmbitionLevel
        if tier in tiers
    )


def not_offered_message(goal: GoalType, ambition: AmbitionLevel) -> str:
    """The refusal an athlete reads for a plan that is not on offer."""
    return (
        f"Endury is not offering a {ambition.value} {_goal_label(goal.value)} "
        f"plan at the moment. On offer now: {offered_names()}."
    )


__all__ = ["OFFERED_TIERS", "is_offered", "not_offered_message", "offered_names"]
