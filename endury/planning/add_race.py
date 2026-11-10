"""Coach-callable bridge from a race to the plan: add_race_to_plan.

Dry-runs the deterministic replan with one more race appended to the active
plan's stored request, so the coach can tell the athlete whether a new race
fits as the priority asked for, falls back to a C tune-up, or does not fit
the current preparation at all — before anything is written. No second
planning path: the same ``run_generate``/``run_replan`` the wizard and the
rest of the plan-building tools use.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from endury.common import clock
from endury.planning.generate_plan import request_from_payload, run_generate
from endury.planning.plan_preview import plan_weeks
from endury.planning.replan_plan import _load_replan_inputs, run_replan
from endury.planning.replan_window import replan_window
from endury.planning.wire.requests import GenerateError, GenerateSuccess

#: Priorities a race can be appended at.
_PRIORITIES = ("A", "B", "C")

#: The one caveat every reply carries: a replan never touches this week.
_CURRENT_WEEK_CAVEAT = (
    "Adding a race never changes this week — a replan keeps today through Sunday."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """The one error shape this use case returns."""
    return {"status": "error", "message": message, **extra}


def _try_fit(
    *,
    repos: Any,
    request_block: dict[str, Any],
    base_races: list[dict[str, Any]],
    new_race: dict[str, Any],
    priority: str,
    window: Any,
) -> tuple[list[dict[str, Any]], GenerateSuccess | GenerateError]:
    """Dry-run generation with ``new_race`` appended at ``priority``."""
    tried = [*base_races, {**new_race, "priority": priority}]
    request = request_from_payload(request_block, tried)
    result = run_generate(
        request,
        repos=repos,
        progress=lambda _payload: None,
        prior_training_weeks=window.prior,
        training_start_override=window.start,
        enforce_athlete_data=False,
        enforce_offered=False,
    )
    return tried, result


def _find_fit(
    *,
    repos: Any,
    request_block: dict[str, Any],
    base_races: list[dict[str, Any]],
    new_race: dict[str, Any],
    priority: str,
    window: Any,
) -> tuple[str | None, list[dict[str, Any]], GenerateSuccess | GenerateError | None]:
    """Try ``priority``, then "C" as a fallback; return the first fit, if any.

    Returns:
        The priority it fit at (or ``None`` when neither did), the races
        tried for the last attempt, and that attempt's result.
    """
    attempts = (priority,) if priority == "C" else (priority, "C")
    tried_races: list[dict[str, Any]] = base_races
    result: GenerateSuccess | GenerateError | None = None
    for attempt_priority in attempts:
        tried_races, result = _try_fit(
            repos=repos,
            request_block=request_block,
            base_races=base_races,
            new_race=new_race,
            priority=attempt_priority,
            window=window,
        )
        if isinstance(result, GenerateSuccess):
            return attempt_priority, tried_races, result
    return None, tried_races, result


def _no_fit_response(
    race_name: str, base_races: list[dict[str, Any]], result: GenerateError | None
) -> dict[str, Any]:
    """The preview shape for a race that fits the plan nowhere."""
    message = result.error_message if result is not None else ""
    return {
        "status": "preview",
        "confirm_required": False,
        "fits_as": None,
        "reason": message,
        "races": base_races,
        "weeks": None,
        "caveats": [],
        "message": f'"{race_name}" does not fit this plan: {message}',
    }


def _preview_response(
    race: Any, fits_as: str, tried_races: list[dict[str, Any]], weeks: int
) -> dict[str, Any]:
    """The preview shape for a race that fits, before it is confirmed."""
    return {
        "status": "preview",
        "confirm_required": True,
        "race_name": race.name,
        "race_date": race.race_date,
        "fits_as": fits_as,
        "reason": f"Fits as a {fits_as} race without disrupting the A-race build.",
        "races": tried_races,
        "weeks": weeks,
        "caveats": [_CURRENT_WEEK_CAVEAT],
        "message": (
            f'"{race.name}" fits as priority {fits_as}. Relay this; the '
            "athlete adds it from its card."
        ),
    }


def _confirm(
    *,
    repos: Any,
    active: Any,
    solve: dict[str, Any],
    request_block: dict[str, Any],
    tried_races: list[dict[str, Any]],
    fits_as: str,
    maybe_get_coach: Callable[[], Any] | None,
    client: Any | None,
    today: date,
) -> dict[str, Any]:
    """Persist the race and replan; put the stored request back on refusal."""
    stored_before = json.dumps(solve)
    request_block["races"] = tried_races
    repos.plan.update_solve_result_json(active.id, json.dumps(solve))
    try:
        replan_result = run_replan(
            repos=repos,
            progress=lambda _payload: None,
            maybe_get_coach=maybe_get_coach or (lambda: None),
            today=today,
            client=client,
        )
    except ValueError as exc:
        # The replan refused (race week, a passed goal, a retired engine).
        # Put the stored request back: a race no plan was regenerated
        # around must not silently reshape the next replan.
        repos.plan.update_solve_result_json(active.id, stored_before)
        return _error(str(exc))

    return {
        "status": replan_result["status"],
        "action": replan_result["action"],
        "fits_as": fits_as,
        "races": tried_races,
        "reasoning": replan_result["reasoning"],
        "push": replan_result["push"],
        "caveats": [_CURRENT_WEEK_CAVEAT],
    }


def add_race_to_plan(
    *,
    repos: Any,
    race_id: int,
    priority: str = "B",
    confirm: bool = False,
    maybe_get_coach: Callable[[], Any] | None = None,
    client: Any | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Preview or confirm adding a race to the active plan's next replan.

    Args:
        repos: Repository container (races, plan).
        race_id: The ``athlete_goals`` row to add.
        priority: Requested priority ("A", "B", or "C"); falls back to "C"
            when the requested priority does not fit and it was not "C"
            already.
        confirm: Must be True to persist the race and replan.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).

    Returns:
        The fit preview, the replanned outcome, or a refusal.
    """
    today = today or clock.today()
    if priority not in _PRIORITIES:
        return _error(f"Unknown priority '{priority}'. Use one of: A, B, C.")

    race = repos.races.get(race_id)
    if race is None:
        return _error(f"No race with id {race_id}.")
    if race.goal_type is None:
        return _error(
            f'"{race.name}" has no goal_type set — set one with upsert_race '
            "before adding it to the plan."
        )

    try:
        active, solve = _load_replan_inputs(repos)
    except ValueError as exc:
        return _error(str(exc))

    request_block = solve["request"]
    original_start = date.fromisoformat(request_block["training_start"])
    window = replan_window(today, original_start)
    if race.race_date < window.start.isoformat():
        return _error(
            f"{race.race_date} is inside the current week or already behind "
            "us — a replan keeps the current week; wait until it has passed."
        )

    base_races = list(request_block["races"])
    new_race = {"goal_type": race.goal_type, "race_date": race.race_date}
    fits_as, tried_races, result = _find_fit(
        repos=repos,
        request_block=request_block,
        base_races=base_races,
        new_race=new_race,
        priority=priority,
        window=window,
    )
    if fits_as is None or not isinstance(result, GenerateSuccess):
        error = result if isinstance(result, GenerateError) else None
        return _no_fit_response(race.name, base_races, error)

    weeks = plan_weeks(result)
    if not confirm:
        return _preview_response(race, fits_as, tried_races, weeks)

    return _confirm(
        repos=repos,
        active=active,
        solve=solve,
        request_block=request_block,
        tried_races=tried_races,
        fits_as=fits_as,
        maybe_get_coach=maybe_get_coach,
        client=client,
        today=today,
    )


__all__ = ["add_race_to_plan"]
