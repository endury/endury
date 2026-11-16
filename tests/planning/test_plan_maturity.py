"""A plan's maturity verdict: its goal's tier, or Experimental for its conditions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from endury.planner.foundations.types import AmbitionLevel, GoalType, PlanMaturity
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import DEFAULT_RECENT_WEEKLY_HOURS
from endury.planning.plan_maturity import (
    _LOW_VOLUME_FRACTION,
    CHECKED_HORIZONS,
    CONFIRM_TAIL,
    TIER_NOTES,
    plan_verdict,
)

# The verdict's condition reasons are only visible on a goal that is Alpha.
pytestmark = pytest.mark.usefixtures("alpha_goals")

_EXPERIMENTAL_NOTE = (
    "This plan type is built from training research, and its sessions "
    "have not yet been reviewed one by one."
)


def _verdict(
    goal: GoalType,
    ambition: AmbitionLevel,
    weeks: int,
    hours: float,
    *,
    short_notice: bool = False,
):
    return plan_verdict(
        goal,
        ambition,
        horizon_weeks=weeks,
        short_notice=short_notice,
        recent_hours=hours,
    )


def test_the_checked_horizons_are_the_scorecards_own():
    """The planning layer cannot import devtools, so a copy must not drift.

    ``CHECKED_HORIZONS`` is the union, per goal, of the scorecard's base
    sweep and its ``_wide`` family — the range the ``_wide`` cells complete.
    """
    from devtools.quality.planner.score_quality import (
        GOAL_MATRIX_HORIZONS,
        GOAL_MATRIX_HORIZONS_WIDE,
    )

    assert set(CHECKED_HORIZONS) == set(GOAL_MATRIX_HORIZONS)
    for goal, base in GOAL_MATRIX_HORIZONS.items():
        wide = GOAL_MATRIX_HORIZONS_WIDE.get(goal, ())
        assert CHECKED_HORIZONS[goal] == tuple(sorted(set(base) | set(wide))), goal


def test_the_low_volume_fraction_is_the_scorecards_own():
    """The planning layer's volume floor must not drift from the scorecard's."""
    from devtools.quality.planner.score_quality import (
        _LOW_VOLUME_FRACTION as scorecard_fraction,
    )

    assert scorecard_fraction == _LOW_VOLUME_FRACTION


def test_the_tier_sentences_are_plain_language():
    assert TIER_NOTES[PlanMaturity.EXPERIMENTAL] == _EXPERIMENTAL_NOTE
    assert TIER_NOTES[PlanMaturity.ALPHA] == (
        "Alpha: this plan's sessions have been reviewed one by one, and no "
        "completed preparation is on record yet. Endury is a hobby project."
    )
    assert TIER_NOTES[PlanMaturity.BETA] == (
        "This plan type passes the project's own plan-quality checks, and "
        "athletes have trained with it."
    )
    assert CONFIRM_TAIL == "You can still build it. Confirm to go ahead."


def test_a_default_competitive_marathon_at_16_weeks_is_alpha():
    seed = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.RUNNING_MARATHON][
        AmbitionLevel.COMPETITIVE
    ]
    verdict = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.COMPETITIVE, 16, seed)
    assert verdict.tier is PlanMaturity.ALPHA
    assert verdict.reasons == ()
    assert verdict.note is None


def test_a_marathon_at_24_weeks_is_alpha():
    """24 weeks sits inside the planner's own 12-28-week range, and the
    ``_wide`` cells now check it — the motivating case for widening the
    evidence."""
    seed = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.RUNNING_MARATHON][
        AmbitionLevel.MODERATE
    ]
    verdict = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE, 24, seed)
    assert verdict.tier is PlanMaturity.ALPHA
    assert verdict.reasons == ()


def test_a_short_notice_competitive_trail_50k_at_low_volume_is_experimental():
    """The athlete who joined a 12-week 50K five weeks out at 3.53 h/week."""
    verdict = _verdict(
        GoalType.TRAIL_50K, AmbitionLevel.COMPETITIVE, 5, 3.53, short_notice=True
    )
    assert verdict.tier is PlanMaturity.EXPERIMENTAL
    assert verdict.reasons == ("short_notice_join", "recent_volume_below_range")
    assert verdict.note == (
        "Your race is closer than a full preparation, so "
        "this plan joins one part-way through. Plans like that have not been "
        "checked. This plan is written for at least 4 h 35 of training a "
        "week, more than you have done recently, so some of its sessions will be "
        "short or optional until your training grows into it."
    )


def test_a_basic_half_ironman_at_a_third_of_the_default_volume_is_experimental():
    seed = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.HALF_IRONMAN][AmbitionLevel.BASIC]
    verdict = _verdict(GoalType.HALF_IRONMAN, AmbitionLevel.BASIC, 20, seed / 3)
    assert verdict.tier is PlanMaturity.EXPERIMENTAL
    assert verdict.reasons == ("recent_volume_below_range",)


def test_a_moderate_ironman_at_30_weeks_is_alpha():
    """30 weeks is not a swept horizon, but it lies inside the checked 20-36."""
    seed = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.IRONMAN][AmbitionLevel.MODERATE]
    verdict = _verdict(GoalType.IRONMAN, AmbitionLevel.MODERATE, 30, seed)
    assert verdict.tier is PlanMaturity.ALPHA


def test_a_basic_athlete_at_0_9_of_the_default_is_alpha_and_at_0_8_is_experimental():
    """The volume floor is 0.85 of the tier's own default, not the tier beneath."""
    basic = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.RUNNING_MARATHON][AmbitionLevel.BASIC]
    above = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.BASIC, 16, basic * 0.9)
    below = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.BASIC, 16, basic * 0.8)
    assert above.tier is PlanMaturity.ALPHA
    assert below.tier is PlanMaturity.EXPERIMENTAL
    assert below.reasons == ("recent_volume_below_range",)


