"""``create_plan``'s trail-band backstop.

Catches a requested trail race band that the athlete's own recorded race
profile contradicts — a caveat alone does not stop a model from calling
``create_plan`` with the wrong ``goal_type`` in the same turn, only a
refusal does. Checks the same table
:func:`endury.coach.tools.race_tools.upsert_race` writes, at the one place
a plan actually gets built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from endury.planning.coach_brief import _goal_label
from endury.planning.trail_band import (
    LONGEST_PLANNED_EFFORT,
    TRAIL_RACE_GOALS,
    km_effort,
    trail_goal_for,
)


def _num(value: float) -> str:
    """Render a float without a trailing ``.0`` — "25", not "25.0"."""
    return f"{value:g}"


@dataclass(frozen=True, slots=True)
class BandMismatch:
    """One requested race whose stored profile names a different trail band.

    Attributes:
        refusal_message: The curated ``trail_band_mismatch`` refusal text.
        caveat: What to relay instead once ``accept_band_mismatch=True``.
        suggested_goal_type: The km-effort-correct ``GoalType`` value, or
            ``None`` for a race longer than any Endury trail plan.
    """

    refusal_message: str
    caveat: str
    suggested_goal_type: str | None


def find_band_mismatch(repos: Any, races: list[Any]) -> BandMismatch | None:
    """Find the first requested trail race its stored profile contradicts.

    For every race whose ``goal_type`` is a trail band, looks up the stored
    race on the same date (whatever band IT is filed under — see
    :meth:`~endury.database.sqlite.repositories.athlete_goals
    .AthleteGoalsRepository.get_by_date`). When that race has both
    ``distance_km`` and ``climb_m`` on file and its km-effort names a
    different band than the one requested, that is the mismatch.

    Args:
        repos: Repository container (races).
        races: The request's validated race goals (``RaceGoalModel``-shaped:
            ``.goal_type``, ``.race_date``).

    Returns:
        The mismatch, or ``None`` when nothing contradicts: no trail goal
        was requested, there is no stored race on that date, it has no
        profile, or the bands already agree.
    """
    for goal in races:
        if goal.goal_type not in TRAIL_RACE_GOALS:
            continue
        stored = repos.races.get_by_date(goal.race_date)
        if stored is None or stored.distance_km is None or stored.climb_m is None:
            continue
        suggested = trail_goal_for(stored.distance_km, stored.climb_m)
        if suggested is not None and suggested.value == goal.goal_type:
            continue
        effort = km_effort(stored.distance_km, stored.climb_m)
        requested_label = _goal_label(goal.goal_type)
        race = (
            f"{stored.name} on {stored.race_date} is "
            f"{_num(stored.distance_km)} km / {_num(stored.climb_m)} m = "
            f"{_num(effort)} km-effort"
        )
        if suggested is None:
            profile = (
                f"{race}, longer than any Endury trail plan (they stop below "
                f"{_num(LONGEST_PLANNED_EFFORT)} km-effort)."
            )
            return BandMismatch(
                refusal_message=(
                    f"{profile} Call again with accept_band_mismatch=true only "
                    f"if the athlete explicitly wants the {requested_label} "
                    "plan for it."
                ),
                caveat=f"{profile} Built as {requested_label} anyway, as requested.",
                suggested_goal_type=None,
            )
        suggested_label = _goal_label(suggested.value)
        profile = f"{race}, which is {suggested_label}, not {requested_label}."
        return BandMismatch(
            refusal_message=(
                f"{profile} Call again with goal_type {suggested.value} — or "
                "with accept_band_mismatch=true if the athlete explicitly "
                f"wants the {requested_label} plan."
            ),
            caveat=f"{profile} Built as {requested_label} anyway, as requested.",
            suggested_goal_type=suggested.value,
        )
    return None


def trail_band_refusal(mismatch: BandMismatch) -> dict[str, Any]:
    """Render a :class:`BandMismatch` into ``create_plan``'s refusal shape."""
    return {
        "status": "error",
        "error_code": "trail_band_mismatch",
        "message": mismatch.refusal_message,
        "suggested_goal_type": mismatch.suggested_goal_type,
    }


__all__ = ["BandMismatch", "find_band_mismatch", "trail_band_refusal"]
