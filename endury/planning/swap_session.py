"""Replace ONE planned session's workout with an ad-hoc one, in the plan.

Sibling of :mod:`endury.planning.adjust_week`, but a swap can move load in
either direction and is gated on an explicit confirm rather than reduce
guarantees. The session keeps its identity — same row id, slot_id, date
and week — so its intervals.icu event is re-pushed in place rather than
deleted and recreated. The prescription is materialized through the
activity registry, so the row, the mirrored solve entry and the pushed
DSL can never disagree.

An optional ``date`` also moves the session within its own week
(:func:`plan_redate`), the same write as the swap itself; a date in
another week is refused (a swap's before/after numbers are one week's
arithmetic) and sent to ``move_planned_session`` instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.common import clock
from endury.planner.activity import (
    build_catalog_enum,
    ensure_registry_loaded,
    get_activity,
)
from endury.planner.activity.materialize import (
    activity_brief,
    activity_display_name,
    assignment_target_tss,
    capability_caveat,
)
from endury.planner.activity.select import VALID_INTENTS, pick_for_intent
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.activity.steps import session_coaching_notes
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    GoalType,
    PhaseType,
    SportType,
    is_maintenance_goal,
)
from endury.planning.activity_match import session_title
from endury.planning.adjust_week import (
    _RESOLVER_REFUSAL,
    _mirror_adjustment_into_solve,
    _plan_used_thresholds,
    _title,
)
from endury.planning.commit_plan import (
    _is_race_day,
    build_plan_events,
    load_plan_context_summary,
    plan_races,
    push_plan_events,
    read_athlete_state,
    read_solve_payload,
)
from endury.planning.generate_plan import SOLVER_VERSION
from endury.planning.load_planner_metrics import PlannerRepos, get_planner_load_context
from endury.planning.materialize_plan import OPTIONAL_TITLE_PREFIX
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.wire.sessions import (
    build_commit_session_rows,
    serialize_planned_session,
)
from endury.planning.wire.types import SerializedSessionPayload

logger = logging.getLogger(__name__)

#: The stdlib date, aliased because the public verb exposes the wire name
#: ``date`` as an argument and would otherwise shadow it in its own
#: annotations.
_Date = date

#: A swap that moves the week's load by more than this is still allowed,
#: but the athlete is told the number before they agree to it.
WEEK_DELTA_WARN_PERCENT = 20.0

_PREVIEW_MESSAGE = (
    "Nothing has changed yet. The athlete applies this exact swap from its "
    "card; relay what it shows."
)
_SCOPE_CAVEAT = (
    "Only this one session changed. The rest of the plan — and the "
    "progression it was built around — is untouched."
)
_REPLAN_CAVEAT = "A replan rebuilds the plan from next Monday and would undo this swap."
_PUSH_CAVEAT = (
    "Your intervals.icu calendar still shows the previous workout until the "
    "push is retried."
)
_OVERRIDE_CAVEAT = (
    "This goes against your {reasons}, which the plan keeps protected: a "
    "deliberate change, made on your request."
)
_STATUS_RESET_CAVEAT = (
    "The session was marked {status}. A swap puts it back in play: it counts "
    "towards the week again, and the day's activity can match it."
)
_OPEN_TEST_REFUSAL = (
    "The plan already holds the {title} on {when}, {state}. Move that one "
    "instead of booking a second test: "
    'move_planned_session(session_id={session_id}, date="{day}").'
)
#: Days before a booked test that an open planned test still counts as the
#: one to move: a test missed in the last three weeks is still due.
_OPEN_TEST_LOOKBACK_DAYS = 21
_CROSS_WEEK_REFUSAL = (
    "{requested} is in a different week than {current}, and a swap reports "
    "one week's load before and after. Swap the workout on its own week, or "
    "move the session with move_planned_session first."
)
_DOUBLE_BOOKED_CAVEAT = (
    "{requested} already holds {count} other session"
    "{plural} — the day would carry {total}."
)


@dataclass(frozen=True, slots=True)
class Redate:
    """The day a swap would move its session to.

    Attributes:
        day: The new date, always in the session's own week.
        caveat: The double-booking warning to relay, or ``""``.
    """

    day: date
    caveat: str


@dataclass(frozen=True, slots=True)
class SessionSwap:
    """One planned session and the workout that would replace it.

    Attributes:
        plan: The active plan row.
        row: The ``plan_sessions`` row being replaced.
        payload: The materialized replacement session payload.
        activity: The resolved replacement activity definition.
        requested_minutes: The duration the replacement was sized to.
        week: Inclusive ``(monday, sunday)`` of the session's week.
        week_rows: Every session row in that week (before the swap).
        guards: Protection labels that were triggered and overridden.
        redate: The day the session also moves to, or None when it keeps its own.
    """

    plan: Any
    row: Any
    payload: SerializedSessionPayload
    activity: ActivityDefinition
    requested_minutes: float
    week: tuple[date, date]
    week_rows: list[Any]
    guards: list[str]
    redate: Redate | None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a refusal the coach relays verbatim. Nothing was written."""
    return {"status": "error", "message": message, **extra}


