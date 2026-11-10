"""Endury's planner as a contestant: its plan exported into the benchmark's table.

The planner gets the same brief the models get, as a ``PlanRequest``: the
athlete's recent hours and longest sessions, the tier, the race and an
optional weekly cap. Every session's steps are its workout card, built at
the same synthetic athlete the scorecard uses: the card's steps in order,
repeat blocks kept, each in the highest zone its targets name (its HR, pace
or power zone, or the zone its watts or pace fall in), so the benchmark
reads what the athlete is told, as it reads a model's steps. The steps are
scaled to the sized hours, which are the session. A swim's pace is a pace
ratio; it is cubed before the zone lookup because drag makes swimming power
scale with the cube of speed, so a swim at 0.8 of threshold pace is easy,
not tempo.
"""

from __future__ import annotations

from bench.load import zone_of
from bench.scenarios import START, Scenario
from bench.schema import Plan, Repeat, Session, Step, Week
from devtools.planner._shared.sessions import build_synthetic_resolver
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.sets import set_occurrences, with_set
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    session_coaching_notes,
    workout_total_meters,
)
from endury.planner.foundations.request import (
    AthleteState,
    PlanningConstraints,
    PlanRequest,
    SportState,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SessionOrigin,
    SportType,
)
from endury.planner.pipeline import PlanResult, generate_plan
from endury.training.load import ResolverProtocol
from endury.training.workout import TargetDef, WorkoutStep

SPORT: dict[str, SportType] = {
    "run": SportType.RUNNING,
    "bike": SportType.CYCLING,
    "swim": SportType.SWIMMING,
}
SPORT_LABEL: dict[SportType, str] = {v: k for k, v in SPORT.items()}
#: The family the athlete's longest recent session is filed under, per sport,
#: as the planner's own default profile files it.
LONGEST_FAMILY: dict[str, ActivityFamily] = {
    "run": ActivityFamily.LONG,
    "bike": ActivityFamily.LONG,
    "swim": ActivityFamily.EASY,
}
FAMILY_TO_KIND: dict[ActivityFamily, str] = {
    ActivityFamily.EASY: "easy",
    ActivityFamily.RECOVERY: "recovery",
    ActivityFamily.LONG: "long",
    ActivityFamily.TEMPO: "tempo",
    ActivityFamily.THRESHOLD: "threshold",
    ActivityFamily.INTERVAL: "intervals",
    ActivityFamily.TEST: "test",
}


def _kind(activity_key: str, family: ActivityFamily, origin: SessionOrigin) -> str:
    """The benchmark's type label for an activity."""
    if origin is SessionOrigin.TEST:
        return "test"
    if "race_pace" in activity_key or activity_key.endswith("_mp"):
        return "race_pace"
    return FAMILY_TO_KIND[family]


#: A step that names only an effort (a fitness test's warm-up and protocol)
#: gets its zone from RPE. RPE 9 is a 20-30 minute all-out test, a threshold
#: effort (IF about 1.0), so zone 5 starts at RPE 10.
_RPE_ZONE = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 4, 10: 5}


def _zone_at(intensity_factor: float) -> int:
    # Menus author intensity factors to two decimals; the card's rounded
    # watts and paces must not tip a step at a zone edge (IF 0.75) over it.
    return zone_of(round(intensity_factor, 2))


def _target_zone(t: TargetDef, sport: SportType, r: ResolverProtocol) -> int | None:
    """The benchmark zone one target names, or None when it names none.

    Watts are read on the bike only: running power is a display metric
    whose watts are not the pace intensity the run is prescribed at.
    """
    if t.type in {"heart_rate_zone", "pace_zone", "power_zone", "swim_pace_zone"}:
        return min(t.zone, 5) if t.zone is not None else None
    if (
        t.type == "power"
        and sport is SportType.CYCLING
        and t.low_watts
        and t.high_watts
        and r.cycling_ftp_watts
    ):
        return _zone_at((t.low_watts + t.high_watts) / 2 / r.cycling_ftp_watts)
    if (
        t.type == "pace"
        and t.low_min_per_km
        and t.high_min_per_km
        and r.running_threshold_pace_min_per_km
    ):
        pace = (t.low_min_per_km + t.high_min_per_km) / 2
        return _zone_at(r.running_threshold_pace_min_per_km / pace)
    if (
        t.type == "swim_pace"
        and t.low_sec_per_100m
        and t.high_sec_per_100m
        and r.swim_css_sec_per_100m
    ):
        pace = (t.low_sec_per_100m + t.high_sec_per_100m) / 2
        return _zone_at((r.swim_css_sec_per_100m / pace) ** 3)
    if t.type == "rpe" and t.rpe is not None:
        return _RPE_ZONE[t.rpe]
    return None


def _step_zone(step: WorkoutStep, sport: SportType, r: ResolverProtocol) -> int:
    """The highest zone a card step names; RPE only when nothing else does."""
    targets = [
        t
        for t in [step.target, step.secondary_target, *(step.additional_targets or [])]
        if t is not None
    ]
    zones = [z for t in targets if t.type != "rpe" if (z := _target_zone(t, sport, r))]
    if not zones:
        zones = [z for t in targets if (z := _target_zone(t, sport, r))]
    return max(zones, default=1)


