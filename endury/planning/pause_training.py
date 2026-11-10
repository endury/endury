"""Pause training through a date, and come back on a trimmed week.

Every pending session in the window is marked skipped, its intervals.icu
event removed, the window stored as an ``unavailable`` directive, the
rest of the plan regenerated from the day the athlete is back, and that
first week trimmed to the planner's post-recovery ceiling
(``RECOVERY_CEILING_FACTOR``). With no active plan this delegates whole
to :func:`~endury.planning.unschedule_workout.pause_without_plan`.

Owns ``[today, through]`` and hands ``[through + 1, race day]`` to the
replan. Always starts today: :func:`validate_from_date` refuses any other
``from_date`` before anything is written, with a curated redirect to
``set_plan_directives`` or ``set_session_status``. Everything remote is
best-effort — the local skips are the source of truth — and a pause also
clears the coach's own ad-hoc bookings from tomorrow through ``through``,
previewed identically to how it is applied.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from endury.common import clock
from endury.planner.macro.capacity import RECOVERY_CEILING_FACTOR
from endury.planning.adjust_week import (
    SessionNote,
    _note,
    _note_payload,
    _title,
    _totals,
    plan_week_adjustment,
)
from endury.planning.adjust_week import _apply as _apply_week_adjustment
from endury.planning.commit_plan import load_plan_context_summary, read_solve_payload
from endury.planning.generate_plan import SOLVER_VERSION
from endury.planning.load_planner_metrics import PlannerRepos
from endury.planning.plan_directives import (
    PlanDirectives,
    merge_unavailable_ranges,
    parse_directives,
)
from endury.planning.replan_plan import (
    _load_replan_inputs,
    replan_readback_diff,
    run_replan,
)
from endury.planning.replan_window import next_monday
from endury.planning.session_status import delete_session_events
from endury.planning.unschedule_workout import (
    _read_ad_hoc_workouts,
    pause_without_plan,
    remove_ad_hoc_workouts,
)

logger = logging.getLogger(__name__)

_PREVIEW_MESSAGE = (
    "Nothing has changed yet. The athlete applies this exact pause from its "
    "card; relay what it shows."
)
_MID_WEEK_CAVEAT = (
    "The replan starts inside the current week, so the rest of this week — "
    "including any trims or swaps already made to it — is regenerated too."
)
_SYNC_CAVEAT = (
    "Your intervals.icu calendar still shows the previous workouts until "
    "Sync now is used."
)
_REPLAN_FAILED = (
    "The sessions are skipped and removed from the calendar, but the replan "
    "failed: {message} Call replan_active_plan once that is fixed."
)
_TRIM_FAILED = (
    "The plan was replanned, but the first week back could not be trimmed: "
    "{message} It stands at full plan volume."
)
_EMPTY_BUT_BOOKED_PREVIEW_MESSAGE = (
    "Nothing plan-side is pending in this window, but you have coach-scheduled "
    "workout(s) booked in it — applying removes those; nothing will be replanned."
)
_EMPTY_BUT_BOOKED_APPLIED_MESSAGE = (
    "Nothing plan-side was pending, so nothing was replanned. Removed the "
    "coach-scheduled workout(s) booked in this window instead."
)
_EMPTY_PREVIEW_MESSAGE = (
    "Nothing between {start} and {end} is still pending, so nothing would "
    "change. For a replan alone use replan_active_plan."
)
_EMPTY_APPLIED_MESSAGE = (
    "Nothing between {start} and {end} was pending, so nothing was skipped "
    "or replanned — the window is recorded as unavailable, so nothing gets "
    "booked into it. For a replan alone use replan_active_plan."
)

#: The athlete comes back onto the planner's own post-recovery ceiling —
#: the same fraction the macro layer uses after a down week, so the ramp
#: back is one the plan would have built anyway.
FIRST_WEEK_REDUCE_PERCENT = round((1.0 - RECOVERY_CEILING_FACTOR) * 100)
_FIRST_WEEK_REASON = "in your first week back"

#: The skip write, minus the row id the caller fills in per session.
_SKIP_WRITE = {"matched_activity_id": None, "status": "skipped"}


@dataclass(frozen=True, slots=True)
class SkipItem:
    """One pending session the pause marks skipped and un-schedules.

    Attributes:
        session_id: The ``plan_sessions`` row id.
        slot_id: The session's slot identifier (addresses its event).
        date: The session's ISO date; ``weekday`` its abbreviation.
        sport: Sport value string; ``title`` the athlete-facing name.
        minutes: The session's current planned duration.
    """

    session_id: int
    slot_id: str
    date: str
    weekday: str
    sport: str
    title: str
    minutes: float


@dataclass(frozen=True, slots=True)
class PausePlan:
    """The computed pause, before anything is written.

    Attributes:
        plan: The active plan row the pause applies to.
        window: Inclusive ``(today, through)`` dates the athlete is out.
        resume_date: The first day back — where the replan cuts.
        to_skip: Sessions that would be skipped and un-scheduled.
        untouched: Sessions in the window this verb leaves alone.
        edited_workouts: Events edited on intervals.icu the replan overwrites.
    """

    plan: Any
    window: tuple[date, date]
    resume_date: date
    to_skip: list[SkipItem]
    untouched: list[SessionNote]
    edited_workouts: list[dict[str, Any]]


def _future_window_redirect(
    from_date: date, through_date: date, today: date
) -> dict[str, Any]:
    """Curate the redirect for a ``from_date`` strictly after ``today``.

    ``suggested_tool`` is picked deterministically from whether ``from_date``
    falls after this week's Sunday: a week or more out is
    ``set_plan_directives``'s window, a day still in the current week is
    ``skip_planned_session``'s.
    """
    this_sunday = next_monday(today) - timedelta(days=1)
    suggested_tool = (
        "set_plan_directives" if from_date > this_sunday else "skip_planned_session"
    )
    from_iso, through_iso = from_date.isoformat(), through_date.isoformat()
    return {
        "status": "refused",
        "error_code": "future_window",
        "message": (
            f"pause_training always starts today. For {from_iso}..{through_iso}: "
            f'use set_plan_directives(unavailable=[["{from_iso}", "{through_iso}"]]) '
            "when the window starts next week or later, or "
            "skip_planned_session(session_id) for days later in the "
            "current week."
        ),
        "suggested_tool": suggested_tool,
    }


def validate_from_date(
    from_date: str | None, *, through: date, today: date
) -> dict[str, Any] | None:
    """Validate ``pause_training``'s optional ``from_date`` against ``today``.

    Args:
        from_date: The raw arg, or ``None`` (today's behavior, unchanged).
        through: The already-parsed ``through_date``.
        today: Reference date.

    Returns:
        ``None`` when the pause may proceed starting today — ``from_date``
        omitted or equal to ``today`` — else the curated ``"refused"``/
        ``"error"`` dict to return verbatim. Nothing is written either way:
        the caller must return this before touching the plan or repos.
    """
    if from_date is None:
        return None
    try:
        start = date.fromisoformat(from_date)
    except ValueError:
        return {
            "status": "error",
            "message": (
                f"'{from_date}' is not a date — pass the first day the "
                "athlete is out as YYYY-MM-DD."
            ),
        }
    if start == today:
        return None
    if start < today:
        return {
            "status": "error",
            "message": (
                f"'{from_date}' is in the past — from_date is the first day "
                "the athlete is out, not a day already lived through."
            ),
        }
    if start > through:
        return {
            "status": "error",
            "message": (
                f"'{from_date}' is after through_date ({through.isoformat()}) "
                "— the window can't start after it ends."
            ),
        }
    return _future_window_redirect(start, through, today)


def preview_coach_bookings(
    *, client: Any | None, today: date, through: date
) -> dict[str, Any] | None:
    """List, but never delete, coach-scheduled ad-hoc workouts confirm would.

    Same window as :func:`clear_coach_bookings` — tomorrow through
    ``through``, today's own booking left alone — and the same ``None`` when
    the pause does not reach that far or there is no calendar to read.

    Returns:
        ``{"status": "preview", "workouts": [...]}`` when something is
        booked, ``{"status": "not_found", "workouts": []}`` when nothing is,
        ``{"status": "error", "message": ..., "workouts": []}`` on a failed
        read, or ``None``.
    """
    start = today + timedelta(days=1)
    if client is None or start > through:
        return None
    workouts, error = _read_ad_hoc_workouts(client, start, through)
    if error is not None:
        return {"status": "error", "message": error, "workouts": []}
    if not workouts:
        return {"status": "not_found", "workouts": []}
    return {"status": "preview", "workouts": workouts}


def clear_coach_bookings(
    *, client: Any | None, today: date, through: date
) -> dict[str, Any] | None:
    """Sweep coach-scheduled ad-hoc workouts from tomorrow through ``through``.

    Today's own bookings are left alone (the athlete may already be
    following one) — the window starts the day after, and there is nothing
    to sweep (``None``) when the pause does not reach that far, or when
    there is no calendar to sweep: an ad-hoc booking only ever exists on
    intervals.icu, so with no client there is nothing there to report on.
    """
    start = today + timedelta(days=1)
    if client is None or start > through:
        return None
    return remove_ad_hoc_workouts(client=client, day=start, through=through)


def ad_hoc_caveats(
    block: dict[str, Any] | None, *, plan_sessions_skipped: bool
) -> list[str]:
    """Name every ad-hoc workout the sweep removed, will remove, or its failure.

    One caveat function for both the preview and the applied outcome, so the
    athlete is told about a booking the same way whether it already came off
    the calendar or is only about to. ``plan_sessions_skipped`` controls the
    "also": a booking-only pause touches nothing plan-side, so the booking
    removal is not an addition to anything else.
    """
    if block is None or block["status"] == "not_found":
        return []
    if block["status"] == "error":
        return [str(block["message"])]
    names = ", ".join(f"{item['date']} {item['name']}" for item in block["workouts"])
    if block["status"] == "ok":
        verb = "Also removed" if plan_sessions_skipped else "Removed"
        return [
            f"{verb} {block['deleted']} coach-scheduled workout(s) you had "
            f"booked: {names}."
        ]
    count = len(block["workouts"])
    lead = "Applying will also remove" if plan_sessions_skipped else "Applying removes"
    return [f"{lead} {count} coach-scheduled workout(s) you have booked: {names}."]


def record_unavailable_window(
    repos: PlannerRepos, active: Any, start: date, end: date
) -> None:
    """Merge ``[start, end]`` into the plan's stored ``unavailable`` directive.

    Called on every CONFIRMED pause with an active plan — whether or not it
    replans — so its window is re-applied by every future replan the same
    way ``set_plan_directives`` own edits are. Coalesces onto whatever is
    already stored via :func:`~endury.planning.plan_directives
    .merge_unavailable_ranges`, so pausing over the same or a touching
    window twice never grows the stored list.

    Args:
        repos: Repository container.
        active: The active plan row.
        start: First unavailable day (inclusive).
        end: Last unavailable day (inclusive).
    """
    solve = read_solve_payload(active)
    request = dict(solve.get("request") or {})
    directives = dict(request.get("directives") or {})
    parsed = parse_directives(directives)
    existing = parsed.unavailable if isinstance(parsed, PlanDirectives) else ()
    merged = merge_unavailable_ranges([*existing, (start, end)])
    directives["unavailable"] = [[s.isoformat(), e.isoformat()] for s, e in merged]
    request["directives"] = directives
    solve["request"] = request
    repos.plan.update_solve_result_json(active.id, json.dumps(solve))


def empty_window_outcome(
    *,
    repos: PlannerRepos,
    active: Any,
    start: date,
    end: date,
    confirm: bool,
    ad_hoc: dict[str, Any] | None,
) -> tuple[str, str]:
    """Status/message for a pause window with nothing plan-side pending.

    Records the window as unavailable whenever confirmed, whatever the
    sweep's own outcome — booking-only, truly empty, or a failed sweep all
    mean the athlete confirmed being out for this window, so a later
    ad-hoc booking or replan must not schedule into it. An unconfirmed
    call (a preview) never writes.

    Args:
        repos: Repository container.
        active: The active plan row.
        start: First day of the window (inclusive).
        end: Last day of the window (inclusive).
        confirm: Whether this call is confirmed.
        ad_hoc: The coach-scheduled-bookings sweep/preview block, or None.

    Returns:
        ``(status, message)`` — status one of "empty", "error", "preview",
        "ok".
    """
    if confirm:
        record_unavailable_window(repos, active, start, end)
    if ad_hoc is None or ad_hoc["status"] == "not_found":
        template = _EMPTY_APPLIED_MESSAGE if confirm else _EMPTY_PREVIEW_MESSAGE
        return "empty", template.format(start=start.isoformat(), end=end.isoformat())
    if ad_hoc["status"] == "error":
        # The sweep is all this branch does: its failure is the outcome,
        # never "nothing is pending, so nothing changed".
        return "error", str(ad_hoc["message"])
    if not confirm:
        return "preview", _EMPTY_BUT_BOOKED_PREVIEW_MESSAGE
    return "ok", _EMPTY_BUT_BOOKED_APPLIED_MESSAGE


def trim_first_week(
    *, repos: PlannerRepos, resume: date, client: Any | None
) -> dict[str, Any]:
    """Cut the seven days from ``resume`` to the planner's recovery ceiling.

    Returns the ``first_week`` wire block; ``status`` is ``error`` when the
    adjust verb refused, and the replan stands regardless.
    """
    through = resume + timedelta(days=6)
    try:
        adjustment = plan_week_adjustment(
            repos=repos,
            factor=RECOVERY_CEILING_FACTOR,
            reason_phrase=_FIRST_WEEK_REASON,
            today=resume,
            through=through,
        )
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    changes = adjustment.changes
    push = (
        _apply_week_adjustment(repos=repos, adjustment=adjustment, client=client)
        if changes
        else None
    )
    return {
        "status": "ok" if changes else "empty",
        "window": {"from": resume.isoformat(), "through": through.isoformat()},
        "reduce_percent": FIRST_WEEK_REDUCE_PERCENT,
        "totals": _totals(adjustment),
        "sessions": [
            {
                "session_id": change.session_id,
                "date": change.date,
                "weekday": change.weekday,
                "title": change.title,
                "before_minutes": change.from_minutes,
                "after_minutes": change.to_minutes,
            }
            for change in changes
        ],
        "unchanged": [_note_payload(note) for note in adjustment.unchanged],
        "untouched": [_note_payload(note) for note in adjustment.untouched],
        "push": push,
        "message": (
            f"Trimmed {len(changes)} session(s) by {FIRST_WEEK_REDUCE_PERCENT}% "
            f"{_FIRST_WEEK_REASON}."
            if changes
            else f"Nothing {_FIRST_WEEK_REASON} could be shortened."
        ),
    }


def plan_pause(
    *, repos: PlannerRepos, client: Any | None, through: date, today: date
) -> PausePlan:
    """Compute the pause over ``[today, through]``. Pure: performs no writes.

    The single function both the preview and the apply run, so the sessions
    the athlete confirms are the sessions that get skipped.

    Raises:
        ValueError: No replannable active plan, the window has already
            passed, or it would swallow the plan's last day.
    """
    active, _ = _load_replan_inputs(repos)
    if through < today:
        raise ValueError(
            f"{through.isoformat()} has already passed, so there is nothing to "
            "pause. Pass the last day the athlete is out."
        )
    goal_date = date.fromisoformat(str(active.goal_date))
    if through >= goal_date:
        raise ValueError(
            f"A pause through {through.isoformat()} runs past the plan's last "
            f"day ({goal_date.isoformat()}) — there would be nothing left to "
            "come back to. Delete the plan or start a new one instead."
        )

    resume = through + timedelta(days=1)
    to_skip: list[SkipItem] = []
    untouched: list[SessionNote] = []
    for row in repos.plan.get_sessions_by_date_range(
        active.id, today.isoformat(), through.isoformat()
    ):
        title = _title(row)
        status = str(getattr(row, "status", "") or "")
        if row.matched_activity_id is not None or status == "completed":
            untouched.append(_note(row, title, "already completed"))
            continue
        if status == "skipped":
            untouched.append(_note(row, title, "already marked skipped"))
            continue
        to_skip.append(
            SkipItem(
                session_id=int(row.id),
                slot_id=str(row.slot_id),
                date=str(row.date),
                weekday=date.fromisoformat(str(row.date)).strftime("%a"),
                sport=str(row.sport),
                title=title,
                minutes=float(row.duration_minutes or 0.0),
            )
        )
    return PausePlan(
        plan=active,
        window=(today, through),
        resume_date=resume,
        to_skip=to_skip,
        untouched=untouched,
        # Skipped rows are all before the resume day, so the diff covers
        # exactly what the replan will overwrite and nothing else.
        edited_workouts=replan_readback_diff(
            repos=repos, client=client, today=today, resume_date=resume
        ),
    )


def _apply(
    *,
    repos: PlannerRepos,
    pause: PausePlan,
    client: Any | None,
    maybe_get_coach: Callable[[], Any] | None,
    today: date,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None
]:
    """Skip the window, clear its events, replan, trim the week, sweep extras.

    The skips are written first and never rolled back, so a later failure
    degrades the outcome instead of losing it. Returns ``(push, replan,
    first_week, ad_hoc)``; ``first_week`` is ``None`` when the replan
    failed, since there is then no week back to trim. ``ad_hoc`` runs
    regardless of the replan's outcome — it is about the athlete's own
    calendar, not the plan.
    """
    active = pause.plan
    with repos.session.transaction():
        repos.plan.bulk_update_session_matches(
            [{"session_id": item.session_id, **_SKIP_WRITE} for item in pause.to_skip]
        )
        record_unavailable_window(repos, active, *pause.window)
    push = delete_session_events(
        active.id, [item.slot_id for item in pause.to_skip], client=client
    )
    logger.info("Paused plan %s: skipped %d sessions", active.id, len(pause.to_skip))
    try:
        result = run_replan(
            repos=repos,
            progress=lambda _payload: None,
            maybe_get_coach=maybe_get_coach or (lambda: None),
            today=today,
            client=client,
            resume_date=pause.resume_date,
        )
    except ValueError as exc:
        # Curated, athlete-facing reasons. The skips already stand.
        ad_hoc = clear_coach_bookings(
            client=client, today=today, through=pause.window[1]
        )
        return push, {"status": "error", "message": str(exc)}, None, ad_hoc

    replan = {key: result[key] for key in ("status", "action", "reasoning", "push")}
    first_week = trim_first_week(repos=repos, resume=pause.resume_date, client=client)
    ad_hoc = clear_coach_bookings(client=client, today=today, through=pause.window[1])
    return push, replan, first_week, ad_hoc


def _leg_caveats(block: dict[str, Any], failure: str) -> list[str]:
    """Relay one applied leg: its own refusal, or a push that is not ``ok``."""
    if block["status"] == "error":
        return [failure.format(message=block["message"])]
    push = block["push"]
    if push is None or push["status"] == "ok":
        return []
    return [str(push["message"]), _SYNC_CAVEAT]


def _caveats(
    pause: PausePlan,
    push: dict[str, Any] | None,
    replan: dict[str, Any] | None,
    first_week: dict[str, Any] | None,
    ad_hoc: dict[str, Any] | None,
) -> list[str]:
    """Build the caveat list the coach must relay verbatim, scope first."""
    start, through = pause.window
    caveats: list[str] = []
    if pause.to_skip:
        caveats.append(
            f"This skips {len(pause.to_skip)} session(s) from "
            f"{start.isoformat()} through {through.isoformat()}, removes them "
            "from the intervals.icu calendar, replans everything from "
            f"{pause.resume_date.isoformat()} ({pause.resume_date:%A}), and "
            f"trims the first week back by {FIRST_WEEK_REDUCE_PERCENT}% off plan."
        )
    # No skips means no replan, so neither warning about one may be relayed.
    if pause.to_skip and pause.resume_date < next_monday(start):
        caveats.append(_MID_WEEK_CAVEAT)
    if pause.to_skip and pause.edited_workouts:
        caveats.append(
            f"{len(pause.edited_workouts)} upcoming workout(s) were edited on "
            "intervals.icu and the replan overwrites them."
        )
    if push is not None and push["status"] != "ok":
        caveats.append(str(push["message"]))
    if replan is not None:
        caveats.extend(_leg_caveats(replan, _REPLAN_FAILED))
    if first_week is not None:
        caveats.extend(_leg_caveats(first_week, _TRIM_FAILED))
    caveats.extend(ad_hoc_caveats(ad_hoc, plan_sessions_skipped=bool(pause.to_skip)))
    return caveats


def _response(
    *,
    status: str,
    pause: PausePlan,
    confirm: bool,
    message: str,
    push: dict[str, Any] | None,
    replan: dict[str, Any] | None,
    first_week: dict[str, Any] | None,
    ad_hoc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the single wire shape the preview and the apply both answer with.

    ``status`` is ``preview``, ``ok``, ``empty`` or ``error``;
    ``push``/``replan``/``first_week`` are ``None`` for all but an applied
    pause, ``ad_hoc`` only with no client or no window past today.
    """
    start, through = pause.window
    return {
        "status": status,
        "confirm_required": not confirm,
        "window": {"from": start.isoformat(), "through": through.isoformat()},
        "resume_date": pause.resume_date.isoformat(),
        "sessions": [asdict(item) for item in pause.to_skip],
        "untouched": [_note_payload(note) for note in pause.untouched],
        "edited_workouts": pause.edited_workouts,
        "push": push,
        "replan": replan,
        "first_week": first_week,
        "ad_hoc": ad_hoc,
        "caveats": _caveats(pause, push, replan, first_week, ad_hoc),
        "message": message,
    }


