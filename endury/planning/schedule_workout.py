"""Schedule ONE ad-hoc structured workout on a date of its own.

Sibling of :mod:`endury.planning.swap_session`, but the workout gets its
own date instead of replacing a planned one, and works with no active
plan. An active plan covering the date makes it a coach extra
(:mod:`endury.planning.plan_extra_session`, guards and all); otherwise it
is a standalone intervals.icu event, so a repeated call creates a second
one. Either path resolves the activity through one picker
(:func:`resolve_adhoc_activity`).

Unlike ``swap_planned_session`` there is no two-phase contract: one call
writes and pushes, since adding a session is cheap and reversible. The
exception is the ``allow_hard``/``allow_protected`` override, reachable
only through the confirm-gated ``schedule_workout_override``, whose first
call only previews. Undoing a booking is ``unschedule_workout``
(standalone) or ``skip_planned_session`` (plan extra).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from endury.common import clock
from endury.intervals.push import ad_hoc_event, ad_hoc_external_id
from endury.intervals.workout_dsl import SportThresholds, workout_to_dsl
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
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    FlatStep,
    build_flat_steps,
)
from endury.planner.foundations.types import ActivityKey, PhaseType, SportType
from endury.planning.activity_match import session_title
from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE
from endury.planning.load_planner_metrics import (
    PlannerRepos,
    get_planner_load_context,
)
from endury.planning.plan_extra_session import (
    ExtraSession,
    apply_extra_session,
    decorate_extra_response,
    extra_message,
    plan_extra_session,
)
from endury.training.load import LoadMetricContext
from endury.training.workout import StructuredWorkout

logger = logging.getLogger(__name__)

_PREVIEW_MESSAGE = (
    "Nothing has been scheduled yet. Show the athlete this workout and the "
    "override it needs; they book it from its card."
)


@dataclass(frozen=True, slots=True)
class AdHocWorkout:
    """One ad-hoc workout ready to push.

    Attributes:
        day: The calendar date to schedule it on.
        activity: The resolved activity definition.
        workout: The built structured workout.
        duration_minutes: Realized duration in minutes.
        target_tss: The TSS the workout was sized to.
        achieved_tss: TSS after deterministic quantization.
        resolver: Athlete threshold resolver used to build it, or None when
            intervals.icu has no thresholds on file.
        plan_collision: The active plan's session on ``day``, if any.
    """

    day: date
    activity: ActivityDefinition
    workout: StructuredWorkout
    duration_minutes: float
    target_tss: float
    achieved_tss: float
    resolver: LoadMetricContext | None
    plan_collision: Any | None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a refusal the coach relays verbatim. Nothing was written."""
    return {"status": "error", "message": message, **extra}


def _plan_collision(repos: PlannerRepos, day: date) -> Any | None:
    """Return the active plan's session on ``day``, if any."""
    active = repos.plan.get_active()
    if active is None:
        return None
    rows = repos.plan.get_sessions_by_date_range(
        active.id, day.isoformat(), day.isoformat()
    )
    return rows[0] if rows else None


def resolve_adhoc_activity(
    sport: str | None,
    intent: str | None,
    activity_key: str | None,
    duration_minutes: float,
) -> ActivityDefinition | dict[str, Any]:
    """Resolve the workout to build, or return a refusal dict.

    Mirrors ``build_workout``'s selection interface: an explicit
    ``activity_key`` wins outright, otherwise a sport + intent word is
    resolved through the shared registry picker.

    Args:
        sport: Requested sport, or None when activity_key is given.
        intent: Requested workout flavor, or None when activity_key is given.
        activity_key: Exact planner activity key, or None.
        duration_minutes: The length asked for, which the intent's pick
            builds to.

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
    if not sport:
        return _error(
            "Name a sport (e.g. 'running') or an exact activity_key.",
            options=sorted(s.value for s in SportType),
        )
    try:
        sport_enum = SportType(sport.strip().lower())
    except ValueError:
        return _error(
            f"Unknown sport '{sport}'.",
            options=sorted(s.value for s in SportType),
        )
    if not intent:
        return _error(
            "Name the workout: either an intent (e.g. 'threshold') or an "
            "exact activity_key.",
            options=VALID_INTENTS,
        )
    intent_lower = intent.strip().lower()
    if intent_lower not in VALID_INTENTS:
        return _error(f"Unknown intent '{intent}'.", options=VALID_INTENTS)
    picked = pick_for_intent(sport_enum, intent_lower, duration_minutes)
    if picked is None:
        return _error(
            f"No '{intent_lower}' activities for sport '{sport_enum.value}'.",
            options=sorted(
                s.value for s in SportType if pick_for_intent(s, intent_lower)
            ),
        )
    return picked


def plan_ad_hoc_workout(
    *,
    repos: PlannerRepos,
    sport: str | None,
    intent: str | None,
    activity_key: str | None,
    duration_minutes: float,
    day: date,
    today: date,
) -> AdHocWorkout | dict[str, Any]:
    """Resolve and build the ad-hoc workout. Pure: performs no writes.

    Split out from the write so the guards in
    :mod:`endury.planning.plan_extra_session` can read the built workout
    before anything is written.

    Args:
        repos: Repository container.
        sport: Requested sport, or None when activity_key is given.
        intent: Requested workout flavor, or None when activity_key is given.
        activity_key: Exact planner activity key, or None.
        duration_minutes: Target duration in minutes.
        day: Calendar date to schedule the workout on.
        today: Reference date — a date before it cannot be scheduled.

    Returns:
        The computed workout, or a refusal dict for the caller to return.
    """
    if day < today:
        return _error(
            f"{day.isoformat()} has passed — an ad-hoc workout cannot be "
            "scheduled into the past."
        )
    if duration_minutes <= 0:
        return _error("duration_minutes must be greater than 0.")

    ensure_registry_loaded()
    activity = resolve_adhoc_activity(sport, intent, activity_key, duration_minutes)
    if isinstance(activity, dict):
        return activity

    resolver = get_planner_load_context(repos)
    target_tss = assignment_target_tss(activity, duration_minutes / 60.0, resolver)
    workout = activity.build(target_tss, resolver=resolver)
    realized_minutes = activity.duration_minutes_exact(target_tss, resolver=resolver)
    achieved_tss = activity.achieved_tss(target_tss, resolver=resolver)

    return AdHocWorkout(
        day=day,
        activity=activity,
        workout=workout,
        duration_minutes=realized_minutes,
        target_tss=target_tss,
        achieved_tss=achieved_tss,
        resolver=resolver,
        plan_collision=_plan_collision(repos, day),
    )


def _flat_step_to_dict(step: FlatStep) -> dict[str, Any]:
    """Serialize one flattened workout step for the tool's JSON response."""
    return {
        "type": step.step_type,
        "duration_s": round(step.duration_s, 1),
        "zone_band": step.zone_band,
        "primary_target": step.primary_label,
        "secondary_target": step.secondary_label,
        "additional_targets": step.extra_labels,
        "description": step.description,
        "distance_m": round(step.distance_m) if step.distance_m else None,
        "zone_numbers": step.zone_numbers,
    }


