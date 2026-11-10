"""Add ONE coach-scheduled extra session to the active plan's own books.

When an active plan covers the day, the workout becomes a real
``plan_sessions`` row marked as a coach extra by its ``x{n}:extra_`` slot
id (:func:`is_coach_extra`) and pushed under the plan's own
``coachie:{plan_id}:{slot_id}`` scheme, so it counts towards the week's
totals, compliance, ``adjust_current_week``, ``pause_training`` and a
replan's cleanup.

An extra can only add load, so it defaults to easy/recovery work; a
harder or brick session needs ``allow_hard``, a protected week needs
``allow_protected``, and every extra is refused inside a stored
``unavailable`` window or once the week's extras would pass
``MAX_EXTRA_WEEK_TSS_FRACTION``. Guard order is pinned: unavailable
window -> taper/race protection -> hard-intent gate -> adjacent hard ->
TSS allowance.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from endury.planner.activity.capabilities import ActivityStimulusTag
from endury.planner.activity.materialize import (
    activity_brief,
    activity_display_name,
)
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.steps import session_coaching_notes
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    GoalType,
    PhaseType,
)
from endury.planning.activity_match import session_title
from endury.planning.adjust_week import _plan_used_thresholds
from endury.planning.commit_plan import (
    build_plan_events,
    load_plan_context_summary,
    push_plan_events,
    read_athlete_state,
    read_solve_payload,
)
from endury.planning.generate_plan import SOLVER_VERSION
from endury.planning.load_planner_metrics import PlannerRepos
from endury.planning.plan_directives import PlanDirectives, parse_directives
from endury.planning.swap_session import (
    _beyond_this_week,
    _percent,
    _protection_labels,
    _week_bounds,
    open_test_refusal,
)
from endury.planning.wire.sessions import (
    build_commit_session_rows,
    serialize_planned_session,
)
from endury.planning.wire.types import SerializedSessionPayload
from endury.planning.wire.weekly import build_weekly_summary

if TYPE_CHECKING:
    # schedule_workout imports this module, so its value object arrives
    # type-only.
    from endury.planning.schedule_workout import AdHocWorkout

logger = logging.getLogger(__name__)

#: A week may carry at most this fraction more TSS than the solver sized it
#: for. An extra is load the macro solver never sized, so it rides on top of
#: the plan rather than inside it — small enough to be absorbed, named out
#: loud when it is refused.
MAX_EXTRA_WEEK_TSS_FRACTION = 0.15

#: Families a coach extra may come from without ``allow_hard``. Harder work
#: changes what the week *is*, which belongs to the planner or a deliberate
#: swap.
EASY_FAMILIES: frozenset[ActivityFamily] = frozenset(
    {ActivityFamily.EASY, ActivityFamily.RECOVERY}
)

#: Slot ids of coach extras: ``x{n}:extra_{date}_{activity_key}``. Never
#: collides with a menu slot (``menu:…``) or a replan revision (``r{n}:…``).
_EXTRA_SLOT_PATTERN = re.compile(r"^x\d+:extra_")

#: What makes an activity a brick rather than merely brick-compatible —
#: the same conjunction ``activity.select`` resolves the "brick" intent
#: with (``run_easy`` carries BRICK_SUPPORT alone). Drift-guarded by
#: ``test_every_intent_word_is_classified_as_the_spec_names_it``.
_BRICK_TAGS: frozenset[ActivityStimulusTag] = frozenset(
    {ActivityStimulusTag.BRICK_SUPPORT, ActivityStimulusTag.RACE_SPECIFIC}
)

_UNAVAILABLE_REFUSAL = (
    "{day} falls inside a window already marked unavailable on this plan "
    "({start}..{end}) — the athlete is not training then. Pick a different "
    "date, or clear that window with set_plan_directives first."
)
_ADJACENT_HARD_REFUSAL = (
    "{blocking_day} already carries {blocking_name}, a {reason} session — "
    "stacking {name} within a day of it eats the recovery the plan is "
    "counting on. Pick a different day, or make this one easy instead."
)
_EMPTY_WEEK_REFUSAL = (
    "The week of {monday} has no planned sessions to add an extra to. Build "
    "or replan the week first, or use swap_planned_session once it does."
)
_BUDGET_REFUSAL = (
    "This would push the week past what an extra may add. The week of "
    "{monday} is sized at {sized} load and is still carrying {carrying} load, "
    "which leaves {room} load of room; this one is {requested} load. Never "
    "shrink a workout to fit: a session cut short is no session. Use "
    "swap_planned_session to replace a planned session instead."
)
_MOVE_HINT = (
    " Or offer to move one of the week's own sessions to {day}: {sessions} "
    "with move_planned_session."
)

_HARDER_REFUSAL = (
    "An extra session defaults to easy, endurance, recovery or strides — "
    "{name} is a {label} session, which adds {adds} the plan did not "
    "budget. If the athlete wants that load, preview it with "
    "schedule_workout_override (allow_hard=true) — it books only on their yes "
    "— or replace one of the week's planned sessions with it using "
    "swap_planned_session instead."
)
_EXTRA_RESOLVER_REFUSAL = (
    "intervals.icu thresholds are unavailable right now and this plan's "
    "other sessions carry real pace/power/HR targets built from them — an "
    "extra with none would look wrong next to the rest of the week. Nothing "
    "was changed."
)
_PROTECTED_REFUSAL = (
    "{day} sits in a {reasons}, which the plan protects — an extra session "
    "there eats the freshness the week exists to build. Say so, and only if "
    "the athlete still wants it, preview it with schedule_workout_override "
    "(allow_protected=true) — it books only on their yes."
)

_INTENSITY_CAVEAT = (
    "This adds {adds} the plan did not budget: {label} is outside an "
    "extra's normal easy/endurance/recovery scope."
)
_WEEK_CAVEAT = (
    "This is an extra session on top of the week as planned: the week goes "
    "from {before} to {after} load ({percent}%). It is a plan session, so it "
    "counts towards the week, towards compliance, and towards any later "
    "adjustment or pause."
)
_REMOVAL_CAVEAT = (
    "It is part of your plan now, and comes back off like any planned session."
)
_REPLAN_CAVEAT = (
    "A replan rebuilds the plan from next Monday and would drop this extra."
)
_OVERRIDE_CAVEAT = (
    "This adds to your {reasons}, which the plan keeps protected: a "
    "deliberate change, made on your request."
)
_PUSH_CAVEAT = (
    "The session is in the plan, but your intervals.icu calendar does not "
    "have it until the push is retried with Sync now."
)


@dataclass(frozen=True, slots=True)
class ExtraSession:
    """One coach extra ready to be written into the active plan.

    Attributes:
        plan: The active plan row it joins.
        payload: The materialized session payload (its future row).
        slot_id: The extra's slot id, unique within the plan.
        week: Inclusive ``(monday, sunday)`` of the extra's week.
        week_rows: Every session row already in that week.
        guards: Protection labels that were triggered and overridden.
        hard_label: What made this extra need ``allow_hard`` (its family,
            or "brick"), or ``None`` for a plain easy/recovery one.
    """

    plan: Any
    payload: SerializedSessionPayload
    slot_id: str
    week: tuple[date, date]
    week_rows: list[Any]
    guards: list[str]
    hard_label: str | None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a refusal the coach relays verbatim. Nothing was written."""
    return {"status": "error", "message": message, **extra}


