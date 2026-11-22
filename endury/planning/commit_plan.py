"""Remaining planner command helpers for read-only and debug flows.

Also the home of the intervals.icu calendar push: committing, replanning
and deleting a plan all mirror the plan onto the athlete's intervals.icu
calendar, and all three treat the remote side as best-effort — the local
plan is the source of truth and a failed push is reported, never rolled
back.

It also builds the coach's active-plan text summary
(:func:`build_plan_summary_text`) — what ``turn.py`` injects as plan
context, built once on commit and rebuilt from the persisted plan on boot.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any, cast

from endury.common import clock
from endury.intervals.push import (
    event_start,
    plan_to_events,
    race_external_id,
    session_external_id,
    stale_external_ids,
)
from endury.intervals.workout_dsl import SportThresholds
from endury.planner.foundations.types import (
    GoalType,
    RaceGoal,
    SportType,
    is_maintenance_goal,
)
from endury.planning.commit_note import (
    record_commit_in_memory,
    record_delete_in_memory,
)
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.race_calendar import race_calendar_id
from endury.planning.sync_status import (
    active_pending_deletes,
    pending_deletes,
    record_pending_deletes,
    repushable_sessions,
)
from endury.planning.wire.requests import (
    GenerateSuccess,
    ScheduleRequest,
)
from endury.planning.wire.types import SerializedSessionPayload

logger = logging.getLogger(__name__)

#: Push status reported when the instance has no intervals.icu API key.
PUSH_SKIPPED_MESSAGE = "skipped: no intervals.icu API key"


def build_solve_result_payload(response: GenerateSuccess) -> dict[str, Any]:
    """Build the persisted solve payload from a successful generation response."""
    return {
        "sessions": response.sessions,
        "weekly_summary": response.weekly_summary,
        "phase_calendar": response.phase_calendar,
        "coach_brief": response.coach_brief,
        "projections": response.projections,
        "macro_plan": response.macro_plan,
    }


def build_plan_summary_text(
    plan_name: str,
    coach_brief: str,
    weekly_summary: Iterable[Mapping[str, Any]],
) -> str:
    """Build the coach's active-plan text summary from plan components.

    Args:
        plan_name: The committed plan's display name.
        coach_brief: The plan's coaching brief (may be empty).
        weekly_summary: Per-week summary dicts (``week_index``, ``phase``,
            ``total_tss``, ``session_count``).

    Returns:
        The multi-line summary text ``turn.py`` injects as plan context.
    """
    lines = [f"Plan: {plan_name}"]
    if coach_brief:
        lines.append(coach_brief)

    for week in weekly_summary:
        # 0-based week_index renders 1-based, matching the plan UI's "Wk N".
        raw_index = week.get("week_index")
        week_label = f"Week {int(raw_index) + 1}" if raw_index is not None else "Week ?"
        phase = week.get("phase", "")
        tss = week.get("total_tss", 0)
        sessions_count = week.get("session_count", 0)
        lines.append(
            f"- {week_label} ({phase}): {tss:.0f} load, {sessions_count} sessions"
        )
    return "\n".join(lines)


def load_plan_context_summary(repos: Any, solver_version: str) -> str | None:
    """Rebuild the coach's plan summary from the persisted active plan.

    Boot-time counterpart of :func:`update_coach_plan_context`: after a
    server restart the in-memory summary is gone, and the persisted
    ``solve_result_json`` carries everything needed to rebuild the same
    text the commit wrote.

    Args:
        repos: Repository container.
        solver_version: Current engine version; foreign plans are skipped.

    Returns:
        The summary text, or ``None`` when there is no readable active
        plan built by the current engine.
    """
    active = repos.plan.get_active()
    if active is None or active.solver_version != solver_version:
        return None
    try:
        solve = json.loads(active.solve_result_json or "{}")
    except json.JSONDecodeError:
        logger.debug("Unreadable solve_result_json on plan %s", active.id)
        return None
    return build_plan_summary_text(
        active.name,
        str(solve.get("coach_brief") or ""),
        list(solve.get("weekly_summary") or []),
    )


def update_coach_plan_context(
    response: GenerateSuccess,
    plan_name: str,
    *,
    maybe_get_coach: Callable[[], Any],
    note_verb: str = "committed",
) -> None:
    """Update the coach's plan context after a plan is persisted.

    The text summary is written onto the ``CoachRuntime`` (via the session's
    ``.runtime`` back reference), not the ``CoachSession`` itself —
    ``turn.py`` reads ``runtime.active_plan_summary`` when assembling the
    system context. The same moment appends the plan to athlete memory, the
    only record that outlives the process — as "committed", or as
    ``note_verb`` when a replan re-runs this hook.
    """
    coach = maybe_get_coach()
    if coach is None:
        return

    coach.runtime.active_plan_summary = build_plan_summary_text(
        plan_name, response.coach_brief, list(response.weekly_summary)
    )
    record_commit_in_memory(response, runtime=coach.runtime, verb=note_verb)


def update_coach_replan_context(
    response: GenerateSuccess,
    plan_name: str,
    *,
    maybe_get_coach: Callable[[], Any],
) -> None:
    """Refresh the coach's plan context after a replan.

    :func:`update_coach_plan_context` with the memory bullet worded
    "replanned" — a replan regenerates the remainder, it does not commit a
    new plan.

    Args:
        response: The combined (kept + regenerated) generate result.
        plan_name: The active plan's name.
        maybe_get_coach: Coach accessor for the plan-context refresh.
    """
    update_coach_plan_context(
        response, plan_name, maybe_get_coach=maybe_get_coach, note_verb="replanned"
    )


# -- intervals.icu calendar push --------------------------------------------


def _remote_result(
    status: str,
    *,
    errors: Sequence[str] = (),
    message: str | None = None,
    **counts: int,
) -> dict[str, Any]:
    """Build the wire status block for one remote calendar operation."""
    return {"status": status, "errors": list(errors), "message": message, **counts}


def _threshold(
    read: Mapping[str, Any], state: Mapping[str, Any], key: str
) -> Any | None:
    """Read one threshold, preferring the sport-settings value."""
    value = read.get(key)
    return value if value is not None else state.get(key)


def thresholds_by_sport(
    athlete_state: Mapping[str, Any] | None,
) -> dict[SportType, SportThresholds]:
    """Per-sport DSL thresholds from a plan's persisted athlete state.

    The ``thresholds`` block carries what intervals.icu's sport settings
    reported; the per-sport state block carries the same numbers the plan
    was actually sized on and backfills anything missing. Every sport is
    present in the result, so rendering a session never raises for a sport
    the athlete has no settings row for — a missing threshold degrades to
    zone-band percentages inside the DSL serializer.

    Args:
        athlete_state: The plan's ``athlete_state`` payload.

    Returns:
        Thresholds keyed by sport.
    """
    payload = athlete_state or {}
    read_block = payload.get("thresholds") or {}
    state_block = payload.get("sports") or {}
    result: dict[SportType, SportThresholds] = {}
    for sport in SportType:
        read = read_block.get(sport.value) or {}
        state = state_block.get(sport.value) or {}
        result[sport] = SportThresholds(
            ftp_watts=_threshold(read, state, "ftp_watts"),
            lthr_bpm=read.get("lthr_bpm"),
            threshold_pace_sec_per_km=_threshold(
                read, state, "threshold_pace_sec_per_km"
            ),
            css_sec_per_100m=_threshold(read, state, "css_sec_per_100m"),
        )
    return result


def goals_from_races(races: Sequence[Mapping[str, Any]]) -> list[RaceGoal]:
    """Rebuild planner race goals from a persisted request block."""
    return [
        RaceGoal(
            goal_type=GoalType(race["goal_type"]),
            race_date=date.fromisoformat(race["race_date"]),
            priority=race.get("priority", "A"),
        )
        for race in races
    ]


def build_plan_events(
    plan_id: int,
    sessions: Sequence[Mapping[str, Any]],
    races: Sequence[Mapping[str, Any]],
    athlete_state: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Render a plan's sessions and races as intervals.icu event payloads.

    Args:
        plan_id: The plan id (keys the external_id scheme).
        sessions: Materialized sessions in the planner wire shape.
        races: The plan's persisted race blocks.
        athlete_state: The plan's persisted athlete state (thresholds).

    Returns:
        Event dicts: one per session, then one per race.
    """
    return plan_to_events(
        plan_id,
        [cast("SerializedSessionPayload", session) for session in sessions],
        goals_from_races(races),
        thresholds_by_sport(athlete_state),
    )