def sport_thresholds(
    sport: SportType, resolver: LoadMetricContext | None
) -> SportThresholds:
    """Translate the live resolver into one sport's DSL push thresholds."""
    if resolver is None:
        return SportThresholds()
    if sport is SportType.RUNNING:
        pace = resolver.running_threshold_pace_min_per_km
        return SportThresholds(
            lthr_bpm=resolver.running_lthr_bpm,
            threshold_pace_sec_per_km=pace * 60.0 if pace else None,
        )
    if sport is SportType.CYCLING:
        ftp = resolver.cycling_ftp_watts
        return SportThresholds(
            ftp_watts=int(ftp) if ftp else None,
            lthr_bpm=resolver.cycling_lthr_bpm,
        )
    return SportThresholds(css_sec_per_100m=resolver.swim_css_sec_per_100m)


def _workout_summary(computed: AdHocWorkout) -> dict[str, Any]:
    """Build the athlete-facing workout block shared by preview and apply."""
    resolver = computed.resolver
    swim_css = (
        resolver.swim_css_sec_per_100m
        if resolver is not None and resolver.swim_css_sec_per_100m
        else DEFAULT_SWIM_CSS_SEC_PER_100M
    )
    flat_steps = build_flat_steps(
        computed.workout.steps, swim_css_sec_per_100m=swim_css
    )
    return {
        "activity_key": computed.activity.key.value,
        "workout_name": activity_display_name(computed.activity.key),
        "sport": computed.activity.sport.value,
        "family": computed.activity.family.value,
        "duration_minutes": round(computed.duration_minutes, 1),
        "target_tss": round(computed.target_tss, 1),
        "achieved_tss": round(computed.achieved_tss, 1),
        "steps": [_flat_step_to_dict(step) for step in flat_steps],
    }


def _apply(*, computed: AdHocWorkout, client: Any | None) -> dict[str, Any]:
    """Push the workout as one calendar event. Never raises.

    Args:
        computed: The computed workout.
        client: intervals.icu client, or None when unconfigured.

    Returns:
        The push status block — never raises; a remote failure is reported
        here instead.
    """
    if client is None:
        return {"status": "skipped", "message": PUSH_SKIPPED_MESSAGE, "pushed": 0}

    thresholds = sport_thresholds(computed.activity.sport, computed.resolver)
    dsl_text = workout_to_dsl(computed.workout, thresholds, computed.activity.sport)
    brief = activity_brief(computed.activity.key, computed.activity.family)
    description = f"{brief}\n\n{dsl_text}" if dsl_text else brief
    external_id = ad_hoc_external_id(computed.day, uuid.uuid4().hex[:8])
    event = ad_hoc_event(
        external_id,
        computed.day,
        computed.activity.sport,
        activity_display_name(computed.activity.key),
        description,
    )
    try:
        created = client.bulk_upsert_events([event])
    except Exception as exc:
        logger.warning("Ad-hoc workout push failed for %s: %s", computed.day, exc)
        return {
            "status": "error",
            "message": (
                "Scheduling this workout on intervals.icu failed — nothing "
                f"was put on the calendar: {exc}"
            ),
            "pushed": 0,
        }
    logger.info("Pushed ad-hoc workout %s for %s", external_id, computed.day)
    return {
        "status": "ok",
        "pushed": len(created),
        "external_id": external_id,
        "event_id": created[0].id if created else None,
    }


