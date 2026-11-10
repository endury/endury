"""The Apply card's one line: what applying does, in the athlete's language.

Every previewing tool's result is read here into the sentence the card
shows under its title — whole weekday-and-date names, counted nouns, and
no field names, so the athlete can agree to it without decoding it.
``messages.summarize_tool_result`` stays the model's own terse digest of
the same result; this is the athlete's.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from endury.planner.activity.materialize import activity_display_name
from endury.planner.foundations.types import ActivityKey, GoalType, is_maintenance_goal


def _day(value: Any) -> str:
    """``2026-09-24`` → ``Thursday 24 September``."""
    day = date.fromisoformat(str(value))
    return f"{day:%A} {day.day} {day:%B}"


def _span(start: Any, through: Any) -> str:
    """``on Thursday 24 September``, or ``from … to …`` over several days."""
    if start == through:
        return f"on {_day(start)}"
    return f"from {_day(start)} to {_day(through)}"


def _count(number: int, noun: str) -> str:
    """``1 session`` / ``5 sessions``."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _num(value: Any) -> str:
    """``70.0`` → ``70``, ``72.5`` → ``72.5``."""
    return f"{float(value):g}"


def _join(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _activity(key: Any) -> str:
    """The athlete-facing name of a plan row's activity key."""
    return activity_display_name(ActivityKey(str(key)))


def _booked(workouts: list[dict[str, Any]]) -> str:
    """``the Easy Run booked for Monday 1 June and the …``."""
    return _join(
        [
            f"the {workout.get('name') or 'workout'} booked for {_day(workout['date'])}"
            for workout in workouts
        ]
    )


def _edited_note(parsed: dict[str, Any]) -> str:
    """The overwrite warning a replan carries, or nothing."""
    edited = len(parsed.get("edited_workouts") or [])
    if not edited:
        return ""
    return f" It overwrites {_count(edited, 'workout')} you edited on intervals.icu."


def _pause_training(parsed: dict[str, Any]) -> str:
    window = parsed.get("window") or {}
    span = _span(window.get("from"), window.get("through"))
    if parsed.get("status") == "empty":
        return (
            f"Mark yourself unavailable {span}. Nothing is planned then, so no "
            "sessions change."
        )
    sessions = parsed.get("sessions") or []
    if not sessions:
        # Booking-only: with a plan the bookings sit in the sweep's block, a
        # plan-less pause reports them flat.
        ad_hoc = parsed.get("ad_hoc") or {}
        workouts = ad_hoc.get("workouts") or parsed.get("workouts") or []
        return f"Pause training {span} and take {_booked(workouts)} off the calendar."
    return (
        f"Pause training {span} ({_count(len(sessions), 'session')} skipped) and "
        f"resume on {_day(parsed.get('resume_date'))}."
    )


def _swap_planned_session(parsed: dict[str, Any]) -> str:
    current = parsed.get("current") or {}
    replacement = parsed.get("replacement") or {}
    week = parsed.get("week") or {}
    line = (
        f"Swap the {_activity(current.get('activity_key'))} on "
        f"{_day(current.get('date'))} for a "
        f"{_num(replacement.get('duration_minutes'))}-minute "
        f"{replacement.get('workout_name')}"
    )
    if parsed.get("date") != current.get("date"):
        line += f", moved to {_day(parsed.get('date'))}"
    return (
        f"{line}. The week's load goes from {_num(week.get('before_tss'))} to "
        f"{_num(week.get('after_tss'))} load."
    )


def _skip_planned_session(parsed: dict[str, Any]) -> str:
    session = parsed.get("session") or {}
    return (
        f"Skip the {_activity(session.get('activity_key'))} on "
        f"{_day(session.get('date'))} and take it off your calendar."
    )


def _adjust_current_week(parsed: dict[str, Any]) -> str:
    totals = parsed.get("totals") or {}
    count = _count(len(parsed.get("sessions") or []), "remaining session")
    percent = parsed.get("reduce_percent")
    change = (
        f"Restore this week's {count} to full length"
        if not percent
        else f"Shorten this week's {count} by {percent}%"
    )
    return (
        f"{change}. The week goes from {_num(totals.get('before_minutes'))} to "
        f"{_num(totals.get('after_minutes'))} minutes."
    )


def _set_plan_directives(parsed: dict[str, Any]) -> str:
    after = (parsed.get("directives") or {}).get("after") or {}
    parts: list[str] = []
    if after.get("resume_date"):
        parts.append(f"a return to training on {_day(after['resume_date'])}")
    if after.get("unavailable"):
        away = _join([_span(start, end) for start, end in after["unavailable"]])
        parts.append(f"time away {away}")
    if after.get("suppress_tests_until"):
        parts.append(f"no fitness tests before {_day(after['suppress_tests_until'])}")
    if after.get("weekly_hours_cap") is not None:
        parts.append(f"a cap of {_num(after['weekly_hours_cap'])} hours a week")
    with_what = _join(parts) if parts else "the earlier constraints cleared"
    line = f"Replan from next Monday with {with_what}."
    removed = len(parsed.get("removes") or [])
    if removed:
        line += f" This drops {_count(removed, 'upcoming session')}."
    return line + _edited_note(parsed)


def _replan_active_plan(parsed: dict[str, Any]) -> str:
    return (
        "Replan everything from next Monday onward using your latest training "
        f"data.{_edited_note(parsed)}"
    )


def _unschedule_workout(parsed: dict[str, Any]) -> str:
    return f"Take {_booked(parsed.get('workouts') or [])} off the calendar."


def _add_race_to_plan(parsed: dict[str, Any]) -> str:
    fits_as = parsed.get("fits_as")
    article = "an" if fits_as == "A" else "a"
    return (
        f"Add {parsed.get('race_name')} on {_day(parsed.get('race_date'))} to the "
        f"plan as {article} {fits_as} race and replan from next Monday."
    )


def _create_plan(parsed: dict[str, Any]) -> str:
    races = parsed.get("races") or []
    a_race = next((race for race in races if race.get("priority") == "A"), races[0])
    weeks = f"{parsed.get('weeks')}-week {parsed.get('goal')}"
    start = _day(parsed.get("plan_start"))
    if is_maintenance_goal(GoalType(str(a_race.get("goal_type")))):
        line = f"Build a {weeks} block from {start}."
    else:
        line = (
            f"Build a {weeks} plan from {start} to race day on "
            f"{_day(a_race.get('race_date'))}."
        )
    if parsed.get("replaces"):
        line += " It replaces your current plan."
    return line


def _schedule_workout_override(parsed: dict[str, Any]) -> str:
    workout = parsed.get("workout") or {}
    line = (
        f"Add a {_num(workout.get('duration_minutes'))}-minute "
        f"{workout.get('workout_name')} on {_day(parsed.get('date'))}"
    )
    collision = parsed.get("plan_collision")
    if collision:
        line += (
            f" alongside the {_activity(collision.get('activity_key'))} already "
            "planned that day"
        )
    line += "."
    if parsed.get("plan_row"):
        week = parsed.get("week") or {}
        line += (
            f" The week's load goes from {_num(week.get('before_tss'))} to "
            f"{_num(week.get('after_tss'))} load."
        )
    return line


def _cancel_or_move_race(parsed: dict[str, Any]) -> str:
    name = (parsed.get("race") or {}).get("name")
    if parsed.get("cancel"):
        return f"Cancel {name} and take it off your calendar."
    return f"Move {name} to {parsed.get('new_date')} on your calendar."


def _set_athlete_thresholds(parsed: dict[str, Any]) -> str:
    changes = _join(
        [f"{change['label']} to {change['proposed']}" for change in parsed["changes"]]
    )
    return f"Set your {parsed.get('sport')} {changes} on intervals.icu."


#: One composer per previewing tool; a gated tool without one is a test
#: failure (test_every_gated_tool_has_a_card_line), never a fallback.
CARD_LINES: dict[str, Callable[[dict[str, Any]], str]] = {
    "pause_training": _pause_training,
    "swap_planned_session": _swap_planned_session,
    "skip_planned_session": _skip_planned_session,
    "adjust_current_week": _adjust_current_week,
    "set_plan_directives": _set_plan_directives,
    "replan_active_plan": _replan_active_plan,
    "unschedule_workout": _unschedule_workout,
    "add_race_to_plan": _add_race_to_plan,
    "cancel_or_move_race": _cancel_or_move_race,
    "create_plan": _create_plan,
    "schedule_workout_override": _schedule_workout_override,
    "set_athlete_thresholds": _set_athlete_thresholds,
}


def preview_summary(name: str, parsed: dict[str, Any]) -> str:
    """Return the card's line for one tool's preview-time payload.

    Args:
        name: The previewing tool.
        parsed: Its result, already parsed — a preview, or
            ``pause_training``'s ``"empty"`` with an active plan.
    """
    return CARD_LINES[name](parsed)


__all__ = ["CARD_LINES", "preview_summary"]
