"""Coach tools for building a real training plan.

``create_plan`` is the coach's front door onto the same run_generate /
run_commit pipeline the plan wizard drives by hand — no second planning
path exists. It previews; the athlete applies the preview from its card.
``record_self_assessment`` is the fallback when create_plan refuses with
``athlete_data_required``: the same questionnaire the wizard's onboarding
step asks, one sport per call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.intervals.client import IntervalsClient
from endury.planning.create_plan import create_plan as run_create_plan
from endury.planning.wire.requests import (
    _SELF_ASSESSMENT_SPORTS,
    SelfAssessmentEntry,
)
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)


def _parse_races(races: Any) -> list[Any] | dict[str, Any]:
    """Tolerantly parse ``races``: a JSON string, or an already-parsed list.

    Only the wire-level concern (is this a JSON array at all) lives here;
    :func:`endury.planning.create_plan.create_plan` validates the parsed
    shape. Anything that is not a list refuses right here — a bare race
    object is the mistake a model actually makes, and it must not survive
    as a dict the caller would mistake for this function's error shape.
    """
    if isinstance(races, list):
        return races
    try:
        parsed: Any = json.loads(races)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    return {
        "status": "error",
        "message": (
            "`races` must be a JSON array of "
            '{"goal_type", "race_date", "priority"}, e.g. '
            '[{"goal_type": "running_marathon", '
            '"race_date": "2026-11-01", "priority": "A"}].'
        ),
    }


def make_plan_build_tools(
    repos: RepositoryContainer,
    maybe_get_coach: Callable[[], Any],
    intervals_client: IntervalsClient | None = None,
) -> list:
    """Create the plan-building coach tools with injected dependencies.

    Args:
        repos: Repository container (active plan, self-assessment).
        maybe_get_coach: Coach accessor for the plan-context refresh after a
            commit — mirrors the wizard's own commit.
        intervals_client: intervals.icu client used to push a committed
            plan's calendar events (optional — a commit without one is
            still persisted, just not pushed).

    Returns:
        List of plan-building coach tools.
    """
    _repos = repos
    _client = intervals_client

    @coach_tool
    def create_plan(
        races: str,
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
    ) -> str:
        """Build a real, persisted training plan — the athlete's own pipeline.

        This is how you actually build a plan: the wizard's exact
        deterministic pipeline (phase skeleton -> session menus -> macro QP
        -> day assignment), never prose periodization. When there is no
        active plan and the athlete wants a season, a next block, or an
        answer to "what should I follow next?", call this instead of
        describing weeks or phases yourself.

        ``races`` is a JSON array of race objects, e.g. ``[{"goal_type":
        "trail_50k", "race_date": "2026-10-24", "priority": "A"},
        {"goal_type": "running_half_marathon", "race_date": "2026-09-27",
        "priority": "C"}]``. Exactly one race must be priority "A" — the
        goal the plan is periodized around; "B"/"C" are tune-up races the
        plan builds around without disrupting. ``goal_type`` is one of:
        running_5k, running_10k, running_half_marathon, running_marathon,
        trail_20k, trail_50k, trail_100k, sprint_triathlon,
        olympic_triathlon, half_ironman, ironman, running_maintenance,
        trail_maintenance, triathlon_maintenance (the last three take a
        block length instead of a race date — pass a ``race_date`` marking
        the block's end). The A race and ``ambition_level`` must be a plan
        on offer (named in your app knowledge), or the call is refused; a
        B or C race may be of any type.

        ``training_start`` must be a Monday (defaults to this week's Monday
        up to Tuesday, next Monday from Wednesday on); a non-Monday is
        refused, naming the Monday to use.

        ``resume_date``, ``suppress_tests_until`` and ``unavailable`` are
        directives — what you know and the planner does not. Sessions dated
        before ``resume_date`` are never built (the athlete is ill, injured
        or away until then), fitness-test sessions before
        ``suppress_tests_until`` are dropped (never a threshold test in an
        illness-return week), and dates inside an ``unavailable`` range are
        dropped outright. All three are stored on the plan, so every future
        replan re-applies them; set_plan_directives edits them and trims the
        week back once the plan exists — a new plan is sized from today's
        fitness, so nothing extra is cut here. Directives that would leave
        the plan with no sessions at all are refused.

        Runs as a preview: the pipeline runs for real and the reply carries
        a LEAN preview — phase runs with dates, per-week hours/TSS with the
        per-sport split, and only the first 10 days of sessions, never the
        full plan. The pipeline is deterministic, so
        what gets committed is exactly what was previewed, unless the
        athlete's intervals.icu training data changed underneath in the
        meantime, in which case nothing is created and a fresh card comes
        back instead.

        An existing active plan refuses unless replace_active=true; the
        preview and the refusal both name what would be replaced under
        ``replaces``.

        Refusals name what to do next: error_code "athlete_data_required"
        means intervals.icu history can't measure a goal sport — relay
        ``missing_sports`` and use record_self_assessment, then retry.
        error_code "horizon_too_short" means the A race is closer than its
        goal's minimum preparation — relay
        ``min_weeks``/``actual_weeks``/``deficit_weeks``, and only pass
        accept_short_horizon=true after the athlete agrees to join an
        established preparation part-way through. error_code
        "trail_band_mismatch" means a trail race's stored distance_km/climb_m
        (from upsert_race or the athlete's own record) computes a different
        band than the goal_type requested here — relay ``message`` verbatim
        (it names the km-effort and the suggested band) and either call
        again with the ``suggested_goal_type`` it names, or with
        accept_band_mismatch=true once the athlete explicitly wants the
        requested band despite the profile; the preview then carries a
        caveat naming the mismatch, so relay that caveat too. error_code
        "experimental_plan" means this plan has not been fully checked yet
        (its goal type, a part-way join, an unusual
        horizon or low recent training — ``reasons`` names which) — relay
        ``message`` verbatim, and only pass accept_experimental=true after
        the athlete agrees to build it anyway; the preview then carries the
        same sentences as a caveat, so relay that caveat too. Every other
        refusal (an infeasible solve, an invalid date, an unknown goal or
        ambition level) is a message to relay verbatim.

        Reply contract — when relaying the result:
        * Never author your own week-by-week or phase outline in prose —
          quote ``phases``/``weekly``/``first_days`` as given.
        * Quote ``warning`` verbatim when present (e.g. a short-notice join).
        * Relay every string in ``caveats`` verbatim, including a
          trail-band mismatch built anyway.
        * The top-level ``directives`` object echoes exactly what you
          passed (empty when you passed none) — quote it, never restate
          the directive args yourself, so the athlete sees what is actually
          in force.
        * Never say a plan was created, or that intervals.icu was updated,
          unless ``status`` is "ok" this turn; when ``push.status`` is not
          "ok", say the plan was created here but the calendar was not, and
          point at "Sync now" in the plan header.
        * Once applied, quote ``weeks`` and ``plan_start`` as returned, not
          the request's own arguments.

        Args:
            races: JSON array of {"goal_type", "race_date", "priority"}
                (priority defaults to "A" when omitted). A parsed list is
                also accepted.
            ambition_level: "basic", "moderate", or "competitive". Defaults
                to "moderate".
            training_start: Plan start date, YYYY-MM-DD, must be a Monday.
                Defaults to this week's Monday up to Tuesday, else next Monday.
            weekly_hours_cap: Optional soft weekly-hours ceiling the
                athlete asked to stay under; session floors still win over
                it.
            resume_date: YYYY-MM-DD the athlete can train from — sessions
                before it are not built. Stored on the plan.
            suppress_tests_until: YYYY-MM-DD before which no fitness test
                is scheduled. Stored on the plan.
            unavailable: JSON array of inclusive ["start", "end"] date-range
                pairs to drop outright, e.g. '[["2026-10-01",
                "2026-10-05"]]'. A parsed list is also accepted. Stored on
                the plan.
            accept_short_horizon: Must be True to build a short-notice plan
                after the athlete has heard the horizon_too_short refusal.
            replace_active: Must be True to replace an existing active plan.
            accept_band_mismatch: Must be True to build the requested trail
                band after the athlete has heard the trail_band_mismatch
                refusal and still wants it.
            accept_experimental: Must be True to build a plan after the
                athlete has heard the experimental_plan refusal and agreed.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.
            expected_input_hash: Filled in by the app from the preview the
                card was built on; not part of your call.

        Returns:
            JSON string: the preview (``status`` "preview", carrying a
            top-level ``directives`` echo), the committed outcome (``status``
            "ok"), or a refusal.
        """
        try:
            parsed_races = _parse_races(races)
            if isinstance(parsed_races, dict):
                return json.dumps(parsed_races)
            result = run_create_plan(
                repos=_repos,
                races=parsed_races,
                ambition_level=ambition_level,
                training_start=training_start,
                weekly_hours_cap=weekly_hours_cap,
                resume_date=resume_date,
                suppress_tests_until=suppress_tests_until,
                unavailable=unavailable,
                accept_short_horizon=accept_short_horizon,
                replace_active=replace_active,
                accept_band_mismatch=accept_band_mismatch,
                accept_experimental=accept_experimental,
                confirm=confirm,
                expected_input_hash=expected_input_hash,
                maybe_get_coach=maybe_get_coach,
                client=_client,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("build the plan", exc)

    @coach_tool
    def record_self_assessment(
        sport: str, weekly_hours: float, longest_session_min: int
    ) -> str:
        """Store one sport's self-reported recent training volume.

        Use after create_plan refuses with error_code
        "athlete_data_required": it names the missing_sports whose
        intervals.icu history can't measure a goal sport. Ask the athlete
        for a typical recent week's hours and their longest recent single
        session, store one sport per call, then retry create_plan — this
        is the same questionnaire the wizard's onboarding step asks.

        Args:
            sport: One of "running", "cycling", "swimming".
            weekly_hours: Typical recent weekly hours for this sport (0-40).
            longest_session_min: Longest recent single session, in minutes
                (0-600).

        Returns:
            JSON string confirming what was stored, or a validation refusal.
        """
        try:
            if sport not in _SELF_ASSESSMENT_SPORTS:
                return json.dumps(
                    {
                        "status": "error",
                        "message": f"Unknown sport '{sport}'.",
                        "options": sorted(_SELF_ASSESSMENT_SPORTS),
                    }
                )
            try:
                entry = SelfAssessmentEntry(
                    weekly_hours=weekly_hours,
                    longest_session_min=longest_session_min,
                )
            except ValidationError as exc:
                return json.dumps({"status": "error", "message": str(exc)})
            _repos.self_assessment.upsert_many(
                {sport: (entry.weekly_hours, entry.longest_session_min)}
            )
            return json.dumps(
                {
                    "status": "ok",
                    "sport": sport,
                    "weekly_hours": entry.weekly_hours,
                    "longest_session_min": entry.longest_session_min,
                    "stored": {
                        s: {
                            "weekly_hours": row.weekly_hours,
                            "longest_session_min": row.longest_session_min,
                        }
                        for s, row in _repos.self_assessment.get_all().items()
                    },
                }
            )
        except Exception as exc:
            return tool_error_response("record the self-assessment", exc)

    return [create_plan, record_self_assessment]


__all__ = ["make_plan_build_tools"]
