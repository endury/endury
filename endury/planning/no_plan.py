"""The no-active-plan refusal every plan-touching verb points at.

A leaf module with no sibling ``endury.planning`` imports, so both
``generate_plan.py`` and ``commit_plan.py`` can point at the one wording
without an import cycle between them.
"""

from __future__ import annotations

#: Reachable from both coach tools and plain UI actions (e.g. the calendar's
#: reschedule), so the wording names both ways to start a plan.
NO_ACTIVE_PLAN_MESSAGE = (
    "No active training plan — create one first (the coach's create_plan "
    "or the plan wizard)."
)

__all__ = ["NO_ACTIVE_PLAN_MESSAGE"]
