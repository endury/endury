"""The browser runtime's methods: one per ``web/data/api.js`` export.

Each method is a plain function of the booted :class:`BrowserApp` and the
JSON parameters its ``api.js`` export sends, and it returns a JSON-ready
dict. A failure the page should explain raises :class:`RpcError` with an
error code. :mod:`endury.browser.rpc` holds the method table, wraps every
answer in the envelope, checks the store revision before a mutating method
and saves the store afterwards.

Nothing streams. ``generate_plan``, ``replan`` and ``chat`` each answer
one final event — ``result`` or ``done`` — which ``api.js`` hands to the
page's event callback; ``chat``'s also carries ``tools_used``, the names
of the tools the turn ran. A failure is an error envelope, which ``api.js``
turns into an ``error`` event. ``replan`` answers its ``confirm_required``
refusal body as a result, since the client shows its diff and repeats the
call with ``confirm``. The planner's four progress frames are the one thing
that arrives mid-call: ``generate_plan`` and ``replan`` hand them to the
``progress`` callback :func:`endury.browser.rpc.dispatch` passes in, and the
worker posts each to the page as it comes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter

from endury import store
from endury.browser import CRASH_MESSAGE, BrowserApp, RpcError
from endury.coach.memory_cleanup import MemoryCleanupError, propose_memory_cleanup
from endury.coach.welcome import generate_welcome_message
from endury.common import clock
from endury.intervals.client import IntervalsApiError
from endury.intervals.sport_settings import set_thresholds
from endury.planner.foundations.types import SportType
from endury.planning.activity_match import link_view
from endury.planning.activity_views import activity_debrief as debrief_view
from endury.planning.activity_views import activity_trace_view
from endury.planning.brief import build_brief
from endury.planning.calendar_data import build_calendar_data
from endury.planning.calendar_data import (
    delete_ad_hoc_workout as delete_ad_hoc_command,
)
from endury.planning.commit_plan import delete_active_plan, push_active_plan
from endury.planning.commit_plan import reschedule_session as reschedule_command
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    ProgressCallback,
    run_commit,
    run_generate,
)
from endury.planning.get_active_plan import (
    build_bootstrap_payload,
    build_self_assessment_block,
    get_active_plan_payload,
    get_macro_plan_payload,
)
from endury.planning.pair_activity import PairingNotRecorded, PairTargetNotFound
from endury.planning.pair_activity import pair_activity as pair_command
from endury.planning.pair_activity import unlink_activity as unlink_command
from endury.planning.race_day import race_of_activity
from endury.planning.replan_plan import (
    replan_or_confirm_required,
    replan_readback_diff,
    run_replan,
)
from endury.planning.sync_status import build_sync_status
from endury.planning.wire.activity_requests import PairActivityRequest
from endury.planning.wire.inputs import inputs_snapshot
from endury.planning.wire.races import build_races_payload
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    ReplanRequest,
    ScheduleRequest,
    SelfAssessmentRequest,
    ThresholdsRequest,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: How many superseded memory versions ``get_memory_history`` hands back.
MEMORY_HISTORY_LIMIT = 5

_INT = TypeAdapter(int)
_PAIR_TARGET: TypeAdapter[PairActivityRequest] = TypeAdapter(PairActivityRequest)


def _ignore_progress(_event: dict[str, Any]) -> None:
    """Drop a planner progress event: the default when nothing listens."""


def _parse_date(value: str, name: str) -> date:
    """Parse a ``YYYY-MM-DD`` parameter, refusing any other format."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RpcError(
            f"Invalid {name}: expected YYYY-MM-DD, got '{value}'", "invalid_request"
        ) from exc


def _read_activity(func: Callable[..., _T], *args: Any) -> _T:
    """Run one intervals.icu activity read, mapping its errors to codes."""
    try:
        return func(*args)
    except IntervalsApiError as exc:
        if exc.status_code == 404:
            raise RpcError(
                "This activity was not found on intervals.icu.", "not_found"
            ) from exc
        raise RpcError(f"intervals.icu error: {exc}", "upstream_error") from exc
    except httpx.HTTPError as exc:
        raise RpcError(
            f"Could not reach intervals.icu: {exc}", "upstream_error"
        ) from exc