def is_coach_extra(row: Any) -> bool:
    """Whether a ``plan_sessions`` row's slot id marks it a coach extra."""
    return bool(_EXTRA_SLOT_PATTERN.match(str(getattr(row, "slot_id", ""))))


def hard_extra_label(activity: Any) -> str | None:
    """Name why *activity* needs ``allow_hard``, or ``None`` when it does not.

    Family alone would miss a brick: ``run_brick_short``/``run_brick_long``
    are family ``easy``, and only :data:`_BRICK_TAGS` separates them from
    the plain easy run they share that family with.

    Args:
        activity: The registry activity the extra would be.

    Returns:
        The word the refusal and the caveat call it by (its family, or
        "brick"), or ``None`` for an easy/recovery non-brick activity.
    """
    if activity.family not in EASY_FAMILIES:
        return str(activity.family.value)
    profile = activity.capability_profile
    tags = set(profile.stimulus_tags) if profile is not None else set()
    return "brick" if tags >= _BRICK_TAGS else None


def _what_it_adds(label: str) -> str:
    """What a hard extra adds to the week: a long one is long, not intense.

    An easy hour on trails is the Long Trail Run, so calling it intensity
    told the athlete their easy run was a hard one.
    """
    return "a long session" if label == ActivityFamily.LONG.value else "intensity"