def test_a_basic_plan_holds_down_to_085_of_its_own_seed():
    basic = DEFAULT_RECENT_WEEKLY_HOURS[GoalType.RUNNING_MARATHON][AmbitionLevel.BASIC]
    floor = _LOW_VOLUME_FRACTION * basic
    assert (
        _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.BASIC, 16, floor).tier
        is PlanMaturity.ALPHA
    )
    assert _verdict(
        GoalType.RUNNING_MARATHON, AmbitionLevel.BASIC, 16, floor - 0.01
    ).reasons == ("recent_volume_below_range",)


def test_a_marathon_shorter_than_the_checked_range_is_experimental():
    """11 weeks sits below the checked range's 12-week floor."""
    verdict = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE, 11, 5.0)
    assert verdict.tier is PlanMaturity.EXPERIMENTAL
    assert verdict.reasons == ("horizon_out_of_range",)
    assert verdict.note == (
        "At 11 weeks this plan is shorter than any plan that has been checked."
    )


def test_a_marathon_longer_than_the_checked_range_is_experimental():
    """29 weeks sits above the checked range's 28-week ceiling."""
    verdict = _verdict(GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE, 29, 5.0)
    assert verdict.tier is PlanMaturity.EXPERIMENTAL
    assert verdict.reasons == ("horizon_out_of_range",)
    assert verdict.note == (
        "At 29 weeks this plan is longer than any plan that has been checked."
    )


def test_a_half_ironman_shorter_than_the_shortest_checked_horizon_is_experimental():
    """Below the scorecard's new 12-week floor (widened from the goal's own)."""
    verdict = _verdict(GoalType.HALF_IRONMAN, AmbitionLevel.MODERATE, 11, 9.0)
    assert verdict.reasons == ("horizon_out_of_range",)
    assert verdict.note == (
        "At 11 weeks this plan is shorter than any plan that has been checked."
    )


def test_a_short_notice_join_is_named_for_the_join_not_its_horizon():
    """A joined marathon is short by construction; one reason says why."""
    verdict = _verdict(
        GoalType.RUNNING_MARATHON, AmbitionLevel.MODERATE, 8, 5.0, short_notice=True
    )
    assert verdict.reasons == ("short_notice_join",)


def test_an_alpha_maintenance_block_at_its_seed_is_alpha():
    """Horizon and joins do not apply to a raceless block."""
    verdict = _verdict(GoalType.RUNNING_MAINTENANCE, AmbitionLevel.MODERATE, 40, 4.5)
    assert verdict.tier is PlanMaturity.ALPHA


def test_a_maintenance_block_below_its_floor_is_experimental():
    """Running maintenance tiers hold 3 / 4.5 / 6 h; the floor is 0.85 of the tier's."""
    verdict = _verdict(GoalType.RUNNING_MAINTENANCE, AmbitionLevel.MODERATE, 8, 2.9)
    assert verdict.reasons == ("recent_volume_below_range",)


def test_a_maintenance_block_is_experimental_for_its_goal(monkeypatch):
    goal = GoalType.TRAIL_MAINTENANCE
    monkeypatch.setitem(
        GOAL_PROFILES,
        goal,
        replace(GOAL_PROFILES[goal], maturity=PlanMaturity.EXPERIMENTAL),
    )
    verdict = _verdict(goal, AmbitionLevel.BASIC, 8, 5.0)
    assert verdict.reasons == ("goal_experimental",)
    assert verdict.note == _EXPERIMENTAL_NOTE


def test_trail_maintenance_basic_at_its_volume_is_not_experimental():
    verdict = _verdict(GoalType.TRAIL_MAINTENANCE, AmbitionLevel.BASIC, 8, 5.0)
    assert verdict.reasons == ()
    assert verdict.note is None


def test_trail_maintenance_basic_at_its_floor_is_alpha():
    """The floor is 0.85 of the tier's 3 h (not the 3 h itself)."""
    verdict = _verdict(GoalType.TRAIL_MAINTENANCE, AmbitionLevel.BASIC, 8, 2.55)
    assert verdict.tier is PlanMaturity.ALPHA


def test_trail_maintenance_basic_below_its_floor_names_it():
    """2.55 h reads as 2 h 35, rounded up to five minutes."""
    verdict = _verdict(GoalType.TRAIL_MAINTENANCE, AmbitionLevel.BASIC, 8, 2.5)
    assert verdict.reasons == ("recent_volume_below_range",)
    assert verdict.note == (
        "This plan is written for at least 2 h 35 of training a week, more than "
        "you have done recently, so some of its sessions will be short or optional "
        "until your training grows into it."
    )


def test_a_goal_the_scorecard_never_sweeps_has_no_horizon_reason():
    """No horizon was ever checked for it; its goal reason already says so."""
    verdict = _verdict(GoalType.RUNNING_5K, AmbitionLevel.MODERATE, 30, 5.0)
    assert verdict.reasons == ("goal_experimental",)