def _caveats(
    computed: AdHocWorkout,
    push: dict[str, Any] | None,
    extra: ExtraSession | None,
) -> list[str]:
    """Build the caveat list the coach must relay verbatim."""
    week = extra.payload if extra is not None else None
    activity_caveat = capability_caveat(
        computed.activity,
        recovery_week=bool(week and week["is_recovery_week"]),
        taper_week=bool(week and week["phase_type"] == PhaseType.TAPER.value),
    )
    caveats = [activity_caveat] if activity_caveat is not None else []
    if computed.resolver is None:
        caveats.append(
            "No athlete thresholds on file in intervals.icu (or it could "
            "not be reached) — targets are RPE and zone number only."
        )
    if computed.plan_collision is not None:
        day = computed.day
        caveats.append(
            f"{day:%A} {day.day} {day:%B} already has your "
            f"{session_title(computed.plan_collision)}. This is a second "
            "session that day, not a replacement for it."
        )
    if push is not None and push["status"] != "ok":
        caveats.append(str(push["message"]))
    return caveats


def _response(
    *,
    status: str,
    computed: AdHocWorkout,
    message: str,
    push: dict[str, Any] | None,
    extra: ExtraSession | None,
) -> dict[str, Any]:
    """Build the wire shape the plan-extra and standalone paths both answer with."""
    collision = computed.plan_collision
    return {
        "status": status,
        "date": computed.day.isoformat(),
        "workout": _workout_summary(computed),
        "plan_collision": (
            {
                "session_id": collision.id,
                "activity_key": collision.activity_key,
                "sport": collision.sport,
            }
            if collision is not None
            else None
        ),
        "push": push,
        "caveats": _caveats(computed, push, extra),
        "message": message,
    }


def schedule_workout(
    *,
    repos: PlannerRepos,
    duration_minutes: float,
    date_str: str,
    sport: str | None = None,
    intent: str | None = None,
    activity_key: str | None = None,
    allow_protected: bool = False,
    allow_hard: bool = False,
    preview: bool = False,
    client: Any | None = None,
    maybe_get_coach: Callable[[], Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Schedule one ad-hoc workout. Writes on this call unless ``preview``.

    With an active plan covering the date the workout joins that plan as a
    coach extra (:mod:`endury.planning.plan_extra_session`) so the books
    stay in one place; otherwise it is pushed as a standalone calendar
    entry.

    Args:
        repos: Repository container.
        duration_minutes: Target duration in minutes.
        date_str: Calendar date to schedule the workout on (YYYY-MM-DD).
        sport: Requested sport, or None when activity_key is given.
        intent: Requested workout flavor, or None when activity_key is given.
        activity_key: Exact planner activity key, or None.
        allow_protected: Whether a plan's taper/race week may take an extra.
        allow_hard: Whether a plan's extra may be a hard-family or long
            session (still refused within a day of another one).
        preview: Build and guard the workout but write and push nothing.
        client: intervals.icu client, or None when unconfigured.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        today: Reference date (defaults to the app clock).

    Returns:
        The preview, the scheduled outcome, or a refusal.
    """
    computed = plan_ad_hoc_workout(
        repos=repos,
        sport=sport,
        intent=intent,
        activity_key=activity_key,
        duration_minutes=duration_minutes,
        day=date.fromisoformat(date_str),
        today=today or clock.today(),
    )
    if isinstance(computed, dict):
        return computed
    extra = plan_extra_session(
        repos=repos,
        computed=computed,
        allow_protected=allow_protected,
        allow_hard=allow_hard,
    )
    if isinstance(extra, dict):
        return extra

    session_id: int | None = None
    if preview:
        status, message, push = "preview", _PREVIEW_MESSAGE, None
    elif extra is not None:
        push, session_id = apply_extra_session(
            repos=repos, extra=extra, client=client, maybe_get_coach=maybe_get_coach
        )
        # The row is written: a failed push degrades the calendar, never the
        # plan, so the outcome stays "ok" and a caveat carries the rest.
        status, message = "ok", extra_message(extra)
    else:
        push = _apply(computed=computed, client=client)
        if push["status"] == "ok":
            status = "ok"
            message = (
                f"{activity_display_name(computed.activity.key)} scheduled on "
                f"intervals.icu for {computed.day.isoformat()}."
            )
        else:
            # No local write backs the plan-less path — a push that did not
            # land means nothing was actually scheduled, so the outer status
            # says so too (see push["message"] for why).
            status = "error"
            message = "The workout was not scheduled — nothing was put on the calendar."
    response = _response(
        status=status,
        computed=computed,
        message=message,
        push=push,
        extra=extra,
    )
    if extra is None:
        return response
    return decorate_extra_response(response, extra, session_id)


__all__ = [
    "plan_ad_hoc_workout",
    "resolve_adhoc_activity",
    "schedule_workout",
    "sport_thresholds",
]
