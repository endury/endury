"""The worker's entry point: ``dispatch(method, params)`` answers an envelope.

``web/worker/py_worker.js`` calls :func:`dispatch` once per ``api.js`` call,
one call at a time. Every answer is an envelope — ``{"ok": true, "result":
…}`` or ``{"ok": false, "error": "…", "error_code": "…"}`` — carrying
``"unsaved"`` as well: whether durable changes still wait to reach the
intervals.icu store note; once booted, ``"read_only"`` too: whether a newer
Endury wrote the store, so every write is refused. ``result`` is JSON-safe.

``boot`` comes first (``dispatch("boot", {"settings": …})``); until it
succeeds every method but ``health`` answers ``not_booted``. Settings with a
truthy ``demo`` (not a ``CoachConfig`` field) boot the demo athlete instead:
an in-memory intervals.icu account that dies with the worker
(:mod:`endury.browser.demo`). Around every other call:

* a :data:`MUTATING` method first checks that the store
  note is still at the revision this session loaded, so a device that fell
  behind never pushes calendar events its store cannot record
  (``store_conflict``: reload and retry);
* afterwards a dirty database is saved as the next revision, and any
  unsaved conversation or activity take to its own note. A failed upload
  keeps the dirty flag, so the next call — ``flush`` when there is nothing
  else to do — retries it;
* a :data:`PROGRESS_METHODS` method hands the planner's progress frames to
  the ``progress`` callback ``dispatch`` was given, which the worker posts
  to the page as they come — the one thing that arrives before an answer.

Error codes: ``not_booted``, ``unknown_method``, ``invalid_request``,
``invalid_settings``, ``not_found``, ``chat_disabled``, ``intervals_auth``,
``upstream_error``, ``store_conflict``, ``store_read_only``,
``store_corrupt`` and ``internal_error``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from endury import store
from endury.browser import CRASH_MESSAGE, BrowserApp, RpcError, api, demo
from endury.coach.runtime import CoachRuntime
from endury.coach.tools.analysis_tools import Executor
from endury.coach.turn import ModelCallError
from endury.common import clock
from endury.intervals.client import (
    IntervalsApiError,
    IntervalsAuthError,
    IntervalsClient,
)
from endury.intervals.ownership import take_over_key_events
from endury.planning.race_book import drop_block_ends
from endury.planning.race_calendar import reconcile_race_events
from endury.planning.race_day import complete_raced_races
from endury.wiring.config import CoachConfig
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)

#: Runs the coach's analysis scripts: the sandbox worker's relay, which
#: ``web/worker/py_worker.js`` installs before the first call. None natively,
#: which leaves ``run_analysis`` unoffered unless a test installs one.
_analysis_executor: Executor | None = None


def set_analysis_executor(executor: Executor | None) -> None:
    """Install what runs the coach's analysis scripts for the next boot.

    Args:
        executor: An async callable taking a payload and returning
            ``{"stdout", "error", "charts"}``; None removes the tool.
    """
    global _analysis_executor
    _analysis_executor = executor


#: The ``CoachConfig`` fields the settings panel may set; blank means default.
SETTINGS_KEYS = (
    "intervals_api_key",
    "intervals_access_token",
    "intervals_athlete_id",
    "intervals_base_url",
    "openrouter_api_key",
    "openrouter_base_url",
    "model",
)

#: Every method the worker may call besides ``boot``, by name.
METHODS: dict[str, Callable[..., Any]] = {
    fn.__name__: fn
    for fn in (
        api.health,
        api.flush,
        api.calendar_data,
        api.delete_ad_hoc_workout,
        api.planner_bootstrap,
        api.planner_active,
        api.planner_macro,
        api.put_self_assessment,
        api.generate_plan,
        api.planner_commit,
        api.planner_delete,
        api.planner_push,
        api.planner_sync_status,
        api.replan,
        api.reschedule_session,
        api.activity_debrief,
        api.activity_trace,
        api.activity_takes,
        api.session_brief,
        api.save_activity_take,
        api.pair_activity,
        api.unlink_activity,
        api.put_activity_verdict,
        api.get_inputs,
        api.put_thresholds,
        api.get_races,
        api.welcome,
        api.conversation_recent,
        api.reset_chat,
        api.chat,
        api.apply_proposal,
        api.refresh_proposal,
        api.dismiss_proposal,
        api.get_memory,
        api.get_memory_history,
        api.put_memory,
        api.cleanup_memory,
    )
}

#: The methods that write the plan, the calendar or the store: each first
#: checks that no other device saved since this one last loaded.
MUTATING = frozenset(
    {
        "planner_commit",
        "planner_delete",
        "planner_push",
        "replan",
        "reschedule_session",
        "pair_activity",
        "unlink_activity",
        "put_self_assessment",
        "put_memory",
        "chat",
        "apply_proposal",
        "delete_ad_hoc_workout",
    }
)

#: Exceptions mapped to an error code, most specific first.
_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (store.StoreConflict, "store_conflict"),
    (store.StoreReadOnly, "store_read_only"),
    (store.StoreCorrupt, "store_corrupt"),
    (IntervalsAuthError, "intervals_auth"),
    (IntervalsApiError, "upstream_error"),
    (httpx.HTTPError, "upstream_error"),
    (ModelCallError, "upstream_error"),
    # Planning refusals (no active plan, a date outside the plan, ...) raise
    # ValueError with an athlete-readable message: not a crash.
    (ValueError, "refused"),
)

_app: BrowserApp | None = None


def _failure(exc: Exception) -> dict[str, Any]:
    """Return the error envelope for ``exc``."""
    if isinstance(exc, RpcError):
        return {"ok": False, "error": str(exc), "error_code": exc.code}
    if isinstance(exc, ValidationError):
        message = "; ".join(error["msg"] for error in exc.errors())
        return {"ok": False, "error": message, "error_code": "invalid_request"}
    code = next((code for kind, code in _ERROR_CODES if isinstance(exc, kind)), None)
    if code is None:
        logger.error("RPC call failed: %s", exc, exc_info=exc)
        return {"ok": False, "error": CRASH_MESSAGE, "error_code": "internal_error"}
    return {"ok": False, "error": str(exc), "error_code": code}


async def _call(
    fn: Callable[..., Any], args: tuple[Any, ...], params: Mapping[str, Any]
) -> Any:
    """Call ``fn`` with ``params`` as keywords, refusing ones it does not take."""
    try:
        bound = inspect.signature(fn).bind(*args, **params)
    except TypeError as exc:
        raise RpcError(f"Bad parameters: {exc}", "invalid_request") from exc
    result = fn(*bound.args, **bound.kwargs)
    return await result if inspect.isawaitable(result) else result


# boot


def _config(settings: Mapping[str, Any] | None) -> CoachConfig:
    """Build the config from the settings panel, blanks left at their defaults."""
    chosen = {
        key: value
        for key, value in (settings or {}).items()
        if key in SETTINGS_KEYS and value not in (None, "")
    }
    return CoachConfig(**chosen)


def _has_openrouter_key(config: CoachConfig) -> bool:
    """Whether the settings carry a usable OpenRouter key."""
    try:
        config.get_openrouter_api_key()
    except ValueError:
        return False
    return True


async def _boot(
    settings: Mapping[str, Any] | None,
    start_fresh: bool,
    read_only: bool,
    transport: httpx.BaseTransport | None,
    llm: Any,
) -> BrowserApp:
    """Build the session: config, client, store, history, coach, clean baseline."""
    config = _config(settings)
    token = config.intervals_access_token
    try:
        api_key = "" if token else config.get_intervals_api_key()
    except ValueError as exc:
        raise RpcError(
            "Add your intervals.icu API key in Settings.", "invalid_settings"
        ) from exc
    client = IntervalsClient(
        api_key,
        athlete_id=config.intervals_athlete_id,
        base_url=config.intervals_base_url,
        transport=transport,
        access_token=token,
    )
    repos = RepositoryContainer.in_memory()
    repos.bind_intervals_client(client)
    if client.uses_oauth and not read_only:
        # Before the note is read: the key's copy of it moves over too.
        take_over_key_events(client, clock.today())
    if start_fresh:
        store.delete(client)
    remote = store.read(client)
    snapshot = remote.snapshot
    connect_owned = remote.connect_owned and not client.uses_oauth
    store.restore(repos.session, snapshot)
    store.chat.restore(repos.session, store.chat.load(client))
    drop_block_ends(repos)
    writable = not (read_only or connect_owned or remote.read_only)
    if writable:
        # A planned race the calendar lost comes back before the coach reads it.
        reconcile_race_events(repos.races.list(), client=client, today=clock.today())
    coach = None
    if llm is not None or _has_openrouter_key(config):
        runtime = CoachRuntime(
            config=config,
            llm=llm,
            repos=repos,
            intervals_client=client,
            stamp_last_session=False,
            analysis_executor=_analysis_executor,
        )
        coach = runtime.create_session()
        await coach.initialize()
        runtime.load_plan_context()
    store.mark_clean(repos.session)
    if writable:
        # After the clean baseline, so the next call saves the completed races.
        complete_raced_races(repos.races, client=client, today=clock.today())
    return BrowserApp(
        config=config,
        repos=repos,
        client=client,
        coach=coach,
        rev=snapshot.rev if snapshot is not None else 0,
        read_only=not writable,
        connect_owned=connect_owned,
        chat_saved=store.chat.last_id(repos.session),
    )


async def _boot_demo(settings: Mapping[str, Any], llm: Any) -> BrowserApp:
    """Boot against a fresh demo account and seed Sam's plan into it."""
    fake = demo.account(clock.today())
    kept = {
        key: settings.get(key)
        for key in ("openrouter_api_key", "openrouter_base_url", "model")
    }
    app = await _boot(
        {**kept, "intervals_api_key": "demo"}, False, False, fake.transport(), llm
    )
    demo.seed(app, fake)
    return app


