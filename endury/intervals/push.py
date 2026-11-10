"""Plan-to-calendar push engine for intervals.icu.

Turns materialized planner sessions and race goals into intervals.icu
calendar event payloads (``POST /athlete/{id}/events/bulk?upsert=true``
shape), and provides the read-back drift check and stale-event diff used by
the replan flow. See ``docs/reference/intervals_api.md`` for the external_id scheme and
event-field contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from endury.intervals.workout_dsl import SportThresholds, workout_to_dsl
from endury.planner.foundations.types import (
    ActivityFamily,
    RaceGoal,
    SportType,
    is_maintenance_goal,
)
from endury.planning.wire.types import (
    SerializedSessionPayload,
    WorkoutPayload,
    WorkoutStepPayload,
    WorkoutTargetPayload,
)
from endury.training.workout import (
    DurationDef,
    StructuredWorkout,
    TargetDef,
    WorkoutStep,
)

#: Our SportType -> the intervals.icu activity type used on WORKOUT events.
#: Public: also used to build ad-hoc (non-plan) workout events.
INTERVALS_TYPE_BY_SPORT: dict[SportType, str] = {
    SportType.RUNNING: "Run",
    SportType.CYCLING: "Ride",
    SportType.SWIMMING: "Swim",
}

_ACTIVITY_KEY_SPORT_PREFIX = re.compile(r"^(run|bike|swim)_")

#: The bold tags of a session's coaching notes, rendered as markdown bold.
_BOLD_TAG = re.compile(r"</?b>")

#: Fields compared between a previously pushed event and the live remote one.
_DIFF_FIELDS: tuple[str, ...] = ("start_date_local", "description", "name")


@dataclass(frozen=True, slots=True)
class EventDiff:
    """Detected drift between a previously pushed event and its live state.

    Attributes:
        external_id: The event's endury external_id.
        date: The pushed event's ``start_date_local`` (for display).
        name: The pushed event's name (for display).
        changed_fields: Field names that differ between the pushed JSON and
            the current remote event.
    """

    external_id: str
    date: str
    name: str
    changed_fields: tuple[str, ...]


def plan_to_events(
    plan_id: int,
    sessions: list[SerializedSessionPayload],
    goals: list[RaceGoal],
    thresholds_by_sport: dict[SportType, SportThresholds],
) -> list[dict[str, Any]]:
    """Build intervals.icu calendar event payloads for a plan.

    One ``WORKOUT`` event per session plus one ``RACE_A``/``RACE_B``/
    ``RACE_C`` event per goal. Never sets ``moving_time``, and sets
    ``icu_training_load`` only on a fitness test — the intervals.icu parser
    computes both from the DSL steps, but cannot price an RPE-only effort.

    A maintenance goal carries a synthesized block-end date rather than a
    real race, so it gets no race event: the athlete is not racing, and
    posting one would put a fictitious "A-Race" on their calendar.

    Args:
        plan_id: The committed plan's id (keys the external_id scheme).
        sessions: Materialized sessions in the wire shape used by the
            planner serializers.
        goals: The plan's race goals, in priority order.
        thresholds_by_sport: Athlete thresholds per sport, used to resolve
            percent-of-threshold targets in the workout DSL. Must contain
            an entry for every sport present in ``sessions``.

    Returns:
        Event dicts ready for ``IntervalsClient.bulk_upsert_events``.
    """
    events = [
        _session_event(plan_id, session, thresholds_by_sport) for session in sessions
    ]
    events.extend(
        _race_event(plan_id, goal)
        for goal in goals
        if not is_maintenance_goal(goal.goal_type)
    )
    return events


def readback_diff(
    client: Any,
    plan_id: int,
    expected: list[dict[str, Any]],
    window: tuple[date, date],
) -> list[EventDiff]:
    """Diff previously pushed events against their current remote state.

    Detects manual edits an athlete made on intervals.icu since we last
    pushed (or since a prior replan): the event's ``start_date_local``,
    ``description``, or ``name`` no longer matches what we stored in
    ``plan_sessions.pushed_event_json``.

    Args:
        client: An ``IntervalsClient``-like object exposing
            ``list_events(oldest, newest) -> list[IntervalsEvent]``.
        plan_id: The plan whose events are being checked (unused directly —
            ``expected`` is already scoped to the plan; kept for callers'
            symmetry with ``plan_to_events`` and for future filtering).
        expected: The event dicts we last pushed, as stored per session.
        window: ``(oldest, newest)`` date range to read back.

    Returns:
        One `EventDiff` per pushed event whose remote state has drifted.
        Events with no matching remote event (never synced, or deleted
        remotely) are skipped — there is nothing live to diff against.
    """
    del plan_id  # kept for signature symmetry; expected is already scoped
    oldest, newest = window
    remote_events = client.list_events(oldest, newest)
    remote_by_id = {
        event.external_id: event
        for event in remote_events
        if getattr(event, "external_id", None)
    }
    diffs: list[EventDiff] = []
    for pushed in expected:
        external_id = pushed.get("external_id")
        if not external_id:
            continue
        remote = remote_by_id.get(external_id)
        if remote is None:
            continue
        changed_fields = _diff_fields(pushed, remote)
        if changed_fields:
            diffs.append(
                EventDiff(
                    external_id=external_id,
                    date=str(pushed.get("start_date_local", "")),
                    name=str(pushed.get("name", "")),
                    changed_fields=changed_fields,
                )
            )
    return diffs


@dataclass(frozen=True, slots=True)
class SessionSyncState:
    """How one planned session stands relative to intervals.icu.

    Attributes:
        slot_id: The plan session's slot id.
        date: The session's ISO date.
        state: One of ``in_sync``, ``missing``, ``removed``,
            ``delivery_failed``, ``edited``.
        services: Platforms that rejected the event (``delivery_failed``).
        changed_fields: Fields the athlete altered remotely (``edited``).
    """

    slot_id: str
    date: str
    state: str
    services: tuple[str, ...] = ()
    changed_fields: tuple[str, ...] = ()


def _rejecting_services(event: Any) -> tuple[str, ...]:
    """Return the device services that rejected ``event`` (``push_errors``)."""
    return tuple(
        str(err.service)
        for err in getattr(event, "push_errors", ())
        if getattr(err, "service", None)
    )


def session_sync_states(
    plan_id: int,
    rows: Sequence[Any],
    remote_events: Sequence[Any],
    today: date,
) -> list[SessionSyncState]:
    """Classify each upcoming session against its remote calendar event.

    Only today onwards is classified: the ownership contract hands the
    past to the athlete, so a past session's sync state is noise — what
    matters there is whether it was completed. Skipped sessions are left
    out too: their event was deleted deliberately.

    A fault outranks information, so an event that reached intervals.icu
    but was rejected by a device reports ``delivery_failed`` even when the
    athlete has also edited it.

    Drift is measured against both the pushed payload and the session row's
    own date: a local move whose calendar write failed leaves the pushed
    payload and the remote event agreeing on the old day, and reports
    ``edited`` with ``start_date_local``.

    Args:
        plan_id: The active plan's id (external_ids are derived from it).
        rows: ``plan_sessions`` rows (``slot_id``, ``date``, and
            ``pushed_event_json`` when the session has been pushed).
        remote_events: Events read back from intervals.icu.
        today: Reference date.

    Returns:
        One `SessionSyncState` per session dated today or later.
    """
    remote_by_id = {
        event.external_id: event
        for event in remote_events
        if getattr(event, "external_id", None)
    }
    cutoff = today.isoformat()
    states: list[SessionSyncState] = []
    for row in rows:
        row_date = str(getattr(row, "date", ""))
        if row_date < cutoff:
            continue
        if getattr(row, "status", "") == "skipped":
            # A skip deletes the event on purpose, so its absence is not
            # drift — reporting it would badge the plan view forever.
            continue
        slot_id = str(getattr(row, "slot_id", ""))
        pushed_raw = getattr(row, "pushed_event_json", None)
        remote = remote_by_id.get(f"coachie:{plan_id}:{slot_id}")
        if remote is None:
            # Pushed once and now absent means the athlete deleted it;
            # never pushed means it simply is not there yet.
            state = "removed" if pushed_raw else "missing"
            states.append(SessionSyncState(slot_id, row_date, state))
            continue
        services = _rejecting_services(remote)
        if services:
            states.append(
                SessionSyncState(slot_id, row_date, "delivery_failed", services)
            )
            continue
        changed = _diff_fields(json.loads(pushed_raw), remote) if pushed_raw else ()
        # A local move whose remote write failed leaves the pushed payload
        # and the remote event agreeing on the old day, so the drift is only
        # visible against the session row's own date.
        remote_day = _as_date(getattr(remote, "start_date_local", ""))
        if remote_day is not None and _as_date(row_date) != remote_day:
            changed = tuple(
                field
                for field in _DIFF_FIELDS
                if field == "start_date_local" or field in changed
            )
        state = "edited" if changed else "in_sync"
        states.append(
            SessionSyncState(slot_id, row_date, state, changed_fields=changed)
        )
    return states


def ad_hoc_sync_states(
    remote_events: Sequence[Any], today: date
) -> list[SessionSyncState]:
    """Classify each upcoming coach-scheduled (ad-hoc) workout event.

    ``schedule_workout`` persists nothing locally for the workouts that end
    up here (inside an active plan's dates it writes a plan row instead), so
    an ad-hoc event that reads back at all is on the calendar by
    construction — the one thing left to check is whether a device rejected
    it. Keyed by external_id, the handle the calendar view already carries
    for these rows.

    Args:
        remote_events: Events read back from intervals.icu.
        today: Reference date; earlier events are skipped.

    Returns:
        One `SessionSyncState` per ad-hoc event dated today or later.
    """
    states: list[SessionSyncState] = []
    for event in remote_events:
        external_id = getattr(event, "external_id", None) or ""
        if not external_id.startswith(AD_HOC_EXTERNAL_ID_PREFIX):
            continue
        day = _as_date(getattr(event, "start_date_local", ""))
        if day is None or day < today:
            continue
        services = _rejecting_services(event)
        state = "delivery_failed" if services else "in_sync"
        states.append(SessionSyncState(external_id, day.isoformat(), state, services))
    return states


def stale_external_ids(
    old_sessions: list[dict[str, Any]],
    new_sessions: list[dict[str, Any]],
) -> list[str]:
    """Return external_ids present in ``old_sessions`` but not ``new_sessions``.

    Used on replan to bulk-delete events for sessions the regenerated plan
    no longer has.

    Args:
        old_sessions: Event dicts (as returned by `plan_to_events`) from
            the previous push.
        new_sessions: Event dicts from the freshly regenerated plan.

    Returns:
        external_ids that must be bulk-deleted.
    """
    new_ids = {
        event["external_id"] for event in new_sessions if event.get("external_id")
    }
    return [
        event["external_id"]
        for event in old_sessions
        if event.get("external_id") and event["external_id"] not in new_ids
    ]


def _as_date(value: Any) -> date | None:
    """Parse an ISO date or datetime string down to its calendar date."""
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def event_start(day: date | str) -> str:
    """Return the ``start_date_local`` wire value for a calendar day.

    intervals.icu parses this field as a ``LocalDateTime`` and rejects a
    bare ``"2026-08-10"`` with ``422 Invalid start date``, so every event
    we push is stamped at local midnight.

    Args:
        day: The calendar day, as a date or an ISO string.

    Returns:
        The ``YYYY-MM-DDT00:00:00`` wire value.
    """
    return f"{str(day)[:10]}T00:00:00"


def _diff_fields(pushed: dict[str, Any], remote: Any) -> tuple[str, ...]:
    """Return the subset of `_DIFF_FIELDS` that differ pushed-vs-remote.

    ``start_date_local`` is compared as a calendar date so that a
    midnight-stamped push still matches whatever time component
    intervals.icu echoes back.
    """
    changed = []
    for field in _DIFF_FIELDS:
        pushed_value = str(pushed.get(field, "") or "")
        remote_value = str(getattr(remote, field, "") or "")
        if field == "start_date_local":
            pushed_day, remote_day = _as_date(pushed_value), _as_date(remote_value)
            if pushed_day is not None and remote_day is not None:
                if pushed_day != remote_day:
                    changed.append(field)
                continue
        if pushed_value != remote_value:
            changed.append(field)
    return tuple(changed)


def _session_event(
    plan_id: int,
    session: SerializedSessionPayload,
    thresholds_by_sport: dict[SportType, SportThresholds],
) -> dict[str, Any]:
    """Build one WORKOUT event payload for a materialized session."""
    sport = SportType(session["sport"])
    thresholds = thresholds_by_sport[sport]
    workout = _structured_workout_from_payload(session["workout"])
    dsl_text = workout_to_dsl(workout, thresholds, sport)
    notes = "\n".join(
        _BOLD_TAG.sub("**", note) for note in session.get("coaching_notes", [])
    )
    paragraphs = [
        session["coaching_intent"].strip(),
        notes,
        *session.get("why_details", []),
        dsl_text,
    ]
    description = "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
    event: dict[str, Any] = {
        "category": "WORKOUT",
        "external_id": session_external_id(plan_id, str(session["slot_id"])),
        "start_date_local": event_start(session["date"]),
        "type": INTERVALS_TYPE_BY_SPORT[sport],
        "name": _event_title(session),
        "description": description,
        "tags": [EVENT_TAG],
    }
    if session["family"] == ActivityFamily.TEST:
        # A test's main effort is RPE-only: intervals.icu prices no load
        # from its steps, so the planned load goes with the event.
        event["icu_training_load"] = round(session["achieved_tss"])
    return event


def session_external_id(plan_id: int, slot_id: str) -> str:
    """Return the stable external_id of a plan session's calendar event.

    Args:
        plan_id: The plan the session belongs to.
        slot_id: The session's slot id.

    Returns:
        The ``coachie:{plan_id}:{slot_id}`` external_id.
    """
    return f"coachie:{plan_id}:{slot_id}"


def session_event_plan_id(external_id: str) -> int | None:
    """Return the plan a session event belongs to, or ``None`` for any other id.

    The inverse of :func:`session_external_id`: an ad-hoc, store or foreign
    id reads as ``None``.

    Args:
        external_id: An event's external_id.

    Returns:
        The ``plan_id`` of a ``coachie:{plan_id}:{slot_id}`` id, else ``None``.
    """
    parts = external_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "coachie" or not parts[1].isdigit():
        return None
    return int(parts[1])


#: The tag on every event Endury writes, shown as #endury in intervals.icu.
#: Nothing reads it back: events are found by their ``coachie:`` external
#: ids, which stay. Events pushed before 2026-10-21 carry ``coachie``
#: until their next push.
EVENT_TAG = "endury"

#: external_id prefix marking an ad-hoc (non-plan) scheduled workout — the
#: one marker by which readers (the calendar view) recognise these events.
AD_HOC_EXTERNAL_ID_PREFIX = "coachie:adhoc:"


def ad_hoc_external_id(day: date, token: str) -> str:
    """Return the external_id of one ad-hoc (non-plan) scheduled workout.

    The ``adhoc`` slot can never collide with `session_external_id` (always
    keyed on an integer ``plan_id`` in that position) or `race_external_id`
    (keyed on ``race``), so this event is structurally invisible to the
    plan-scoped session sync scan and readback diff (`ad_hoc_sync_states`
    reads it on its own terms). The token distinguishes
    two ad-hoc workouts scheduled for the same day rather than letting the
    second silently overwrite the first on upsert.

    Args:
        day: The workout's calendar date.
        token: A short random token unique to this push.

    Returns:
        The ``coachie:adhoc:{date}:{token}`` external_id.
    """
    return f"{AD_HOC_EXTERNAL_ID_PREFIX}{day.isoformat()}:{token}"


def ad_hoc_event(
    external_id: str,
    day: date | str,
    sport: SportType,
    name: str,
    description: str,
) -> dict[str, Any]:
    """Build one ad-hoc WORKOUT event payload, outside any plan.

    Same WORKOUT event shape `_session_event` builds for a plan session,
    but keyed on a caller-supplied external_id instead of a plan/slot pair
    — for a workout that is scheduled on its own date without occupying or
    replacing a planned session.

    Args:
        external_id: The event's external_id, e.g. from `ad_hoc_external_id`.
        day: Calendar day to schedule the workout on.
        sport: Sport of the workout, sets the intervals.icu activity type.
        name: Athlete-facing event title.
        description: Event description (typically coaching prose followed
            by the blank-line-separated workout DSL).

    Returns:
        Event dict ready for `IntervalsClient.bulk_upsert_events`.
    """
    return {
        "category": "WORKOUT",
        "external_id": external_id,
        "start_date_local": event_start(day),
        "type": INTERVALS_TYPE_BY_SPORT[sport],
        "name": name,
        "description": description,
        "tags": [EVENT_TAG],
    }


def race_external_id(plan_id: int, goal: RaceGoal) -> str:
    """Return the stable external_id of a plan's race event.

    Keyed on priority *and* date rather than list position: a plan may
    carry several B or C races, and dropping one must not silently remap
    the survivors onto each other's remote events.

    Args:
        plan_id: The plan the race belongs to.
        goal: The race goal.

    Returns:
        The ``coachie:{plan_id}:race:{priority}:{date}`` external_id.
    """
    return f"coachie:{plan_id}:race:{goal.priority}:{goal.race_date.isoformat()}"


def _race_event(plan_id: int, goal: RaceGoal) -> dict[str, Any]:
    """Build one RACE_A/RACE_B/RACE_C event payload for a plan goal.

    ``type`` is mandatory on a RACE_ event — omitting it rejects the whole
    push with ``422 type is required for category RACE_A``. Single-sport
    races carry their own activity type; multi-sport races take ``Other``,
    which is the neutral type rather than a mislabel: intervals.icu has no
    combined type, and both ``Triathlon`` and ``MultiSport`` come back as
    ``422 Invalid type``; the accepted types are
    ``Swim``/``Ride``/``Run``/``Other``/``Workout``.
    """
    label = goal.goal_type.value.replace("_", " ").title()
    return {
        "category": f"RACE_{goal.priority}",
        "external_id": race_external_id(plan_id, goal),
        "start_date_local": event_start(goal.race_date),
        "type": "Run" if goal.goal_type.value.startswith("running") else "Other",
        "name": f"{label} ({goal.priority}-Race)",
        "tags": [EVENT_TAG],
    }


def _event_title(session: SerializedSessionPayload) -> str:
    """Return the pushed event's title: the catalog workout display name.

    The serialized session payload already carries the athlete-facing
    display name at ``workout.name`` — the same one the app's UI renders —
    so the pushed event must show that title rather than re-deriving one
    from the activity_key. Falls back to `_session_title` only when the
    payload name is empty (older rows serialized before this field carried
    the display name).

    Args:
        session: The materialized session payload.

    Returns:
        The event title to push.
    """
    workout_name = session["workout"].get("name", "") or ""
    if workout_name.strip():
        return workout_name
    return _session_title(session["activity_key"], session["family"])


def _session_title(activity_key: str, family: str) -> str:
    """Derive an athlete-facing event title from activity_key/family."""
    stripped = _ACTIVITY_KEY_SPORT_PREFIX.sub("", activity_key)
    base = stripped or family
    return base.replace("_", " ").title()


def _structured_workout_from_payload(payload: WorkoutPayload) -> StructuredWorkout:
    """Reconstruct a `StructuredWorkout` from its flattened wire payload."""
    return StructuredWorkout(
        workout_name=payload.get("name"),
        steps=[_workout_step_from_payload(step) for step in payload["steps"]],
    )


def _workout_step_from_payload(step: WorkoutStepPayload) -> WorkoutStep:
    """Reconstruct one `WorkoutStep` from its flattened wire payload."""
    children = step.get("steps")
    additional = [
        target
        for target in (
            _target_def_from_payload(raw) for raw in step.get("additional_targets", [])
        )
        if target is not None
    ]
    return WorkoutStep(
        type=step["type"],
        duration=_duration_def_from_step(step),
        target=_target_def_from_payload(step.get("target")),
        secondary_target=_target_def_from_payload(step.get("secondary_target")),
        additional_targets=additional or None,
        iterations=step.get("iterations"),
        steps=[_workout_step_from_payload(child) for child in children]
        if children
        else None,
        description=step.get("description") or None,
    )


def _duration_def_from_step(step: WorkoutStepPayload) -> DurationDef | None:
    """Reconstruct a `DurationDef` from a step's flattened duration fields."""
    meters = step.get("duration_meters") or 0.0
    seconds = step.get("duration_seconds") or 0.0
    if meters:
        return DurationDef(
            type="distance", value_meters=meters, value_seconds=seconds or None
        )
    if seconds:
        return DurationDef(type="time", value_seconds=seconds)
    return None


def _target_def_from_payload(payload: WorkoutTargetPayload | None) -> TargetDef | None:
    """Reconstruct a `TargetDef` from its flattened wire payload."""
    if payload is None:
        return None
    return TargetDef(
        type=payload["target_type"],
        zone=payload.get("zone"),
        rpe=payload.get("rpe"),
        low_min_per_km=payload.get("low_min_per_km"),
        high_min_per_km=payload.get("high_min_per_km"),
        low_sec_per_100m=payload.get("low_sec_per_100m"),
        high_sec_per_100m=payload.get("high_sec_per_100m"),
        low_bpm=payload.get("low_bpm"),
        high_bpm=payload.get("high_bpm"),
        low_watts=payload.get("low_watts"),
        high_watts=payload.get("high_watts"),
        low_rpm=payload.get("low_rpm"),
        high_rpm=payload.get("high_rpm"),
        low_kph=payload.get("low_kph"),
        high_kph=payload.get("high_kph"),
    )


__all__ = [
    "AD_HOC_EXTERNAL_ID_PREFIX",
    "EventDiff",
    "ad_hoc_event",
    "ad_hoc_external_id",
    "plan_to_events",
    "race_external_id",
    "readback_diff",
    "stale_external_ids",
]