# health and calendar


def health(app: BrowserApp | None) -> dict[str, Any]:
    """Report readiness; the one method that also answers before boot."""
    return {
        "status": "ok",
        "ready": app is not None,
        "coach_ready": app is not None and app.coach is not None,
    }


def flush(app: BrowserApp) -> dict[str, Any]:
    """Do nothing: the dispatcher saves a dirty store after every call."""
    return {"status": "ok"}


def calendar_data(app: BrowserApp, start_date: str, end_date: str) -> dict[str, Any]:
    """Return planned sessions and completed activities for a date range."""
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    try:
        return build_calendar_data(app.repos, app.client, start, end)
    except Exception as exc:
        logger.error("Error in calendar_data: %s", exc, exc_info=True)
        upstream = isinstance(exc, (IntervalsApiError, httpx.HTTPError))
        return {
            "error": str(exc) if upstream else CRASH_MESSAGE,
            "activities": [],
            "ad_hoc": [],
            "plans": [],
            "phases": [],
            "races": [],
            "active_plan": None,
            "compliance_unavailable": True,
        }


def delete_ad_hoc_workout(app: BrowserApp, external_id: str) -> dict[str, Any]:
    """Delete one coach-scheduled ad-hoc workout from the calendar."""
    return delete_ad_hoc_command(external_id, app.client)


# planner


def planner_bootstrap(app: BrowserApp) -> dict[str, Any]:
    """Return the goal/ambition catalogs plus athlete-data readiness."""
    return build_bootstrap_payload(repos=app.repos)


def planner_active(app: BrowserApp) -> dict[str, Any]:
    """Return the canonical summary of the active plan."""
    return get_active_plan_payload(repos=app.repos, solver_version=SOLVER_VERSION)


def planner_macro(app: BrowserApp) -> dict[str, Any]:
    """Return the active macro plan."""
    return get_macro_plan_payload(repos=app.repos)


def put_self_assessment(app: BrowserApp, **body: Any) -> dict[str, Any]:
    """Store the wizard's self-reported training volume per sport."""
    req = SelfAssessmentRequest.model_validate(body)
    app.repos.self_assessment.upsert_many(
        {
            sport: (entry.weekly_hours, entry.longest_session_min)
            for sport, entry in req.sports.items()
        }
    )
    return {
        "status": "ok",
        "self_assessment": build_self_assessment_block(app.repos),
    }


def generate_plan(
    app: BrowserApp, progress: ProgressCallback = _ignore_progress, **body: Any
) -> dict[str, Any]:
    """Generate a plan preview; answers the stream's ``result`` event."""
    response = run_generate(
        GenerateRequest.model_validate(body),
        repos=app.repos,
        progress=progress,
    )
    return {"type": "result", "source": "planner", **response.model_dump()}


def planner_commit(app: BrowserApp, **body: Any) -> dict[str, Any]:
    """Persist the previewed plan and push it to intervals.icu."""
    return run_commit(
        CommitRequest.model_validate(body),
        repos=app.repos,
        maybe_get_coach=app.maybe_get_coach,
        client=app.client,
    )


def planner_delete(app: BrowserApp) -> dict[str, Any]:
    """Archive the active plan and remove its intervals.icu events from today on."""
    return delete_active_plan(
        get_repos=app.get_repos,
        maybe_get_coach=app.maybe_get_coach,
        client=app.client,
    )


def planner_push(app: BrowserApp) -> dict[str, Any]:
    """Sync the active plan's upcoming workouts to the calendar."""
    return push_active_plan(repos=app.repos, client=app.client)


def planner_sync_status(app: BrowserApp) -> dict[str, Any]:
    """Report how the active plan's upcoming sessions stand on intervals.icu."""
    return build_sync_status(app.repos, app.client)