def unavailable_window_refusal(active: Any, day: date) -> dict[str, Any] | None:
    """Refuse a date the plan's own stored directives already blacked out.

    Args:
        active: The active plan row.
        day: The date the extra would land on.

    Returns:
        A refusal naming the blocking window, or ``None`` when *day* is
        clear of every stored ``unavailable`` range.
    """
    request = read_solve_payload(active).get("request") or {}
    parsed = parse_directives(request.get("directives"))
    if not isinstance(parsed, PlanDirectives):
        return None
    for start, end in parsed.unavailable:
        if start <= day <= end:
            return _error(
                _UNAVAILABLE_REFUSAL.format(
                    day=day.isoformat(), start=start.isoformat(), end=end.isoformat()
                )
            )
    return None


def _hard_or_long(row: Any) -> tuple[bool, str] | None:
    """Return ``(is_long, display name)`` when *row* needs ``allow_hard``.

    Uses the same :func:`hard_extra_label` notion the ``allow_hard`` gate
    itself checks a new extra against — so a brick neighbour (family
    ``easy``, but tagged as one) blocks an adjacent hard extra exactly like
    a brick extra itself needs ``allow_hard``.

    Args:
        row: A ``plan_sessions`` row.

    Returns:
        ``None`` when the row needs no ``allow_hard``, else whether it is
        the long family (rather than physiologically hard or a brick) and
        its display name.
    """
    try:
        activity = get_activity(ActivityKey(str(row.activity_key)))
    except (ValueError, KeyError):
        return None
    label = hard_extra_label(activity)
    if label is None:
        return None
    return label == ActivityFamily.LONG.value, activity_display_name(activity.key)


def adjacent_hard_refusal(
    repos: PlannerRepos, plan_id: int, day: date, name: str
) -> dict[str, Any] | None:
    """Refuse a hard extra within a day of another one needing ``allow_hard``.

    Checks the day before, the day itself, and the day after: a
    non-skipped row in any of those three that :func:`hard_extra_label`
    names — a hard family, long, or a brick hiding in the easy one —
    blocks the extra, named in the refusal.

    Args:
        repos: Repository container.
        plan_id: The active plan's id.
        day: The date the hard extra would land on.
        name: The extra's own display name, for the refusal text.

    Returns:
        The refusal, or ``None`` when the three days are clear.
    """
    ensure_registry_loaded()
    start = day - timedelta(days=1)
    end = day + timedelta(days=1)
    rows = repos.plan.get_sessions_by_date_range(
        plan_id, start.isoformat(), end.isoformat()
    )
    for row in sorted(rows, key=lambda r: str(r.date)):
        if str(getattr(row, "status", "") or "") == "skipped":
            continue
        classified = _hard_or_long(row)
        if classified is None:
            continue
        is_long, blocking_name = classified
        return _error(
            _ADJACENT_HARD_REFUSAL.format(
                blocking_day=row.date,
                blocking_name=blocking_name,
                reason="long" if is_long else "hard",
                name=name,
            )
        )
    return None


