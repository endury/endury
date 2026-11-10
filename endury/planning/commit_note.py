"""The athlete-memory notes a plan commit and a plan delete leave behind.

The memory document is the coach's only durable record, so a committed
plan is noted here even when the coach never thinks to write it down, and
so is its deletion: without it the commit note reads as a plan still
running.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from endury.common import clock
from endury.common.memory_notes import append_note
from endury.database.sqlite.repositories.athlete_memory import MEMORY_CHAR_CAP
from endury.planning.coach_brief import _goal_label
from endury.planning.wire.requests import GenerateSuccess

logger = logging.getLogger(__name__)


def _commit_note_bullet(macro: Mapping[str, Any], verb: str) -> str | None:
    """Compose the "## Notes" bullet recording a freshly committed plan.

    Args:
        macro: The committed plan's macro payload.
        verb: What happened — "committed" for a new plan, "replanned" when
            the remainder of an active one was regenerated.

    Returns:
        The dated bullet line, or ``None`` when the payload names no goal.
    """
    goal_type = str(macro.get("goal_type") or "")
    goal_date = str(macro.get("goal_date") or "")
    if not goal_type or not goal_date:
        return None
    tier = str(macro.get("ambition_level") or "").lower()
    return (
        f"- {clock.today().isoformat()}: {verb} {_goal_label(goal_type)} "
        f"plan, {tier} tier, {macro.get('total_weeks')} weeks to {goal_date}"
    )


def _append_to_memory(bullet: str, runtime: Any) -> None:
    """Append *bullet* under the memory document's "## Notes" heading."""
    memory_repo = runtime.memory_repo
    document = memory_repo.get().content
    updated = append_note(document, bullet, cap=MEMORY_CHAR_CAP)
    if updated != document:
        memory_repo.set(updated)


def record_commit_in_memory(
    response: GenerateSuccess, *, runtime: Any, verb: str = "committed"
) -> None:
    """Note the committed plan in the athlete-memory document.

    Strictly best-effort: a memory failure must never fail a commit that has
    already persisted, so every error is logged and swallowed.

    Args:
        response: The committed generate result.
        runtime: The coach runtime holding the memory repository.
        verb: The bullet's verb — "committed" (default) or "replanned".
    """
    try:
        bullet = _commit_note_bullet(dict(response.macro_plan), verb)
        if bullet is None:
            return
        _append_to_memory(bullet, runtime)
    except Exception as exc:
        logger.warning("Could not note the plan in athlete memory: %s", exc)


def record_delete_in_memory(plan: Any, *, runtime: Any) -> None:
    """Note the deleted plan in the athlete-memory document.

    Best-effort like :func:`record_commit_in_memory`: the plan is already
    archived, so every error is logged and swallowed.

    Args:
        plan: The archived plan row.
        runtime: The coach runtime holding the memory repository.
    """
    try:
        _append_to_memory(
            f"- {clock.today().isoformat()}: deleted "
            f"{_goal_label(str(plan.goal_type))} plan to {plan.goal_date}",
            runtime,
        )
    except Exception as exc:
        logger.warning("Could not note the deleted plan in athlete memory: %s", exc)
