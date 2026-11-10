"""Ad-hoc structured workout tool for the coach agent.

Builds ONE ad-hoc structured workout — e.g. "give me a ~45min threshold
run" — tailored to the athlete's real thresholds when available. This is
deliberately separate from the committed training plan: nothing here is
persisted, and it never creates, edits, or replaces a planned session.
Periodized, progressive training still comes from the plan — the
deterministic session menus in ``endury.planner.sessions.menus`` remain
the single source of truth for what belongs in a training week. This tool
only serves one-off requests that fall outside that plan.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.coach.workout_outline import build_workout_outline
from endury.planner.activity import (
    build_catalog_enum,
    ensure_registry_loaded,
    get_activity,
)
from endury.planner.activity.materialize import (
    activity_display_name,
    assignment_target_tss,
    workout_total_meters,
)
from endury.planner.activity.select import VALID_INTENTS, pick_for_intent
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    FlatStep,
    build_flat_steps,
    coaching_notes,
)
from endury.planner.foundations.types import (
    ActivityKey,
    PlacedSession,
    SessionOrigin,
    SportType,
)
from endury.planning.load_planner_metrics import get_planner_load_context
from endury.training.load import ResolverProtocol
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)


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


def make_workout_tools(repos: RepositoryContainer) -> list:
    """Create the ad-hoc workout tool list with injected repositories.

    Args:
        repos: Repository container; satisfies the ``PlannerRepos``
            structural protocol that ``get_planner_load_context`` uses to
            resolve real HR/pace/power thresholds.

    Returns:
        List containing the ``build_workout`` coach tool.
    """
    _repos = repos

    @coach_tool
    def build_workout(
        sport: str,
        intent: str,
        duration_minutes: float,
        activity_key: str | None = None,
    ) -> str:
        """Build one ad-hoc structured workout sized to a target duration.

        Generates a single structured workout — steps, personalised
        intensity targets, estimated TSS — on the fly, e.g. "give me a
        ~45min threshold run". This is NOT part of the committed training
        plan: nothing is persisted and no plan session is created or
        changed. Periodized, progressive training still comes from the
        plan; the deterministic session menus remain the source of truth
        for what belongs in a training week. Use this only for one-off
        requests that fall outside the plan.

        This tool is for a workout with NO date ("what would a threshold
        run look like"). A request that names a day ("bike today", "easy
        run Thursday") goes straight to ``schedule_workout``, which books
        it on that one call — do not build it here first.

        One call builds one kind of session. A request that mixes two
        ("VO2max with sweet spot on top") cannot be built as one workout:
        say so and offer each on its own — never swap in another kind
        without saying it.

        Reply contract — when relaying the result:
        * Quote ``outline`` line for line: it is the workout written out
          one line per step (repeats grouped) plus a total.
        * The pace, watts or bpm band on a step is the prescription. A Z
          number beside it is the athlete's own intervals.icu label for
          that band (a step without one spans two of their zones); never
          change a band or call the session wrong because of it.
        * When ``duration_minutes`` is well off ``target_duration_minutes``,
          say the session builds to that length, not the one asked for.

        Args:
            sport: Sport for the workout: "running", "cycling", or
                "swimming". Ignored when activity_key is given.
            intent: Workout flavor: "threshold", "sweet_spot" (just under
                FTP, cycling), "vo2", "easy" or
                "endurance" (steady aerobic session from the easy family —
                Easy Run / Endurance Ride / Endurance Swim), "long" (the
                sport's key long session; never recovery-safe), "trail"
                (any run on trails, steered by heart rate: the Easy Trail
                Run up to about 75 minutes, the Long Trail Run beyond —
                "easy" and "long" are road runs), "recovery" (the
                lightest session), "test" (the sport's fitness test:
                Threshold Run Test, FTP Test, CSS Test — what "a threshold
                test" means; "threshold" builds intervals), "brick", "race_pace"
                (continuous tempo run at sub-threshold race effort), or
                "strides" (easy session finishing with short relaxed
                strides — Easy Run + Strides, 4-8 x 20-30 s; the only way
                to put strides into a workout: never add strides, pickups
                or extra reps in prose that the built workout does not
                contain). Ignored when activity_key is given.
            duration_minutes: Target total workout duration in minutes.
            activity_key: Optional exact planner activity key (e.g.
                "run_threshold") to build instead of resolving sport+intent.

        Returns:
            JSON string with the resolved activity, flattened steps with
            targets, total duration, estimated TSS, coaching notes,
            and ``outline``.
        """
        try:
            ensure_registry_loaded()

            if duration_minutes <= 0:
                return json.dumps({"error": "duration_minutes must be greater than 0."})

            if activity_key:
                try:
                    activity: ActivityDefinition = get_activity(
                        ActivityKey(activity_key)
                    )
                except ValueError:
                    return json.dumps(
                        {
                            "error": f"Unknown activity_key '{activity_key}'.",
                            "options": build_catalog_enum(),
                        }
                    )
            else:
                try:
                    sport_enum = SportType(sport.strip().lower())
                except ValueError:
                    return json.dumps(
                        {
                            "error": f"Unknown sport '{sport}'.",
                            "options": sorted(s.value for s in SportType),
                        }
                    )
                intent_lower = intent.strip().lower()
                if intent_lower not in VALID_INTENTS:
                    return json.dumps(
                        {
                            "error": f"Unknown intent '{intent}'.",
                            "options": VALID_INTENTS,
                        }
                    )
                picked = pick_for_intent(sport_enum, intent_lower, duration_minutes)
                if picked is None:
                    supporting_sports = sorted(
                        s.value
                        for s in SportType
                        if pick_for_intent(s, intent_lower) is not None
                    )
                    return json.dumps(
                        {
                            "error": (
                                f"No '{intent_lower}' activities for sport "
                                f"'{sport_enum.value}'."
                            ),
                            "options": supporting_sports,
                        }
                    )
                activity = picked

            # intervals.icu is the live source of thresholds, so there is
            # no local staleness to police — a read either succeeds (and is
            # current) or the targets degrade to RPE/zone numbers.
            resolver: ResolverProtocol | None = get_planner_load_context(_repos)
            targets_note: str | None = None
            if resolver is None:
                targets_note = (
                    "No athlete thresholds on file in intervals.icu (or it "
                    "could not be reached) — targets are shown as RPE and "
                    "zone number only."
                )

            hours = duration_minutes / 60.0
            target_tss = assignment_target_tss(activity, hours, resolver)
            workout = activity.build(target_tss, resolver=resolver)
            realized_duration_min = activity.duration_minutes_exact(
                target_tss, resolver=resolver
            )
            achieved_tss = activity.achieved_tss(target_tss, resolver=resolver)

            swim_css = (
                resolver.swim_css_sec_per_100m
                if resolver is not None and resolver.swim_css_sec_per_100m
                else DEFAULT_SWIM_CSS_SEC_PER_100M
            )
            flat_steps = build_flat_steps(workout.steps, swim_css_sec_per_100m=swim_css)
            total_meters = workout_total_meters(workout.steps)

            # Throwaway PlacedSession — only used to satisfy coaching_notes'
            # signature; slot/week/day fields are meaningless for an ad-hoc
            # workout and never surfaced in the response.
            placed = PlacedSession(
                slot_id="adhoc",
                activity_key=activity.key,
                sport=activity.sport,
                origin=SessionOrigin.ANCHOR,
                week=0,
                day_of_week=None,
                target_hours=hours,
            )
            # goal=None: an ad-hoc workout belongs to no plan, so it gets
            # no goal-specific notes rather than a guessed race distance.
            notes = coaching_notes(
                placed,
                duration_min=realized_duration_min,
                total_meters=total_meters,
                flat_steps=flat_steps,
                goal=None,
            )

            response: dict[str, Any] = {
                "activity_key": activity.key.value,
                "workout_name": activity_display_name(activity.key),
                "sport": activity.sport.value,
                "target_duration_minutes": round(duration_minutes, 1),
                "duration_minutes": round(realized_duration_min, 1),
                "target_tss": round(target_tss, 1),
                "achieved_tss": round(achieved_tss, 1),
                "steps": [_flat_step_to_dict(s) for s in flat_steps],
                "coaching_notes": notes,
                "note": (
                    "Ad-hoc workout — not part of your committed training "
                    "plan; nothing was saved. To put it INTO the plan in "
                    "place of a planned session, call swap_planned_session "
                    "(the athlete applies it from its card); to give it a date of its "
                    "own, call schedule_workout, which books it on that one "
                    "call — inside an active plan's dates that adds it "
                    "to the plan as an extra session, outside them it is a "
                    "standalone calendar entry; "
                    "to leave the plan alone and just drop that session, "
                    "use skip_planned_session. Present the workout by "
                    'quoting "outline" line for line — do not restate '
                    "durations in your own words."
                ),
            }
            response["outline"] = build_workout_outline(response).text
            if targets_note is not None:
                response["targets_note"] = targets_note
            return json.dumps(response, indent=2)
        except Exception as exc:
            return tool_error_response("build workout", exc)

    return [build_workout]


__all__ = ["make_workout_tools"]
