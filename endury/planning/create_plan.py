"""Coach-callable plan creation: the same run_generate/run_commit the wizard uses.

Preview runs the deterministic pipeline once and returns a lean
projection (phase runs, per-week hours/TSS, the opening ten days);
confirm regenerates with identical arguments and persists it through
:func:`~endury.planning.generate_plan.run_commit` — no second planning
path exists, this is a front door onto the one the wizard's Apply button
drives.

:func:`confirm_or_stale_preview` either commits, or, when the athlete's
live intervals.icu training data changed since the preview they agreed
to, hands back a fresh preview instead, committing nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal, cast

from pydantic import ValidationError

from endury.common import clock
from endury.planner.foundations.types import AmbitionLevel, GoalType
from endury.planning.coach_brief import _goal_label
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.load_planner_metrics import PlannerRepos
from endury.planning.plan_directives import parse_directives, parse_unavailable_arg
from endury.planning.plan_preview import build_plan_preview, plan_weeks
from endury.planning.trail_band_guard import find_band_mismatch, trail_band_refusal
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateError,
    GenerateRequest,
    RaceGoalModel,
)

#: Every goal_type the planner knows, for a curated "unknown goal" refusal.
_GOAL_TYPE_OPTIONS = sorted(goal.value for goal in GoalType)
#: Every ambition tier, for a curated "unknown ambition" refusal.
_AMBITION_OPTIONS = sorted(level.value for level in AmbitionLevel)
#: Mirrors ``GenerateRequest.ambition_level`` — ``_validate_ambition`` has
#: already checked membership in ``_AMBITION_OPTIONS`` by the time this is used.
_AmbitionLiteral = Literal["basic", "moderate", "competitive"]

#: Relayed when a confirm's expected_input_hash no longer matches: nothing
#: was committed, and the fresh preview attached takes its place.
_STALE_INPUT_CAVEAT = (
    "Your training data on intervals.icu changed since that preview — "
    "nothing was created. This is a fresh preview; apply it if it still "
    "looks right."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """The one error shape this use case returns."""
    return {"status": "error", "message": message, **extra}


#: The last weekday (Tuesday) a plan still starts in the current week: the
#: opening test sits on Tuesday, so a later start would lose week 1's test.
_LAST_START_WEEKDAY = 1


def _this_monday(today: date) -> date:
    """The Monday of the current week."""
    return today - timedelta(days=today.weekday())


def _default_training_start(today: date) -> date:
    """The default plan start: this week's Monday up to Tuesday, else next Monday."""
    monday = _this_monday(today)
    if today.weekday() > _LAST_START_WEEKDAY:
        return monday + timedelta(days=7)
    return monday


def _validate_training_start(
    training_start: str | None, today: date
) -> date | dict[str, Any]:
    """Parse and validate the requested start, or fall back to the default start."""
    if training_start is None:
        return _default_training_start(today)
    try:
        start = date.fromisoformat(training_start)
    except ValueError:
        return _error(f"'{training_start}' is not a date — pass YYYY-MM-DD.")
    if start.weekday() != 0:
        monday = start - timedelta(days=start.weekday())
        return _error(
            f"{training_start} is a {start.strftime('%A')}, and plans must "
            f"start on a Monday — use {monday.isoformat()} instead."
        )
    # Only a start in the current week is trimmed to today by
    # ``materialize_plan.drop_past``; an earlier one would materialize,
    # persist and push sessions the athlete never had a chance to do.
    earliest = _this_monday(today)
    if start < earliest:
        return _error(
            f"{training_start} is already behind us — a plan cannot start "
            f"before this week's Monday ({earliest.isoformat()})."
        )
    return start


def _validate_races(races: Any) -> list[RaceGoalModel] | dict[str, Any]:
    """Validate the raw race objects into typed race goals.

    Only what ``RaceGoalModel`` cannot catch on its own — an unknown
    ``goal_type``, since the field is a plain string — is curated here with
    the valid options; a bad priority, an unparsable date, a missing A-goal,
    or a race that does not fit before the A-race are the phase planner's
    own validation, relayed verbatim by :func:`_generate_error_response`.
    """
    if not isinstance(races, list) or not races:
        return _error(
            "`races` must be a non-empty list of "
            '{"goal_type", "race_date", "priority"}.'
        )
    goals: list[RaceGoalModel] = []
    for index, race in enumerate(races):
        if not isinstance(race, dict):
            return _error(
                f"races[{index}] must be an object, not {type(race).__name__}."
            )
        goal_type = race.get("goal_type")
        if goal_type not in _GOAL_TYPE_OPTIONS:
            return _error(
                f"Unknown goal_type '{goal_type}' in races[{index}].",
                options=_GOAL_TYPE_OPTIONS,
            )
        try:
            goals.append(RaceGoalModel(**race))
        except ValidationError as exc:
            return _error(f"races[{index}] is invalid: {exc}")
    return goals