def budget_refusal(
    week_rows: list[Any], monday: date, requested_tss: float
) -> dict[str, Any] | None:
    """Refuse when the week would carry more than an extra may add to it.

    The ceiling is the week as the *solver sized it* — every non-extra row's
    ``target_tss``, skipped or not — plus
    :data:`MAX_EXTRA_WEEK_TSS_FRACTION`. What the week is still carrying (its
    non-skipped rows, extras included) is measured against that ceiling. A
    pause that hollows a week out therefore frees room rather than collapsing
    the allowance to a fraction of whatever survived; with nothing skipped
    the rule is exactly "extras may add that fraction of the week". A skipped
    extra counts for nothing on either side: it neither sizes the week nor
    eats its room.

    Args:
        week_rows: Every session row already in the extra's week.
        monday: The week's Monday (named in the refusal).
        requested_tss: The TSS this extra would add.

    Returns:
        The refusal dict, or None when it fits.
    """
    # An optional session is outside the plan's load (R-SESS-037).
    week_rows = [row for row in week_rows if not getattr(row, "optional", False)]
    sized = sum(
        (float(row.target_tss or 0.0) for row in week_rows if not is_coach_extra(row)),
        0.0,
    )
    if sized <= 0:
        return _error(_EMPTY_WEEK_REFUSAL.format(monday=monday.isoformat()))
    carrying = sum(
        (
            float(row.target_tss or 0.0)
            for row in week_rows
            if str(getattr(row, "status", "") or "") != "skipped"
        ),
        0.0,
    )
    ceiling = sized * (1.0 + MAX_EXTRA_WEEK_TSS_FRACTION)
    if carrying + requested_tss <= ceiling:
        return None
    return _error(
        _BUDGET_REFUSAL.format(
            monday=monday.isoformat(),
            sized=round(sized, 1),
            carrying=round(carrying, 1),
            room=round(max(ceiling - carrying, 0.0), 1),
            requested=round(requested_tss, 1),
        )
    )


def _not_this_extra(
    active: Any, rows: list[Any], activity: Any, day: date
) -> dict[str, Any] | None:
    """Refuse first a day the athlete is away, then a test the plan holds open."""
    return unavailable_window_refusal(active, day) or open_test_refusal(
        rows, activity, day
    )


def _with_move_hint(
    refusal: dict[str, Any], week_rows: list[Any], activity: Any, day: date
) -> dict[str, Any]:
    """Name the week's own open sessions of the sport that could move to ``day``."""
    movable = [
        row
        for row in week_rows
        if str(row.sport) == activity.sport.value
        and str(row.date) != day.isoformat()
        and row.matched_activity_id is None
        and str(row.status) in ("pending", "unlinked")
    ]
    if movable:
        refusal["message"] += _MOVE_HINT.format(
            day=day.isoformat(),
            sessions=" or ".join(_movable_label(row) for row in movable[:3]),
        )
    return refusal


def _movable_label(row: Any) -> str:
    """``Friday 22 May's Easy Run (60 min, session_id 501)``."""
    day = date.fromisoformat(str(row.date))
    minutes = f"{float(row.duration_minutes or 0):g} min"
    return (
        f"{day:%A} {day.day} {day:%B}'s {session_title(row)} "
        f"({minutes}, session_id {row.id})"
    )


def _week_index(plan: Any, day: date) -> int:
    """Return the plan-relative week index *day* falls in."""
    plan_start = date.fromisoformat(str(plan.plan_start))
    week0_monday = plan_start - timedelta(days=plan_start.weekday())
    return (day - week0_monday).days // 7


def _next_slot_id(taken: set[str], day: date, activity_key: str) -> str:
    """Return the first coach-extra slot id for this day none of ``taken`` holds.

    ``plan_sessions`` is unique on ``(plan_id, slot_id)``, so a second
    identical extra on the same day has to take the next number.
    """
    index = 1
    while f"x{index}:extra_{day.isoformat()}_{activity_key}" in taken:
        index += 1
    return f"x{index}:extra_{day.isoformat()}_{activity_key}"


