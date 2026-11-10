"""Deterministic replan: regenerate the active plan from its resume day.

No session locking or diffing — athlete feedback flows only through the
activities table. A replan keeps everything before the cutoff (next
Monday by default, see ``replan_window``), regenerates the rest with
``prior_training_weeks`` truncation so the plan resumes mid-prep instead
of restarting BASE, and persists the merged plan in place.

The splice is pure functions over the stored solve payload and the fresh
generate result: which sessions survive (:func:`retained_sessions`), how
regenerated ones are renumbered (:func:`renumbered`), how macro weeks
splice (:func:`merged_macro`), and which athlete state seeds the forecast
(:func:`seed_athlete`). :func:`run_replan` composes them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, cast

from endury.common import clock
from endury.intervals.push import readback_diff
from endury.planner.foundations.request import AthleteState, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    GoalType,
    SportType,
    is_maintenance_goal,
)
from endury.planning.commit_plan import (
    build_solve_result_payload,
    push_plan_events,
    update_coach_replan_context,
)
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    ProgressCallback,
    can_retry_fresh,
    request_from_payload,
    run_generate,
)
from endury.planning.load_planner_metrics import PlannerRepos
from endury.planning.materialize_plan import drop_past
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.projections import build_projections
from endury.planning.race_book import sync_plan_races
from endury.planning.replan_window import (
    next_monday,
    previous_race_events,
    pushed_events,
    replan_window,
)
from endury.planning.sync_status import (
    PENDING_DELETES_KEY,
    READBACK_PAD_DAYS,
    active_pending_deletes,
)
from endury.planning.wire.requests import (
    GenerateError,
    GenerateSuccess,
)
from endury.planning.wire.sessions import build_commit_session_rows
from endury.planning.wire.types import SerializedSessionPayload
from endury.planning.wire.weekly import build_weekly_summary

logger = logging.getLogger(__name__)


def _load_replan_inputs(repos: PlannerRepos) -> tuple[Any, dict[str, Any]]:
    """Fetch the active plan and its stored request, or raise."""
    active = repos.plan.get_active()
    if active is None:
        raise ValueError(NO_ACTIVE_PLAN_MESSAGE)
    if active.solver_version != SOLVER_VERSION:
        raise ValueError(
            "The active plan was built by a retired engine; create a new plan."
        )
    solve = json.loads(active.solve_result_json or "{}")
    request_block = solve.get("request")
    if not isinstance(request_block, dict):
        raise ValueError(
            "The active plan has no stored request; create a new plan instead."
        )
    return active, solve


def replan_readback_diff(
    *,
    repos: PlannerRepos,
    client: Any | None,
    today: date | None = None,
    resume_date: date | None = None,
) -> list[dict[str, Any]]:
    """Read back the events a replan would overwrite and report the drift.

    The current week belongs to the athlete and the future belongs to the
    plan — but a future event the athlete edited by hand on intervals.icu
    would be silently overwritten, so the replan asks for confirmation
    first. Never raises: an unreachable API reports no drift and the
    replan proceeds.

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).
        resume_date: First day the replan would rewrite (defaults to next
            Monday) — the same cutoff ``run_replan`` will use.

    Returns:
        One dict per drifted event (``external_id``, ``date``, ``name``,
        ``changed_fields``); empty when nothing drifted.
    """
    active = repos.plan.get_active()
    if client is None or active is None:
        return []
    cutoff = resume_date or next_monday(today or clock.today())
    expected = pushed_events(repos.plan.get_sessions(active.id), cutoff.isoformat())
    if not expected:
        return []
    # Pad both bounds: readback_diff skips events with no remote match, so an
    # event hand-moved outside the plan's own span would report no drift and
    # be overwritten silently. Extras are keyed out by external_id.
    pad = timedelta(days=READBACK_PAD_DAYS)
    try:
        diffs = readback_diff(
            client,
            active.id,
            expected,
            (cutoff - pad, date.fromisoformat(str(active.goal_date)) + pad),
        )
    except Exception as exc:  # a read-back failure must not block replanning
        logger.warning("intervals.icu read-back failed for plan %s: %s", active.id, exc)
        return []
    return [
        {
            "external_id": diff.external_id,
            "date": diff.date,
            "name": diff.name,
            "changed_fields": list(diff.changed_fields),
        }
        for diff in diffs
    ]


def replan_or_confirm_required(diff: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Decide whether a replan may run or must first be confirmed.

    Args:
        diff: The drift :func:`replan_readback_diff` reported for the events
            the replan would overwrite.

    Returns:
        ``None`` when nothing drifted and the replan may run, else the
        ``confirm_required`` error body the athlete must acknowledge (by
        repeating the replan with ``confirm: true``).
    """
    if not diff:
        return None
    return {
        "status": "error",
        "error_code": "confirm_required",
        "error_message": (
            f"{len(diff)} upcoming workout(s) were edited on "
            "intervals.icu and would be overwritten by this replan."
        ),
        "diff": diff,
    }