def _step_minutes(step: WorkoutStep, r: ResolverProtocol) -> float:
    d = step.duration
    if d is None:
        return 0.0
    if d.value_seconds is not None:
        return d.value_seconds / 60.0
    css = r.swim_css_sec_per_100m or DEFAULT_SWIM_CSS_SEC_PER_100M
    return (d.value_meters or 0.0) * css / 100.0 / 60.0


def card_steps(
    steps: list[WorkoutStep], sport: SportType, r: ResolverProtocol
) -> tuple[list[Step], list[Repeat]]:
    """A card's steps in order, a repeat block's first step as work, the rest as rest."""
    out: list[Step] = []
    repeats: list[Repeat] = []
    for step in steps:
        if step.type == "repeat" and step.steps and step.iterations:
            inner, _ = card_steps(step.steps, sport, r)
            if not inner:
                continue
            on, off = inner[0], inner[1] if len(inner) > 1 else None
            repeats.append(
                Repeat(
                    step.iterations,
                    on.zone,
                    on.minutes,
                    off.zone if off else None,
                    off.minutes if off else 0.0,
                )
            )
            for _ in range(step.iterations):
                out.extend(
                    Step(s.zone, s.minutes, "on" if i == 0 else "off")
                    for i, s in enumerate(inner)
                )
        elif (minutes := _step_minutes(step, r)) > 0:
            out.append(Step(_step_zone(step, sport, r), minutes))
    return out, repeats


def plan_request(scenario: Scenario) -> PlanRequest:
    """The planner's request for a benchmark scenario."""
    athlete = AthleteState(
        sports={
            SPORT[sport]: SportState(
                recent_weekly_hours=hours,
                longest_recent_min={
                    LONGEST_FAMILY[sport]: scenario.athlete.longest_min[sport]
                },
            )
            for sport, hours in scenario.athlete.hours.items()
        }
    )
    return PlanRequest(
        goals=[RaceGoal(GoalType(scenario.goal), scenario.race_day, "A")],
        training_start=START,
        ambition=AmbitionLevel(scenario.tier),
        athlete=athlete,
        constraints=PlanningConstraints(weekly_hours_cap=scenario.weekly_cap_hours),
    )


def export(result: PlanResult, scenario: Scenario) -> Plan:
    """Flatten a planner result into the benchmark's plan table, steps included."""
    ensure_registry_loaded()
    resolver = build_synthetic_resolver()
    by_slot = {s.slot_id: s for s in result.sessions}
    weeks = {
        ws.week: Week(ws.week + 1, ws.phase.value, ws.mode.value)
        for ws in result.phase_plan.weeks
    }
    goal = result.phase_plan.a_goal.goal_type
    # The athlete's cards rotate their sets (endury.planner.activity.sets).
    occurrences = set_occurrences(result.assignments)
    for assignment in sorted(result.assignments, key=lambda a: (a.date, a.slot_id)):
        placed = by_slot[assignment.slot_id]
        activity = with_set(
            get_activity(assignment.activity_key), occurrences[assignment.slot_id]
        )
        target_tss = assignment_target_tss(activity, assignment.target_hours, resolver)
        minutes = assignment.target_hours * 60.0
        workout = activity.build(target_tss, resolver=resolver)
        steps, repeats = card_steps(workout.steps, placed.sport, resolver)
        # The sized hours are the session; the card gives its steps. Steps
        # are whole seconds, as a workout card shows them (a 10-minute
        # warm-up must not export as 9.999999 minutes of float noise); the
        # rounding residual goes to the longest step between the warm-up and
        # the cool-down so they still sum to the session.
        card_total = sum(s.minutes for s in steps)
        scale = minutes / card_total if card_total > 0 else 0.0
        steps = [
            Step(s.zone, round(s.minutes * scale * 60.0) / 60.0, s.role) for s in steps
        ]
        if steps:
            middle = range(1, len(steps) - 1) if len(steps) > 2 else range(len(steps))
            main = max(middle, key=lambda i: steps[i].minutes)
            steps[main].minutes += minutes - sum(s.minutes for s in steps)
        for rp in repeats:
            rp.on_minutes *= scale
            rp.off_minutes *= scale
        metres = (
            workout_total_meters(workout.steps)
            if placed.sport is SportType.SWIMMING
            else None
        )
        kind = _kind(assignment.activity_key.value, activity.family, placed.origin)
        notes = " ".join(
            session_coaching_notes(
                activity, workout, duration_min=minutes, resolver=resolver, goal=goal
            )
        )
        weeks[placed.week].sessions.append(
            Session(
                day=(assignment.date - START).days % 7,
                sport=SPORT_LABEL[placed.sport],
                kind=kind,
                minutes=minutes,
                metres=metres or None,
                steps=[s for s in steps if s.minutes > 0],
                repeats=repeats,
                notes=notes,
            )
        )
    return Plan(
        [weeks[k] for k in sorted(weeks)], scenario.goal, scenario.tier, scenario.weeks
    )


def endury_plan(scenario: Scenario) -> Plan:
    """Endury's answer to a scenario, in the benchmark's table."""
    return export(generate_plan(plan_request(scenario)), scenario)
