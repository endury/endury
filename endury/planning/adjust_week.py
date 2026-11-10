"""Shorten the rest of the current training week (today through Sunday).

Owns ``[today, this Sunday]`` — the one window no replan or push verb
already claims. Reduce-only: every QP guarantee is an upper bound
monotone in hours, so shrinking a session preserves them all and TSB can
only improve; increases would need them re-checked, so they are refused.

Sessions are re-materialized through the activity registry rather than
scaled in place, always from ``plan_sessions.duration_target_minutes``
(the planner's own hours-space intent, never rewritten here), so repeated
adjustments do not compound and ``reduce_percent=0`` restores the
committed prescription exactly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.common import clock
from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import (
    activity_brief,
    activity_display_name,
    assignment_target_tss,
)
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.activity.steps import session_coaching_notes
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    is_maintenance_goal,
)
from endury.planning.commit_plan import (
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
from endury.planning.replan_window import next_monday
from endury.planning.wire.sessions import (
    build_commit_session_rows,
    serialize_planned_session,
)
from endury.planning.wire.types import SerializedSessionPayload
from endury.planning.wire.weekly import build_weekly_summary
from endury.training.load import ResolverProtocol

logger = logging.getLogger(__name__)

#: Beyond this the honest answer is to skip sessions, not to shrink them.
MAX_REDUCE_PERCENT = 60

_PREVIEW_MESSAGE = (
    "Nothing has changed yet. The athlete applies these exact changes from "
    "their card; relay what it shows."
)
_NOT_STARTED_CAVEAT = (
    "This plan has not started yet, so a replan would rewrite from the plan "
    "start and discard this adjustment."
)
_PUSH_CAVEAT = (
    "Your intervals.icu calendar still shows the previous workouts until the "
    "push is retried."
)
_RESOLVER_REFUSAL = (
    "intervals.icu thresholds are unavailable right now and this plan's "
    "workouts were built from them — rebuilding the week without them would "
    "strip your pace and power targets. Nothing was changed."
)


@dataclass(frozen=True, slots=True)
class SessionChange:
    """One session this adjustment shortens, before and after.

    Attributes:
        session_id: The ``plan_sessions`` row id.
        slot_id: The session's slot identifier.
        date: The session's ISO date.
        weekday: Three-letter weekday abbreviation.
        sport: Sport value string.
        title: Athlete-facing workout title.
        is_hard: Whether the session is hard overall.
        from_minutes: Current materialized duration.
        to_minutes: Rebuilt duration.
        from_tss: Current planned TSS.
        to_tss: Rebuilt planned TSS.
        payload: The rebuilt session payload (row, solve entry and DSL).
    """

    session_id: int
    slot_id: str
    date: str
    weekday: str
    sport: str
    title: str
    is_hard: bool
    from_minutes: float
    to_minutes: float
    from_tss: float
    to_tss: float
    payload: SerializedSessionPayload


@dataclass(frozen=True, slots=True)
class SessionNote:
    """A session in the window that this adjustment did not rewrite.

    Attributes:
        session_id: The ``plan_sessions`` row id.
        date: The session's ISO date.
        title: Athlete-facing workout title.
        reason: Why it was left alone, in the athlete's language.
        minutes: Its current duration — an ``unchanged`` session still
            weighs in on both sides of the window's realized reduction.
    """

    session_id: int
    date: str
    title: str
    reason: str
    minutes: float


@dataclass(frozen=True, slots=True)
class WeekAdjustment:
    """The computed rescale of one week, before anything is written.

    Attributes:
        plan: The active plan row the adjustment applies to.
        window: Inclusive ``(from, through)`` dates the adjustment covers.
        changes: Sessions that would be shortened.
        unchanged: Sessions whose rebuild came out identical.
        untouched: Sessions this verb may not rewrite at all.
    """

    plan: Any
    window: tuple[date, date]
    changes: list[SessionChange]
    unchanged: list[SessionNote]
    untouched: list[SessionNote]


def _resolve_factor(
    reduce_percent: int | None,
) -> tuple[float, str] | dict[str, Any]:
    """Validate the requested reduction into a retained-hours factor.

    Args:
        reduce_percent: Whole percent to cut, or None.

    Returns:
        ``(factor, reason_phrase)``, or an error dict for the caller to
        return verbatim.
    """
    if reduce_percent is None:
        return {
            "status": "error",
            "message": (
                "Pass reduce_percent — a whole percent between 0 and "
                f"{MAX_REDUCE_PERCENT}; 0 restores the week."
            ),
        }
    if reduce_percent < 0:
        return {
            "status": "error",
            "message": (
                "reduce_percent must be between 0 and "
                f"{MAX_REDUCE_PERCENT} — this tool only shortens sessions."
            ),
        }
    if reduce_percent > MAX_REDUCE_PERCENT:
        return {
            "status": "error",
            "message": (
                f"I can only trim a week by up to {MAX_REDUCE_PERCENT}% — "
                "beyond that we should skip sessions instead."
            ),
        }
    return 1.0 - reduce_percent / 100.0, "at your request"


def _title(row: Any) -> str:
    """Athlete-facing workout title for a session row."""
    try:
        return activity_display_name(ActivityKey(str(row.activity_key)))
    except ValueError:
        return str(row.activity_key)


def _note(row: Any, title: str, reason: str) -> SessionNote:
    """Build the per-session note the reply contract makes the coach relay."""
    return SessionNote(
        session_id=int(row.id),
        date=str(row.date),
        title=title,
        reason=reason,
        minutes=float(row.duration_minutes or 0.0),
    )


def _plan_used_thresholds(plan: Any) -> bool:
    """Whether the plan's stored athlete state carries any threshold value."""
    thresholds = read_athlete_state(plan).get("thresholds") or {}
    return any(
        value for block in thresholds.values() for value in (block or {}).values()
    )


