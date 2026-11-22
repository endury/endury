"""Plan-editing coach tools: the write half of the planner tools.

Mark a session's status, move a session to another date, replan from next
Monday, rescale the rest of the current week, pause training through a date
the athlete is out, swap one session's workout for an ad-hoc one, and put a
single ad-hoc workout on the calendar (or take one back off). The tools with
a ``confirm`` parameter only ever preview inside a turn; the athlete applies
them from a card (:mod:`endury.coach.proposals`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.intervals.client import IntervalsClient
from endury.planning.adjust_week import (
    # Aliased: the coach tool below owns the bare name.
    adjust_current_week as run_week_adjustment,
)
from endury.planning.commit_plan import (
    reschedule_session,
)
from endury.planning.pair_activity import name_mismatch
from endury.planning.pause_training import (
    # Aliased: the coach tool below owns the bare name.
    pause_training as run_pause_training,
)
from endury.planning.replan_plan import replan_readback_diff, run_replan
from endury.planning.schedule_workout import (
    # Aliased: the coach tool below owns the bare name.
    schedule_workout as run_schedule_workout,
)
from endury.planning.session_status import (
    # Aliased: the coach tool below owns the bare name.
    set_session_status as run_set_session_status,
)
from endury.planning.session_status import skip_session as run_skip_session
from endury.planning.swap_session import (
    # Aliased: the coach tool below owns the bare name.
    swap_planned_session as run_session_swap,
)
from endury.planning.unschedule_workout import (
    # Aliased: the coach tool below owns the bare name.
    unschedule_workout as run_unschedule_workout,
)
from endury.planning.wire.requests import ScheduleRequest
from endury.wiring.container import RepositoryContainer


def make_plan_edit_tools(
    repos: RepositoryContainer,
    maybe_get_coach: Callable[[], Any],
    intervals_client: IntervalsClient | None = None,
) -> list:
    """Create the plan-editing coach tools with injected dependencies.

    Args:
        repos: Repository container (active plan and its sessions).
        maybe_get_coach: Accessor returning the live coach session (or None),
            used to refresh the coach's plan-context summary after a write.
        intervals_client: intervals.icu client the writes push through
            (optional — without it they are stored but not pushed).

    Returns:
        List of plan-editing coach tools.
    """
    _repos = repos
    _client = intervals_client

    @coach_tool
    def set_session_status(
        session_id: int, status: str, activity_id: str | None = None
    ) -> str:
        """Mark a planned session's status, optionally linking an activity.

        Use the ``session_id`` reported by ``get_planned_sessions``. ``status``
        must be one of "completed", "pending" or "unlinked". Pass
        ``activity_id`` to link the intervals.icu activity that fulfilled the
        session. Omitting ``activity_id`` keeps any existing link (it does not
        unlink).

        An athlete who did a session on another day than planned — early or
        late, "I ran Sunday's long run today" — links it here: "completed"
        with the activity moves the session onto the activity's day, takes
        the activity off any session it completed before, and pairs it on
        intervals.icu. A coming session is no obstacle. Link in the same turn
        they ask; when two sessions fit their words, ask which with two
        dated replies.

        A skip is never this tool: ``skip_planned_session`` previews it as a
        card. Marking a skipped session "pending" or "unlinked" puts its
        workout back on the intervals.icu calendar — only when the athlete
        asks for that session back.

        Use "unlinked" to clear a wrong activity match: the link is dropped,
        the session reads as not-done again (missed if its date has passed),
        it can be rescheduled, and the automatic intervals.icu matching will
        not silently re-link it.

        When the result's ``push`` block reports anything but "ok", relay its
        ``message`` — the plan changed here, the calendar did not.

        Args:
            session_id: The plan session's id (from ``get_planned_sessions``).
            status: One of "completed", "pending", "unlinked".
            activity_id: Optional intervals.icu activity id to link as the
                fulfilling activity. Ignored when status is "unlinked".

        Returns:
            JSON string confirming the update, or a validation/error message.
        """
        if status == "skipped":
            return json.dumps(
                {
                    "error": (
                        "A skip is previewed as a card: call "
                        "skip_planned_session with this session_id."
                    )
                }
            )
        try:
            if status == "completed" and activity_id and _client is not None:
                refusal = name_mismatch(_repos, _client, activity_id, session_id)
                if refusal is not None:
                    return json.dumps({"error": refusal})
            return json.dumps(
                run_set_session_status(
                    repos=_repos,
                    client=_client,
                    session_id=session_id,
                    status=status,
                    activity_id=activity_id,
                )
            )
        except Exception as exc:
            return tool_error_response("update session status", exc)

    @coach_tool
    def skip_planned_session(session_id: int, confirm: bool = False) -> str:
        """Take one planned session out of the plan and off the calendar.

        Use when the athlete will not do a session, or when you recommend
        dropping it (a race week, a niggle, a day that already held the
        work). Runs as a preview: the athlete gets a card and nothing
        changes until they apply it; applying removes the workout from
        their intervals.icu calendar, so their watch stops asking for it.
        One call per session, one card each. For a stretch of days off —
        illness, travel — use ``pause_training`` instead.

        Reply contract — when relaying the result:
        * Say which session the card skips, by its day and name; never say
          it is skipped before the athlete applies it.
        * When ``status`` is "refused", relay ``message`` and offer nothing
          in its place.

        Args:
            session_id: The plan session's id (from ``get_planned_sessions``).
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview, the applied skip, or a refusal.
        """
        try:
            result = run_skip_session(
                repos=_repos, client=_client, session_id=session_id, confirm=confirm
            )
            return json.dumps(result)
        except Exception as exc:
            return tool_error_response("skip the session", exc)

    @coach_tool
    def move_planned_session(session_id: int, date: str) -> str:
        """Move a planned session to another date.

        Same action as dragging the session in the Schedule calendar: only
        this session moves, the rest of the plan is untouched, and the
        session's intervals.icu calendar event is re-dated with it. Use the
        ``session_id`` reported by ``get_planned_sessions``.

        The date may be a day that has passed: a session the athlete did on
        another day goes back to that day, where its activity pairs with it.
        To switch two sessions, move each to the other's date, the one whose
        target day has passed included.

        The move is refused with a reason when the date is outside the plan
        window, on race day, or when the session is already completed —
        relay that reason to the athlete. The completed refusal is the one
        you can act on: if the athlete says the linked activity is the wrong
        match, call ``set_session_status(session_id, "unlinked")``
        to drop the link, then move the session. When the result carries a
        ``warning`` (the day is now double-booked, or the intervals.icu
        re-date failed), relay it verbatim.

        Args:
            session_id: The plan session's id (from ``get_planned_sessions``).
            date: The new date in YYYY-MM-DD format.

        Returns:
            JSON string with the move outcome, or a refusal message.
        """
        try:
            result = reschedule_session(
                session_id,
                ScheduleRequest(date=date),
                get_repos=lambda: _repos,
                client=_client,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("move the planned session", exc)

    @coach_tool
    def replan_active_plan(confirm: bool = False) -> str:
        """Regenerate the active plan's upcoming sessions from next Monday.

        Deterministic replan (no LLM): keeps the current week's sessions
        unchanged and regenerates everything from next Monday onward using
        the athlete's latest training data — the same action as the
        Schedule tab's Replan button. Runs as a preview, which also reports
        any upcoming workouts the athlete edited on intervals.icu that the
        replan would overwrite — relay those alongside the card.

        Args:
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string with the replan outcome and reasoning, the preview
            (``status`` "preview") when not applied, or an error message
            (e.g. no active plan, or the solver could not find a feasible
            plan).
        """
        if not confirm:
            diff = replan_readback_diff(repos=_repos, client=_client)
            message = (
                "Nothing has changed yet. Replanning regenerates upcoming "
                "sessions from next Monday; the athlete applies it from "
                "its card."
            )
            if diff:
                message += (
                    f" {len(diff)} upcoming workout(s) were edited on "
                    "intervals.icu and would be overwritten."
                )
            return json.dumps(
                {
                    "status": "preview",
                    "confirm_required": True,
                    "message": message,
                    "edited_workouts": diff,
                }
            )
        try:
            result = run_replan(
                repos=_repos,
                progress=lambda _payload: None,
                maybe_get_coach=maybe_get_coach,
                client=_client,
            )
            return json.dumps(
                {
                    "status": result["status"],
                    "action": result["action"],
                    "reasoning": result["reasoning"],
                },
                indent=2,
            )
        except ValueError as exc:
            # run_replan's ValueErrors are curated, athlete-facing reasons
            # (race passed, race week, block ended, infeasible solve) — the
            # same text the app's replan button shows.
            return json.dumps({"status": "error", "message": str(exc)})
        except Exception as exc:
            return tool_error_response("replan the active training plan", exc)

    @coach_tool
    def adjust_current_week(
        reduce_percent: int | None = None,
        confirm: bool = False,
    ) -> str:
        """Shorten the rest of this week's sessions (today through Sunday).

        Use when the athlete is wrecked or asks to back off for the rest of
        the week: "reduce this week by 30%" is reduce_percent=30. Every
        adjustable session from today through Sunday is re-prescribed
        shorter through the planner's own workout builder and re-pushed to
        intervals.icu; the rest of the plan is untouched.

        Not for illness or injury — an athlete who cannot train through a
        date wants `pause_training`; this tool shortens sessions the athlete
        will still do.

        reduce_percent=0 restores this week to its originally prescribed
        sizes — use it when the athlete recovers mid-week.

        Runs as a preview: the reply carries the exact per-session
        before/after.

        Reply contract — when relaying the result:
        * Quote ``totals`` and ``totals.actual_percent`` (the realized
          reduction). When it differs from the requested percent, quote the
          realized one — never the request. Never re-add the session list
          by hand.
        * Name every session in ``unchanged`` and ``untouched`` with its
          reason. Silence about a session that did not shrink is the
          failure mode.
        * Relay every string in ``caveats`` verbatim.
        * Sessions get SHORTER, not EASIER — a trimmed VO2max session is
          still a VO2max session. For a session the athlete should not do
          at all, offer skip_planned_session(session_id).
        * Never say intervals.icu was updated unless ``push.status`` is
          "ok"; when it is not, say the plan changed here but the calendar
          did not, and point at "Sync now" in the plan header.

        Args:
            reduce_percent: Whole percent to cut, 0-60. 0 restores the
                original prescription.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview (``status`` "preview"), the applied
            outcome (``status`` "ok"), ``status`` "empty" when nothing in
            the window is adjustable, or an error/refusal message.
        """
        try:
            result = run_week_adjustment(
                repos=_repos,
                reduce_percent=reduce_percent,
                confirm=confirm,
                client=_client,
                maybe_get_coach=maybe_get_coach,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("adjust the current training week", exc)

    @coach_tool
    def pause_training(
        through_date: str, from_date: str | None = None, confirm: bool = False
    ) -> str:
        """Pause training through a date the athlete cannot train to.

        Use whenever the athlete is sick, injured or away "until X". With an
        active plan, one call does the whole job: every still-pending
        session from today through through_date (inclusive) is marked
        skipped and removed from their intervals.icu calendar, the plan is
        regenerated from the day after — landing mid-week is fine — the
        first seven days back are trimmed to the planner's own post-recovery
        ceiling, and any of your own coach-scheduled ad-hoc workouts from
        tomorrow through through_date come off the calendar too (today's own
        booking is left alone). A preview NAMES those workouts before
        applying removes them — never let the athlete apply a pause
        without seeing that list. A window with no plan session pending
        still previews/removes them: only when nothing is pending AND
        nothing is booked does the result come back "empty" — and even then
        an applied pause records the window as unavailable on the plan, so
        nothing gets scheduled back into it.

        With no active plan this still works: there is nothing to skip or
        replan, so it just clears your own coach-scheduled ad-hoc workouts
        in [today, through_date] and the result says plainly that nothing
        was replanned. Record the constraint yourself with update_memory,
        including a "- Next check-in YYYY-MM-DD: <why>" bullet.
        unschedule_workout does the same ad-hoc removal on its own, without
        the pause framing, when that is all that is needed.

        This ALWAYS starts TODAY. Pass from_date as the day the athlete
        actually goes out, even when that is not today ("I'm away August 27
        to 29" said on the 19th passes from_date="2026-08-27") — from_date
        omitted or equal to today runs the pause as usual; anything later is
        refused, writing nothing, with a curated redirect naming
        set_plan_directives(unavailable=[[start, end]]) (a week or more out)
        or skip_planned_session (later this same week)
        instead of silently pausing from today. Relay that refusal and call
        the tool it names — never retry this one with a different
        through_date to compensate.

        Do NOT mark those sessions skipped one by one with
        set_session_status when the window starts today. Do NOT use
        adjust_current_week — that shortens sessions the athlete will still
        do. Do NOT use replan_active_plan — it cuts from next Monday and
        skips nothing. And never tell the athlete to replan themselves from
        the Schedule tab; there is no pause control there.

        Resolve "until Wednesday" against the current date in your context
        and pass an absolute date. The pause includes through_date.

        Runs as a preview.

        Reply contract — when relaying the result:
        * ``no_plan`` true means there is no active plan: say so plainly,
          that nothing was replanned and no plan sessions were skipped, and
          record the constraint plus a "- Next check-in" bullet via
          update_memory this turn.
        * Otherwise name every session in ``sessions`` and in ``untouched``,
          each with its reason. Silence about a session is the failure mode.
        * When ``first_week`` is not null, state ``resume_date`` and that
          the first week back is reduced, quoting
          ``first_week.totals.actual_percent`` — the realized cut, never
          the requested 40. Null means nothing was replanned: say that
          instead.
        * ``ad_hoc``, when not null, names every workout it removed
          (``status`` "ok") or will remove on apply (``status``
          "preview") — relay that list either way, before the athlete
          applies, not just after.
        * Relay every string in ``caveats`` verbatim.
        * Never say intervals.icu was updated unless ``push.status`` AND
          ``replan.push.status`` are both "ok"; when either is not, say the
          plan changed here but the calendar did not, and point at "Sync
          now" in the plan header.
        * ``replan.status`` "error" means the sessions are skipped but the
          plan was NOT replanned. Say so plainly.
        * An athlete who tells you they are ill or hurt gets care, not
          intake: no profile or experience-level questions on this turn.

        Args:
            through_date: Last day the athlete is out, YYYY-MM-DD
                (inclusive).
            from_date: first day the athlete is out; omit when they are out
                from today.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview (``status`` "preview"), the applied
            outcome (``status`` "ok"), ``status`` "empty" only when neither a
            plan session nor a booked workout is in the window, a curated
            ``status`` "refused" naming ``suggested_tool`` when from_date is
            later than today, or an error message.
        """
        try:
            result = run_pause_training(
                repos=_repos,
                through_date=through_date,
                from_date=from_date,
                confirm=confirm,
                client=_client,
                maybe_get_coach=maybe_get_coach,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("pause training", exc)

    @coach_tool
    def swap_planned_session(
        session_id: int,
        sport: str | None = None,
        intent: str | None = None,
        activity_key: str | None = None,
        duration_minutes: float | None = None,
        date: str | None = None,
        confirm: bool = False,
        allow_protected: bool = False,
    ) -> str:
        """Replace ONE planned session's workout, keeping it in the plan.

        Use when the athlete wants a different workout on a planned day and
        wants it to COUNT: "swap tomorrow's easy run for a threshold
        session". The session keeps its place in the plan; its sport,
        workout, duration and load are replaced, and its intervals.icu
        calendar event is re-pushed so the new workout lands on their
        watch. This is the tool that makes a ``build_workout`` result real
        — ``build_workout`` alone saves nothing.

        It keeps the session's day unless you pass ``date``. Do pass it
        when the athlete names a day the session is not on ("give me an
        easy 30 on Thursday" while the only pending session is Friday):
        the preview then shows both days and applying it moves and
        replaces the session in one write, under the same calendar event.
        ``date`` must stay inside the session's own week — for a day in
        another week, move the session with ``move_planned_session``
        first.

        The replacement is selected exactly like ``build_workout``:
        ``sport`` + ``intent``, or an exact ``activity_key``. Both ``sport``
        and ``duration_minutes`` default to the session's own, so "same
        length, but make it a threshold run" needs only intent.

        Runs as a preview: the reply carries the current session, the
        proposed one, and the week's load before and after.

        Refused, with a reason to relay, when the session is already done
        or matched to an activity (offer set_session_status "unlinked" or
        skip_planned_session), when its date has passed, or when it sits in a taper or
        race week. Only pass allow_protected=true after the athlete has
        heard that refusal and still wants the swap. A session the athlete
        had marked "skipped" or "unlinked" can still be swapped, and the
        swap makes it live again — a caveat says so; relay it.

        Reply contract — when relaying the result:
        * Quote ``week`` — before/after TSS and minutes and both percent
          deltas — as given. Never re-add the week yourself.
        * Quote the replacement's ``duration_minutes`` and ``achieved_tss``,
          not the ones that were requested.
        * Relay every string in ``caveats`` verbatim, in prose, the
          protected-week one (it comes first) first and loudest. Quote both
          percentages of a "moves the week by" caveat before they apply it.
        * This is a deviation from a periodized plan, not an improvement to
          it. Say what the swapped-out session was for.
        * Never say intervals.icu was updated unless ``push.status`` is
          "ok"; when it is not, say the plan changed here but the calendar
          did not, and point at "Sync now" in the plan header.

        Args:
            session_id: The plan session's id (from ``get_planned_sessions``).
            sport: Replacement sport ("running", "cycling", "swimming").
                Defaults to the session's own sport.
            intent: Replacement flavor, as for ``build_workout``.
            activity_key: Optional exact planner activity key (e.g.
                "run_threshold") instead of sport+intent.
            duration_minutes: Replacement duration. Defaults to the
                session's current duration.
            date: YYYY-MM-DD to move the session to as part of the same
                swap. Defaults to the session's own day. Must be in the
                session's week, not in the past, inside the plan window,
                and not race day.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.
            allow_protected: Must be True to swap a session in a taper or
                race week. Defaults to False.

        Returns:
            JSON string: the preview (``status`` "preview"), the applied
            outcome (``status`` "ok"), or a refusal message.
        """
        try:
            result = run_session_swap(
                repos=_repos,
                session_id=session_id,
                sport=sport,
                intent=intent,
                activity_key=activity_key,
                duration_minutes=duration_minutes,
                date=date,
                confirm=confirm,
                allow_protected=allow_protected,
                client=_client,
                maybe_get_coach=maybe_get_coach,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("swap the planned session", exc)

    @coach_tool
    def schedule_workout(
        duration_minutes: float,
        date: str,
        sport: str | None = None,
        intent: str | None = None,
        activity_key: str | None = None,
    ) -> str:
        """Schedule ONE ad-hoc structured workout on a date. Books it at once.

        Use when the athlete wants a specific workout on a date that is NOT
        one of the plan's own sessions ("bike today", "threshold run
        Thursday"). Call it straight away — do NOT ``build_workout`` first
        and do NOT ask permission: this books immediately, and adding a
        session is cheap and reversible. ``build_workout`` is for a workout
        with no date ("what would a threshold run look like"); it puts
        nothing on the athlete's device.

        Never tell the athlete a workout is scheduled or "locked in" unless
        this tool returned status "ok" this turn.

        With an active plan covering the date the workout JOINS THE PLAN as
        an extra session (``plan_row`` in the reply), so it counts towards
        the week, compliance, ``adjust_current_week`` and ``pause_training``
        — and it defaults to easy/endurance/recovery/strides, an easy trail
        run included. A harder one (threshold/vo2/long/brick/race_pace, or a
        trail run past about 75 minutes) is refused, and so is a taper/race
        week or an extra that takes the week past its TSS
        allowance; a date inside a stored unavailable window is refused
        outright, naming it. With no plan, or a date outside it, the workout
        is a standalone calendar entry as before.

        The workout is selected exactly like ``build_workout``: ``sport`` +
        ``intent``, or an exact ``activity_key``.

        Those refusals are the only "ask first" left: when one comes back,
        relay it and let the athlete decide. Only if they still want the hard
        or taper-week session is it ``schedule_workout_override`` — which
        shows as a card and books only when the athlete applies it.
        Undoing a booking is one call — ``unschedule_workout`` for a
        standalone one, ``skip_planned_session`` for a plan
        extra — so say it can come back off.

        Refused when ``date`` is in the past. When the plan already has a
        session that day, the reply carries a ``plan_collision`` naming it:
        this adds a second session that day rather than replacing it — use
        ``swap_planned_session`` for that.

        Reply contract — when relaying the result:
        * Relay every string in ``caveats`` verbatim, especially a
          ``plan_collision`` caveat — the athlete needs to know they now
          have two things on that day — and, for a hard extra, the "did not
          budget" caveat that comes first.
        * A refusal over the week's room: say in plain words that the week
          has no room for more, without TSS figures, and offer the move it
          names. Never book the workout shorter to fit.
        * When ``plan_row`` is present, quote the week's before/after TSS
          from ``week`` — the week just got bigger — and say the session is
          part of the plan now.
        * status "ok" is the only status that means something was
          scheduled; quote the date, never say "scheduled" for any other
          status. Without ``plan_row``, "ok" also means intervals.icu has
          it; with ``plan_row``, say the calendar is out of step whenever
          ``push.status`` is not "ok".
        * Quote the workout's ``duration_minutes`` and ``achieved_tss`` as
          given, not the requested ones.
        * ``event_id`` and ``external_id`` are internal plumbing — never
          quote either one to the athlete.

        Args:
            duration_minutes: Target duration in minutes.
            date: Date to schedule the workout on, in YYYY-MM-DD format.
            sport: Sport for the workout ("running", "cycling", "swimming").
                Ignored when activity_key is given.
            intent: Workout flavor, as for ``build_workout``. Ignored when
                activity_key is given.
            activity_key: Optional exact planner activity key (e.g.
                "run_threshold") instead of sport+intent.

        Returns:
            JSON string: the scheduled outcome (``status`` "ok"), or a
            refusal/failure message.
        """
        try:
            result = run_schedule_workout(
                repos=_repos,
                duration_minutes=duration_minutes,
                date_str=date,
                sport=sport,
                intent=intent,
                activity_key=activity_key,
                client=_client,
                maybe_get_coach=maybe_get_coach,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("schedule the workout", exc)

    @coach_tool
    def schedule_workout_override(
        duration_minutes: float,
        date: str,
        sport: str | None = None,
        intent: str | None = None,
        activity_key: str | None = None,
        allow_protected: bool = False,
        allow_hard: bool = False,
        confirm: bool = False,
    ) -> str:
        """Book a hard or taper-week extra that ``schedule_workout`` refused.

        Use ONLY after ``schedule_workout`` refused the booking as a hard
        extra (threshold/vo2/long/brick/race_pace or a long trail run —
        ``allow_hard=true``) or as landing in a taper/race week
        (``allow_protected=true``), and the
        athlete still wants it. Both add load the plan did not budget, so
        this one runs as a preview — quote its workout and every caveat. A
        hard extra is still refused within a day of another hard or long
        plan session.

        Everything else — workout selection, ``plan_row``/``week`` reporting,
        status "ok" as the only "scheduled" — is exactly
        ``schedule_workout``'s contract, and so is this:
        ``event_id`` and ``external_id`` are internal plumbing — never quote
        either one to the athlete.

        Args:
            duration_minutes: Target duration in minutes.
            date: Date to schedule the workout on, in YYYY-MM-DD format.
            sport: Sport, as for ``schedule_workout``.
            intent: Workout flavor, as for ``build_workout``.
            activity_key: Optional exact planner activity key instead of
                sport+intent.
            allow_protected: True adds the extra to a plan's taper or race
                week anyway.
            allow_hard: True allows a hard-family or long extra inside a
                plan.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview (``status`` "preview"), the scheduled
            outcome (``status`` "ok"), or a refusal/failure message.
        """
        try:
            result = run_schedule_workout(
                repos=_repos,
                duration_minutes=duration_minutes,
                date_str=date,
                sport=sport,
                intent=intent,
                activity_key=activity_key,
                allow_protected=allow_protected,
                allow_hard=allow_hard,
                preview=not confirm,
                client=_client,
                maybe_get_coach=maybe_get_coach,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("schedule the workout", exc)

    @coach_tool
    def unschedule_workout(
        date: str,
        through_date: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Remove coach-scheduled ad-hoc workout(s) from the intervals.icu calendar.

        Use when a workout that YOU put on the calendar — via this tool or
        ``schedule_workout`` — needs to come back off: "cancel Thursday's
        run", "clear anything you scheduled while I'm out sick". It can
        only reach coach-scheduled ad-hoc events, keyed by their own
        external_id scheme: a planned session is structurally out of
        reach here, so it never touches the plan and needs no active plan.
        To take a planned session off the athlete's calendar use
        ``skip_planned_session`` instead, or ``pause_training``
        for a whole window.

        Looks on ``date`` alone, or through ``through_date`` inclusive for a
        range — every matching workout in the window is removed together.

        Runs as a preview: the reply lists every matching workout.

        Reply contract — when relaying the result:
        * status "not_found" means nothing matched — say so plainly, never
          imply a workout was ever scheduled there.
        * status "preview" means nothing is removed yet — name every
          workout in ``workouts`` (its date and name); the card does the
          asking.
        * status "ok" is the only status that means something actually
          left the calendar — never say removed/cleared/cancelled for any
          other status.
        * status "error" means nothing was removed; relay ``message``
          verbatim.

        Args:
            date: First date to look on, YYYY-MM-DD.
            through_date: Last date to look through (inclusive), or None to
                look on date alone. Defaults to None.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: ``status`` "not_found", the preview (``status``
            "preview"), the applied outcome (``status`` "ok"), or an error.
        """
        try:
            result = run_unschedule_workout(
                date_str=date,
                through_str=through_date,
                confirm=confirm,
                client=_client,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("unschedule the workout", exc)

    return [
        set_session_status,
        skip_planned_session,
        move_planned_session,
        replan_active_plan,
        adjust_current_week,
        pause_training,
        swap_planned_session,
        schedule_workout,
        schedule_workout_override,
        unschedule_workout,
    ]