def _replan_races(
    request_block: dict[str, Any], start: date, today: date
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the races still ahead of ``start`` and the A-race, or refuse.

    Raises:
        ValueError: The A-race (or maintenance block end) is on or before
            ``start``, so there is nothing left to regenerate.
    """
    races = [
        race
        for race in request_block["races"]
        if race["priority"] == "A" or date.fromisoformat(race["race_date"]) >= start
    ]
    a_race = next(race for race in races if race["priority"] == "A")
    a_race_date = date.fromisoformat(a_race["race_date"])
    if a_race_date > start:
        return races, a_race
    # Regeneration starts next Monday, so the final calendar week of any
    # plan has nothing left to replan — but the block/race has NOT
    # "ended" until the goal date is actually behind the athlete.
    maintenance = is_maintenance_goal(GoalType(a_race["goal_type"]))
    if maintenance and today > a_race_date:
        raise ValueError("The maintenance block has ended; start a new one instead.")
    if maintenance:
        raise ValueError(
            f"The maintenance block ends {a_race_date.isoformat()} — the "
            "remaining days are already planned; start a new block after "
            "it finishes."
        )
    if today > a_race_date:
        raise ValueError("The goal race has passed; create a new plan instead.")
    raise ValueError(
        f"Race week — the plan ends on race day {a_race_date.isoformat()}; "
        "there is nothing after it to replan."
    )


def _regenerate(
    request_block: dict[str, Any],
    races: list[dict[str, Any]],
    a_race: dict[str, Any],
    *,
    repos: PlannerRepos,
    progress: ProgressCallback,
    start: date,
    prior: int,
) -> tuple[GenerateSuccess, int]:
    """Run the pipeline from ``start``, retrying fresh once if it is rejected.

    Returns:
        The generated plan and the ``prior_training_weeks`` it was built
        with (``0`` after a fresh retry).

    Raises:
        ValueError: The engine rejects the request and its fresh retry.
    """
    # The tune-ups behind the start keep their weeks on the plan's timeline,
    # so their race and easier weeks and the recovery count stay where they
    # were (R-PHASE-002b); a fresh retry has no timeline to keep them on.
    passed = [race for race in request_block["races"] if race not in races]

    def generate(prior_weeks: int) -> GenerateSuccess | GenerateError:
        return run_generate(
            request_from_payload(
                request_block, [*races, *passed] if prior_weeks else races
            ),
            repos=repos,
            progress=progress,
            prior_training_weeks=prior_weeks,
            training_start_override=start,
            # A plan committed before onboarding existed must still replan.
            enforce_athlete_data=False,
            # A stored plan replans whatever is on offer today.
            enforce_offered=False,
        )

    result = generate(prior)
    if isinstance(result, GenerateError) and can_retry_fresh(a_race, start, prior):
        # E.g. macro QP infeasible for a detrained athlete mid-prep.
        logger.warning(
            "Replan with prior=%d rejected (%s); retrying fresh",
            prior,
            result.error_message,
        )
        prior = 0
        result = generate(0)
    if isinstance(result, GenerateError):
        raise ValueError(result.error_message)
    return result, prior


def _persist(
    repos: PlannerRepos,
    plan_id: int,
    macro_row: Any,
    *,
    cutoff: str,
    new_sessions: list[SerializedSessionPayload],
    solve_payload: dict[str, Any],
    macro_weeks: list[dict[str, Any]],
    coach_brief: str,
    revision: int,
) -> int:
    """Swap the plan's sessions from ``cutoff`` on in one transaction.

    The regenerated payload keeps the plan's pending deletes, so the push
    that follows still retries them.

    Returns:
        How many stored sessions the replan deleted.
    """
    pending = active_pending_deletes(repos, plan_id)
    if pending:
        solve_payload = {**solve_payload, PENDING_DELETES_KEY: pending}
    with repos.session.transaction():
        deleted = repos.plan.delete_future_sessions(plan_id, cutoff)
        repos.plan.bulk_insert_sessions(
            plan_id, build_commit_session_rows(new_sessions)
        )
        repos.plan.update_solve_result_json(plan_id, json.dumps(solve_payload))
        if macro_row is not None:
            repos.plan.revise_macro(
                macro_row.id,
                weeks_json=json.dumps(macro_weeks),
                coaching_brief=coach_brief,
                revision=revision,
                revision_reason=f"deterministic replan from {cutoff}",
            )
    return int(deleted)


def athlete_state_from_payload(payload: dict[str, Any]) -> AthleteState:
    """Rebuild an :class:`AthleteState` from the persisted JSON snapshot."""
    sports: dict[SportType, SportState] = {}
    for sport_value, state in (payload.get("sports") or {}).items():
        sports[SportType(sport_value)] = SportState(
            recent_weekly_hours=state["recent_weekly_hours"],
            longest_recent_min={
                ActivityFamily(family): minutes
                for family, minutes in (state.get("longest_recent_min") or {}).items()
            },
            ctl_0=state.get("ctl_0"),
            atl_0=state.get("atl_0"),
            css_sec_per_100m=state.get("css_sec_per_100m"),
            ftp_watts=state.get("ftp_watts"),
            threshold_pace_sec_per_km=state.get("threshold_pace_sec_per_km"),
        )
    return AthleteState(sports=sports)


def retained_sessions(
    solve: dict[str, Any], session_rows: list[Any], cutoff: str
) -> list[SerializedSessionPayload]:
    """Return the stored session payloads a replan keeps.

    Everything before ``cutoff``, plus every later session already matched
    with a completed activity: ``delete_future_sessions`` exempts matched
    rows (a completed workout is history, whatever its date), so the solve
    payload must keep their twins too, or a manually-moved, later-matched
    session survives on the calendar while vanishing from the plan's own
    weekly summaries.
    """
    matched_future_slots = {
        str(row.slot_id)
        for row in session_rows
        if row.matched_activity_id is not None and str(row.date) >= cutoff
    }
    return [
        cast("SerializedSessionPayload", session)
        for session in solve.get("sessions", [])
        if isinstance(session, dict)
        and (
            str(session.get("date", "")) < cutoff
            or str(session.get("slot_id", "")) in matched_future_slots
        )
    ]


def renumbered(
    sessions: list[SerializedSessionPayload], revision: int, prior: int
) -> list[SerializedSessionPayload]:
    """Shift regenerated sessions onto the plan's own week and slot numbering.

    Retained rows keep their original slot_ids; menu-derived slot_ids of the
    regenerated suffix restart at w0 and would collide with the
    UNIQUE(plan_id, slot_id) constraint — hence the revision prefix.
    """
    renumbered: list[SerializedSessionPayload] = []
    for session in sessions:
        merged: dict[str, Any] = dict(session)
        merged["slot_id"] = f"r{revision}:{merged['slot_id']}"
        merged["week_index"] = int(merged["week_index"]) + prior
        renumbered.append(cast("SerializedSessionPayload", merged))
    return renumbered


def merged_macro(
    old_macro: dict[str, Any],
    new_macro: dict[str, Any],
    *,
    prior: int,
    revision: int,
    original_start: date,
) -> dict[str, Any]:
    """Splice the regenerated macro weeks after the first ``prior`` old ones."""
    new_weeks = [
        {**week, "week_index": week["week_index"] + prior}
        for week in new_macro.get("weeks", [])
    ]
    return {
        **new_macro,
        "plan_start": original_start.isoformat(),
        "total_weeks": prior + len(new_weeks),
        "weeks": list(old_macro.get("weeks") or [])[:prior] + new_weeks,
        "revision": revision,
    }


def seed_athlete(active: Any, result: GenerateSuccess) -> AthleteState:
    """Return the plan's original athlete state, or the fresh one if unreadable.

    Seeding the forecast from the original state keeps the pre-replan
    portion of the projections aligned with what the athlete saw.
    """
    seed_payload = None
    try:
        seed_payload = json.loads(active.athlete_state_json or "null")
    except (json.JSONDecodeError, TypeError):
        logger.debug("Unreadable athlete_state_json; seeding from fresh state")
    if not isinstance(seed_payload, dict):
        seed_payload = result.athlete_state or {}
    return athlete_state_from_payload(seed_payload)


def run_replan(
    *,
    repos: PlannerRepos,
    progress: ProgressCallback,
    maybe_get_coach: Callable[[], Any],
    today: date | None = None,
    client: Any | None = None,
    resume_date: date | None = None,
) -> dict[str, Any]:
    """Regenerate the active plan from its resume day and persist the merge.

    Args:
        repos: Repository container.
        progress: Progress callback forwarded to the generator.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        today: Reference date (defaults to the app clock).
        client: intervals.icu client, or ``None`` when unconfigured — the
            regenerated sessions are pushed to the calendar and the events
            of dropped sessions deleted.
        resume_date: First day to rewrite, for a mid-week return from a
            break (defaults to next Monday; must be after ``today``).

    Returns:
        The result payload: ``{"status": "ok", "action": "replanned",
        "reasoning": str, "plan_result": {...}, "push": {...}}``.

    Raises:
        ValueError: No replannable active plan, the goal race has passed,
            or the engine rejects the request (and its fresh retry, if any).
    """
    today = today or clock.today()
    active, solve = _load_replan_inputs(repos)
    request_block = solve["request"]

    original_start = date.fromisoformat(request_block["training_start"])
    window = replan_window(today, original_start, resume_date)
    races, a_race = _replan_races(request_block, window.start, today)
    result, prior = _regenerate(
        request_block,
        races,
        a_race,
        repos=repos,
        progress=progress,
        start=window.start,
        prior=window.prior,
    )

    # A mid-week resume generates from its Monday but rewrites only from the
    # resume day; the macro row keeps the week whole, as adjust_week does.
    sessions = drop_past(result.sessions, window.start, today=window.cutoff)
    cutoff = window.cutoff.isoformat()
    session_rows = repos.plan.get_sessions(active.id)
    previous_events = pushed_events(session_rows, cutoff) + previous_race_events(
        active.id, request_block["races"], cutoff
    )
    retained = retained_sessions(solve, session_rows, cutoff)

    macro_row = repos.plan.get_macro_for_plan(active)
    revision = (macro_row.revision if macro_row is not None else 1) + 1
    new_sessions = renumbered(sessions, revision, prior)

    combined_sessions = retained + new_sessions
    phase_calendar = (
        list(solve.get("phase_calendar") or [])[:prior] + result.phase_calendar
    )
    weekly_summary = build_weekly_summary(
        combined_sessions, phase_calendar, original_start
    )
    combined_macro = merged_macro(
        solve.get("macro_plan") or {},
        dict(result.macro_plan),
        prior=prior,
        revision=revision,
        original_start=original_start,
    )
    # Continuous forecast over the full original window, optional ones aside.
    projections = build_projections(
        sessions=[s for s in combined_sessions if not s.get("optional")],
        athlete=seed_athlete(active, result),
        goal_type=GoalType(a_race["goal_type"]),
        plan_start=original_start,
        goal_date=date.fromisoformat(a_race["race_date"]),
    )

    plan_parts: dict[str, Any] = {
        "sessions": combined_sessions,
        "weekly_summary": weekly_summary,
        "phase_calendar": phase_calendar,
        "coach_brief": result.coach_brief,
        "projections": projections,
        "macro_plan": combined_macro,
    }
    combined = GenerateSuccess(
        **plan_parts,
        athlete_state=result.athlete_state,
        solver_version=SOLVER_VERSION,
        input_hash=result.input_hash,
    )
    solve_payload = build_solve_result_payload(combined)
    solve_payload["request"] = request_block

    deleted = _persist(
        repos,
        active.id,
        macro_row,
        cutoff=cutoff,
        new_sessions=new_sessions,
        solve_payload=solve_payload,
        macro_weeks=combined_macro["weeks"],
        coach_brief=result.coach_brief,
        revision=revision,
    )

    update_coach_replan_context(combined, active.name, maybe_get_coach=maybe_get_coach)
    sync_plan_races(repos, races)
    push = push_plan_events(
        active.id,
        new_sessions,
        races,
        result.athlete_state,
        repos=repos,
        client=client,
        previous_events=previous_events,
    )
    logger.info(
        "Replanned plan %d from %s: kept %d sessions, replaced %d with %d",
        active.id,
        cutoff,
        len(retained),
        deleted,
        len(new_sessions),
    )

    reasoning = (
        f"Regenerated {len(new_sessions)} upcoming sessions from {cutoff} "
        f"using your latest training data; {len(retained)} past sessions "
        f"kept unchanged."
    )
    plan_result = {
        "status": "ok",
        **plan_parts,
        "goal_type": str(active.goal_type),
        "plan_start": str(active.plan_start),
        "goal_date": str(active.goal_date),
    }
    return {
        "status": "ok",
        "action": "replanned",
        "reasoning": reasoning,
        "plan_result": plan_result,
        "push": push,
    }