def _rebuild(
    row: Any,
    entry: dict[str, Any],
    activity: ActivityDefinition,
    factor: float,
    reason_phrase: str,
    resolver: ResolverProtocol | None,
    goal: GoalType,
) -> SerializedSessionPayload:
    """Re-materialize one session at ``factor`` of its planned duration.

    The loop body of ``materialize_sessions``, fed from the persisted row
    instead of a fresh pipeline assignment: identity fields come from the
    row (authoritative after a move), only ``is_recovery_week`` comes from
    the solve entry.

    Args:
        row: The ``plan_sessions`` row being rebuilt.
        entry: The row's twin in the plan's persisted solve payload.
        activity: The row's activity definition.
        factor: Fraction of the planned duration to retain.
        reason_phrase: Why the week was trimmed, for the intent sentence.
        resolver: Athlete threshold resolver.
        goal: The plan's goal type, for goal-gated coaching notes.

    Returns:
        The rebuilt session payload.
    """
    base = float(row.duration_target_minutes or row.duration_minutes)
    hours = base * factor / 60.0
    target_tss = assignment_target_tss(activity, hours, resolver)
    workout = activity.build(target_tss, resolver=resolver)
    duration_min = activity.duration_minutes_exact(target_tss, resolver=resolver)
    intent = activity_brief(activity.key, activity.family)
    if factor != 1.0:
        intent = (
            f"{intent} Trimmed to {round(factor * 100)}% of plan this week "
            f"{reason_phrase}."
        )
    return serialize_planned_session(
        slot_id=str(row.slot_id),
        session_date=date.fromisoformat(str(row.date)),
        week_index=int(row.week_index),
        phase_type=str(row.phase_type),
        sport=str(row.sport),
        activity_key=str(row.activity_key),
        family=str(row.family),
        is_recovery_week=bool(entry.get("is_recovery_week")),
        coaching_intent=intent,
        hardness=activity.hardness,
        workout=workout,
        workout_name=(OPTIONAL_TITLE_PREFIX if getattr(row, "optional", False) else "")
        + activity_display_name(activity.key),
        target_tss=target_tss,
        achieved_tss=activity.achieved_tss(target_tss, resolver=resolver),
        duration_minutes=duration_min,
        # Never rewritten: the immutable base a later adjustment scales from.
        duration_target_minutes=row.duration_target_minutes,
        tid_fractions=activity.tid_fractions(target_tss, resolver=resolver),
        coaching_notes=session_coaching_notes(
            activity, workout, duration_min=duration_min, resolver=resolver, goal=goal
        ),
        # A rescale does not change why the session exists.
        why_details=json.loads(row.workout_json).get("why_details") or [],
        optional=getattr(row, "optional", False),
    )