def _materialize(
    plan: Any,
    computed: AdHocWorkout,
    *,
    slot_id: str,
    week_index: int,
    phase_type: str,
) -> SerializedSessionPayload:
    """Serialize the built workout as the plan row it is about to become.

    The workout itself is the one ``plan_ad_hoc_workout`` already built, so
    the numbers the reply quotes are the numbers that were written and
    pushed. Its hours-space base is the realized duration: an extra has no
    menu prescription behind it to scale from.

    Args:
        plan: The active plan row.
        computed: The built ad-hoc workout.
        slot_id: The extra's slot id.
        week_index: The plan week it lands in.
        phase_type: The phase that week carries.

    Returns:
        The materialized session payload — an extra only by its slot id.
    """
    activity = computed.activity
    entry = next(
        (
            item
            for item in (read_solve_payload(plan).get("sessions") or [])
            if isinstance(item, dict) and item.get("week_index") == week_index
        ),
        {},
    )
    payload = serialize_planned_session(
        slot_id=slot_id,
        session_date=computed.day,
        week_index=week_index,
        phase_type=phase_type,
        sport=activity.sport.value,
        activity_key=activity.key.value,
        family=activity.family.value,
        is_recovery_week=bool(entry.get("is_recovery_week")),
        coaching_intent=(
            f"{activity_brief(activity.key, activity.family)} Added on request "
            "as an extra session, on top of the week as planned."
        ),
        hardness=activity.hardness,
        workout=computed.workout,
        workout_name=activity_display_name(activity.key),
        target_tss=computed.target_tss,
        achieved_tss=computed.achieved_tss,
        duration_minutes=computed.duration_minutes,
        duration_target_minutes=computed.duration_minutes,
        tid_fractions=activity.tid_fractions(
            computed.target_tss, resolver=computed.resolver
        ),
        coaching_notes=session_coaching_notes(
            activity,
            computed.workout,
            duration_min=computed.duration_minutes,
            resolver=computed.resolver,
            goal=GoalType(str(plan.goal_type)),
        ),
        # An ad-hoc extra has no menu rationale behind it.
        why_details=(),
    )
    return payload


def plan_extra_session(
    *,
    repos: PlannerRepos,
    computed: AdHocWorkout,
    allow_protected: bool,
    allow_hard: bool = False,
) -> ExtraSession | dict[str, Any] | None:
    """Compute the coach extra for a built workout. Pure: performs no writes.

    Args:
        repos: Repository container.
        computed: The built ad-hoc workout.
        allow_protected: Whether a taper/race week may take an extra anyway.
        allow_hard: Whether a hard-family or long extra may be added at all
            (still bound by :func:`adjacent_hard_refusal`).

    Returns:
        The computed extra, a refusal dict, or ``None`` when no active plan
        covers the day — the caller then schedules a standalone calendar
        entry as it always has.
    """
    active = repos.plan.get_active()
    if active is None:
        return None
    day = computed.day
    if not str(active.plan_start) <= day.isoformat() <= str(active.goal_date):
        return None
    # Guard order is pinned: unavailable -> open planned test ->
    # taper/race protection -> hard-intent gate -> adjacent hard -> TSS
    # allowance.
    rows = list(repos.plan.get_sessions(active.id))
    refusal = _not_this_extra(active, rows, computed.activity, day)
    if refusal is not None:
        return refusal

    monday, sunday = _week_bounds(day)
    week_rows = [
        row for row in rows if monday.isoformat() <= str(row.date) <= sunday.isoformat()
    ]
    # The week's own rows carry the phase the plan committed for it — the
    # same value a reschedule into this week would stamp.
    phase_type = str(week_rows[0].phase_type) if week_rows else PhaseType.BASE.value
    guards = _protection_labels(
        # _protection_labels reads phase_type off the row and nothing else.
        active,
        SimpleNamespace(phase_type=phase_type),
        (monday, sunday),
    )
    if guards and not allow_protected:
        return _error(
            _PROTECTED_REFUSAL.format(
                day=day.isoformat(), reasons=" and a ".join(guards)
            ),
            guards=guards,
        )

    activity = computed.activity
    hard_label = hard_extra_label(activity)
    if hard_label is not None:
        if not allow_hard:
            return _error(
                _HARDER_REFUSAL.format(
                    name=activity_display_name(activity.key),
                    label=hard_label,
                    adds=_what_it_adds(hard_label),
                )
            )
        refusal = adjacent_hard_refusal(
            repos, active.id, day, activity_display_name(activity.key)
        )
        if refusal is not None:
            return refusal
    if computed.resolver is None and _plan_used_thresholds(active):
        # Same reason as a swap: the steps of every other session in this
        # plan carry absolute pace/watt/bpm targets built from the resolver.
        return _error(_EXTRA_RESOLVER_REFUSAL)

    refusal = budget_refusal(week_rows, monday, computed.target_tss)
    if refusal is not None:
        return _with_move_hint(refusal, week_rows, activity, day)

    week_index = _week_index(active, day)
    slot_id = _next_slot_id({str(row.slot_id) for row in rows}, day, activity.key.value)
    return ExtraSession(
        plan=active,
        payload=_materialize(
            active,
            computed,
            slot_id=slot_id,
            week_index=week_index,
            phase_type=phase_type,
        ),
        slot_id=slot_id,
        week=(monday, sunday),
        week_rows=week_rows,
        guards=guards,
        hard_label=hard_label,
    )


