"""Tests for the athlete-authored planning constraints (weekly hours cap).

The cap is a SOFT macro-QP target: a linear hinge penalty on every
LOAD/RECOVERY week's total hours, so the athlete's number beats the menu's
prescription but never a session's own floor — and never makes the QP
infeasible.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pytest

from endury.planner import PlanRequest, generate_plan
from endury.planner.foundations.request import (
    AthleteState,
    PlanningConstraints,
    SportState,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PlacedSession,
    RaceGoal,
    SportType,
    WeekMode,
)

_START = date(2026, 6, 1)

#: Below the trail-maintenance MODERATE tier's ~4.5 h/wk target, so the cap
#: binds every LOAD/RECOVERY week.
_LOW_VOLUME_CAP = 2.4

#: Five-minute rounding (``_ROUND_STEP_HOURS``) is the largest gap the QP
#: can leave between a solved week and the floor it settled on.
_ROUNDING_SLACK = 0.09

_MAINTENANCE_GOALS = [
    RaceGoal(GoalType.TRAIL_MAINTENANCE, _START + timedelta(weeks=8, days=-1), "A")
]


def _maintenance_request(cap: float | None) -> PlanRequest:
    """Build the trail-maintenance MODERATE request with an optional cap."""
    return PlanRequest(
        goals=_MAINTENANCE_GOALS,
        training_start=_START,
        ambition=AmbitionLevel.MODERATE,
        athlete=AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=_LOW_VOLUME_CAP,
                    longest_recent_min={ActivityFamily.LONG: 45},
                )
            }
        ),
        constraints=PlanningConstraints(weekly_hours_cap=cap),
    )


def _by_week(sessions: list[PlacedSession]) -> dict[int, list[PlacedSession]]:
    """Group solved sessions by their week index."""
    grouped: dict[int, list[PlacedSession]] = defaultdict(list)
    for session in sessions:
        grouped[session.week].append(session)
    return grouped


def _floor_sum(sessions: list[PlacedSession]) -> float:
    """Total hours the week cannot go below (R-SESS-030 session floors)."""
    return sum(
        s.min_hours if s.min_hours > 0 else s.target_hours for s in _booked(sessions)
    )


def _total(sessions: list[PlacedSession]) -> float:
    """Solved total hours for one week."""
    return sum(s.target_hours for s in _booked(sessions))


def _booked(sessions: list[PlacedSession]) -> list[PlacedSession]:
    """The sessions the cap governs: not the optional ones (R-SESS-037)."""
    return [s for s in sessions if s.optional_reason is None]


class TestContract:
    def test_the_default_request_carries_no_cap(self) -> None:
        """Defaults are exactly today's behaviour."""
        request = PlanRequest(goals=_MAINTENANCE_GOALS)
        assert request.constraints == PlanningConstraints()
        assert request.constraints.weekly_hours_cap is None

    def test_constraints_are_frozen(self) -> None:
        constraints = PlanningConstraints(weekly_hours_cap=4.0)
        with pytest.raises(AttributeError):
            constraints.weekly_hours_cap = 5.0  # type: ignore[misc]


class TestCapHonoured:
    def test_an_absent_cap_leaves_the_plan_untouched(self) -> None:
        """``weekly_hours_cap=None`` reproduces the uncapped plan exactly."""
        without = generate_plan(PlanRequest(goals=_MAINTENANCE_GOALS))
        with_empty = generate_plan(
            PlanRequest(goals=_MAINTENANCE_GOALS, constraints=PlanningConstraints())
        )
        assert with_empty.sessions == without.sessions
        assert with_empty.assignments == without.assignments

    def test_the_cap_pulls_every_load_week_down_to_the_number(self) -> None:
        """A week the floors leave room in lands at the cap, not above it."""
        uncapped = _by_week(generate_plan(_maintenance_request(None)).sessions)
        capped_plan = generate_plan(_maintenance_request(_LOW_VOLUME_CAP))
        capped = _by_week(capped_plan.sessions)
        modes = [week.mode for week in capped_plan.phase_plan.weeks]

        # The uncapped plan converges on the tier volume, well past the cap.
        assert max(_total(week) for week in uncapped.values()) > _LOW_VOLUME_CAP * 1.10

        for k, sessions in sorted(capped.items()):
            if modes[k] not in (WeekMode.LOAD, WeekMode.RECOVERY):
                continue
            assert (
                _total(sessions) <= max(_LOW_VOLUME_CAP, _floor_sum(sessions)) * 1.10
            ), f"week {k} stayed above the cap with room to come down"

    def test_a_week_whose_floors_forbid_the_cap_settles_on_its_floor(self) -> None:
        """Floors win — but the week gives up everything above them."""
        capped = _by_week(generate_plan(_maintenance_request(_LOW_VOLUME_CAP)).sessions)
        over_floor = {
            k: sessions
            for k, sessions in capped.items()
            if _floor_sum(sessions) > _LOW_VOLUME_CAP
        }
        assert over_floor, "the scenario must contain floor-bound weeks"
        for k, sessions in over_floor.items():
            assert _total(sessions) == pytest.approx(
                _floor_sum(sessions), abs=_ROUNDING_SLACK
            ), f"week {k} kept hours the cap asked it to drop"

    def test_a_cap_below_every_floor_degrades_gracefully(self) -> None:
        """The hinge absorbs the impossible remainder — no infeasibility."""
        capped = generate_plan(_maintenance_request(0.1))
        uncapped = generate_plan(_maintenance_request(None))

        assert len(capped.sessions) == len(uncapped.sessions)
        for week, sessions in _by_week(capped.sessions).items():
            assert _total(sessions) == pytest.approx(
                _floor_sum(sessions), abs=_ROUNDING_SLACK
            ), f"week {week} did not settle on its floor"

    def test_taper_and_race_weeks_are_capped_too(self) -> None:
        """An exempt taper is where the QP would park the cut volume."""
        goals = [
            RaceGoal(
                GoalType.RUNNING_HALF_MARATHON,
                _START + timedelta(weeks=14, days=-1),
                "A",
            )
        ]
        uncapped = generate_plan(PlanRequest(goals=goals, training_start=_START))
        capped = generate_plan(
            PlanRequest(
                goals=goals,
                training_start=_START,
                constraints=PlanningConstraints(weekly_hours_cap=1.0),
            )
        )
        modes = [week.mode for week in capped.phase_plan.weeks]
        free, held = _by_week(uncapped.sessions), _by_week(capped.sessions)
        tapering = [
            k
            for k, mode in enumerate(modes)
            if mode in (WeekMode.TAPER, WeekMode.RACE) and k in held
        ]
        assert tapering, "the scenario must contain a taper to assert on"
        for k in tapering:
            # Never larger than uncapped, and never larger than the week
            # before it unless its own floors are: the cut volume has
            # nowhere to hide.
            assert _total(held[k]) <= _total(free[k]) + _ROUNDING_SLACK
            assert _total(held[k]) <= (
                max(_total(held[k - 1]), _floor_sum(held[k])) + _ROUNDING_SLACK
            )