def open_test_refusal(
    rows: list[Any], activity: Any, day: date
) -> dict[str, Any] | None:
    """Point at the plan's own open test instead of booking a second one.

    Args:
        rows: The active plan's session rows.
        activity: The activity the extra would be.
        day: The day it would be booked on.

    Returns:
        The refusal naming the planned test and the move that books it on
        ``day``, or None when the extra is not a test or the plan holds no
        open test of its sport from :data:`_OPEN_TEST_LOOKBACK_DAYS` before.
    """
    if activity.family != ActivityFamily.TEST:
        return None
    earliest = (day - timedelta(days=_OPEN_TEST_LOOKBACK_DAYS)).isoformat()
    planned = next(
        (
            row
            for row in sorted(rows, key=lambda r: str(r.date))
            if str(row.family) == ActivityFamily.TEST.value
            and str(row.sport) == activity.sport.value
            and row.matched_activity_id is None
            and str(row.status) in ("pending", "unlinked")
            and str(row.date) >= earliest
        ),
        None,
    )
    if planned is None:
        return None
    when = date.fromisoformat(str(planned.date))
    return _error(
        _OPEN_TEST_REFUSAL.format(
            title=session_title(planned),
            when=f"{when:%A} {when.day} {when:%B}",
            state="missed" if when < clock.today() else "still to come",
            session_id=planned.id,
            day=day.isoformat(),
        )
    )


def _week_bounds(day: date) -> tuple[date, date]:
    """Return the Monday and Sunday bracketing *day*."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _steps_line(steps: list[Any]) -> str:
    """One-line summary of a session's structured steps."""
    if not steps:
        return "No structured steps recorded."
    types = [str(step.get("type", "step")) for step in steps]
    return f"{len(types)} steps: {', '.join(types)}"