def _mirror_extra_into_solve(
    repos: PlannerRepos, active: Any, payload: SerializedSessionPayload
) -> None:
    """Append the extra to the plan's persisted solve payload.

    The render, coach and re-push layers all read the solve payload, so an
    extra that only reached ``plan_sessions`` would show the athlete two
    different weeks — and would vanish from the plan's own summaries the
    moment a replan rebuilt them from the payload.

    Args:
        repos: Repository container.
        active: The active plan row.
        payload: The extra's materialized payload.
    """
    solve = read_solve_payload(active)
    sessions = [*(solve.get("sessions") or []), dict(payload)]
    sessions.sort(
        key=lambda item: (str(item.get("date", "")), str(item.get("slot_id")))
    )
    solve["sessions"] = sessions
    solve["weekly_summary"] = build_weekly_summary(
        sessions,
        list(solve.get("phase_calendar") or []),
        date.fromisoformat(str(active.plan_start)),
    )
    repos.plan.update_solve_result_json(active.id, json.dumps(solve))


def _inserted_session_id(repos: PlannerRepos, extra: ExtraSession) -> int | None:
    """Read back the row id of the extra just inserted, or None."""
    day = str(extra.payload["date"])
    rows = repos.plan.get_sessions_by_date_range(extra.plan.id, day, day)
    return next(
        (int(row.id) for row in rows if str(row.slot_id) == extra.slot_id), None
    )


def apply_extra_session(
    *,
    repos: PlannerRepos,
    extra: ExtraSession,
    client: Any | None,
    maybe_get_coach: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any], int | None]:
    """Insert the extra as a plan row, then mirror it onto intervals.icu.

    The intended event is stored *inside* the transaction, before the
    network call, exactly as a swap does: a failed push then leaves the row
    carrying a payload the remote does not have, so the session reads
    ``missing`` and the plan header offers "Sync now" until it converges.

    Args:
        repos: Repository container.
        extra: The computed extra.
        client: intervals.icu client, or ``None`` when unconfigured.
        maybe_get_coach: Coach accessor for the plan-context refresh.

    Returns:
        ``(push status block, new session id or None)``; never raises — the
        local write stands whatever the remote does.
    """
    active = extra.plan
    payload = extra.payload
    athlete_state = read_athlete_state(active)
    events = (
        build_plan_events(active.id, [payload], [], athlete_state)
        if client is not None
        else []
    )
    rows = build_commit_session_rows([payload])

    with repos.session.transaction():
        repos.plan.bulk_insert_sessions(active.id, rows)
        _mirror_extra_into_solve(repos, active, payload)
        if client is not None:
            repos.plan.update_pushed_events(
                active.id, {extra.slot_id: json.dumps(events[0])}
            )
    session_id = _inserted_session_id(repos, extra)
    logger.info(
        "Added coach extra %s to plan %s on %s",
        extra.slot_id,
        active.id,
        payload["date"],
    )
    push = push_plan_events(
        active.id, [payload], [], athlete_state, repos=repos, client=client
    )
    coach = maybe_get_coach() if maybe_get_coach else None
    if coach is not None:
        coach.runtime.active_plan_summary = load_plan_context_summary(
            repos, SOLVER_VERSION
        )
    return push, session_id


