"""Coach tools for the athlete's races — the single home for goal-race data.

``upsert_race`` is how a race is named, dated, or has its details or status
changed (no hard delete — cancel is ``status="cancelled"``); ``list_races``
reads them back. ``add_race_to_plan`` is the previewing bridge onto an
active plan: it dry-runs the deterministic replan with the race appended,
reporting whether it fits as the requested priority, falls back to a C
tune-up, or does not fit at all — never a second planning path.
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
from endury.domain.models.race import RacePriority, RaceStatus
from endury.intervals.client import IntervalsClient
from endury.planner.foundations.types import GoalType
from endury.planning.add_race import add_race_to_plan as run_add_race_to_plan
from endury.planning.coach_brief import _goal_label
from endury.planning.race_calendar import sync_race_event
from endury.planning.trail_band import (
    LONGEST_PLANNED_EFFORT,
    TRAIL_RACE_GOALS,
    km_effort,
    trail_goal_for,
)
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)

#: Every goal_type the planner knows, for a curated "unknown goal" refusal.
_GOAL_TYPE_OPTIONS = sorted(goal.value for goal in GoalType)
_PRIORITIES = ("A", "B", "C")
_STATUSES = ("planned", "completed", "dns", "dnf", "cancelled")


def _validate_race_fields(
    race_date: str,
    goal_type: str | None,
    priority: str,
    status: str | None,
    distance_km: float | None,
    climb_m: float | None,
) -> str | None:
    """Curated refusal for the first invalid field, else ``None``."""
    try:
        date.fromisoformat(race_date)
    except ValueError:
        return f"'{race_date}' is not a date — pass YYYY-MM-DD."
    if goal_type is not None and goal_type not in _GOAL_TYPE_OPTIONS:
        return (
            f"Unknown goal_type '{goal_type}'. Use one of: "
            f"{', '.join(_GOAL_TYPE_OPTIONS)}."
        )
    if priority not in _PRIORITIES:
        return f"Unknown priority '{priority}'. Use one of: {', '.join(_PRIORITIES)}."
    if status is not None and status not in _STATUSES:
        return f"Unknown status '{status}'. Use one of: {', '.join(_STATUSES)}."
    if distance_km is not None and distance_km <= 0:
        return "distance_km must be greater than 0."
    if climb_m is not None and climb_m <= 0:
        return "climb_m must be greater than 0."
    return None


def _trail_suggestion(
    distance_km: float | None, climb_m: float | None
) -> tuple[float, str | None] | None:
    """``(km_effort, suggested_trail_goal_type)`` for a race profile, or ``None``.

    The suggested goal type is ``None`` from 170 km-effort: no Endury trail
    plan goes that long.

    Only fires once both fields are given (and, by the time this is called,
    already validated positive) — a distance alone is not yet a trail
    profile.
    """
    if distance_km is None or climb_m is None:
        return None
    goal = trail_goal_for(distance_km, climb_m)
    return km_effort(distance_km, climb_m), goal.value if goal else None


def make_race_tools(
    repos: RepositoryContainer,
    maybe_get_coach: Callable[[], Any],
    intervals_client: IntervalsClient | None = None,
) -> list:
    """Create the race coach tools with injected dependencies.

    Args:
        repos: Repository container (races, plan).
        maybe_get_coach: Coach accessor for the plan-context refresh after
            ``add_race_to_plan`` confirms a replan.
        intervals_client: intervals.icu client used to push a confirmed
            replan's calendar events (optional).

    Returns:
        List of race coach tools.
    """
    _repos = repos
    _client = intervals_client

    @coach_tool
    def upsert_race(
        name: str,
        race_date: str,
        goal_type: str | None = None,
        priority: str = "A",
        distance_km: float | None = None,
        climb_m: float | None = None,
        status: str | None = None,
        notes: str | None = None,
        race_id: int | None = None,
    ) -> str:
        """Create or edit a race, and keep it on the athlete's calendar.

        Use it whenever the athlete names a race or asks to put one on the
        calendar. A planned race from today on goes onto the intervals.icu
        calendar at once, even inside this week and with no plan around it;
        a cancelled one comes back off. ``calendar`` in the reply says what
        happened there: relay its ``message`` unless ``status`` is "ok". It
        does not fit the race into the plan: that is ``add_race_to_plan``.

        This is the single home for race data — the athlete-memory
        document should not also list races; if it does, move them here
        and drop the bullet. Pass ``race_id`` to edit an existing race
        (send every field, not just the one changing — this replaces the
        row). Without ``race_id``, a race matching the same date and
        goal_type is updated instead of duplicated.

        There is no hard delete: a race the athlete drops is cancelled, not
        removed. Cancelling a race or changing its date is
        ``cancel_or_move_race``, which the athlete applies on a card; this
        tool refuses both.

        Reply contract — when relaying the result:
        * Say the race is saved, with its ``race_day`` and date, and when
          ``calendar.status`` is "ok" that it is on their intervals.icu
          calendar.
        * A plan does not know the race until it is fitted in: offer that
          in plain words, never a tool name.
        * Pass on each of ``caveats``. One about a weekday date is a
          question: ask the athlete to confirm the date before planning
          anything around the race.

        Either of ``distance_km``/``climb_m``, if given at all, must be
        greater than 0 — passing one alone is fine (e.g. distance without a
        known climb), but a zero or negative value is refused outright.
        With BOTH given, the reply also carries the UTMB-style
        ``km_effort`` (distance + climb/100) and ``suggested_trail_goal_type``
        — offered "if this is a trail race", never stored automatically:
        ``goal_type`` is only ever what you pass, so omitting it leaves the
        race with no goal_type. A trail ``goal_type`` you did give that
        disagrees with the suggestion is kept as given, with a caveat
        naming the band the profile suggests instead. From 170 km-effort
        the suggestion is null: no Endury trail plan goes that long.

        Args:
            name: The race's name.
            race_date: YYYY-MM-DD.
            goal_type: One of the create_plan goal types, or None when the
                race has no plan built for it yet. Never inferred from
                distance_km/climb_m — pass suggested_trail_goal_type
                yourself if the athlete agrees it fits.
            priority: "A", "B", or "C". Defaults to "A".
            distance_km: Race distance, if known; must be greater than 0
                when given. With climb_m, drives the km-effort trail-band
                suggestion.
            climb_m: Race climb, if known; must be greater than 0 when
                given. See distance_km.
            status: "planned", "completed", "dns" or "dnf".
                Defaults to "planned" for a new race; omitting it leaves an
                existing race's status alone.
            notes: Free-text notes.
            race_id: The race to edit, or None to create/match by date +
                goal_type.

        Returns:
            JSON string: the stored race, whether it was newly created, the
            ``calendar`` block, the km-effort/suggested_trail_goal_type block
            when a trail profile was given, and caveats — or a validation
            refusal.
        """
        try:
            error = _validate_race_fields(
                race_date, goal_type, priority, status, distance_km, climb_m
            )
            if error is not None:
                return json.dumps({"status": "error", "message": error})

            suggestion = _trail_suggestion(distance_km, climb_m)
            caveats: list[str] = []
            if suggestion is not None:
                effort, suggested_goal_type = suggestion
                if goal_type in TRAIL_RACE_GOALS and suggested_goal_type is None:
                    caveats.append(
                        f"This race profile is {effort:.0f} km-effort, longer "
                        "than any Endury trail plan (they stop below "
                        f"{LONGEST_PLANNED_EFFORT:g}); kept "
                        f"{_goal_label(goal_type)} since you set it directly."
                    )
                elif (
                    goal_type in TRAIL_RACE_GOALS
                    and suggested_goal_type is not None
                    and goal_type != suggested_goal_type
                ):
                    caveats.append(
                        "This race profile suggests "
                        f"{_goal_label(suggested_goal_type)} ({effort:.0f} "
                        f"km-effort); kept {_goal_label(goal_type)} since "
                        "you set it directly."
                    )

            existing = (
                _repos.races.get(race_id)
                if race_id is not None
                else _repos.races.get_by_natural_key(race_date, goal_type)
            )
            if status == "cancelled" or (
                race_id is not None
                and existing is not None
                and existing.race_date != race_date
            ):
                return json.dumps(
                    {
                        "status": "refused",
                        "message": "Cancelling a race or changing its date goes "
                        "on a card: use cancel_or_move_race.",
                    }
                )
            race = _repos.races.upsert(
                race_id=race_id,
                name=name,
                race_date=race_date,
                goal_type=goal_type,
                priority=cast("RacePriority", priority),
                distance_km=distance_km,
                climb_m=climb_m,
                status=cast(
                    "RaceStatus",
                    status or (existing.status if existing is not None else "planned"),
                ),
                notes=notes,
            )
            result: dict[str, Any] = {
                "status": "ok",
                "race": race.model_dump(),
                "created": existing is None,
                "calendar": sync_race_event(race, client=_client, today=clock.today()),
            }
            if suggestion is not None:
                result["km_effort"] = round(suggestion[0], 1)
                result["suggested_trail_goal_type"] = suggestion[1]
            day = date.fromisoformat(race.race_date)
            if race.status == "planned" and day.weekday() < 5:
                # A wrong year moves a weekend race onto a weekday; the
                # weekday is the one cue the athlete can check at a glance.
                caveats.append(
                    f"{race.race_date} is a {day.strftime('%A')}, and races are "
                    "mostly at weekends: ask the athlete to confirm the date, "
                    "year included."
                )
            if caveats:
                result["caveats"] = caveats
            return json.dumps(result)
        except Exception as exc:
            return tool_error_response("save the race", exc)

    @coach_tool
    def cancel_or_move_race(
        race_id: int,
        cancel: bool = False,
        new_date: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Cancel a race or change its date, once the athlete applies the card.

        Runs as a preview: the reply says what would change, and the athlete
        applies it on a card. Applying also updates the intervals.icu
        calendar. It does not change a plan built around the race: offer
        that in plain words afterwards.

        Args:
            race_id: The race to change (from list_races).
            cancel: Take the race off: its status becomes "cancelled".
            new_date: The race's new date, YYYY-MM-DD, instead of cancelling.
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview, the saved race, or a refusal.
        """
        try:
            race = _repos.races.get(race_id)
            if race is None:
                return json.dumps(
                    {"status": "error", "message": f"No race with id {race_id}."}
                )
            if cancel == (new_date is not None):
                return json.dumps(
                    {
                        "status": "error",
                        "message": "Pass either cancel or new_date, one of them.",
                    }
                )
            race_date = new_date or race.race_date
            error = _validate_race_fields(
                race_date, race.goal_type, race.priority, None, None, None
            )
            if error is not None:
                return json.dumps({"status": "error", "message": error})
            if not confirm:
                return json.dumps(
                    {
                        "status": "preview",
                        "race": race.model_dump(),
                        "cancel": cancel,
                        "new_date": new_date,
                    }
                )
            saved = _repos.races.upsert(
                race_id=race.id,
                name=race.name,
                race_date=race_date,
                goal_type=race.goal_type,
                priority=race.priority,
                distance_km=race.distance_km,
                climb_m=race.climb_m,
                status="cancelled" if cancel else race.status,
                notes=race.notes,
            )
            return json.dumps(
                {
                    "status": "ok",
                    "race": saved.model_dump(),
                    "calendar": sync_race_event(
                        saved, client=_client, today=clock.today()
                    ),
                }
            )
        except Exception as exc:
            return tool_error_response("change the race", exc)

    @coach_tool
    def list_races(include_past_days: int = 30) -> str:
        """List the athlete's races, upcoming first, ordered by date.

        Args:
            include_past_days: How far back past races are still included.
                Defaults to 30.

        Returns:
            JSON string with ``races`` (each field of the race table) and
            ``count``.
        """
        try:
            races = _repos.races.list(include_past_days=include_past_days)
            return json.dumps(
                {"races": [race.model_dump() for race in races], "count": len(races)}
            )
        except Exception as exc:
            return tool_error_response("list races", exc)

    @coach_tool
    def add_race_to_plan(
        race_id: int, priority: str = "B", confirm: bool = False
    ) -> str:
        """Dry-run adding a race to the active plan, then replan if it fits.

        Loads the race from the race table and asks the deterministic
        pipeline whether it fits the active plan at the requested priority
        (default "B", a tune-up race) without disrupting the A-race build;
        when it does not, it retries as "C" before refusing. A race dated
        inside the current week, or already behind us, is refused outright
        — a replan never rewrites the current week.

        Runs as a preview: the reply names `fits_as` ("B", "C", or null
        when it fits nowhere) and `reason`. Applying persists the race into
        the plan's stored races and replans from next Monday.

        Reply contract — when relaying the result:
        * When `fits_as` is null, relay `reason` verbatim and do not
          suggest a workaround the tool did not offer.
        * Never say the plan changed, or intervals.icu was updated, unless
          `status` is "ok" this turn; when `push.status` is not "ok", say
          the plan changed here but the calendar did not.
        * Relay every string in `caveats` verbatim.

        Args:
            race_id: The athlete_goals row to add (from list_races).
            priority: "A", "B", or "C". Defaults to "B".
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the fit preview, the replanned outcome, or a
            refusal.
        """
        try:
            result = run_add_race_to_plan(
                repos=_repos,
                race_id=race_id,
                priority=priority,
                confirm=confirm,
                maybe_get_coach=maybe_get_coach,
                client=_client,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("add the race to the plan", exc)

    return [upsert_race, cancel_or_move_race, list_races, add_race_to_plan]


__all__ = ["make_race_tools"]