def _validate_ambition(ambition_level: str) -> dict[str, Any] | None:
    """Curated refusal for an unknown ambition level, else ``None``."""
    if ambition_level not in _AMBITION_OPTIONS:
        return _error(
            f"Unknown ambition_level '{ambition_level}'.",
            options=_AMBITION_OPTIONS,
        )
    return None


def _directives_payload(
    *,
    resume_date: str | None,
    suppress_tests_until: str | None,
    unavailable: str | None,
    weekly_hours_cap: float | None,
) -> dict[str, Any] | str | None:
    """Bundle and validate the directive-shaped args.

    ``weekly_hours_cap`` is carried here too, so it round-trips through
    the same object a later ``set_plan_directives`` call would edit, even
    though it also flows into ``GenerateRequest.weekly_hours_cap``
    directly for the pipeline. ``unavailable`` accepts the same
    JSON-string idiom as ``set_plan_directives``, via
    :func:`~endury.planning.plan_directives.parse_unavailable_arg`.

    Returns:
        The payload to persist, ``None`` when no directive was given, or
        a curated error message (from :func:`parse_unavailable_arg` or
        :func:`~endury.planning.plan_directives.parse_directives`).
    """
    payload: dict[str, Any] = {}
    if resume_date:
        payload["resume_date"] = resume_date
    if suppress_tests_until:
        payload["suppress_tests_until"] = suppress_tests_until
    if unavailable:
        parsed_unavailable = parse_unavailable_arg(unavailable)
        if isinstance(parsed_unavailable, str):
            return parsed_unavailable
        payload["unavailable"] = parsed_unavailable
    if weekly_hours_cap is not None:
        payload["weekly_hours_cap"] = weekly_hours_cap
    if not payload:
        return None
    parsed = parse_directives(payload)
    return parsed if isinstance(parsed, str) else payload


def _emptied_by_directives(
    result: Any, directives: dict[str, Any] | None, race_date: str
) -> dict[str, Any] | None:
    """Refuse directives that leave nothing to train, else ``None``.

    Committing that plan would archive the active one and clear its
    calendar in exchange for an empty plan — a mistyped year is all it
    takes.
    """
    if not directives or result.sessions:
        return None
    return _error(
        "Those directives leave the plan with no sessions at all — nothing "
        f"survives between them and {race_date}. Check "
        "resume_date/suppress_tests_until against the race date."
    )


def _plan_name(goal_type: str, race_date: str) -> str:
    """Name a plan from its A-goal: ``_goal_label`` capitalized, plus date.

    ``_goal_label`` is written for inline sentences ("your marathon plan"),
    so half its entries are lowercase — wrong for a name a plan is stored
    and shown under.
    """
    label = _goal_label(goal_type)
    return f"{label[0].upper()}{label[1:]} — {race_date}"


def _replaces_block(active: Any) -> dict[str, Any] | None:
    """Name the plan a create_plan call would archive, or ``None``."""
    if active is None:
        return None
    return {"plan_id": active.id, "name": active.name, "goal_date": active.goal_date}


def _generate_error_response(error: GenerateError) -> dict[str, Any]:
    """Map a ``GenerateError`` onto the coach's actionable refusal shape."""
    if error.error_code == "athlete_data_required":
        return _error(
            error.error_message,
            error_code=error.error_code,
            missing_sports=error.missing_sports,
        )
    if error.error_code == "horizon_too_short":
        return _error(
            error.error_message,
            error_code=error.error_code,
            min_weeks=error.min_weeks,
            actual_weeks=error.actual_weeks,
            deficit_weeks=error.deficit_weeks,
        )
    if error.error_code == "experimental_plan":
        return _error(
            error.error_message, error_code=error.error_code, reasons=error.reasons
        )
    return _error(error.error_message)