def replan(
    app: BrowserApp, progress: ProgressCallback = _ignore_progress, **body: Any
) -> dict[str, Any]:
    """Replan from next Monday, unless hand-edited events need a confirm first.

    Returns:
        The ``result`` event, or — without ``confirm`` while the rewrite
        window holds events edited on intervals.icu — the
        ``confirm_required`` refusal body (``error_code``, ``diff``).
    """
    if not ReplanRequest.model_validate(body).confirm:
        diff = replan_readback_diff(repos=app.repos, client=app.client)
        refusal = replan_or_confirm_required(diff)
        if refusal is not None:
            return refusal
    result = run_replan(
        repos=app.repos,
        progress=progress,
        maybe_get_coach=app.maybe_get_coach,
        client=app.client,
    )
    return {"type": "result", "source": "planner", **result}


def reschedule_session(app: BrowserApp, session_id: int, **body: Any) -> dict[str, Any]:
    """Move a plan session to another date and re-date its remote event."""
    return reschedule_command(
        _INT.validate_python(session_id),
        ScheduleRequest.model_validate(body),
        get_repos=app.get_repos,
        client=app.client,
    )


# activities


def _plan_link(
    app: BrowserApp, view: dict[str, Any], activity_id: str
) -> dict[str, Any] | None:
    """The activity's place in the active plan (``link_view``), if there is one."""
    plan = app.repos.plan.get_active()
    if plan is None or not view.get("date"):
        return None
    return link_view(
        app.repos.plan.get_sessions(plan.id),
        activity_id,
        day=date.fromisoformat(str(view["date"])),
        activity_type=view.get("type"),
        name=view.get("name"),
        minutes=view.get("duration_minutes"),
    )


def activity_debrief(app: BrowserApp, activity_id: str) -> dict[str, Any]:
    """Return the debrief projection for one intervals.icu activity.

    Carries ``plan_link`` (the session the activity completes, the open
    sessions it could have been, and the one its name says it was), and
    ``race`` when the activity was a race in the race table, so the page can
    judge it as the race and show the race's brief.
    """
    view = _read_activity(debrief_view, app.client, activity_id)
    link = _plan_link(app, view, activity_id)
    if link is not None:
        view = {**view, "plan_link": link}
    race = (
        race_of_activity(app.repos.races, app.client, activity_id, str(view["date"]))
        if view.get("date")
        else None
    )
    if race is not None:
        view = {**view, "race": {"name": race.name, "priority": race.priority}}
    return view


def session_brief(
    app: BrowserApp, session_id: int | None = None, race_date: str | None = None
) -> dict[str, Any]:
    """Return the deterministic brief sheet for a coming session or race.

    Args:
        app: The booted session.
        session_id: The ``plan_sessions`` id to brief.
        race_date: The ISO date of the race to brief.

    Raises:
        RpcError: ``invalid_request`` unless exactly one of the two is
            given; ``not_found`` for a subject that is not on the calendar.
    """
    if (session_id is None) == (race_date is None):
        raise RpcError("Give either session_id or race_date", "invalid_request")
    try:
        return build_brief(
            repos=app.repos,
            client=app.client,
            session_id=session_id,
            race_date=race_date,
        )
    except ValueError as exc:
        raise RpcError(str(exc), "not_found") from exc


def activity_trace(app: BrowserApp, activity_id: str) -> dict[str, Any]:
    """Return the debrief's intensity trace for one activity."""
    return _read_activity(activity_trace_view, app.client, activity_id)


def _takes(app: BrowserApp) -> list[dict[str, str]]:
    """Return the saved takes, reading their note on first use."""
    if app.takes is None:
        app.takes = store.takes.load(app.client)
    return app.takes


def activity_takes(app: BrowserApp) -> dict[str, Any]:
    """Return the coach's saved takes on activities, oldest first."""
    return {"takes": _takes(app)}