def _is_noop(row: Any, payload: SerializedSessionPayload) -> bool:
    """Whether the rebuild reproduced the session exactly as it stands."""
    stored = json.loads(row.workout_json)
    stored.pop("coaching_intent", None)
    stored.pop("coaching_notes", None)
    stored.pop("why_details", None)
    return bool(
        payload["duration_minutes"] == row.duration_minutes
        and payload["target_tss"] == round(float(row.target_tss), 1)
        and stored == payload["workout"]
    )


def _unchanged_reason(
    activity: ActivityDefinition, minutes: float, resolver: ResolverProtocol | None
) -> str:
    """Explain why a session came out of the rebuild unchanged."""
    if activity.tss_from_duration(minutes, resolver=resolver) is None:
        return "fixed test protocol — its length is the test"
    return "already at its shortest"


def _classify(
    rows: list[Any],
    *,
    entries: dict[str, dict[str, Any]],
    race_dates: set[str],
    resolver: ResolverProtocol | None,
    goal: GoalType,
    factor: float,
    reason_phrase: str,
) -> tuple[list[SessionChange], list[SessionNote], list[SessionNote]]:
    """Sort the window's rows, rebuilding every one this verb may rewrite.

    Returns:
        ``(changes, unchanged, untouched)``.
    """
    changes: list[SessionChange] = []
    unchanged: list[SessionNote] = []
    untouched: list[SessionNote] = []
    for row in rows:
        title = _title(row)
        status = str(getattr(row, "status", "") or "")
        if row.matched_activity_id is not None or status == "completed":
            untouched.append(_note(row, title, "already completed"))
            continue
        if status == "skipped":
            untouched.append(_note(row, title, "already marked skipped"))
            continue
        if str(row.date) in race_dates:
            untouched.append(_note(row, title, "race day"))
            continue
        entry = entries.get(str(row.slot_id))
        if entry is None:
            untouched.append(
                _note(
                    row,
                    title,
                    "not in the plan payload — regenerate the plan to adjust it",
                )
            )
            continue
        try:
            activity = get_activity(ActivityKey(str(row.activity_key)))
        except (ValueError, KeyError):
            untouched.append(
                _note(
                    row,
                    title,
                    "unknown workout type — this plan was built by a retired engine",
                )
            )
            continue
        base = row.duration_target_minutes or row.duration_minutes
        if not base:
            untouched.append(_note(row, title, "no duration on record"))
            continue

        payload = _rebuild(row, entry, activity, factor, reason_phrase, resolver, goal)
        if _is_noop(row, payload):
            unchanged.append(
                _note(row, title, _unchanged_reason(activity, base * factor, resolver))
            )
            continue
        changes.append(
            SessionChange(
                session_id=int(row.id),
                slot_id=str(row.slot_id),
                date=str(row.date),
                weekday=date.fromisoformat(str(row.date)).strftime("%a"),
                sport=str(row.sport),
                title=title,
                is_hard=bool(activity.hardness.is_hard_overall),
                from_minutes=float(row.duration_minutes or 0.0),
                to_minutes=float(payload["duration_minutes"]),
                from_tss=round(float(row.target_tss), 1),
                to_tss=float(payload["target_tss"]),
                payload=payload,
            )
        )
    return changes, unchanged, untouched


def plan_week_adjustment(
    *,
    repos: PlannerRepos,
    factor: float,
    reason_phrase: str,
    today: date,
    through: date | None = None,
) -> WeekAdjustment:
    """Compute this week's rescale. Pure: performs no writes.

    The single function both the preview and the apply run, so the numbers
    the athlete confirms are the numbers that get written.

    Args:
        repos: Repository container.
        factor: Fraction of each session's planned duration to retain.
        reason_phrase: Why the week was trimmed, for the intent sentence.
        today: Reference date — the first day of the window.
        through: Last day of the window. Defaults to the Sunday that closes
            ``today``'s week; ``pause_training`` passes its own end date to
            trim the athlete's first week back.

    Returns:
        The classified, rebuilt week.

    Raises:
        ValueError: No active plan, or intervals.icu thresholds are
            unavailable while the plan itself was built with thresholds.
    """
    active = repos.plan.get_active()
    if active is None:
        raise ValueError(NO_ACTIVE_PLAN_MESSAGE)

    solve = read_solve_payload(active)
    entries = {
        str(entry["slot_id"]): entry
        for entry in (solve.get("sessions") or [])
        if isinstance(entry, dict) and entry.get("slot_id")
    }
    resolver = get_planner_load_context(repos)
    if resolver is None and _plan_used_thresholds(active):
        # activity.build bakes absolute pace/watt/bpm targets into the steps
        # from the resolver, so rebuilding without one would silently swap
        # this week's personalized targets for bare zone numbers — and push
        # that to the athlete's device.
        raise ValueError(_RESOLVER_REFUSAL)

    end = through or next_monday(today) - timedelta(days=1)
    rows = repos.plan.get_sessions_by_date_range(
        active.id, today.isoformat(), end.isoformat()
    )
    race_dates = {
        str(race["race_date"])
        for race in plan_races(solve)
        if not is_maintenance_goal(GoalType(str(race["goal_type"])))
    }

    ensure_registry_loaded()
    changes, unchanged, untouched = _classify(
        rows,
        entries=entries,
        race_dates=race_dates,
        resolver=resolver,
        goal=GoalType(str(active.goal_type)),
        factor=factor,
        reason_phrase=reason_phrase,
    )
    return WeekAdjustment(
        plan=active,
        window=(today, end),
        changes=changes,
        unchanged=unchanged,
        untouched=untouched,
    )