def pause_training(
    *,
    repos: PlannerRepos,
    through_date: str,
    from_date: str | None = None,
    confirm: bool,
    client: Any | None,
    maybe_get_coach: Callable[[], Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Preview or apply a training pause through ``through_date``.

    With no active plan there is nothing to skip or replan, so this
    delegates whole to :func:`pause_without_plan
    <endury.planning.unschedule_workout.pause_without_plan>`, which clears
    the athlete's own coach-scheduled bookings in the window instead and
    says plainly that there was no plan to touch. ``from_date`` is validated
    identically either way, before either branch runs.

    Args:
        repos: Repository container.
        through_date: Last day the athlete is out, ISO ``YYYY-MM-DD``.
        from_date: first day the athlete is out; omit when they are out
            from today.
        confirm: Must be True to write anything.
        client: intervals.icu client, or ``None`` when unconfigured.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        today: Reference date (defaults to the app clock).

    Returns:
        The preview, the applied outcome, an ``empty``/``not_found`` report,
        a curated ``"refused"``/``"error"`` for a ``from_date`` other than
        today, or an error — ``empty`` only when the window holds neither a
        pending plan session nor a coach booking (a failed sweep is an
        error).
    """
    try:
        through = date.fromisoformat(str(through_date))
    except ValueError:
        return {
            "status": "error",
            "message": (
                f"'{through_date}' is not a date — pass the last day the "
                "athlete is out as YYYY-MM-DD."
            ),
        }

    today = today or clock.today()
    from_date_guard = validate_from_date(from_date, through=through, today=today)
    if from_date_guard is not None:
        return from_date_guard
    if repos.plan.get_active() is None:
        return pause_without_plan(
            through=through, confirm=confirm, client=client, today=today
        )
    try:
        pause = plan_pause(repos=repos, client=client, through=through, today=today)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    start, end = pause.window
    push: dict[str, Any] | None = None
    replan: dict[str, Any] | None = None
    first_week: dict[str, Any] | None = None
    ad_hoc: dict[str, Any] | None = None

    if pause.to_skip and confirm:
        push, replan, first_week, ad_hoc = _apply(
            repos=repos,
            pause=pause,
            client=client,
            maybe_get_coach=maybe_get_coach,
            today=today,
        )
        coach = maybe_get_coach() if maybe_get_coach else None
        if coach is not None:
            # The trim re-solved the week after run_replan's own refresh.
            coach.runtime.active_plan_summary = load_plan_context_summary(
                repos, SOLVER_VERSION
            )
        status, message = (
            "ok",
            (
                f"Paused {len(pause.to_skip)} session(s) from {start.isoformat()} "
                f"through {end.isoformat()}; training resumes "
                f"{pause.resume_date.isoformat()}."
            ),
        )
    elif pause.to_skip:
        # A preview must name every booking confirm would also remove — the
        # athlete cannot agree to a loss they were never shown.
        ad_hoc = preview_coach_bookings(client=client, today=today, through=end)
        status, message = "preview", _PREVIEW_MESSAGE
    else:
        # Nothing plan-side is pending, but the coach's own bookings might
        # still be — a pause is one call for "I'm out", not two.
        ad_hoc = (
            clear_coach_bookings(client=client, today=today, through=end)
            if confirm
            else preview_coach_bookings(client=client, today=today, through=end)
        )
        status, message = empty_window_outcome(
            repos=repos,
            active=pause.plan,
            start=start,
            end=end,
            confirm=confirm,
            ad_hoc=ad_hoc,
        )
    return _response(
        status=status,
        pause=pause,
        confirm=confirm,
        message=message,
        push=push,
        replan=replan,
        first_week=first_week,
        ad_hoc=ad_hoc,
    )
