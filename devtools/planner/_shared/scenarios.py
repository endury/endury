"""Planner evaluation scenario registry.

Defines ``Scenario`` — a frozen bag of pipeline inputs — and a curated set
of named scenarios covering all goal categories, ambition levels, and known
regression cases.

Usage::

    from devtools.planner._shared.scenarios import SCENARIOS, scenarios_by_tag

    scenario = SCENARIOS["ironman_moderate"]
    regression = scenarios_by_tag("regression")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from devtools.planner._shared.pipeline import (
    DEFAULT_START,
    AthleteState,
    PlanRequest,
    SchedulePreferences,
    SolverOptions,
    SportState,
    default_profile_for,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    TestProtocol,
)

# Scenario dataclass


@dataclass(frozen=True)
class Scenario:
    """A single planner evaluation scenario.

    Holds the flat pipeline inputs used by the report tooling. ``None``
    athlete fields fall through to the goal-type defaults via
    :meth:`to_request`. Use :meth:`to_request` to build the
    :class:`PlanRequest` that ``generate_plan`` consumes.

    ``prior_training_weeks`` > 0 makes the scenario resume mid-preparation:
    the phase skeleton is built over the virtual (prior + actual) horizon
    and the leading prior weeks truncated. Used by scenarios
    that cover only the closing block of a longer preparation.
    """

    name: str
    description: str
    tags: tuple[str, ...] = ()
    goals: tuple[RaceGoal, ...] = ()
    training_start: date = DEFAULT_START
    ambition: AmbitionLevel = AmbitionLevel.MODERATE
    longest_recent: dict[tuple[SportType, ActivityFamily], int] | None = None
    recent_hours: dict[SportType, float] | None = None
    test_protocol: TestProtocol | None = None
    ctl_0: dict[SportType, float] | None = None
    atl_0: dict[SportType, float] | None = None
    prior_training_weeks: int = 0

    def to_request(self, solver: SolverOptions | None = None) -> PlanRequest:
        """Build a :class:`PlanRequest` from this scenario's flat fields.

        Blend rule ("default sport set, then overlay explicit fields"):
        when every athlete field is ``None`` the request's
        ``athlete`` is left ``None`` (so ``default_profile_for`` applies);
        otherwise a per-sport :class:`AthleteState` is built from the
        effective recent-hours sport set with explicit overlays.

        Args:
            solver: Solver toggles. ``None`` → defaults (both solvers on).

        Returns:
            A :class:`PlanRequest` equivalent to this scenario.
        """
        athlete: AthleteState | None
        if all(
            f is None
            for f in (self.recent_hours, self.longest_recent, self.ctl_0, self.atl_0)
        ):
            athlete = None
        else:
            base = default_profile_for(self.goals[0].goal_type)
            recent = self.recent_hours or {
                s: st.recent_weekly_hours for s, st in base.sports.items()
            }
            ctl = self.ctl_0 or {}
            atl = self.atl_0 or {}
            sports: dict[SportType, SportState] = {}
            for sport in recent:
                if self.longest_recent is not None:
                    longest = {
                        family: minutes
                        for (sp, family), minutes in self.longest_recent.items()
                        if sp == sport
                    }
                elif sport in base.sports:
                    longest = dict(base.sports[sport].longest_recent_min)
                else:
                    longest = {}
                sports[sport] = SportState(
                    recent_weekly_hours=recent[sport],
                    longest_recent_min=longest,
                    ctl_0=ctl.get(sport),
                    atl_0=atl.get(sport),
                )
            athlete = AthleteState(sports=sports)

        return PlanRequest(
            goals=list(self.goals),
            training_start=self.training_start,
            ambition=self.ambition,
            athlete=athlete,
            schedule=SchedulePreferences(test_protocol=self.test_protocol),
            solver=solver or SolverOptions(),
            prior_training_weeks=self.prior_training_weeks,
        )


# Athlete state presets

# Recent weekly hours are the athlete's volume BEFORE the plan starts, and
# must sit below the plan's own target volume — otherwise the scenario
# detrains its own athlete and every CTL trace in the report falls, which
# reads as a plan defect. Presets stay roughly 1.4-2.2x below the plan's
# peak week, per goal distance and tier (a mid triathlete trains far less
# before an Olympic race than before an Ironman), a range pinned by
# ``tests/devtools/test_scenarios.py``.
_NOVICE_RUNNER_HOURS: dict[SportType, float] = {SportType.RUNNING: 1.25}
_BEGINNER_RUNNER_HOURS: dict[SportType, float] = {SportType.RUNNING: 2.5}
_MID_RUNNER_HOURS: dict[SportType, float] = {SportType.RUNNING: 3.0}
# Deliberately NOT scaled down: a competitive runner really does train
# 10 h/wk, and the running menus peak well below that — the mismatch
# is the plan's, not the scenario's.
_HIGH_RUNNER_HOURS: dict[SportType, float] = {SportType.RUNNING: 10.0}

_BEGINNER_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 1.0,
    SportType.CYCLING: 1.5,
    SportType.SWIMMING: 0.5,
}
_MID_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 1.5,
    SportType.CYCLING: 2.5,
    SportType.SWIMMING: 1.0,
}
_MID_HALF_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 2.0,
    SportType.CYCLING: 2.75,
    SportType.SWIMMING: 1.25,
}
_MID_LONG_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 2.5,
    SportType.CYCLING: 4.25,
    SportType.SWIMMING: 1.75,
}
_HIGH_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 3.0,
    SportType.CYCLING: 3.5,
    SportType.SWIMMING: 2.0,
}
# Held at 7.5 h/wk rather than pushed down into the reference band: recent
# hours are also the capacity projector's ramp base, and below ~7 h/wk the
# 20-week BASIC Ironman plan stops reaching race-distance long sessions
# (five shape rules regress). Ratio 1.2-1.6 — above the bar, below the
# reference band, deliberately.
_LOW_TRI_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 2.5,
    SportType.CYCLING: 3.5,
    SportType.SWIMMING: 1.5,
}

_MAINT_RUN_BASIC_HOURS: dict[SportType, float] = {SportType.RUNNING: 3.0}
_MAINT_RUN_MODERATE_HOURS: dict[SportType, float] = {SportType.RUNNING: 4.5}
_MAINT_RUN_COMPETITIVE_HOURS: dict[SportType, float] = {SportType.RUNNING: 6.0}

_MAINT_TRI_BASIC_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 1.5,
    SportType.CYCLING: 2.5,
    SportType.SWIMMING: 1.0,
}
_MAINT_TRI_MODERATE_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 2.0,
    SportType.CYCLING: 3.5,
    SportType.SWIMMING: 1.5,
}
_MAINT_TRI_COMPETITIVE_HOURS: dict[SportType, float] = {
    SportType.RUNNING: 2.5,
    SportType.CYCLING: 4.5,
    SportType.SWIMMING: 2.0,
}


def _runner_longest(long_min: int = 60) -> dict[tuple[SportType, ActivityFamily], int]:
    return {(SportType.RUNNING, ActivityFamily.LONG): long_min}


def _tri_longest(
    run_min: int = 60, bike_min: int = 90, swim_min: int = 30
) -> dict[tuple[SportType, ActivityFamily], int]:
    return {
        (SportType.RUNNING, ActivityFamily.LONG): run_min,
        (SportType.CYCLING, ActivityFamily.LONG): bike_min,
        (SportType.SWIMMING, ActivityFamily.EASY): swim_min,
    }


# Scenario definitions (20 curated)

_START = DEFAULT_START

_SCENARIOS_LIST: list[Scenario] = [
    # Running Short
    Scenario(
        name="5k_beginner",
        description="5K 10-week plan, beginner runner, basic ambition.",
        tags=("running", "short", "beginner"),
        goals=(
            RaceGoal(GoalType.RUNNING_5K, _START + timedelta(weeks=10, days=-1), "A"),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_NOVICE_RUNNER_HOURS,
        longest_recent=_runner_longest(45),
    ),
    Scenario(
        name="10k_competitive",
        description="10K 12-week plan, high-fitness runner, competitive.",
        tags=("running", "short", "competitive"),
        goals=(
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=12, days=-1), "A"),
        ),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_HIGH_RUNNER_HOURS,
        longest_recent=_runner_longest(90),
    ),
    # Running Long
    Scenario(
        name="hm_moderate",
        description="Half marathon 14-week plan, mid runner, moderate.",
        tags=("running", "long"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_HALF_MARATHON,
                _START + timedelta(weeks=14, days=-1),
                "A",
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_RUNNER_HOURS,
        longest_recent=_runner_longest(75),
    ),
    Scenario(
        name="marathon_beginner",
        description="Marathon 20-week plan, beginner runner, basic.",
        tags=("running", "long", "beginner"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MARATHON, _START + timedelta(weeks=20, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_BEGINNER_RUNNER_HOURS,
        longest_recent=_runner_longest(50),
    ),
    Scenario(
        name="marathon_competitive",
        description="Marathon 16-week, high runner, competitive. Regression: T1+T2.",
        tags=("running", "long", "competitive", "regression"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MARATHON, _START + timedelta(weeks=16, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_HIGH_RUNNER_HOURS,
        longest_recent=_runner_longest(100),
    ),
    # Triathlon Short
    Scenario(
        name="sprint_tri_beginner",
        description="Sprint triathlon 10-week, beginner, basic.",
        tags=("triathlon", "short", "beginner"),
        goals=(
            RaceGoal(
                GoalType.SPRINT_TRIATHLON, _START + timedelta(weeks=10, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_BEGINNER_TRI_HOURS,
        longest_recent=_tri_longest(40, 60, 20),
    ),
    Scenario(
        name="olympic_tri_moderate",
        description="Olympic triathlon 12-week, mid triathlete, moderate.",
        tags=("triathlon", "short"),
        goals=(
            RaceGoal(
                GoalType.OLYMPIC_TRIATHLON, _START + timedelta(weeks=12, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_TRI_HOURS,
        longest_recent=_tri_longest(),
    ),
    # Triathlon Long
    Scenario(
        name="half_ironman_basic",
        description="Half Ironman 16-week, low triathlete, basic.",
        tags=("triathlon", "long", "beginner"),
        goals=(
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=16, days=-1), "A"),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_LOW_TRI_HOURS,
        longest_recent=_tri_longest(55, 75, 25),
    ),
    Scenario(
        name="half_ironman_moderate",
        description="Half Ironman 16-week, mid triathlete, moderate.",
        tags=("triathlon", "long"),
        goals=(
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=16, days=-1), "A"),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_HALF_TRI_HOURS,
        longest_recent=_tri_longest(),
    ),
    Scenario(
        name="half_ironman_competitive",
        description="Half Ironman 16-week, high triathlete, competitive.",
        tags=("triathlon", "long", "competitive"),
        goals=(
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=16, days=-1), "A"),
        ),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_HIGH_TRI_HOURS,
        longest_recent=_tri_longest(75, 120, 45),
    ),
    Scenario(
        name="ironman_moderate",
        description="Ironman 22-week, mid triathlete, moderate. Regression: T3+T4+T6.",
        tags=("triathlon", "long", "regression"),
        goals=(RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=22, days=-1), "A"),),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_LONG_TRI_HOURS,
        longest_recent=_tri_longest(),
    ),
    Scenario(
        name="ironman_competitive",
        description="Ironman 22-week, high triathlete, competitive. Regression: T5.",
        tags=("triathlon", "long", "competitive", "regression"),
        goals=(RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=22, days=-1), "A"),),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_HIGH_TRI_HOURS,
        longest_recent=_tri_longest(75, 120, 45),
    ),
    # Multi-goal
    Scenario(
        name="marathon_b_hm",
        description="Marathon A (wk 20) + HM B (wk 12), mid runner, moderate.",
        tags=("running", "long", "multi-goal"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MARATHON, _START + timedelta(weeks=20, days=-1), "A"
            ),
            RaceGoal(
                GoalType.RUNNING_HALF_MARATHON,
                _START + timedelta(weeks=12, days=-1),
                "B",
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_RUNNER_HOURS,
        longest_recent=_runner_longest(75),
    ),
    Scenario(
        name="ironman_a_oly_b",
        description="Ironman A (wk 24) + Olympic B (wk 14), mid tri, moderate.",
        tags=("triathlon", "long", "multi-goal"),
        goals=(
            RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=24, days=-1), "A"),
            RaceGoal(
                GoalType.OLYMPIC_TRIATHLON, _START + timedelta(weeks=14, days=-1), "B"
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_LONG_TRI_HOURS,
        longest_recent=_tri_longest(),
    ),
    Scenario(
        name="ironman_a_half_b",
        description="Ironman A (wk 29) + half-Ironman B (wk 21), the last week "
        "before the peak, mid tri, moderate.",
        tags=("triathlon", "long", "multi-goal"),
        goals=(
            RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=29, days=-1), "A"),
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=21, days=-1), "B"),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_LONG_TRI_HOURS,
        longest_recent=_tri_longest(),
    ),
    # Edge cases
    Scenario(
        name="marathon_fatigued",
        description="Marathon 16-week, fatigued runner (ATL >> CTL), moderate.",
        tags=("running", "long", "regression", "fatigued"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MARATHON, _START + timedelta(weeks=16, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MID_RUNNER_HOURS,
        longest_recent=_runner_longest(75),
        ctl_0={SportType.RUNNING: 25.0},
        atl_0={SportType.RUNNING: 45.0},
    ),
    Scenario(
        name="ironman_recovery",
        description="Ironman 22-week, low triathlete, basic. Regression: T8.",
        tags=("triathlon", "long", "regression", "recovery"),
        goals=(RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=22, days=-1), "A"),),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_LOW_TRI_HOURS,
        longest_recent=_tri_longest(55, 75, 25),
    ),
    # Trail
    Scenario(
        name="trail_20k_basic",
        description="Trail 20K 20-week, default basic profile.",
        tags=("trail", "beginner"),
        goals=(
            RaceGoal(GoalType.TRAIL_20K, _START + timedelta(weeks=20, days=-1), "A"),
        ),
        ambition=AmbitionLevel.BASIC,
    ),
    Scenario(
        name="trail_50k_moderate",
        description="Trail 50K 24-week, default moderate profile.",
        tags=("trail",),
        goals=(
            RaceGoal(GoalType.TRAIL_50K, _START + timedelta(weeks=24, days=-1), "A"),
        ),
        ambition=AmbitionLevel.MODERATE,
    ),
    Scenario(
        name="trail_returner_20k",
        description=(
            "Trail 20K 20-week from a ~1 h/wk returner base — the miniature-"
            "plan / race-rehearsal-warning case (maintenance-first advice)."
        ),
        tags=("trail", "regression", "beginner"),
        goals=(
            RaceGoal(GoalType.TRAIL_20K, _START + timedelta(weeks=20, days=-1), "A"),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours={SportType.RUNNING: 1.0},
        longest_recent=_runner_longest(35),
    ),
    Scenario(
        name="running_returner_10k",
        description=(
            "Running 10K 16-week from a ~1 h/wk returner base — the "
            "low-capacity floor-drop case (R-SESS-030) for a run goal."
        ),
        tags=("running", "regression", "beginner"),
        goals=(
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=16, days=-1), "A"),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours={SportType.RUNNING: 1.0},
        longest_recent=_runner_longest(35),
    ),
    Scenario(
        name="sprint_returner_tri",
        description=(
            "Sprint triathlon 12-week from a ~2.5 h/wk returner base — the "
            "low-capacity floor-drop case (R-SESS-030) for a multi-sport goal."
        ),
        tags=("triathlon", "regression", "beginner"),
        goals=(
            RaceGoal(
                GoalType.SPRINT_TRIATHLON, _START + timedelta(weeks=12, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours={
            SportType.RUNNING: 1.0,
            SportType.CYCLING: 1.0,
            SportType.SWIMMING: 0.5,
        },
        longest_recent={
            (SportType.RUNNING, ActivityFamily.LONG): 35,
            (SportType.CYCLING, ActivityFamily.LONG): 45,
            (SportType.SWIMMING, ActivityFamily.EASY): 15,
        },
    ),
    # Maintenance
    Scenario(
        name="trail_maintenance_basic",
        description="Trail maintenance 8-week block, basic ambition.",
        tags=("trail", "maintenance", "beginner"),
        goals=(
            RaceGoal(
                GoalType.TRAIL_MAINTENANCE, _START + timedelta(weeks=8, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours={SportType.RUNNING: 1.5},
        longest_recent=_runner_longest(35),
    ),
    Scenario(
        name="running_maintenance_basic",
        description="Running maintenance 8-week block, basic ambition.",
        tags=("running", "maintenance", "beginner"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MAINTENANCE, _START + timedelta(weeks=8, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_MAINT_RUN_BASIC_HOURS,
        longest_recent=_runner_longest(40),
    ),
    Scenario(
        name="running_maintenance_moderate",
        description="Running maintenance 8-week block, moderate ambition.",
        tags=("running", "maintenance"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MAINTENANCE, _START + timedelta(weeks=8, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MAINT_RUN_MODERATE_HOURS,
        longest_recent=_runner_longest(50),
    ),
    Scenario(
        name="running_maintenance_competitive",
        description="Running maintenance 8-week block, competitive ambition.",
        tags=("running", "maintenance", "competitive"),
        goals=(
            RaceGoal(
                GoalType.RUNNING_MAINTENANCE, _START + timedelta(weeks=8, days=-1), "A"
            ),
        ),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_MAINT_RUN_COMPETITIVE_HOURS,
        longest_recent=_runner_longest(65),
    ),
    Scenario(
        name="triathlon_maintenance_basic",
        description="Triathlon maintenance 8-week block, basic ambition.",
        tags=("triathlon", "maintenance", "beginner"),
        goals=(
            RaceGoal(
                GoalType.TRIATHLON_MAINTENANCE,
                _START + timedelta(weeks=8, days=-1),
                "A",
            ),
        ),
        ambition=AmbitionLevel.BASIC,
        recent_hours=_MAINT_TRI_BASIC_HOURS,
        longest_recent=_tri_longest(35, 60, 20),
    ),
    Scenario(
        name="triathlon_maintenance_moderate",
        description="Triathlon maintenance 8-week block, moderate ambition.",
        tags=("triathlon", "maintenance"),
        goals=(
            RaceGoal(
                GoalType.TRIATHLON_MAINTENANCE,
                _START + timedelta(weeks=8, days=-1),
                "A",
            ),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MAINT_TRI_MODERATE_HOURS,
        longest_recent=_tri_longest(45, 75, 25),
    ),
    Scenario(
        name="triathlon_maintenance_moderate_tune_up",
        description=(
            "Triathlon maintenance 8-week block, moderate ambition, with a "
            "trail 50K B race on the Saturday of week 5 — the maintenance "
            "tune-up case (R-PHASE-019)."
        ),
        tags=("triathlon", "maintenance", "tune-up"),
        goals=(
            RaceGoal(
                GoalType.TRIATHLON_MAINTENANCE,
                _START + timedelta(weeks=8, days=-1),
                "A",
            ),
            RaceGoal(GoalType.TRAIL_50K, _START + timedelta(weeks=5, days=-2), "B"),
        ),
        ambition=AmbitionLevel.MODERATE,
        recent_hours=_MAINT_TRI_MODERATE_HOURS,
        longest_recent=_tri_longest(45, 75, 25),
    ),
    Scenario(
        name="triathlon_maintenance_competitive",
        description="Triathlon maintenance 8-week block, competitive ambition.",
        tags=("triathlon", "maintenance", "competitive"),
        goals=(
            RaceGoal(
                GoalType.TRIATHLON_MAINTENANCE,
                _START + timedelta(weeks=8, days=-1),
                "A",
            ),
        ),
        ambition=AmbitionLevel.COMPETITIVE,
        recent_hours=_MAINT_TRI_COMPETITIVE_HOURS,
        longest_recent=_tri_longest(55, 90, 30),
    ),
]

# Registry

SCENARIOS: dict[str, Scenario] = {s.name: s for s in _SCENARIOS_LIST}
"""Name-keyed scenario registry."""


def scenarios_by_tag(tag: str) -> list[Scenario]:
    """Return all scenarios whose tags contain *tag*.

    Args:
        tag: Tag to filter by (case-sensitive exact match).

    Returns:
        List of matching scenarios (may be empty).
    """
    return [s for s in _SCENARIOS_LIST if tag in s.tags]


def list_scenarios() -> None:
    """Print a formatted table of all registered scenarios."""
    print(f"\n{'Name':<26}  {'Ambition':<12}  {'Tags':<40}  Description")
    print("-" * 110)
    for s in _SCENARIOS_LIST:
        tags_str = ", ".join(s.tags)
        print(f"{s.name:<26}  {s.ambition.value:<12}  {tags_str:<40}  {s.description}")