def _mirror_adjustment_into_solve(
    repos: PlannerRepos,
    active: Any,
    payloads_by_slot: dict[str, SerializedSessionPayload],
) -> None:
    """Write the rebuilt sessions into the plan's persisted solve payload.

    The render, coach and re-push layers all read the solve payload, so
    leaving it at the committed prescription would show the athlete two
    different weeks. Projections and the macro plan stay as committed.

    Args:
        repos: Repository container.
        active: The active plan row.
        payloads_by_slot: Rebuilt payloads keyed by slot id.
    """
    solve = read_solve_payload(active)
    solve["sessions"] = [
        payloads_by_slot.get(str(entry.get("slot_id")), entry)
        for entry in (solve.get("sessions") or [])
    ]
    solve["weekly_summary"] = build_weekly_summary(
        solve["sessions"],
        list(solve.get("phase_calendar") or []),
        date.fromisoformat(str(active.plan_start)),
    )
    repos.plan.update_solve_result_json(active.id, json.dumps(solve))


def _apply(
    *, repos: PlannerRepos, adjustment: WeekAdjustment, client: Any | None
) -> dict[str, Any]:
    """Persist the rescale, then mirror it onto intervals.icu.

    The intended event is stored *inside* the transaction, before the
    network call: a failed push then leaves the stored payload carrying the
    new DSL while the remote still holds the old one, so the session reads
    ``edited`` and the plan header offers "Sync now" until it converges. The
    pre-store is skipped without a client so a keyless instance never gains
    a phantom payload that would later classify as ``removed``.

    Args:
        repos: Repository container.
        adjustment: The computed rescale.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The push status block (never raises; the local write stands).
    """
    active = adjustment.plan
    changes = adjustment.changes
    payloads = [change.payload for change in changes]
    athlete_state = read_athlete_state(active)
    events = (
        build_plan_events(active.id, payloads, [], athlete_state)
        if client is not None
        else []
    )

    with repos.session.transaction():
        for change in changes:
            row_values = build_commit_session_rows([change.payload])[0]
            repos.plan.update_session_prescription(
                change.session_id,
                active.id,
                target_tss=change.payload["target_tss"],
                achieved_tss=change.payload["achieved_tss"],
                duration_minutes=change.payload["duration_minutes"],
                workout_json=row_values.workout_json,
                tid_fractions_json=row_values.tid_fractions_json,
            )
        _mirror_adjustment_into_solve(
            repos, active, {change.slot_id: change.payload for change in changes}
        )
        if client is not None:
            repos.plan.update_pushed_events(
                active.id,
                {
                    change.slot_id: json.dumps(event)
                    for change, event in zip(changes, events, strict=False)
                },
            )
    logger.info("Adjusted %d sessions of plan %s", len(changes), active.id)
    return push_plan_events(
        active.id, payloads, [], athlete_state, repos=repos, client=client
    )


def _note_payload(note: SessionNote) -> dict[str, Any]:
    """Serialize one ``unchanged``/``untouched`` note for the wire."""
    return {
        "session_id": note.session_id,
        "date": note.date,
        "title": note.title,
        "reason": note.reason,
    }


