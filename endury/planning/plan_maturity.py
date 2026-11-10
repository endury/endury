"""A plan's maturity: its goal's tier, or Experimental for its own conditions.

A goal's tier (``GoalProfile.maturity``) says how well-checked its plan type
is. One plan can still leave the conditions that were checked — a
short-notice join, a horizon the scorecard never sweeps, recent volume
below any it grades — and is then Experimental whatever its goal. The
verdict is a pure function of the request and the athlete state, so nothing
about it is stored with the plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PlanMaturity,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_recent_weekly_hours

#: The horizons, in weeks, the scorecard's matrix grades each goal at: the
#: union of its base sweep and its ``_wide`` family (a copy of
#: ``devtools``' ``GOAL_MATRIX_HORIZONS`` and ``GOAL_MATRIX_HORIZONS_WIDE``,
#: which ``endury`` may not import; ``tests/planning/test_plan_maturity.py``
#: holds the two equal). Every cell runs the tier's default recent volume,
#: so a plan inside the horizon span and at or above the volume floor below
#: is one the scorecard brackets.
CHECKED_HORIZONS: dict[GoalType, tuple[int, ...]] = {
    GoalType.IRONMAN: (22, 24, 28, 32, 36),
    GoalType.HALF_IRONMAN: (16, 20, 24, 28),
    GoalType.RUNNING_MARATHON: (12, 16, 18, 22, 28),
    GoalType.RUNNING_HALF_MARATHON: (8, 10, 12, 16, 20, 24),
    GoalType.SPRINT_TRIATHLON: (6, 8, 10, 12, 16, 20),
    GoalType.OLYMPIC_TRIATHLON: (12, 14, 16, 20, 24),
    GoalType.RUNNING_10K: (6, 8, 12, 16, 20),
    GoalType.TRAIL_50K: (12, 16, 20, 24),
    GoalType.TRAIL_20K: (12, 16, 20),
}

_LOW_VOLUME_FRACTION = 0.85
"""The volume floor's scale, copied from ``score_quality._LOW_VOLUME_FRACTION``
(``tests/planning/test_plan_maturity.py`` holds the two equal).

The least a plan is written for: the trail 20K basic at 0.85 of its 2.5 h
default is 2.1 h a week, the smallest week that holds its 50-minute climb,
a 50-minute long run and a 25-minute easy run. Below it the plan books
short runs or leaves sessions optional. The scorecard's ``_low`` cells
check the tier's default athlete at this fraction, so the gate guards the
athletes the plan is for (0.75 until 2026-10-08, below that floor). A
maintenance block takes the same floor: 2.55 h for trail maintenance basic.
"""

#: One sentence per tier, as the athlete reads it.
TIER_NOTES: dict[PlanMaturity, str] = {
    PlanMaturity.EXPERIMENTAL: (
        "This plan type is built from training research, and its sessions "
        "have not yet been reviewed one by one."
    ),
    PlanMaturity.ALPHA: (
        "Alpha: this plan's sessions have been reviewed one by one, and no "
        "completed preparation is on record yet. Endury is a hobby project."
    ),
    PlanMaturity.BETA: (
        "This plan type passes the project's own plan-quality checks, and "
        "athletes have trained with it."
    ),
}

#: Closes the refusal that asks the athlete to confirm an Experimental plan.
CONFIRM_TAIL = "You can still build it. Confirm to go ahead."

_SHORT_NOTICE = (
    "Your race is closer than a full preparation, so this plan joins one "
    "part-way through. Plans like that have not been checked."
)
_HORIZON_LONGER = (
    "At {weeks} weeks this plan is longer than any plan that has been checked."
)
_HORIZON_SHORTER = (
    "At {weeks} weeks this plan is shorter than any plan that has been checked."
)
_VOLUME = (
    "This plan is written for at least {hours} of training a week, more than "
    "you have done recently, so some of its sessions will be short or optional "
    "until your training grows into it."
)


@dataclass(frozen=True, slots=True)
class PlanVerdict:
    """How well-checked one plan is.

    Attributes:
        tier: The plan's effective tier.
        reasons: Why it is Experimental, in a fixed order; empty otherwise.
        note: The reasons as the athlete reads them, or ``None`` when the
            plan is not Experimental.
    """

    tier: PlanMaturity
    reasons: tuple[str, ...]
    note: str | None

    def wire_fields(self) -> dict[str, Any]:
        """The verdict as ``GenerateSuccess``'s ``maturity*`` fields."""
        return {
            "maturity": self.tier.value,
            "maturity_reasons": list(self.reasons),
            "maturity_note": self.note
            or (TIER_NOTES[self.tier] if self.tier is PlanMaturity.ALPHA else None),
        }


def _hours_text(hours: float) -> str:
    """Hours as the athlete reads them, up to five minutes: "2 h 10"."""
    whole, minutes = divmod(5 * math.ceil(hours * 12 - 1e-9), 60)
    return f"{whole} h {minutes:02d}" if minutes else f"{whole} hours"


def _volume_floor(goal_type: GoalType, ambition: AmbitionLevel) -> float:
    """The lowest recent volume the scorecard grades a plan of this tier at.

    The scorecard's ``_low`` cells check the tier's own default athlete down
    to :data:`_LOW_VOLUME_FRACTION` of their recent volume, so that is the
    floor. A maintenance block has no ``_low`` cells (the scorecard only
    grades race preparations) and takes the same floor (2026-10-08; before,
    the default of the tier beneath).
    """
    return _LOW_VOLUME_FRACTION * default_recent_weekly_hours(goal_type, ambition)


def plan_verdict(
    goal_type: GoalType,
    ambition: AmbitionLevel,
    *,
    horizon_weeks: int,
    short_notice: bool,
    recent_hours: float,
) -> PlanVerdict:
    """Judge one plan: its goal's tier, or Experimental with the reasons.

    Args:
        goal_type: The A-goal.
        ambition: The plan's ambition tier.
        horizon_weeks: Weeks from the plan's start to race day, race week
            included.
        short_notice: The plan joins a preparation part-way through.
        recent_hours: The athlete's recent weekly hours over the goal's
            sports.

    Returns:
        The verdict. A goal the scorecard never sweeps has no horizon to be
        outside of; a maintenance block, with no race to join, is judged on
        its goal and volume only.
    """
    goal_tier = GOAL_PROFILES[goal_type].maturity
    checked = CHECKED_HORIZONS.get(goal_type)
    sentences: dict[str, str] = {}
    if goal_tier is PlanMaturity.EXPERIMENTAL:
        sentences["goal_experimental"] = TIER_NOTES[PlanMaturity.EXPERIMENTAL]
    if short_notice:
        sentences["short_notice_join"] = _SHORT_NOTICE
    elif checked and not min(checked) <= horizon_weeks <= max(checked):
        template = _HORIZON_LONGER if horizon_weeks > max(checked) else _HORIZON_SHORTER
        sentences["horizon_out_of_range"] = template.format(weeks=horizon_weeks)
    if recent_hours < _volume_floor(goal_type, ambition):
        sentences["recent_volume_below_range"] = _VOLUME.format(
            hours=_hours_text(_volume_floor(goal_type, ambition))
        )
    if not sentences:
        return PlanVerdict(tier=goal_tier, reasons=(), note=None)
    return PlanVerdict(
        tier=PlanMaturity.EXPERIMENTAL,
        reasons=tuple(sentences),
        note=" ".join(sentences.values()),
    )


__all__ = [
    "CHECKED_HORIZONS",
    "CONFIRM_TAIL",
    "TIER_NOTES",
    "PlanVerdict",
    "plan_verdict",
]