def save_activity_take(
    app: BrowserApp, activity_id: str, depth: str, text: str, kind: str = "take"
) -> dict[str, Any]:
    """Keep the coach's take or brief; the dispatcher uploads it.

    It replaces the earlier row of the same kind, subject and depth.

    Args:
        app: The booted session.
        activity_id: The intervals.icu activity id, or a brief's subject
            handle (``session:412``, ``race:2026-10-24``).
        depth: ``quick`` or ``full``.
        text: The coach's reply.
        kind: ``take`` (a done activity) or ``brief`` (a coming subject).
    """
    if kind not in store.takes.KINDS:
        raise RpcError(
            f"kind must be one of: {', '.join(store.takes.KINDS)}", "invalid_request"
        )
    if depth not in store.takes.DEPTHS:
        raise RpcError(
            f"depth must be one of: {', '.join(store.takes.DEPTHS)}", "invalid_request"
        )
    if not activity_id.strip() or not text.strip():
        raise RpcError("A take needs an activity id and a text", "invalid_request")
    app.takes = store.takes.put(_takes(app), activity_id, depth, text.strip(), kind)
    app.takes_unsaved = True
    return {"takes": app.takes}


def pair_activity(app: BrowserApp, activity_id: str, **body: Any) -> dict[str, Any]:
    """Pair a completed activity with a plan session or an event id."""
    target = _PAIR_TARGET.validate_python(body)
    try:
        return _read_activity(
            pair_command, app.client, activity_id, target, app.get_repos
        )
    except PairTargetNotFound as exc:
        raise RpcError(str(exc), "not_found") from exc
    except PairingNotRecorded as exc:
        raise RpcError(str(exc), "upstream_error") from exc


def unlink_activity(app: BrowserApp, activity_id: str) -> dict[str, Any]:
    """Undo an activity's link to the planned session it completed."""
    try:
        return unlink_command(app.repos, activity_id)
    except PairTargetNotFound as exc:
        raise RpcError(str(exc), "not_found") from exc


def put_activity_verdict(
    app: BrowserApp,
    activity_id: str,
    feel: int | None = None,
    icu_rpe: int | None = None,
) -> dict[str, Any]:
    """Write the athlete's own feel (1-5) and effort (1-10) on an activity.

    Args:
        app: The booted session.
        activity_id: The intervals.icu activity id.
        feel: "How did you feel?", 1 (strong) to 5 (weak).
        icu_rpe: Perceived effort, 1 to 10.

    Returns:
        The activity's ``feel`` and ``rpe`` as intervals.icu now holds them.
    """
    if feel is None and icu_rpe is None:
        raise RpcError("Give a feel or an effort to save", "invalid_request")
    if feel is not None and not 1 <= feel <= 5:
        raise RpcError("feel runs from 1 to 5", "invalid_request")
    if icu_rpe is not None and not 1 <= icu_rpe <= 10:
        raise RpcError("icu_rpe runs from 1 to 10", "invalid_request")
    updated = _read_activity(
        lambda: app.client.update_activity_verdict(
            activity_id, feel=feel, icu_rpe=icu_rpe
        )
    )
    return {"feel": updated.feel, "rpe": updated.icu_rpe}


def get_inputs(app: BrowserApp) -> dict[str, Any]:
    """Return per-sport thresholds/hours/CTL-ATL, wellness and push errors."""
    return inputs_snapshot(app.client, app.get_repos)


def put_thresholds(app: BrowserApp, **body: Any) -> dict[str, Any]:
    """Write one sport's thresholds to intervals.icu and return fresh inputs.

    Every sport-settings row covering the sport gets the values, so Ride
    and VirtualRide stay in step.
    """
    req = ThresholdsRequest.model_validate(body)
    values = req.model_dump(exclude={"sport"}, exclude_none=True)
    types = set_thresholds(app.client, SportType(req.sport), **values)
    return {"types": types, "inputs": inputs_snapshot(app.client, app.get_repos)}


def get_races(app: BrowserApp) -> dict[str, Any]:
    """Return the athlete's still-planned races dated after today."""
    return build_races_payload(app.repos)


# coach and conversation


def welcome(app: BrowserApp) -> dict[str, Any]:
    """Return a fresh welcome message for the current athlete profile."""
    profile = app.repos.athlete.get()
    runtime = app.runtime
    health_summary = None
    if runtime is not None:
        runtime.set_athlete_profile(profile)
        health_summary = runtime.recent_health_summary
    message = generate_welcome_message(
        profile=profile,
        config=app.config,
        health_summary=health_summary,
        memory_empty=not app.repos.memory.get().content.strip(),
    )
    return {"message": message}