def _totals(adjustment: WeekAdjustment) -> dict[str, Any]:
    """Sum the window's realized reduction into the ``totals`` block."""
    # `unchanged` sessions weigh in at their full length on both sides —
    # that is exactly what makes actual_percent the *realized* reduction.
    held = sum(note.minutes for note in adjustment.unchanged)
    before = sum(change.from_minutes for change in adjustment.changes) + held
    after = sum(change.to_minutes for change in adjustment.changes) + held
    return {
        "sessions": len(adjustment.changes) + len(adjustment.unchanged),
        "before_minutes": round(before, 1),
        "after_minutes": round(after, 1),
        "actual_percent": round((before - after) / before * 100, 1) if before else 0.0,
    }


def _response(
    *,
    status: str,
    adjustment: WeekAdjustment,
    factor: float,
    confirm: bool,
    message: str,
    push: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the single wire shape the preview and the apply both answer with.

    Args:
        status: ``preview``, ``ok`` or ``empty``.
        adjustment: The computed rescale.
        factor: Fraction of planned duration retained.
        confirm: Whether this call was allowed to write.
        message: The server-authored headline sentence.
        push: The push status block, or None when nothing was pushed.

    Returns:
        The tool payload.
    """
    start, through = adjustment.window
    caveats = [
        f"Only {start.isoformat()} through {through.isoformat()} changed. Next "
        "week is untouched — a replan regenerates from next Monday and leaves "
        "this week alone."
    ]
    if start < date.fromisoformat(str(adjustment.plan.plan_start)):
        caveats.append(_NOT_STARTED_CAVEAT)
    if push is not None and push["status"] != "ok":
        caveats.append(str(push["message"]))
        caveats.append(_PUSH_CAVEAT)

    return {
        "status": status,
        "confirm_required": not confirm,
        "window": {"from": start.isoformat(), "through": through.isoformat()},
        "reduce_percent": round((1.0 - factor) * 100),
        "totals": _totals(adjustment),
        "sessions": [
            {
                "session_id": change.session_id,
                "date": change.date,
                "weekday": change.weekday,
                "sport": change.sport,
                "title": change.title,
                "is_hard": change.is_hard,
                "before_minutes": change.from_minutes,
                "after_minutes": change.to_minutes,
                "before_tss": change.from_tss,
                "after_tss": change.to_tss,
            }
            for change in adjustment.changes
        ],
        "unchanged": [_note_payload(note) for note in adjustment.unchanged],
        "untouched": [_note_payload(note) for note in adjustment.untouched],
        "push": push,
        "caveats": caveats,
        "message": message,
    }


def adjust_current_week(
    *,
    repos: PlannerRepos,
    reduce_percent: int | None,
    confirm: bool,
    client: Any | None,
    maybe_get_coach: Callable[[], Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Preview or apply a rescale of this week's remaining sessions.

    Args:
        repos: Repository container.
        reduce_percent: Whole percent to cut (0 restores), or None.
        confirm: Must be True to write anything.
        client: intervals.icu client, or ``None`` when unconfigured.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        today: Reference date (defaults to the app clock).

    Returns:
        The preview, the applied outcome, an ``empty`` report, or an error.
    """
    resolved = _resolve_factor(reduce_percent)
    if isinstance(resolved, dict):
        return resolved
    factor, reason_phrase = resolved

    today = today or clock.today()
    try:
        adjustment = plan_week_adjustment(
            repos=repos, factor=factor, reason_phrase=reason_phrase, today=today
        )
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    start, through = adjustment.window
    if not adjustment.changes:
        return _response(
            status="empty",
            adjustment=adjustment,
            factor=factor,
            confirm=confirm,
            message=(
                f"Nothing between {start.isoformat()} and {through.isoformat()} "
                "can be shortened, so nothing changed. Use replan_active_plan "
                "for the weeks after this one."
            ),
            push=None,
        )
    if not confirm:
        return _response(
            status="preview",
            adjustment=adjustment,
            factor=factor,
            confirm=confirm,
            message=_PREVIEW_MESSAGE,
            push=None,
        )

    push = _apply(repos=repos, adjustment=adjustment, client=client)
    coach = maybe_get_coach() if maybe_get_coach else None
    if coach is not None:
        coach.runtime.active_plan_summary = load_plan_context_summary(
            repos, SOLVER_VERSION
        )
    return _response(
        status="ok",
        adjustment=adjustment,
        factor=factor,
        confirm=confirm,
        message=(
            f"Applied to {len(adjustment.changes)} session(s) from "
            f"{start.isoformat()} through {through.isoformat()}."
        ),
        push=push,
    )
