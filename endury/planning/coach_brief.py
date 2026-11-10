"""Deterministic coach brief for a planner plan.

A templated summary built from the phase plan and the materialized weekly
hours: goal + horizon, phase arc with week ranges, secondary races, peak
volume, and the taper.
Rendered in the plan-preview strip and injected into the coach chat
context after commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from endury.planner.foundations.maintenance_tables import maintenance_weekly_hours
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planning.wire.types import SerializedSessionPayload

#: Provenance labels from ``athlete_state["sources"]``, worded for prose.
_SOURCE_LABELS: dict[str, str] = {
    "history": "intervals.icu history",
    "self_report": "self-reported",
    "default": "goal default",
}

#: Relative tolerance for calling measured and tier volume "about" equal.
_TIER_COMPARISON_TOLERANCE = 0.10

_GOAL_LABELS: dict[str, str] = {
    "running_5k": "5K",
    "running_10k": "10K",
    "running_half_marathon": "half marathon",
    "running_marathon": "marathon",
    "trail_20k": "Trail 20K",
    "trail_50k": "Trail 50K",
    "trail_100k": "Trail 100K",
    "sprint_triathlon": "sprint triathlon",
    "olympic_triathlon": "standard-distance triathlon",
    "half_ironman": "half-distance triathlon",
    "ironman": "full-distance triathlon",
    "running_maintenance": "running maintenance",
    "trail_maintenance": "trail maintenance",
    "triathlon_maintenance": "triathlon maintenance",
}


def _goal_label(goal_type_value: str) -> str:
    """Human label for a goal-type value."""
    return _GOAL_LABELS.get(goal_type_value, goal_type_value.replace("_", " "))


#: Peak-week climb guidance per trail goal and tier, metres of gain per
#: week (docs/research/trail_running.md, "Weekly vertical gain" — a
#: derived table, presented as guidance, never a measured norm). The
#: maintenance rows hold at roughly half the 20K peak bands.
_TRAIL_WEEKLY_VERT_M: dict[GoalType, dict[AmbitionLevel, tuple[int, int]]] = {
    GoalType.TRAIL_20K: {
        AmbitionLevel.BASIC: (800, 1200),
        AmbitionLevel.MODERATE: (1200, 1800),
        AmbitionLevel.COMPETITIVE: (1800, 2500),
    },
    GoalType.TRAIL_50K: {
        AmbitionLevel.BASIC: (1500, 2000),
        AmbitionLevel.MODERATE: (2000, 2800),
        AmbitionLevel.COMPETITIVE: (2800, 3500),
    },
    GoalType.TRAIL_100K: {
        AmbitionLevel.BASIC: (2500, 3500),
        AmbitionLevel.MODERATE: (3500, 4500),
        AmbitionLevel.COMPETITIVE: (4500, 6000),
    },
    GoalType.TRAIL_MAINTENANCE: {
        AmbitionLevel.BASIC: (450, 750),
        AmbitionLevel.MODERATE: (700, 1100),
        AmbitionLevel.COMPETITIVE: (900, 1500),
    },
}

#: The peak-week hours each vertical band above assumes: the low end of
#: each tier's peak weekly hours (docs/research/trail_running.md §13.4,
#: "Peak weekly hours by tier"). A plan whose own peak week falls short of
#: this gets its band scaled down in proportion, so the brief never
#: promises climbing the plan's weeks cannot hold. No maintenance rows:
#: those goals keep their steady band unscaled.
_TRAIL_BAND_PEAK_HOURS: dict[GoalType, dict[AmbitionLevel, float]] = {
    GoalType.TRAIL_20K: {
        AmbitionLevel.BASIC: 4.0,
        AmbitionLevel.MODERATE: 6.0,
        AmbitionLevel.COMPETITIVE: 8.0,
    },
    GoalType.TRAIL_50K: {
        AmbitionLevel.BASIC: 5.0,
        AmbitionLevel.MODERATE: 7.0,
        AmbitionLevel.COMPETITIVE: 9.0,
    },
    GoalType.TRAIL_100K: {
        AmbitionLevel.BASIC: 8.0,
        AmbitionLevel.MODERATE: 10.0,
        AmbitionLevel.COMPETITIVE: 12.0,
    },
}


def _phase_ranges(plan: PhasePlan) -> list[tuple[str, Any, int, int]]:
    """Contiguous (phase, owning_goal, first_week, last_week) runs, 1-indexed.

    Splitting on ``owning_goal`` keeps a B/C race week as its own run even
    when its phase matches the neighbouring A-goal weeks.
    """
    ranges: list[tuple[str, Any, int, int]] = []
    for week in plan.weeks:
        label = week.phase.value
        if (
            ranges
            and ranges[-1][0] == label
            and ranges[-1][1] == week.owning_goal
            and ranges[-1][3] == week.week
        ):
            ranges[-1] = (label, week.owning_goal, ranges[-1][2], week.week + 1)
        else:
            ranges.append((label, week.owning_goal, week.week + 1, week.week + 1))
    return ranges


def _phase_arc_sentence(plan: PhasePlan, prior_weeks: int = 0) -> str:
    """One sentence describing the phase progression.

    A run owned by a B/C tune-up race is labelled with that race, so a
    mid-plan "taper week 11" before more build weeks reads as intentional.

    Args:
        plan: The (possibly truncated) phase plan.
        prior_weeks: Completed weeks preceding the plan's first week; shifts
            the printed week numbers into the full-plan frame.
    """
    parts = []
    for label, owning_goal, first, last in _phase_ranges(plan):
        first += prior_weeks
        last += prior_weeks
        span = f"week {first}" if first == last else f"weeks {first}-{last}"
        if owning_goal is not None and owning_goal.priority != "A":
            parts.append(
                f"{label} {span} (tune-up: "
                f"{_goal_label(owning_goal.goal_type.value)}, "
                f"{owning_goal.priority}-race)"
            )
        else:
            parts.append(f"{label} {span}")
    return "Phase arc: " + ", ".join(parts) + "."


def _sport_hours_clause(
    sport_names: list[str],
    sports: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> str:
    """One clause naming hours/sport/source for the "Built at" sentence.

    All goal sports sharing one source ("history", "self_report", or
    "default") are folded into a single combined clause; a multi-sport
    athlete whose sports resolved from different sources gets one
    sub-clause per sport instead.

    Args:
        sport_names: The goal's sport keys, in payload order.
        sports: The payload's ``sports`` block (per-sport hours/thresholds).
        sources: The payload's ``sources`` block (per-sport provenance).

    Returns:
        The hours/sport/source clause, without a leading "Built at ...".
    """
    unique_sources = {sources.get(name, "default") for name in sport_names}
    if len(unique_sources) == 1:
        source = next(iter(unique_sources))
        label = _SOURCE_LABELS.get(source, source)
        prefix = "measured " if source == "history" else ""
        total_hours = sum(sports[name]["recent_weekly_hours"] for name in sport_names)
        return f"{total_hours:.1f} h/week of {prefix}{', '.join(sport_names)} ({label})"
    parts = []
    for name in sport_names:
        source = sources.get(name, "default")
        label = _SOURCE_LABELS.get(source, source)
        prefix = "measured " if source == "history" else ""
        hours = sports[name]["recent_weekly_hours"]
        parts.append(f"{hours:.1f} h/week of {prefix}{name} ({label})")
    return ", ".join(parts)


def _inputs_sentence(
    goal_type: GoalType,
    ambition: AmbitionLevel,
    athlete_state: Mapping[str, Any],
    *,
    minutes_by_week: dict[int, float],
    prior_weeks: int,
) -> str | None:
    """The "what this plan was built from" sentence, or ``None`` if unknown.

    Args:
        goal_type: The plan's A-goal type.
        ambition: Ambition level of the request.
        athlete_state: The ``athlete_state`` payload the plan was sized
            from (same shape as ``plans.athlete_state_json``).
        minutes_by_week: Materialized session minutes by week index, used
            for the race-goal ramp clause (first-week hours).
        prior_weeks: Completed weeks preceding this plan's first week.

    Returns:
        One sentence, or ``None`` when the payload has no per-sport data.
    """
    sports = athlete_state.get("sports") or {}
    if not sports:
        return None
    sources = athlete_state.get("sources") or {}
    sport_names = list(sports.keys())
    sentence = (
        f"Built at {ambition.value} ambition from "
        f"{_sport_hours_clause(sport_names, sports, sources)}"
    )
    if is_maintenance_goal(goal_type):
        tier_hours = maintenance_weekly_hours(goal_type, ambition)
        measured_hours = sum(
            sports[name]["recent_weekly_hours"] for name in sport_names
        )
        if measured_hours:
            diff = tier_hours - measured_hours
            if abs(diff) <= _TIER_COMPARISON_TOLERANCE * measured_hours:
                comparison = "about"
            elif diff > 0:
                comparison = "above"
            else:
                comparison = "below"
            sentence += (
                f"; the block holds the tier's {tier_hours:.1f} h/week — "
                f"{comparison} what you currently train"
            )
    elif minutes_by_week:
        first_week = min(minutes_by_week)
        first_week_hours = minutes_by_week[first_week] / 60.0
        sentence += (
            f"; weekly volume ramps from {first_week_hours:.1f} h in "
            f"week {first_week + 1 + prior_weeks} to the peak"
        )
    return sentence + "."


def _scaled_vert_band(
    vert_band: tuple[int, int],
    goal_type: GoalType,
    ambition: AmbitionLevel,
    peak_minutes: float,
) -> tuple[float, float]:
    """Scale a climb-guidance band down to the plan's own peak week.

    Args:
        vert_band: The tier's unscaled (low, high) metres-of-gain band.
        goal_type: The A-goal type.
        ambition: The plan's ambition tier.
        peak_minutes: The plan's own peak week, in minutes (``0`` when the
            plan has no sessions).

    Returns:
        ``vert_band`` scaled by ``min(1, plan peak hours / the tier's own
        peak-hours anchor)``, rounded to 100 m; unscaled when the goal has
        no peak-hours anchor (maintenance goals) or the plan has no
        sessions to find a peak week in.
    """
    band_peak_hours = _TRAIL_BAND_PEAK_HOURS.get(goal_type, {}).get(ambition)
    if not band_peak_hours or not peak_minutes:
        return float(vert_band[0]), float(vert_band[1])
    share = min(1.0, peak_minutes / 60.0 / band_peak_hours)
    return round(vert_band[0] * share, -2), round(vert_band[1] * share, -2)


def build_coach_brief(
    plan: PhasePlan,
    sessions: list[SerializedSessionPayload],
    *,
    ambition: AmbitionLevel,
    prior_weeks: int = 0,
    athlete_state: Mapping[str, Any] | None = None,
) -> str:
    """Build the deterministic coach brief for a plan.

    Args:
        plan: The L1 phase plan (the regenerated remainder, on a replan).
        sessions: Materialized session payloads (for the weekly-hours arc).
        ambition: Ambition level of the request.
        prior_weeks: Completed weeks preceding this plan's first week (a
            mid-prep replan). Week numbering and the horizon are printed in
            the full-plan frame so the brief never describes the fragment
            as if it were the whole plan.
        athlete_state: The ``athlete_state`` payload the plan was sized from
            (the same dict persisted as ``plans.athlete_state_json``). When
            given, one sentence naming what the plan was built from and how
            that compares to the athlete's actual training is inserted
            right after the opening line.

    Returns:
        A short multi-sentence plan narrative.
    """
    a_goal = plan.a_goal
    total_weeks = prior_weeks + len(plan.weeks)
    minutes_by_week: dict[int, float] = {}
    for session in sessions:
        minutes_by_week[session["week_index"]] = (
            minutes_by_week.get(session["week_index"], 0.0)
            + session["duration_minutes"]
        )
    if is_maintenance_goal(a_goal.goal_type):
        lines = [
            f"{total_weeks}-week {_goal_label(a_goal.goal_type.value)} block at "
            f"{ambition.value} ambition, holding fitness through "
            f"{a_goal.race_date.isoformat()} — no race on the calendar."
        ]
    else:
        lines = [
            f"{total_weeks}-week {_goal_label(a_goal.goal_type.value)} plan at "
            f"{ambition.value} ambition, racing {a_goal.race_date.isoformat()}."
        ]
    if athlete_state:
        inputs_sentence = _inputs_sentence(
            a_goal.goal_type,
            ambition,
            athlete_state,
            minutes_by_week=minutes_by_week,
            prior_weeks=prior_weeks,
        )
        if inputs_sentence:
            lines.append(inputs_sentence)
    if prior_weeks:
        lines.append(f"Resumed mid-plan at week {prior_weeks + 1}.")
    lines.append(_phase_arc_sentence(plan, prior_weeks))

    secondary = [e for e in plan.race_events if e.goal.priority != "A"]
    if secondary:
        tune_ups = ", ".join(
            f"{_goal_label(e.goal.goal_type.value)} ({e.goal.priority}-race) "
            f"in week {e.week + 1 + prior_weeks}"
            for e in sorted(secondary, key=lambda e: e.week)
        )
        lines.append(f"Tune-up races: {tune_ups}.")

    peak_minutes = 0.0
    if minutes_by_week:
        peak_week, peak_minutes = max(minutes_by_week.items(), key=lambda item: item[1])
        lines.append(
            f"Volume peaks around {peak_minutes / 60.0:.1f} h in "
            f"week {peak_week + 1 + prior_weeks}."
        )

    vert_band = _TRAIL_WEEKLY_VERT_M.get(a_goal.goal_type, {}).get(ambition)
    if vert_band:
        band_low, band_high = _scaled_vert_band(
            vert_band, a_goal.goal_type, ambition, peak_minutes
        )
        when = "steady" if is_maintenance_goal(a_goal.goal_type) else "at peak"
        lines.append(
            f"Weekly climb guidance: roughly {band_low:.0f}-{band_high:.0f} m "
            f"of gain per week {when} (derived guidance, not a measured "
            f"norm) — compare it against the weekly summary's measured "
            f"climbing."
        )

    recovery_weeks = sum(1 for w in plan.weeks if w.mode is WeekMode.RECOVERY)
    if recovery_weeks:
        adjective = "remaining" if prior_weeks else "scheduled"
        verb = "keeps" if recovery_weeks == 1 else "keep"
        lines.append(
            f"{recovery_weeks} {adjective} recovery "
            f"week{'s' if recovery_weeks != 1 else ''} {verb} absorption "
            f"ahead of accumulation."
        )

    taper_weeks = sum(
        1 for w in plan.weeks if w.phase is PhaseType.TAPER and w.owning_goal is None
    )
    if taper_weeks:
        lines.append(
            f"The final {taper_weeks}-week taper sheds fatigue while "
            f"holding intensity, so you arrive fresh and sharp."
        )
    return " ".join(lines)