def _store_pushed_events(
    repos: Any,
    plan_id: int,
    sessions: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> None:
    """Persist the event we pushed for each session (read-back baseline).

    ``build_plan_events`` emits the session events first and in order, so
    they pair positionally with ``sessions``.
    """
    with repos.session.transaction():
        repos.plan.update_pushed_events(
            plan_id,
            {
                str(session["slot_id"]): json.dumps(event)
                for session, event in zip(sessions, events, strict=False)
            },
        )


def superseded_plan_events(plan: Any) -> list[dict[str, Any]]:
    """external_id stubs for the calendar events a plan put on today or later.

    Committing a new plan archives the previous one locally, but its
    events live on at intervals.icu under their own ``coachie:{plan_id}:``
    ids — nothing about a *new* plan's push collides with them. Unless the
    push is told to clear them the athlete ends up with two plans stacked
    on the same days, one of them belonging to a plan the app no longer
    shows anywhere. The days before today are the athlete's (the ownership
    contract): the workouts done or missed there stay on their calendar.

    Ids are derived from the stored solve payload rather than from
    ``pushed_event_json``, so a plan whose push half-succeeded is still
    fully cleaned up.

    Args:
        plan: The outgoing plan row, or ``None`` when none was active.

    Returns:
        One ``{"external_id": ...}`` stub per event, empty for no plan.
    """
    if plan is None:
        return []
    today = clock.today()
    solve = read_solve_payload(plan)
    ids = [
        session_external_id(plan.id, str(session["slot_id"]))
        for session in solve.get("sessions") or []
        if session.get("slot_id") and str(session.get("date")) >= today.isoformat()
    ]
    ids += [
        race_external_id(plan.id, goal)
        for goal in goals_from_races(plan_races(solve))
        if goal.race_date >= today
    ]
    ids += pending_deletes(plan)
    return [{"external_id": external_id} for external_id in ids]


def push_plan_events(
    plan_id: int,
    sessions: Sequence[Mapping[str, Any]],
    races: Sequence[Mapping[str, Any]],
    athlete_state: Mapping[str, Any] | None,
    *,
    repos: Any,
    client: Any | None,
    previous_events: Sequence[Mapping[str, Any]] = (),
    supersedes: Any = None,
) -> dict[str, Any]:
    """Mirror a plan onto the athlete's intervals.icu calendar.

    Bulk-upserts the current events **first**, then deletes the events of
    sessions the plan no longer has: a failure mid-push must never leave
    the athlete's calendar emptier than it was. Never raises: the local
    plan is already persisted, so a remote failure is reported in the
    returned status block instead. Replaced workouts an earlier push failed
    to delete are retried every time, and a delete that fails again is
    recorded on the plan (``leftover`` counts them).

    Args:
        plan_id: The plan being pushed.
        sessions: Materialized sessions to push.
        races: The plan's persisted race blocks.
        athlete_state: The plan's persisted athlete state (thresholds).
        repos: Repository container (stores the pushed event per session).
        client: intervals.icu client, or ``None`` when unconfigured.
        previous_events: Events pushed by an earlier revision; any whose
            external_id is gone from the new set is deleted afterwards.
        supersedes: The plan this push replaces (a fresh commit archives
            it), whose events are cleared the same way.

    Returns:
        ``{"status": "ok" | "error" | "skipped", "pushed": int,
        "errors": [...], "message": str | None}``, plus ``leftover`` (the
        replaced workouts still on the calendar) once the upsert landed.
    """
    if client is None:
        return _remote_result("skipped", message=PUSH_SKIPPED_MESSAGE, pushed=0)

    try:
        events = build_plan_events(plan_id, sessions, races, athlete_state)
        outgoing = [
            *previous_events,
            *superseded_plan_events(supersedes),
            *({"external_id": i} for i in active_pending_deletes(repos, plan_id)),
        ]
        stale = stale_external_ids([dict(event) for event in outgoing], events)
        client.bulk_upsert_events(events)
    except Exception as exc:  # remote/rendering failure must not fail the commit
        logger.warning("intervals.icu push failed for plan %s: %s", plan_id, exc)
        return _remote_result(
            "error",
            errors=[str(exc)],
            message=(
                "The plan was saved, but pushing it to intervals.icu failed — "
                "your intervals.icu calendar is unchanged."
            ),
            pushed=0,
        )

    _store_pushed_events(repos, plan_id, sessions, events)
    logger.info("Pushed %d events for plan %s to intervals.icu", len(events), plan_id)
    if stale:
        try:
            client.bulk_delete_events(stale)
        except Exception as exc:  # the new events are already live
            logger.warning("Stale-event delete failed for plan %s: %s", plan_id, exc)
            record_pending_deletes(repos, plan_id, stale)
            return _remote_result(
                "ok",
                errors=[str(exc)],
                message=(
                    "The plan was pushed, but removing the replaced workouts "
                    "from intervals.icu failed; the next sync retries it."
                ),
                pushed=len(events),
                leftover=len(stale),
            )
    _clear_doubled_races(repos, client, events)
    record_pending_deletes(repos, plan_id, [])
    return _remote_result("ok", pushed=len(events), leftover=0)


def _clear_doubled_races(
    repos: Any, client: Any, events: Sequence[Mapping[str, Any]]
) -> None:
    """Take off the race-table events a plan race event now stands for.

    A race the coach put on the calendar (``race_calendar``) gets a plan
    race event of its own once a plan is built around it; the first one
    comes off so the day holds one race, not two. Best-effort and outside
    the replaced-workout bookkeeping: an id with no event is ignored by the
    API, and one missed here goes on the next push.
    """
    race_days = {
        str(event["start_date_local"])[:10]
        for event in events
        if str(event.get("category", "")).startswith("RACE_")
    }
    try:
        doubled = [
            race_calendar_id(race.id)
            for race in repos.races.list(include_past_days=0)
            if race.race_date in race_days
        ]
        if doubled:
            client.bulk_delete_events(doubled)
    except Exception as exc:
        logger.warning("Could not clear doubled race events: %s", exc)


def read_solve_payload(plan: Any) -> dict[str, Any]:
    """Return a plan's persisted solve payload, or ``{}`` when unreadable."""
    try:
        payload = json.loads(plan.solve_result_json or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.debug("Unreadable solve_result_json on plan %s", plan.id)
        return {}
    return payload if isinstance(payload, dict) else {}


def read_athlete_state(plan: Any) -> dict[str, Any]:
    """Return a plan's persisted athlete state, or ``{}`` when unreadable."""
    try:
        payload = json.loads(plan.athlete_state_json or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.debug("Unreadable athlete_state_json on plan %s", plan.id)
        return {}
    return payload if isinstance(payload, dict) else {}


def plan_races(solve: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the race blocks of a persisted solve payload."""
    request_block = solve.get("request")
    if not isinstance(request_block, dict):
        return []
    races = request_block.get("races")
    return list(races) if isinstance(races, list) else []


def push_active_plan(
    *, repos: Any, client: Any | None, today: date | None = None
) -> dict[str, Any]:
    """Re-push the active plan's calendar events (retry endpoint).

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).

    Returns:
        ``{"status": "ok", "plan_id": id, "push": {...}}``, or an error dict
        when there is no active plan.
    """
    active = repos.plan.get_active()
    if active is None:
        return {"status": "error", "message": "No active plan to push."}
    solve = read_solve_payload(active)
    sessions = repushable_sessions(
        list(solve.get("sessions") or []),
        list(repos.plan.get_sessions(active.id)),
        today or clock.today(),
    )
    push = push_plan_events(
        active.id,
        sessions,
        plan_races(solve),
        read_athlete_state(active),
        repos=repos,
        client=client,
    )
    return {"status": "ok", "plan_id": active.id, "push": push}


def _delete_plan_events(plan: Any, *, client: Any | None) -> dict[str, Any]:
    """Best-effort removal of a plan's events from today on from intervals.icu."""
    if client is None:
        return _remote_result("skipped", message=PUSH_SKIPPED_MESSAGE, deleted=0)
    try:
        external_ids = [stub["external_id"] for stub in superseded_plan_events(plan)]
        deleted = client.bulk_delete_events(external_ids) if external_ids else 0
    except Exception as exc:  # the plan is archived locally regardless
        logger.warning("intervals.icu delete failed for plan %s: %s", plan.id, exc)
        return _remote_result(
            "error",
            errors=[str(exc)],
            message=(
                "The plan was archived, but removing its intervals.icu "
                "calendar events failed."
            ),
            deleted=0,
        )
    return _remote_result("ok", deleted=int(deleted))


def delete_active_plan(
    *,
    get_repos: Callable[[], Any],
    maybe_get_coach: Callable[[], Any],
    client: Any | None = None,
) -> dict[str, Any]:
    """Archive the active plan and its macro plan; clear its events from today on.

    Args:
        get_repos: Repository container factory.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        client: intervals.icu client, or ``None`` when unconfigured. Remote
            deletion is best-effort — a failure still archives locally and
            is surfaced under ``remote_delete``.
    """
    repos = get_repos()
    active = repos.plan.get_active()
    if active is None:
        return {"status": "empty", "message": "No active plan to delete."}
    with repos.session.transaction():
        repos.plan.archive(active.id)
        logger.info("Archived plan %d", active.id)

        active_macro = repos.plan.get_macro_for_plan(active)
        if active_macro is not None:
            repos.plan.archive_macro(active_macro.id)
            logger.info("Archived macro plan %d", active_macro.id)

    coach = maybe_get_coach()
    if coach is not None:
        coach.runtime.active_plan_summary = None
        coach.runtime.macro_plan_summary = None
        record_delete_in_memory(active, runtime=coach.runtime)

    return {
        "status": "ok",
        "plan_id": active.id,
        "remote_delete": _delete_plan_events(active, client=client),
    }


def _is_race_day(active: Any, new_date: str) -> bool:
    """Whether ``new_date`` is the plan's race day.

    Maintenance blocks have no race — their ``goal_date`` only marks the end
    of the block — so their last day is a legal target.

    Args:
        active: The active plan row.
        new_date: The requested ISO date.
    """
    if new_date != active.goal_date:
        return False
    with contextlib.suppress(ValueError):
        if is_maintenance_goal(GoalType(str(active.goal_type))):
            return False
    return True


def _phase_for_week(solve: Mapping[str, Any], week_index: int) -> str | None:
    """Return the plan's phase for one week index, or ``None`` when unknown.

    ``phase_calendar`` is the plan's authoritative week→phase mapping: it is
    written from ``phase_plan.weeks[i].phase.value``, the same value the
    materializer stamps onto each session's ``phase_type``, so indexing it by
    week index yields exactly the phase a session belonging to that week
    would have been committed with.

    Args:
        solve: The plan's persisted solve payload.
        week_index: The plan week to look up.

    Returns:
        The phase string, or ``None`` when the payload carries no usable
        entry for that week.
    """
    calendar = solve.get("phase_calendar")
    if not isinstance(calendar, list) or not 0 <= week_index < len(calendar):
        return None
    phase = calendar[week_index]
    return phase if isinstance(phase, str) and phase else None


def _mirror_move_into_solve(
    repos: Any,
    active: Any,
    slot_id: str,
    new_date: date,
    *,
    new_week_index: int,
    new_phase_type: str | None,
    plan_start: date,
) -> None:
    """Write a session's new date into the plan's persisted solve payload.

    Keeps the render/coach/replan payload in step with the materialized
    ``plan_sessions`` rows; a replan otherwise partitions the two layers by
    different dates and duplicates or drops the moved session. The nested
    ``weekly_summary`` embeds per-week aggregates of the same sessions, so
    it is re-derived from the updated list (projections stay as committed —
    the next replan re-derives them). A payload that cannot be read is left
    untouched — the move itself still stands.

    Args:
        repos: Repository container.
        active: The active plan row.
        slot_id: Slot identifier of the moved session.
        new_date: The session's new date.
        new_week_index: The plan week the new date falls in.
        new_phase_type: The target week's phase; ``None`` keeps the entry's
            own phase (the target week's phase could not be determined).
        plan_start: The plan's first day (weekly-summary rebuild input).
    """
    from endury.planning.wire.weekly import build_weekly_summary

    try:
        solve = json.loads(active.solve_result_json or "{}")
        entry = next(s for s in solve["sessions"] if s["slot_id"] == slot_id)
    except (json.JSONDecodeError, TypeError, KeyError, StopIteration):
        logger.debug("Slot %s not mirrored into plan %s payload", slot_id, active.id)
        return

    entry["date"] = new_date.isoformat()
    entry["week_index"] = new_week_index
    if new_phase_type is not None:
        entry["phase_type"] = new_phase_type
    try:
        solve["weekly_summary"] = build_weekly_summary(
            solve["sessions"],
            list(solve.get("phase_calendar") or []),
            plan_start,
        )
    except Exception:
        logger.debug("Weekly summary not rebuilt for plan %s", active.id)
    repos.plan.update_solve_result_json(active.id, json.dumps(solve))


def _push_moved_session(
    repos: Any,
    plan_id: int,
    row: Any,
    new_date: str,
    *,
    client: Any | None,
) -> str | None:
    """Move a single session's event on intervals.icu.

    Single-session writes execute immediately (the ownership contract), so
    the moved workout must land on the athlete's device now rather than at
    the next replan. The stored event is re-dated rather than re-rendered:
    the workout itself did not change, only its day.

    Args:
        repos: Repository container.
        plan_id: The active plan's id.
        row: The moved session row (carries ``pushed_event_json``).
        new_date: The session's new ISO date.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        A warning message when the remote move failed, else ``None``. The
        local move stands either way.
    """
    raw = getattr(row, "pushed_event_json", None)
    # A skipped session has no event any more — moving it must not
    # resurrect the workout the athlete just dropped.
    if client is None or not raw or getattr(row, "status", "") == "skipped":
        return None
    try:
        event = json.loads(raw)
        event["start_date_local"] = event_start(new_date)
        client.bulk_upsert_events([event])
        repos.plan.update_pushed_events(plan_id, {str(row.slot_id): json.dumps(event)})
    except Exception as exc:
        logger.warning("Moving session %s on intervals.icu failed: %s", row.id, exc)
        return (
            "The session was moved here, but updating it on intervals.icu "
            f"failed: {exc}"
        )
    return None


def reschedule_session(
    session_id: int,
    req: ScheduleRequest,
    *,
    get_repos: Callable[[], Any],
    client: Any | None = None,
) -> dict[str, Any]:
    """Move a plan session to another date on the materialized layer.

    Validates that an active plan exists, the date parses, lies inside the
    plan window and is not the race day. A day that has passed is allowed:
    a session done on another day goes back to it, where the day's activity
    can pair with it. planner is
    never involved; the session row and the persisted solve payload are
    updated together, and the session's intervals.icu event is re-dated in
    the same call. A session that lands in a different phase takes that
    week's ``phase_type`` on both layers — consumers that gate on the week's
    character (the swap verb's taper refusal, phase labels) would otherwise
    keep reading the phase the session left.

    Args:
        session_id: The plan session to move.
        req: The requested new date.
        get_repos: Repository container factory.
        client: intervals.icu client, or ``None`` when unconfigured — a
            failed remote move is reported as a warning, never rolled back.
    """
    repos = get_repos()
    active = repos.plan.get_active()
    if active is None:
        return {"status": "error", "message": NO_ACTIVE_PLAN_MESSAGE}

    try:
        new_date = date.fromisoformat(req.date)
    except ValueError:
        return {"status": "error", "message": f"Invalid date: {req.date!r}"}

    if not (active.plan_start <= req.date <= active.goal_date):
        return {"status": "error", "message": "Date is outside the plan window."}
    if _is_race_day(active, req.date):
        return {"status": "error", "message": "Cannot move a session onto race day."}

    row = next(
        (r for r in repos.plan.get_sessions(active.id) if r.id == session_id), None
    )
    if row is None:
        return {"status": "error", "message": f"Session {session_id} not found."}
    if (
        getattr(row, "matched_activity_id", None) is not None
        or getattr(row, "status", "") == "completed"
    ):
        # A delivered workout happened on its real date; "moving" it
        # would show a completed session in the future.
        return {
            "status": "error",
            "message": (
                f"Session {session_id} is already completed — the logged "
                "workout keeps its real date. Unlink the activity first "
                "if the match is wrong."
            ),
        }
    result: dict[str, Any] = {
        "status": "ok",
        "session_id": session_id,
        "date": req.date,
    }
    warnings = move_session_row(repos, active, row, new_date, client=client)
    if warnings:
        result["warning"] = " ".join(warnings)
    return result


def move_session_row(
    repos: Any, active: Any, row: Any, new_date: date, *, client: Any | None
) -> list[str]:
    """Re-date one session on both layers and on intervals.icu.

    The caller has already decided the move is allowed: the date sits in
    the plan window and is not race day.

    Args:
        repos: Repository container.
        active: The active plan row.
        row: The session row to move.
        new_date: Its new date.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        Warnings to relay: the day is now double-booked, or the
        intervals.icu re-date failed.
    """
    day = new_date.isoformat()
    with repos.session.transaction():
        sessions = repos.plan.get_sessions(active.id)
        same_day = [r for r in sessions if str(r.date) == day and r.id != row.id]
        plan_start = date.fromisoformat(active.plan_start)
        week0_monday = plan_start - timedelta(days=plan_start.weekday())
        new_week_index = (new_date - week0_monday).days // 7
        new_phase_type = _phase_for_week(read_solve_payload(active), new_week_index)
        repos.plan.update_session_date(
            row.id,
            active.id,
            day,
            week_index=new_week_index,
            phase_type=new_phase_type,
        )
        _mirror_move_into_solve(
            repos,
            active,
            row.slot_id,
            new_date,
            new_week_index=new_week_index,
            new_phase_type=new_phase_type,
            plan_start=plan_start,
        )
    warnings = []
    if same_day:
        warnings.append(
            f"{day} already had {len(same_day)} session"
            f"{'s' if len(same_day) != 1 else ''} — the day now holds "
            f"{len(same_day) + 1}."
        )
    push_warning = _push_moved_session(repos, active.id, row, day, client=client)
    if push_warning is not None:
        warnings.append(push_warning)
    return warnings
