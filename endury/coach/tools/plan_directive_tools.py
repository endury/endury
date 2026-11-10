"""Coach tool for persisting plan directives on an active plan.

``set_plan_directives`` is the surface for an already-active plan; a brand
new plan takes ``resume_date``/``suppress_tests_until`` directly as
``create_plan`` args. Both ultimately validate through
:func:`endury.planning.plan_directives.parse_directives` and are re-applied
by every future replan via the persisted request payload — the coach never
has to repeat a constraint.

An applied card persists the directives, replans from next Monday, and —
for a future ``resume_date`` — cuts the seven days from it through
:func:`~endury.planning.pause_training.trim_first_week`, the same first
week back ``pause_training`` builds. A refused replan puts the stored
request back: the plan and its directives never diverge.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.planning.pause_training import FIRST_WEEK_REDUCE_PERCENT, trim_first_week
from endury.planning.plan_directives import (
    PlanDirectives,
    apply_directives,
    parse_directives,
    parse_unavailable_arg,
)
from endury.planning.replan_plan import (
    _load_replan_inputs,
    replan_readback_diff,
    run_replan,
)
from endury.planning.replan_window import next_monday
from endury.planning.wire.types import SerializedSessionPayload
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)

#: An explicit clear value for a string-typed directive arg — distinct from
#: ``None``, which leaves that field untouched.
_CLEAR_VALUES = frozenset({"", "none"})

#: The one caveat every reply carries: directives never reach this week.
_CURRENT_WEEK_CAVEAT = (
    "Directives never change this week — pause_training and "
    "adjust_current_week own today through Sunday."
)

#: Relayed when the first week back could not be cut — same wording, and the
#: same "the replan still stands" reading, as ``pause_training``'s own.
_TRIM_FAILED = (
    "The plan was replanned, but the first week back could not be trimmed: "
    "{message} It stands at full plan volume."
)

#: Every arg left at its default: there is nothing to persist, and calling
#: this to replan alone is the wrong tool.
_NO_DIRECTIVE_GIVEN = (
    "Pass at least one directive to set — resume_date, suppress_tests_until, "
    "unavailable, or weekly_hours_cap. To replan with nothing new to say, "
    "call replan_active_plan instead."
)


def _merge_directive_payload(
    existing: dict[str, Any],
    *,
    resume_date: str | None,
    suppress_tests_until: str | None,
    unavailable: str | None,
    weekly_hours_cap: float | None,
) -> dict[str, Any] | str:
    """Merge new directive args onto the plan's stored directives.

    ``None`` leaves a field untouched. An explicit ``""`` or ``"none"``
    clears a string field (``resume_date``, ``suppress_tests_until``,
    ``unavailable``); ``weekly_hours_cap`` has no clear form — pass a new
    positive value to change it.
    """
    merged = dict(existing)
    for key, value in (
        ("resume_date", resume_date),
        ("suppress_tests_until", suppress_tests_until),
    ):
        if value is None:
            continue
        if value.strip().lower() in _CLEAR_VALUES:
            merged.pop(key, None)
        else:
            merged[key] = value
    if unavailable is not None:
        if (
            isinstance(unavailable, str)
            and unavailable.strip().lower() in _CLEAR_VALUES
        ):
            merged.pop("unavailable", None)
        else:
            parsed_unavailable = parse_unavailable_arg(unavailable)
            if isinstance(parsed_unavailable, str):
                return parsed_unavailable
            merged["unavailable"] = parsed_unavailable
    if weekly_hours_cap is not None:
        merged["weekly_hours_cap"] = weekly_hours_cap
    return merged


def _removed_sessions(
    repos: RepositoryContainer,
    plan_id: int,
    cutoff: date,
    merged_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """Which upcoming (>= cutoff) sessions the directives drop, and how many there are.

    The count is what tells a preview that a directive would empty the rest
    of the plan outright — a mistyped year, not a plan.
    """
    rows = [
        row
        for row in repos.plan.get_sessions(plan_id)
        if row.date >= cutoff.isoformat()
    ]
    minimal = cast(
        "list[SerializedSessionPayload]",
        [
            {"date": row.date, "activity_key": row.activity_key, "slot_id": row.slot_id}
            for row in rows
        ],
    )
    kept_slots = {
        session["slot_id"] for session in apply_directives(minimal, merged_payload)
    }
    return [
        {"date": row.date, "sport": row.sport, "activity_key": row.activity_key}
        for row in rows
        if row.slot_id not in kept_slots
    ], len(rows)


def _trim_week_back(
    repos: RepositoryContainer,
    parsed: PlanDirectives | None,
    cutoff: date,
    client: IntervalsClient | None,
) -> dict[str, Any] | None:
    """Cut the seven days from a future ``resume_date``, or ``None``.

    The athlete comes back onto the planner's own post-recovery ceiling
    rather than the ramp they walked away from — the exact cut
    ``pause_training`` makes, through the shared
    :func:`~endury.planning.pause_training.trim_first_week`. A
    ``resume_date`` already behind us is inert and trims nothing; the
    current week is refused before this ever runs.
    """
    if parsed is None or parsed.resume_date is None or parsed.resume_date < cutoff:
        return None
    return trim_first_week(repos=repos, resume=parsed.resume_date, client=client)


def _trim_caveats(block: dict[str, Any] | None) -> list[str]:
    """Relay the trim: its own refusal, its message, or a failed push."""
    if block is None:
        return []
    if block["status"] == "error":
        return [_TRIM_FAILED.format(message=block["message"])]
    caveats = [str(block["message"])]
    push = block["push"]
    if push is not None and push["status"] != "ok":
        caveats.append(str(push["message"]))
    return caveats


def make_plan_directive_tools(
    repos: RepositoryContainer,
    maybe_get_coach: Callable[[], Any],
    intervals_client: IntervalsClient | None = None,
) -> list:
    """Create the plan-directive coach tools with injected dependencies.

    Args:
        repos: Repository container (active plan, sessions).
        maybe_get_coach: Coach accessor for the plan-context refresh after a
            replan — mirrors ``replan_active_plan``'s own wiring.
        intervals_client: intervals.icu client used to read back manual
            calendar edits and push the replan (optional).

    Returns:
        List of plan-directive coach tools.
    """
    _repos = repos
    _client = intervals_client

    @coach_tool
    def set_plan_directives(
        resume_date: str | None = None,
        suppress_tests_until: str | None = None,
        unavailable: str | None = None,
        weekly_hours_cap: float | None = None,
        confirm: bool = False,
    ) -> str:
        """Persist directives on the active plan and replan from next Monday.

        This is the tool for a FUTURE away window that starts NEXT WEEK or
        later: "I'm away for work next month", a trip that has not started
        yet. pause_training always starts today and skips/replans right
        now, so it is the wrong tool for a window that has not begun; a
        window that starts later THIS week is neither tool's job — mark
        each of those days' sessions skipped one by one with
        skip_planned_session instead, since directives only take effect from
        the next replan (next Monday onward) and never touch this week.

        A directive is something you know that the planner does not:
        resume_date (sessions before it are dropped, e.g. "back from injury
        on the 12th", and the seven days from it come back off plan — the
        same first week back pause_training builds), suppress_tests_until
        (fitness-test sessions before it are dropped — never schedule a
        threshold test in an illness-return week), unavailable (inclusive
        date ranges dropped outright, e.g. a work trip), and
        weekly_hours_cap (a soft volume ceiling — the same field create_plan
        takes). Every directive is stored on the plan and re-applied by
        every future replan, so you only ever say it once.

        Requires an active plan — build one with create_plan first, or use
        its own resume_date/suppress_tests_until args for a brand new plan.
        Directives never touch this week: that window belongs to
        pause_training / adjust_current_week. A resume_date landing inside
        the current week is refused, pointing at pause_training; an
        unavailable range only ever removes sessions from next Monday's
        replan onward, so one dated entirely inside the current week
        changes nothing there — use skip_planned_session for those days.

        Each argument left as None keeps whatever is already stored; pass
        an explicit "" or "none" for resume_date/suppress_tests_until/
        unavailable to clear that one field (weekly_hours_cap has no clear
        form — set a new positive value to change it).

        unavailable is a JSON array of ["YYYY-MM-DD", "YYYY-MM-DD"]
        inclusive pairs, e.g. '[["2026-10-01", "2026-10-05"]]'. A parsed
        list is also accepted.

        Runs as a preview: the reply carries the directives before/after,
        which upcoming sessions would be dropped, and any intervals.icu
        edits the replan would overwrite.

        Reply contract — when relaying the result:
        * Preview: name every session in `removes` and quote
          `edited_workouts` verbatim when non-empty.
        * Relay every string in `caveats` verbatim.
        * Never say intervals.icu was updated unless `push.status` is "ok";
          when it is not, say the plan changed here but the calendar did
          not, and point at "Sync now" in the plan header.
        * Once applied, describe what changed from `reasoning`, not your
          own summary of the directives, and report the first week back
          from `first_week` when it is present.

        Args:
            resume_date: First day sessions may be materialized again,
                YYYY-MM-DD, or "" / "none" to clear it. The seven days from
                it are trimmed to the planner's post-recovery ceiling.
            suppress_tests_until: Fitness-test sessions before this date
                are dropped, YYYY-MM-DD, or "" / "none" to clear it.
            unavailable: JSON array of inclusive ["start", "end"] date-range
                pairs to drop outright, or "" / "none" to clear it.
            weekly_hours_cap: Soft weekly-hours ceiling; session floors
                still win over it. No clear form.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview (`status` "preview"), the replan
            outcome (`status` "ok", with the `first_week` trim block when a
            future resume_date is stored), or a refusal/error message.
        """
        try:
            if (
                resume_date is None
                and suppress_tests_until is None
                and unavailable is None
                and weekly_hours_cap is None
            ):
                return json.dumps({"status": "error", "message": _NO_DIRECTIVE_GIVEN})

            try:
                active, solve = _load_replan_inputs(_repos)
            except ValueError as exc:
                return json.dumps({"status": "error", "message": str(exc)})

            request_block = solve["request"]
            existing = request_block.get("directives") or {}
            merged = _merge_directive_payload(
                existing,
                resume_date=resume_date,
                suppress_tests_until=suppress_tests_until,
                unavailable=unavailable,
                weekly_hours_cap=weekly_hours_cap,
            )
            if isinstance(merged, str):
                return json.dumps({"status": "error", "message": merged})

            parsed = parse_directives(merged)
            if isinstance(parsed, str):
                return json.dumps({"status": "error", "message": parsed})

            today = clock.today()
            cutoff = next_monday(today)
            if (
                parsed is not None
                and parsed.resume_date is not None
                and today <= parsed.resume_date < cutoff
            ):
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"{parsed.resume_date.isoformat()} is inside the "
                            "current week — pause_training owns today through "
                            "Sunday; use it for this week instead."
                        ),
                    }
                )

            if not confirm:
                removed, upcoming = _removed_sessions(_repos, active.id, cutoff, merged)
                diff = replan_readback_diff(repos=_repos, client=_client)
                caveats = [_CURRENT_WEEK_CAVEAT]
                if removed and len(removed) == upcoming:
                    caveats.append(
                        "That is every session from next Monday to race day — "
                        "the plan would be left empty. Check the dates before "
                        "applying."
                    )
                if parsed is not None and parsed.resume_date is not None:
                    if parsed.resume_date >= cutoff:
                        caveats.append(
                            "The seven days from "
                            f"{parsed.resume_date.isoformat()} come back "
                            f"{FIRST_WEEK_REDUCE_PERCENT}% off plan — the same "
                            "first week back pause_training builds."
                        )
                    else:
                        caveats.append(
                            f"{parsed.resume_date.isoformat()} is already "
                            "behind us, so that directive changes nothing."
                        )
                if diff:
                    caveats.append(
                        f"{len(diff)} upcoming workout(s) were edited on "
                        "intervals.icu and would be overwritten."
                    )
                return json.dumps(
                    {
                        "status": "preview",
                        "confirm_required": True,
                        "directives": {"before": existing, "after": merged},
                        "removes": removed,
                        "edited_workouts": diff,
                        "caveats": caveats,
                        "message": (
                            "Nothing has changed yet — relay this preview; "
                            "the athlete applies it from its card."
                        ),
                    },
                    indent=2,
                )

            stored_before = json.dumps(solve)
            if weekly_hours_cap is not None:
                request_block["weekly_hours_cap"] = weekly_hours_cap
            if merged:
                request_block["directives"] = merged
            else:
                request_block.pop("directives", None)
            _repos.plan.update_solve_result_json(active.id, json.dumps(solve))

            try:
                result = run_replan(
                    repos=_repos,
                    progress=lambda _payload: None,
                    maybe_get_coach=maybe_get_coach,
                    client=_client,
                )
            except ValueError as exc:
                # The replan refused (race week, a passed goal, an engine the
                # plan predates). Put the stored request back: directives that
                # no plan was ever regenerated around would silently reshape
                # the next replan the athlete never asked for.
                _repos.plan.update_solve_result_json(active.id, stored_before)
                return json.dumps({"status": "error", "message": str(exc)})

            first_week = _trim_week_back(_repos, parsed, cutoff, _client)
            return json.dumps(
                {
                    "status": result["status"],
                    "action": result["action"],
                    "reasoning": result["reasoning"],
                    "push": result["push"],
                    "directives": merged,
                    "first_week": first_week,
                    "caveats": [_CURRENT_WEEK_CAVEAT, *_trim_caveats(first_week)],
                },
                indent=2,
            )
        except Exception as exc:
            return tool_error_response("set the plan's directives", exc)

    return [set_plan_directives]


__all__ = ["make_plan_directive_tools"]