def conversation_recent(app: BrowserApp, limit: int = 20) -> dict[str, Any]:
    """Return the user/assistant turns after the last reset marker.

    Args:
        app: The booted session.
        limit: Raw history rows to scan, 1-100.
    """
    rows = _INT.validate_python(limit)
    if not 1 <= rows <= 100:
        raise RpcError("limit must be between 1 and 100", "invalid_request")
    messages: list[dict[str, str]] = []
    try:
        entries = app.repos.history.get_recent(rows)
    except Exception:
        # Best effort: a history read never breaks the chat.
        logger.warning("Could not read the conversation history", exc_info=True)
        return {"messages": []}
    for entry in entries:
        if entry.role == "reset":
            messages = []
        elif entry.role in {"user", "assistant"} and entry.content:
            messages.append({"role": entry.role, "content": entry.content})
    return {"messages": messages}


async def reset_chat(app: BrowserApp) -> dict[str, Any]:
    """Start a new conversation and return the welcome message that opens it."""
    return {"message": await app.require_coach().initialize(reset=True)}


async def chat(app: BrowserApp, message: str, isolated: bool = False) -> dict[str, Any]:
    """Run one coaching turn; answers the stream's ``done`` event.

    Args:
        app: The booted session.
        message: The athlete's message, or a canned request from a sheet.
        isolated: Run outside the conversation: no history in, none kept.
            A take or a brief asked from a sheet is answered this way.
    """
    text = message.strip()
    if not text:
        raise RpcError("Message cannot be empty", "invalid_request")
    result = await app.require_coach().chat(text, isolated=bool(isolated))
    done: dict[str, Any] = {
        "type": "done",
        "text": result.reply,
        "proposals": [proposal.to_dict() for proposal in result.proposals],
        "plan_changed": result.plan_changed,
        "tools_used": list(result.tools_used),
        "charts": result.charts,
    }
    if result.reason:
        done["reason"] = result.reason
    return done


async def apply_proposal(
    app: BrowserApp, proposal: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a card the athlete tapped, or hand back its replacement card."""
    outcome = await app.require_coach().apply_proposal(dict(proposal))
    return outcome.to_dict()


async def refresh_proposal(
    app: BrowserApp, proposal: Mapping[str, Any]
) -> dict[str, Any]:
    """Bring an open card up to date after another card wrote the plan."""
    outcome = await app.require_coach().refresh_proposal(dict(proposal))
    return outcome.to_dict()


async def dismiss_proposal(
    app: BrowserApp, proposal: Mapping[str, Any]
) -> dict[str, Any]:
    """Tell the coach the athlete dismissed a card."""
    await app.require_coach().dismiss_proposal(dict(proposal))
    return {"status": "ok"}


# memory


def _memory_payload(app: BrowserApp) -> dict[str, Any]:
    """Render the stored memory document as the wire payload."""
    memory = app.repos.memory.get()
    return {"content": memory.content, "updated_at": memory.updated_at}


def get_memory(app: BrowserApp) -> dict[str, Any]:
    """Return the athlete-memory document and when it was last written."""
    return _memory_payload(app)


def get_memory_history(app: BrowserApp) -> dict[str, Any]:
    """Return the recent superseded versions of the document, newest first."""
    versions = app.repos.memory.history(MEMORY_HISTORY_LIMIT)
    return {"versions": [version.model_dump() for version in versions]}


def put_memory(app: BrowserApp, content: str) -> dict[str, Any]:
    """Replace the memory document with the athlete's edit."""
    try:
        app.repos.memory.set(content)
    except ValueError as exc:
        raise RpcError(str(exc), "invalid_request") from exc
    return _memory_payload(app)


async def cleanup_memory(app: BrowserApp) -> dict[str, Any]:
    """Ask the coach model for a tidied memory document, for preview only."""
    document = app.repos.memory.get().content
    if not document.strip():
        return {"content": ""}
    runtime = app.require_coach().runtime
    try:
        proposal = await propose_memory_cleanup(
            runtime.coach_llm(), document, clock.now().date()
        )
    except MemoryCleanupError as exc:
        raise RpcError(str(exc), "upstream_error") from exc
    return {"content": proposal}