async def boot(
    settings: Mapping[str, Any] | None,
    start_fresh: bool = False,
    read_only: bool = False,
    *,
    transport: httpx.BaseTransport | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """Boot (or re-boot) the session and answer its envelope.

    A failed boot leaves the session unbooted. ``store_corrupt`` means the
    note exists but cannot be read; booting again with ``start_fresh`` — on
    the athlete's explicit click only — deletes it and starts empty.

    Args:
        settings: :data:`SETTINGS_KEYS` values; ``intervals_access_token``
            or ``intervals_api_key`` is required, unless a truthy ``demo``
            boots the demo athlete: its intervals.icu fields,
            ``start_fresh``, ``read_only`` and ``transport`` are then
            ignored, its OpenRouter fields kept.
        start_fresh: Delete the store note before loading.
        read_only: Never save or write — the page's second tab, which does
            not hold the writer lock.
        transport: An httpx transport for the intervals.icu client (tests).
        llm: A coach LLM standing in for OpenRouter (tests).

    Returns:
        The envelope; its result is ``{coach_ready, read_only,
        connect_owned, rev}``.
    """
    global _app
    _app = None
    try:
        if (settings or {}).get(demo.DEMO_SETTINGS_KEY):
            _app = await _boot_demo(settings or {}, llm)
        else:
            _app = await _boot(settings, start_fresh, read_only, transport, llm)
    except Exception as exc:
        return {**_failure(exc), "unsaved": False}
    result = {
        "coach_ready": _app.coach is not None,
        "read_only": _app.read_only,
        "connect_owned": _app.connect_owned,
        "rev": _app.rev,
    }
    return {"ok": True, "result": result, "unsaved": False, "read_only": _app.read_only}


# dispatch


def _check_rev(app: BrowserApp) -> store.Remote:
    """Refuse a write when the store is read-only or another device saved.

    Returns:
        The note as read, for the save after the call to reuse.
    """
    if app.read_only:
        raise store.StoreReadOnly(
            "This copy of Endury is read-only; nothing can be changed here."
        )
    remote = store.read(app.client)
    if remote.read_only:
        raise store.StoreReadOnly(
            "A newer version of Endury wrote this store; this copy is read-only."
        )
    if remote.connect_owned and not app.client.uses_oauth:
        raise store.StoreReadOnly(
            "This account now signs in through Connect intervals.icu; "
            "connect on this device to change anything."
        )
    if remote.rev != app.rev:
        raise store.StoreConflict(
            f"the store is at revision {remote.rev}, not {app.rev}; reload to continue"
        )
    return remote


def _save_chat(app: BrowserApp) -> None:
    """Save unsaved conversation rows to their own note; never raises.

    Runs after the store save, on its own schedule: a chat save never
    turns a call's envelope into an error, and a store conflict never
    holds the chat back.
    """
    session = app.repos.session
    if app.read_only or store.chat.last_id(session) <= app.chat_saved:
        return
    try:
        app.chat_saved = store.chat.save(app.client, session)
    except Exception:
        logger.warning("Could not save the chat; retry next call", exc_info=True)


def _save_takes(app: BrowserApp) -> None:
    """Save a waiting activity take to its own note; never raises, like the chat."""
    if app.read_only or not app.takes_unsaved:
        return
    try:
        app.takes = store.takes.save(app.client, app.takes or [])
        app.takes_unsaved = False
    except Exception:
        logger.warning("Could not save the takes; retry next call", exc_info=True)


def _save(
    app: BrowserApp, envelope: dict[str, Any], remote: store.Remote | None = None
) -> dict[str, Any]:
    """Save a dirty store and any unsaved chat or take, then report what still is.

    A call that already did its work keeps its result when the store save
    after it is refused: the envelope stays ``ok`` and carries the store's
    ``error_code`` alongside, so the page shows the conflict banner without
    losing — or re-running — what the call returned.
    """
    session = app.repos.session
    if not app.read_only and store.is_dirty(session):
        try:
            app.rev = store.save(app.client, session, app.rev, remote)
        except store.StoreError as exc:
            failure = _failure(exc)
            if envelope.get("ok"):
                envelope = {
                    **envelope,
                    "error": failure["error"],
                    "error_code": failure["error_code"],
                }
            else:
                envelope = failure
        except Exception:
            logger.warning("Could not save the store; retry next call", exc_info=True)
    _save_chat(app)
    _save_takes(app)
    unsaved = not app.read_only and (
        store.is_dirty(session)
        or store.chat.last_id(session) > app.chat_saved
        or app.takes_unsaved
    )
    return {**envelope, "unsaved": unsaved, "read_only": app.read_only}


#: The methods that report the planner's progress frames while they run.
PROGRESS_METHODS = frozenset({"generate_plan", "replan"})


async def dispatch(
    method: str,
    params: Mapping[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one method and answer its envelope.

    Args:
        method: ``boot`` or a :data:`METHODS` name.
        params: The method's parameters by name.
        progress: Receives each progress frame a :data:`PROGRESS_METHODS`
            method emits while it runs; no other method calls it.

    Returns:
        ``{"ok": true, "result", "unsaved", "read_only"}`` or ``{"ok": false,
        "error", "error_code", "unsaved", "read_only"}`` (no ``read_only``
        before boot).
    """
    params = dict(params or {})
    if progress is not None and method in PROGRESS_METHODS:
        params["progress"] = progress
    if method == "boot":
        try:
            envelope: dict[str, Any] = await _call(boot, (), params)
        except RpcError as exc:
            envelope = {**_failure(exc), "unsaved": False}
        return envelope
    app = _app
    if app is None:
        if method == "health":
            return {"ok": True, "result": api.health(None), "unsaved": False}
        not_booted = RpcError("Endury has not started yet.", "not_booted")
        return {**_failure(not_booted), "unsaved": False}
    remote: store.Remote | None = None
    try:
        fn = METHODS.get(method)
        if fn is None:
            raise RpcError(f"Unknown method '{method}'", "unknown_method")
        remote = _check_rev(app) if method in MUTATING else None
        result = await _call(fn, (app,), params)
        envelope = {"ok": True, "result": to_jsonable_python(result)}
    except Exception as exc:
        envelope = _failure(exc)
    return _save(app, envelope, remote)


__all__ = [
    "METHODS",
    "MUTATING",
    "PROGRESS_METHODS",
    "SETTINGS_KEYS",
    "boot",
    "dispatch",
    "set_analysis_executor",
]