def _stored_steps(row: Any) -> list[Any]:
    """Parse the structured steps off a session row's ``workout_json``."""
    try:
        workout = json.loads(row.workout_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    steps = workout.get("steps") if isinstance(workout, dict) else None
    return list(steps) if isinstance(steps, list) else []


def _protection_labels(plan: Any, row: Any, week: tuple[date, date]) -> list[str]:
    """Name the protections covering a session's week.

    Args:
        plan: The active plan row.
        row: The session row.
        week: Inclusive ``(monday, sunday)`` of the session's week.

    Returns:
        Short labels, e.g. ``["taper week", "race week"]``; empty when the
        week is ordinary.
    """
    labels: list[str] = []
    if str(row.phase_type) == PhaseType.TAPER.value:
        labels.append("taper week")
    monday, sunday = week
    for race in plan_races(read_solve_payload(plan)):
        if is_maintenance_goal(GoalType(str(race["goal_type"]))):
            continue
        if monday.isoformat() <= str(race["race_date"]) <= sunday.isoformat():
            labels.append("race week")
            break
    return labels


def plan_redate(
    active: Any,
    rows: list[Any],
    row: Any,
    requested: str | None,
    *,
    week: tuple[date, date],
    today: date,
) -> Redate | dict[str, Any] | None:
    """Validate a swap's requested new date.

    Args:
        active: The active plan row.
        rows: Every session row of the plan (for the double-booking check).
        row: The session being swapped.
        requested: The requested ISO date, or None when the caller asked
            for no move at all.
        week: Inclusive ``(monday, sunday)`` of the session's own week.
        today: Reference date — a session cannot move into the past.

    Returns:
        ``None`` when nothing moves — no date asked for, or the session's
        own — a refusal dict, or the validated :class:`Redate`.
    """
    if not requested or requested == str(row.date):
        return None
    try:
        day = date.fromisoformat(requested)
    except ValueError:
        return {"status": "error", "message": f"Invalid date: {requested!r}"}
    if day < today:
        return {"status": "error", "message": "Cannot move a session into the past."}
    if not (active.plan_start <= requested <= active.goal_date):
        return {"status": "error", "message": "Date is outside the plan window."}
    if _is_race_day(active, requested):
        return {"status": "error", "message": "Cannot move a session onto race day."}
    monday, sunday = week
    if not monday <= day <= sunday:
        return {
            "status": "error",
            "message": _CROSS_WEEK_REFUSAL.format(
                requested=requested, current=row.date
            ),
        }
    taken = sum(1 for item in rows if str(item.date) == requested and item.id != row.id)
    return Redate(
        day=day,
        caveat=(
            _DOUBLE_BOOKED_CAVEAT.format(
                requested=requested,
                count=taken,
                plural="s" if taken != 1 else "",
                total=taken + 1,
            )
            if taken
            else ""
        ),
    )


def _replacement(
    rows: list[Any],
    row: Any,
    sport: str | None,
    intent: str | None,
    activity_key: str | None,
    minutes: float,
    new_date: str | None,
) -> ActivityDefinition | dict[str, Any]:
    """Resolve the replacement, refusing a test the plan already holds open."""
    activity = _resolve_activity(row, sport, intent, activity_key, minutes)
    if isinstance(activity, dict):
        return activity
    try:
        day = _Date.fromisoformat(new_date or str(row.date))
    except ValueError:
        return activity  # plan_redate refuses the date with its own reason
    others = [item for item in rows if item.id != row.id]
    return open_test_refusal(others, activity, day) or activity


def _resolve_activity(
    row: Any,
    sport: str | None,
    intent: str | None,
    activity_key: str | None,
    minutes: float,
) -> ActivityDefinition | dict[str, Any]:
    """Resolve the replacement activity, or return a refusal dict.

    Mirrors ``build_workout``'s selection interface: an explicit
    ``activity_key`` wins outright, otherwise a sport + intent word is
    resolved through the shared registry picker. The sport defaults to the
    session's own.

    Args:
        row: The session row being replaced (supplies the default sport).
        sport: Requested sport, or None for the session's own.
        intent: Requested workout flavor, or None.
        activity_key: Exact planner activity key, or None.
        minutes: The replacement's length, which the intent's pick builds to.

    Returns:
        The chosen activity, or an error dict for the caller to return.
    """
    if activity_key:
        try:
            return get_activity(ActivityKey(activity_key))
        except ValueError:
            return _error(
                f"Unknown activity_key '{activity_key}'.",
                options=build_catalog_enum(),
            )
    if not intent:
        return _error(
            "Name the replacement: either an intent (e.g. 'threshold') or an "
            "exact activity_key.",
            options=VALID_INTENTS,
        )
    try:
        sport_enum = SportType(str(sport or row.sport).strip().lower())
    except ValueError:
        return _error(
            f"Unknown sport '{sport}'.",
            options=sorted(s.value for s in SportType),
        )
    intent_lower = intent.strip().lower()
    if intent_lower not in VALID_INTENTS:
        return _error(f"Unknown intent '{intent}'.", options=VALID_INTENTS)
    picked = pick_for_intent(sport_enum, intent_lower, minutes)
    if picked is None:
        return _error(
            f"No '{intent_lower}' activities for sport '{sport_enum.value}'.",
            options=sorted(
                s.value for s in SportType if pick_for_intent(s, intent_lower)
            ),
        )
    return picked


def _materialize(
    row: Any,
    plan: Any,
    activity: ActivityDefinition,
    minutes: float,
    resolver: Any,
    redate: Redate | None,
) -> SerializedSessionPayload:
    """Build the replacement session payload on the row's own slot.

    Args:
        row: The session row being replaced (identity fields).
        plan: The active plan row (recovery-week flag and goal lookup).
        activity: The replacement activity definition.
        minutes: Duration to size the replacement to.
        resolver: Athlete threshold resolver, or None.
        redate: The day the session moves to, or None to keep its own. The
            move stays inside the session's week, so ``week_index`` and
            ``phase_type`` are unchanged by it.

    Returns:
        The materialized replacement payload.
    """
    entry = next(
        (
            item
            for item in (read_solve_payload(plan).get("sessions") or [])
            if isinstance(item, dict) and item.get("slot_id") == str(row.slot_id)
        ),
        {},
    )
    target_tss = assignment_target_tss(activity, minutes / 60.0, resolver)
    workout = activity.build(target_tss, resolver=resolver)
    duration_min = activity.duration_minutes_exact(target_tss, resolver=resolver)
    replaced = _title(row)
    return serialize_planned_session(
        slot_id=str(row.slot_id),
        session_date=redate.day if redate else date.fromisoformat(str(row.date)),
        week_index=int(row.week_index),
        phase_type=str(row.phase_type),
        sport=activity.sport.value,
        activity_key=activity.key.value,
        family=activity.family.value,
        is_recovery_week=bool(entry.get("is_recovery_week")),
        coaching_intent=(
            f"{activity_brief(activity.key, activity.family)} Swapped in on "
            f"request for the planned {replaced}."
        ),
        hardness=activity.hardness,
        workout=workout,
        workout_name=(OPTIONAL_TITLE_PREFIX if getattr(row, "optional", False) else "")
        + activity_display_name(activity.key),
        target_tss=target_tss,
        achieved_tss=activity.achieved_tss(target_tss, resolver=resolver),
        duration_minutes=duration_min,
        # The swap redefines the session, so its hours-space base becomes the
        # duration the athlete asked for — a later adjust_current_week scales
        # from this, not from the retired prescription.
        duration_target_minutes=minutes,
        tid_fractions=activity.tid_fractions(target_tss, resolver=resolver),
        coaching_notes=session_coaching_notes(
            activity,
            workout,
            duration_min=duration_min,
            resolver=resolver,
            goal=GoalType(str(plan.goal_type)),
        ),
        # An ad-hoc replacement has no menu rationale behind it.
        why_details=(),
        optional=getattr(row, "optional", False),
    )


def plan_session_swap(
    *,
    repos: PlannerRepos,
    session_id: int,
    sport: str | None,
    intent: str | None,
    activity_key: str | None,
    duration_minutes: float | None,
    allow_protected: bool,
    today: date,
    new_date: str | None = None,
) -> SessionSwap | dict[str, Any]:
    """Compute the swap and run every guard. Pure: performs no writes.

    The single function both the preview and the apply run, so the numbers
    the athlete confirms are the numbers that get written.

    Args:
        repos: Repository container.
        session_id: The ``plan_sessions`` row to replace.
        sport: Replacement sport, or None for the session's own.
        intent: Replacement workout flavor, or None.
        activity_key: Exact planner activity key, or None.
        duration_minutes: Replacement duration, or None for the session's own.
        allow_protected: Whether a taper/race week may be swapped anyway.
        today: Reference date — a session before it cannot be swapped.
        new_date: ISO date to move the session to, or None to keep its own.

    Returns:
        The computed swap, or a refusal dict for the caller to return.
    """
    active = repos.plan.get_active()
    if active is None:
        return _error(NO_ACTIVE_PLAN_MESSAGE)
    rows = list(repos.plan.get_sessions(active.id))
    row = next((item for item in rows if item.id == session_id), None)
    if row is None:
        return _error(f"Session {session_id} is not part of the active plan.")
    if row.matched_activity_id is not None or str(row.status) == "completed":
        return _error(
            f"Session {session_id} is already done — a delivered workout is "
            "not rewritten. If the linked activity is the wrong match, call "
            'set_session_status(session_id, "unlinked") first; if the athlete '
            "simply did not do it, use skip_planned_session(session_id)."
        )
    if str(row.date) < today.isoformat():
        return _error(
            f"Session {session_id} was on {row.date}, which has passed — the "
            "plan is not rewritten backwards. Mark it skipped instead."
        )

    week = _week_bounds(date.fromisoformat(str(row.date)))
    guards = _protection_labels(active, row, week)
    if guards and not allow_protected:
        return _error(
            f"Session {session_id} sits in a {' and a '.join(guards)}, which "
            "the plan protects. Say so, and only call again with "
            "allow_protected=true if the athlete still wants it.",
            guards=guards,
        )

    minutes = float(
        duration_minutes
        if duration_minutes is not None
        else (row.duration_minutes or row.duration_target_minutes or 0.0)
    )
    if minutes <= 0:
        return _error(
            "duration_minutes must be greater than 0 (the session on record "
            "has no duration to fall back on)."
        )

    ensure_registry_loaded()
    activity = _replacement(rows, row, sport, intent, activity_key, minutes, new_date)
    if isinstance(activity, dict):
        return activity

    resolver = get_planner_load_context(repos)
    if resolver is None and _plan_used_thresholds(active):
        # activity.build bakes absolute pace/watt/bpm targets into the steps
        # from the resolver, so building without one would push a workout
        # stripped of this athlete's personalized targets to their device.
        return _error(_RESOLVER_REFUSAL)

    redate = plan_redate(active, rows, row, new_date, week=week, today=today)
    if isinstance(redate, dict):
        return redate

    monday, sunday = week
    week_rows = [
        item
        for item in rows
        if monday.isoformat() <= str(item.date) <= sunday.isoformat()
    ]
    return SessionSwap(
        plan=active,
        row=row,
        payload=_materialize(row, active, activity, minutes, resolver, redate),
        activity=activity,
        requested_minutes=minutes,
        week=week,
        week_rows=week_rows,
        guards=guards,
        redate=redate,
    )


def _week_delta(swap: SessionSwap) -> dict[str, Any]:
    """The week's planned load and hours, before and after the swap."""
    # An optional session is outside the plan's load (R-SESS-037), and a
    # swap keeps it optional.
    booked = [item for item in swap.week_rows if not getattr(item, "optional", False)]
    before_tss = sum(float(item.target_tss or 0.0) for item in booked)
    before_minutes = sum(float(item.duration_minutes or 0.0) for item in booked)
    after_tss = before_tss
    after_minutes = before_minutes
    if not getattr(swap.row, "optional", False):
        after_tss += swap.payload["target_tss"] - float(swap.row.target_tss or 0.0)
        after_minutes += swap.payload["duration_minutes"] - float(
            swap.row.duration_minutes or 0.0
        )
    monday, sunday = swap.week
    return {
        "from": monday.isoformat(),
        "through": sunday.isoformat(),
        "before_tss": round(before_tss, 1),
        "after_tss": round(after_tss, 1),
        "tss_delta_percent": _percent(before_tss, after_tss),
        "before_minutes": round(before_minutes, 1),
        "after_minutes": round(after_minutes, 1),
        "minutes_delta_percent": _percent(before_minutes, after_minutes),
    }


def _percent(before: float, after: float) -> float:
    """Signed percent change from *before* to *after* (0.0 from nothing)."""
    return round((after - before) / before * 100, 1) if before else 0.0


def _apply(
    *, repos: PlannerRepos, swap: SessionSwap, client: Any | None
) -> dict[str, Any]:
    """Persist the swap, then mirror it onto intervals.icu.

    The intended event is stored *inside* the transaction, before the
    network call, exactly as a week adjustment does: a failed push then
    leaves the stored payload carrying the new DSL while the remote still
    holds the old one, so the session reads ``edited`` and the plan header
    offers "Sync now" until it converges.

    A session that was marked ``skipped`` or ``unlinked`` is put back to
    ``pending``: both statuses are manual overrides that keep the row out of
    live activity matching and out of the missed count, which is wrong for a
    row that now holds a workout the athlete just agreed to do.

    Args:
        repos: Repository container.
        swap: The computed swap.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The push status block (never raises; the local write stands).
    """
    active = swap.plan
    payload = swap.payload
    athlete_state = read_athlete_state(active)
    events = (
        build_plan_events(active.id, [payload], [], athlete_state)
        if client is not None
        else []
    )
    row_values = build_commit_session_rows([payload])[0]

    with repos.session.transaction():
        repos.plan.update_session_swap(
            swap.row.id,
            active.id,
            sport=row_values.sport,
            activity_key=row_values.activity_key,
            family=row_values.family,
            target_tss=row_values.target_tss,
            achieved_tss=row_values.achieved_tss,
            duration_minutes=row_values.duration_minutes,
            duration_target_minutes=row_values.duration_target_minutes,
            workout_json=row_values.workout_json,
            tid_fractions_json=row_values.tid_fractions_json,
        )
        if swap.redate is not None:
            # Same week, so the week index and phase the row already carries
            # still hold; only the day moves.
            repos.plan.update_session_date(
                swap.row.id,
                active.id,
                swap.redate.day.isoformat(),
                week_index=int(swap.row.week_index),
            )
        if str(swap.row.status) != "pending":
            repos.plan.update_session_status(swap.row.id, "pending", None)
        _mirror_adjustment_into_solve(repos, active, {str(swap.row.slot_id): payload})
        if client is not None:
            repos.plan.update_pushed_events(
                active.id, {str(swap.row.slot_id): json.dumps(events[0])}
            )
    logger.info("Swapped session %s of plan %s", swap.row.id, active.id)
    return push_plan_events(
        active.id, [payload], [], athlete_state, repos=repos, client=client
    )


def _beyond_this_week(day: str) -> bool:
    """Whether ``day`` sits past this week, inside a replan's reach."""
    today = clock.today()
    next_monday = today + timedelta(days=7 - today.weekday())
    return day >= next_monday.isoformat()


def _caveats(
    swap: SessionSwap, week: dict[str, Any], push: dict[str, Any] | None
) -> list[str]:
    """Build the caveat list the coach must relay verbatim.

    Args:
        swap: The computed swap.
        week: The week delta block.
        push: The push status block, or None when nothing was pushed.

    Returns:
        The caveat strings, most specific first.
    """
    caveats = [_SCOPE_CAVEAT]
    if _beyond_this_week(str(swap.payload["date"])):
        caveats.append(_REPLAN_CAVEAT)
    activity_caveat = capability_caveat(
        swap.activity,
        recovery_week=swap.payload["is_recovery_week"],
        taper_week=swap.payload["phase_type"] == PhaseType.TAPER.value,
    )
    if activity_caveat is not None:
        caveats.insert(0, activity_caveat)
    if swap.guards:
        caveats.insert(0, _OVERRIDE_CAVEAT.format(reasons=", ".join(swap.guards)))
    if swap.redate is not None and swap.redate.caveat:
        caveats.append(swap.redate.caveat)
    if str(swap.row.status) != "pending":
        caveats.append(_STATUS_RESET_CAVEAT.format(status=swap.row.status))
    if str(swap.payload["sport"]) != str(swap.row.sport):
        caveats.append(
            f"This changes the session's sport: {swap.row.sport} -> "
            f"{swap.payload['sport']}. The week's sport balance shifts with it."
        )
    if (
        abs(week["tss_delta_percent"]) > WEEK_DELTA_WARN_PERCENT
        or abs(week["minutes_delta_percent"]) > WEEK_DELTA_WARN_PERCENT
    ):
        caveats.append(
            f"This moves the week by {week['tss_delta_percent']}% of planned "
            f"load and {week['minutes_delta_percent']}% of planned hours."
        )
    if push is not None and push["status"] != "ok":
        caveats.append(str(push["message"]))
        caveats.append(_PUSH_CAVEAT)
    return caveats


def _response(
    *,
    status: str,
    swap: SessionSwap,
    confirm: bool,
    message: str,
    push: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the single wire shape the preview and the apply both answer with.

    Args:
        status: ``preview`` or ``ok``.
        swap: The computed swap.
        confirm: Whether this call was allowed to write.
        message: The server-authored headline sentence.
        push: The push status block, or None when nothing was pushed.

    Returns:
        The tool payload.
    """
    week = _week_delta(swap)
    payload = swap.payload
    return {
        "status": status,
        "confirm_required": not confirm,
        "session_id": int(swap.row.id),
        "date": payload["date"],
        "current": {
            "date": str(swap.row.date),
            "activity_key": str(swap.row.activity_key),
            "family": str(swap.row.family),
            "sport": str(swap.row.sport),
            "phase": str(swap.row.phase_type),
            "duration_minutes": swap.row.duration_minutes,
            "target_tss": round(float(swap.row.target_tss or 0.0), 1),
            "steps_summary": _steps_line(_stored_steps(swap.row)),
        },
        "replacement": {
            "activity_key": payload["activity_key"],
            "workout_name": payload["workout"]["name"],
            "sport": payload["sport"],
            "family": payload["family"],
            "requested_duration_minutes": round(swap.requested_minutes, 1),
            "duration_minutes": payload["duration_minutes"],
            "target_tss": payload["target_tss"],
            "achieved_tss": payload["achieved_tss"],
            "coaching_intent": payload["coaching_intent"],
            "coaching_notes": payload["coaching_notes"],
            "steps": payload["workout"]["steps"],
        },
        "week": week,
        "guards": swap.guards,
        "push": push,
        "caveats": _caveats(swap, week, push),
        "message": message,
    }


def swap_planned_session(
    *,
    repos: PlannerRepos,
    session_id: int,
    sport: str | None = None,
    intent: str | None = None,
    activity_key: str | None = None,
    duration_minutes: float | None = None,
    date: str | None = None,
    confirm: bool = False,
    allow_protected: bool = False,
    client: Any | None = None,
    maybe_get_coach: Callable[[], Any] | None = None,
    today: _Date | None = None,
) -> dict[str, Any]:
    """Preview or apply the replacement of one planned session's workout.

    Args:
        repos: Repository container.
        session_id: The ``plan_sessions`` row to replace.
        sport: Replacement sport, or None for the session's own.
        intent: Replacement workout flavor, or None.
        activity_key: Exact planner activity key, or None.
        duration_minutes: Replacement duration, or None for the session's own.
        date: ISO date to move the session to as part of the same swap, or
            None to keep its own day. Must stay inside the session's week.
        confirm: Must be True to write anything.
        allow_protected: Whether a taper/race week may be swapped anyway.
        client: intervals.icu client, or ``None`` when unconfigured.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        today: Reference date (defaults to the app clock).

    Returns:
        The preview, the applied outcome, or a refusal.
    """
    swap = plan_session_swap(
        repos=repos,
        session_id=session_id,
        sport=sport,
        intent=intent,
        activity_key=activity_key,
        duration_minutes=duration_minutes,
        allow_protected=allow_protected,
        today=today or clock.today(),
        new_date=date,
    )
    if isinstance(swap, dict):
        return swap
    if not confirm:
        return _response(
            status="preview",
            swap=swap,
            confirm=confirm,
            message=_PREVIEW_MESSAGE,
            push=None,
        )

    push = _apply(repos=repos, swap=swap, client=client)
    coach = maybe_get_coach() if maybe_get_coach else None
    if coach is not None:
        coach.runtime.active_plan_summary = load_plan_context_summary(
            repos, SOLVER_VERSION
        )
    return _response(
        status="ok",
        swap=swap,
        confirm=confirm,
        message=(
            f"{swap.payload['workout']['name']} replaced the planned "
            f"{swap.row.activity_key} on {swap.payload['date']}."
            + (f" (moved from {swap.row.date}.)" if swap.redate else "")
        ),
        push=push,
    )