def _validate_inputs(
    *,
    races: Any,
    ambition_level: str,
    training_start: str | None,
    resume_date: str | None,
    suppress_tests_until: str | None,
    unavailable: str | None,
    weekly_hours_cap: float | None,
    replace_active: bool,
    accept_band_mismatch: bool,
    repos: PlannerRepos,
    today: date,
) -> (
    tuple[date, list[RaceGoalModel], dict[str, Any] | None, Any, str | None]
    | dict[str, Any]
):
    """Validate every ``create_plan`` arg that does not need the pipeline run.

    Returns:
        ``(start, goals, directives, active, band_caveat)`` once every check
        passes, or a refusal dict.
    """
    start = _validate_training_start(training_start, today)
    if isinstance(start, dict):
        return start
    goals = _validate_races(races)
    if isinstance(goals, dict):
        return goals
    band_mismatch = find_band_mismatch(repos, goals)
    if band_mismatch is not None and not accept_band_mismatch:
        return trail_band_refusal(band_mismatch)
    band_caveat = band_mismatch.caveat if band_mismatch is not None else None
    ambition_error = _validate_ambition(ambition_level)
    if ambition_error is not None:
        return ambition_error
    directives = _directives_payload(
        resume_date=resume_date,
        suppress_tests_until=suppress_tests_until,
        unavailable=unavailable,
        weekly_hours_cap=weekly_hours_cap,
    )
    if isinstance(directives, str):
        return _error(directives)

    active = repos.plan.get_active()
    if active is not None and not replace_active:
        return _error(
            f'A plan is already active ("{active.name}"). Pass '
            "replace_active=true once the athlete agrees to replace it.",
            replaces=_replaces_block(active),
        )
    return start, goals, directives, active, band_caveat


def _confirmed_response(
    *,
    plan_id: int,
    plan_name: str,
    goal_type: str,
    plan_start: date,
    weeks: int,
    warning: str | None,
    push: dict[str, Any],
) -> dict[str, Any]:
    """Build the committed-plan response, once ``run_commit`` has landed."""
    caveats = [warning] if warning else []
    if push["status"] != "ok" and push.get("message"):
        caveats.append(str(push["message"]))
    return {
        "status": "ok",
        "plan_id": plan_id,
        "plan_name": plan_name,
        "goal": _goal_label(goal_type),
        "plan_start": plan_start.isoformat(),
        "weeks": weeks,
        "push": push,
        "caveats": caveats,
        "message": (
            f'Created "{plan_name}" — {weeks} weeks starting {plan_start.isoformat()}.'
        ),
    }


def confirm_or_stale_preview(
    *,
    preview: dict[str, Any],
    expected_input_hash: str | None,
    request: GenerateRequest,
    result: Any,
    plan_name: str,
    a_goal: RaceGoalModel,
    start: date,
    repos: PlannerRepos,
    maybe_get_coach: Callable[[], Any] | None,
    client: Any | None,
) -> dict[str, Any]:
    """Commit the previewed plan, or hand back a fresh preview if it went stale.

    Args:
        preview: The just-built preview (carries its own ``input_hash``).
        expected_input_hash: The athlete's earlier preview's ``input_hash``,
            or ``None`` to skip the freshness check.
        request: The generate request the preview and this commit share.
        result: The freshly generated (not yet committed) plan.
        plan_name: The name the plan would be committed under.
        a_goal: The request's A-goal.
        start: The plan's training start date.
        repos: Repository container.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        The committed outcome, or the stale-input preview.
    """
    if expected_input_hash is not None and expected_input_hash != preview["input_hash"]:
        # The athlete's live intervals.icu training data changed since the
        # preview they agreed to — committing now would not be the plan
        # they saw. Hand back the fresh one instead of silently diverging.
        preview["caveats"] = [_STALE_INPUT_CAVEAT, *preview["caveats"]]
        return preview

    commit = run_commit(
        CommitRequest(name=plan_name, generate_request=request, generate_result=result),
        repos=repos,
        maybe_get_coach=maybe_get_coach or (lambda: None),
        client=client,
    )
    return _confirmed_response(
        plan_id=commit["plan_id"],
        plan_name=plan_name,
        goal_type=a_goal.goal_type,
        plan_start=start,
        weeks=plan_weeks(result),
        warning=result.warning,
        push=commit["push"],
    )


