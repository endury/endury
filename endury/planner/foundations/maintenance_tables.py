"""Maintenance-goal policy tables.

A maintenance block has no race to build toward; instead each ambition
tier defines a target weekly volume — the fitness floor the block
converges on. The capacity projection uses this value as its
``max_weekly_hours_ceiling``: an athlete below the tier volume ramps up
to it at the ambition ramp rate (the adaptive ramp reaches the ceiling
around 75% of LOAD weeks), an athlete at or above it trains flat at the
tier volume from week one.

Volumes sit at roughly half to two thirds of each tier's peak race-prep
volume, per retain-intensity/cut-volume maintenance research (fitness holds
at substantially reduced volume provided some intensity touches remain), and
match the mid-band sums of the maintenance menu blueprints, which author
one quality session per sport per week plus one longer aerobic session.
"""

from __future__ import annotations

from endury.planner.foundations.types import AmbitionLevel, GoalType

#: Target weekly training hours per maintenance goal and ambition tier.
#: Feeds ``build_capacity_projection`` as the hours ceiling; the menu
#: blueprints author session bounds whose mid-band weekly sums match.
MAINTENANCE_WEEKLY_HOURS: dict[GoalType, dict[AmbitionLevel, float]] = {
    GoalType.RUNNING_MAINTENANCE: {
        AmbitionLevel.BASIC: 3.0,
        AmbitionLevel.MODERATE: 4.5,
        AmbitionLevel.COMPETITIVE: 6.0,
    },
    # Trail maintenance holds the same volumes as running maintenance:
    # the sport is running either way, and the trail menu spends the same
    # weekly minutes on terrain-flavoured sessions (trail long run,
    # hill/vert quality) rather than on more of them.
    GoalType.TRAIL_MAINTENANCE: {
        AmbitionLevel.BASIC: 3.0,
        AmbitionLevel.MODERATE: 4.5,
        AmbitionLevel.COMPETITIVE: 6.0,
    },
    # MODERATE 8.5 h (the maintainer's call; 7 h before): 0.63 of the
    # half-Ironman MODERATE peak week of 13.5 h, inside the reduced-training
    # trials, where a third to two thirds less volume held endurance for up
    # to 15 weeks while intensity and frequency held (Hickson's series,
    # reviewed by Spiering et al. 2021). It is what the one-session-a-day
    # week holds at maintenance-sized long sessions (2:30 ride, 90-min run);
    # the maintainer's ceiling was about 9 h. COMPETITIVE 10 h (9 h before)
    # only so it stays above MODERATE: the wizard seeds the highest tier
    # within 1.25x of the athlete's hours, so a tie would never seed
    # MODERATE; 10 h is what its unreviewed menu reaches.
    GoalType.TRIATHLON_MAINTENANCE: {
        AmbitionLevel.BASIC: 5.0,
        AmbitionLevel.MODERATE: 8.5,
        AmbitionLevel.COMPETITIVE: 10.0,
    },
}


def maintenance_weekly_hours(goal_type: GoalType, ambition: AmbitionLevel) -> float:
    """Return the tier's target weekly maintenance volume in hours.

    Args:
        goal_type: A maintenance goal type.
        ambition: Ambition tier.

    Raises:
        KeyError: If *goal_type* is not a maintenance goal.
    """
    return MAINTENANCE_WEEKLY_HOURS[goal_type][ambition]


__all__ = [
    "MAINTENANCE_WEEKLY_HOURS",
    "maintenance_weekly_hours",
]