def _week_delta(extra: ExtraSession) -> dict[str, Any]:
    """The week's planned load and hours, before and after the extra."""
    booked = [row for row in extra.week_rows if not getattr(row, "optional", False)]
    before_tss = sum(float(row.target_tss or 0.0) for row in booked)
    before_minutes = sum(float(row.duration_minutes or 0.0) for row in booked)
    after_tss = before_tss + float(extra.payload["target_tss"])
    after_minutes = before_minutes + float(extra.payload["duration_minutes"] or 0.0)
    monday, sunday = extra.week
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


def _extra_caveats(
    extra: ExtraSession, week: dict[str, Any], push: dict[str, Any] | None
) -> list[str]:
    """Build the coach-extra caveats, most specific first.

    Args:
        extra: The computed extra.
        week: Its week delta block.
        push: The push status block, or None when nothing was pushed.

    Returns:
        The caveat strings.
    """
    caveats = [
        _WEEK_CAVEAT.format(
            before=week["before_tss"],
            after=week["after_tss"],
            percent=week["tss_delta_percent"],
        ),
        _REMOVAL_CAVEAT,
    ]
    if _beyond_this_week(extra.payload["date"]):
        caveats.append(_REPLAN_CAVEAT)
    if extra.guards:
        caveats.insert(0, _OVERRIDE_CAVEAT.format(reasons=", ".join(extra.guards)))
    if extra.hard_label is not None:
        caveats.insert(
            0,
            _INTENSITY_CAVEAT.format(
                label=extra.hard_label, adds=_what_it_adds(extra.hard_label)
            ),
        )
    if push is not None and push["status"] != "ok":
        caveats.append(_PUSH_CAVEAT)
    return caveats


def extra_message(extra: ExtraSession) -> str:
    """The headline sentence for an extra that was written into the plan."""
    payload = extra.payload
    return (
        f"{payload['workout']['name']} added to your plan on {payload['date']} "
        f"as an extra session ({payload['duration_minutes']} min, "
        f"~{payload['achieved_tss']} load)."
    )


def decorate_extra_response(
    response: dict[str, Any], extra: ExtraSession, session_id: int | None
) -> dict[str, Any]:
    """Add the plan-row and week blocks to an ad-hoc workout response.

    Args:
        response: The response ``schedule_workout`` built for the workout.
        extra: The computed extra.
        session_id: The new row's id, or None when the read-back found
            nothing.

    Returns:
        The same dict, with ``plan_row``/``week``/``guards`` and the extra's
        caveats.
    """
    week = _week_delta(extra)
    response["plan_row"] = {
        "session_id": session_id,
        "slot_id": extra.slot_id,
        "week_index": extra.payload["week_index"],
        "phase": extra.payload["phase_type"],
    }
    response["week"] = week
    response["guards"] = extra.guards
    response["caveats"] = [
        *_extra_caveats(extra, week, response["push"]),
        *response["caveats"],
    ]
    return response


__all__ = [
    "EASY_FAMILIES",
    "MAX_EXTRA_WEEK_TSS_FRACTION",
    "ExtraSession",
    "adjacent_hard_refusal",
    "apply_extra_session",
    "budget_refusal",
    "decorate_extra_response",
    "extra_message",
    "hard_extra_label",
    "is_coach_extra",
    "plan_extra_session",
    "unavailable_window_refusal",
]