def create_plan(
    *,
    repos: PlannerRepos,
    races: Any,
    ambition_level: str = "moderate",
    training_start: str | None = None,
    weekly_hours_cap: float | None = None,
    resume_date: str | None = None,
    suppress_tests_until: str | None = None,
    unavailable: str | None = None,
    accept_short_horizon: bool = False,
    replace_active: bool = False,
    accept_band_mismatch: bool = False,
    accept_experimental: bool = False,
    confirm: bool = False,
    expected_input_hash: str | None = None,
    maybe_get_coach: Callable[[], Any] | None = None,
    client: Any | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Preview or commit a deterministic plan through run_generate/run_commit.

    Args:
        repos: Repository container.
        races: Race objects (already parsed): a list of
            ``{"goal_type", "race_date", "priority"}`` dicts.
        ambition_level: "basic", "moderate", or "competitive".
        training_start: Requested plan start (must be a Monday), or ``None``
            for the default start (:func:`_default_training_start`).
        weekly_hours_cap: Optional soft weekly-hours ceiling.
        resume_date: Sessions dated before this are not materialized —
            persisted so every future replan re-applies it too.
        suppress_tests_until: Fitness-test sessions dated before this are
            dropped — persisted the same way.
        unavailable: JSON array of inclusive ``["start", "end"]`` date-range
            pairs to drop outright, same idiom as
            ``set_plan_directives.unavailable``. Persisted the same way.
        accept_short_horizon: Acknowledges a short-notice mid-preparation join.
        replace_active: Must be True when a plan is already active.
        accept_band_mismatch: True once the athlete wants a trail race's
            requested band despite a stored profile naming a different one.
        accept_experimental: True once the athlete confirms an Experimental plan.
        confirm: Must be True to persist and push anything.
        expected_input_hash: The preview's own ``input_hash``. On confirm,
            a mismatch against the freshly regenerated plan's hash means
            the athlete's intervals.icu training data changed since that
            preview: nothing is committed, and a fresh preview comes back
            instead.
        maybe_get_coach: Coach accessor for the plan-context refresh on commit.
        client: intervals.icu client, or ``None`` when unconfigured.
        today: Reference date (defaults to the app clock).

    Returns:
        The lean preview, the committed outcome, or a refusal.
    """
    validated = _validate_inputs(
        races=races,
        ambition_level=ambition_level,
        training_start=training_start,
        resume_date=resume_date,
        suppress_tests_until=suppress_tests_until,
        unavailable=unavailable,
        weekly_hours_cap=weekly_hours_cap,
        replace_active=replace_active,
        accept_band_mismatch=accept_band_mismatch,
        repos=repos,
        today=today or clock.today(),
    )
    if isinstance(validated, dict):
        return validated
    start, goals, directives, active, band_caveat = validated

    # weekly_hours_cap's only pydantic constraint (gt=0) is already checked
    # above via parse_directives, and every other field is either a plain
    # str/bool or was built from an already-validated RaceGoalModel/literal
    # — nothing here can raise ValidationError.
    request = GenerateRequest(
        races=goals,
        training_start=start.isoformat(),
        ambition_level=cast(_AmbitionLiteral, ambition_level),
        accept_short_horizon=accept_short_horizon,
        accept_experimental=accept_experimental,
        weekly_hours_cap=weekly_hours_cap,
        directives=directives,
    )
    result = run_generate(request, repos=repos, progress=lambda _payload: None)
    if isinstance(result, GenerateError):
        return _generate_error_response(result)

    a_goal = next((goal for goal in goals if goal.priority == "A"), goals[0])
    emptied = _emptied_by_directives(result, directives, a_goal.race_date)
    if emptied is not None:
        return emptied
    plan_name = _plan_name(a_goal.goal_type, a_goal.race_date)

    preview = build_plan_preview(
        result,
        plan_name=plan_name,
        goal_type=a_goal.goal_type,
        races=[goal.model_dump() for goal in goals],
        plan_start=start,
        replaces=_replaces_block(active),
        directives=directives,
        extra_caveats=[band_caveat] if band_caveat else [],
    )
    if not confirm:
        return preview

    return confirm_or_stale_preview(
        preview=preview,
        expected_input_hash=expected_input_hash,
        request=request,
        result=result,
        plan_name=plan_name,
        a_goal=a_goal,
        start=start,
        repos=repos,
        maybe_get_coach=maybe_get_coach,
        client=client,
    )


__all__ = ["confirm_or_stale_preview", "create_plan"]
