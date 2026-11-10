"""Derive a planner ``AthleteState`` from intervals.icu.

The measured-state bridge between intervals.icu and the deterministic
planner: per-sport CTL/ATL from :mod:`endury.intervals.fitness`, recent
weekly hours via a sick-week-robust median, longest recent session per
activity family, and thresholds read off the athlete's sport-settings
rows.

Precedence per sport: measured history,
then the athlete's stored self-assessment answers, then the goal-type
default profile. The default tier is a *last resort* — it describes an
athlete who already trains a lot, so callers that enforce onboarding
refuse to build a plan on it.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from endury.domain.analysis.zones import derive_cycling_lthr_bpm
from endury.intervals.fitness import (
    activity_date,
    bucket_by_sport,
    per_sport_ctl_atl,
)
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    AthleteProfile,
    IntervalsActivity,
    SportSettingsRow,
)
from endury.intervals.workout_dsl import SportThresholds
from endury.planner.foundations.request import AthleteState, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    GoalType,
    RaceGoal,
    SportType,
)
from endury.planner.pipeline import default_profile_for

#: History window: 12 training weeks plus a 42-day EWA warmup.
_LOOKBACK_DAYS = 12 * 7 + 42

#: Fewer activities than this → no measured state (fall back to defaults).
_MIN_ACTIVITIES = 5

#: Window for the longest-recent-session lookup.
_LONGEST_WINDOW_DAYS = 42

#: Number of complete ISO weeks feeding the weekly-hours median.
_HOURS_MEDIAN_WEEKS = 6

#: Floor measured weekly hours at this fraction of the goal default so a
#: thin history cannot drive capacity below the menu minimums (QP infeasible).
_HOURS_FLOOR_FRACTION = 0.5

#: Family used for the longest-recent-session key, per sport (matches
#: ``default_profile_for``: LONG for land sports, EASY for swim).
_LONGEST_FAMILY: dict[SportType, ActivityFamily] = {
    SportType.RUNNING: ActivityFamily.LONG,
    SportType.CYCLING: ActivityFamily.LONG,
    SportType.SWIMMING: ActivityFamily.EASY,
}

#: Note recorded when a threshold was inferred rather than read.
DERIVED_CYCLING_LTHR = "cycling LTHR derived from running LTHR"


class SelfAssessmentEntry(Protocol):
    """One stored self-assessment answer for a single sport."""

    @property
    def weekly_hours(self) -> float:
        """Self-reported average weekly training hours."""

    @property
    def longest_session_min(self) -> int:
        """Self-reported longest recent session, in minutes."""


class IntervalsSource(Protocol):
    """The slice of :class:`~endury.intervals.client.IntervalsClient` used here."""

    def get_athlete(self) -> AthleteProfile:
        """Return the athlete profile including sport-settings rows."""

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        """Return activities in an inclusive date window."""


@dataclass(frozen=True)
class ResolvedAthleteState:
    """An athlete state plus the provenance of each sport's numbers.

    Attributes:
        athlete: The resolved planner athlete state.
        sources: Sport -> ``"history"`` | ``"self_report"`` | ``"default"``.
        thresholds: Sport -> thresholds read from intervals.icu sport
            settings (absent when the athlete has no row for that sport).
        notes: Sport -> provenance notes for values that were derived
            rather than read (see :data:`DERIVED_CYCLING_LTHR`).
        upload_destinations: Platforms intervals.icu forwards planned
            workouts to. Empty means a pushed plan reaches no device.
        recent_weekly_elevation_m: Sport -> metres climbed in a typical
            recent week, reported beside the measured weekly hours. Only
            history-measured sports have one; ``None`` where the history
            carries no elevation at all. The planner does not consume it.
    """

    athlete: AthleteState
    sources: dict[SportType, str]
    thresholds: dict[SportType, SportThresholds] = field(default_factory=dict)
    notes: dict[SportType, list[str]] = field(default_factory=dict)
    upload_destinations: list[str] = field(default_factory=list)
    recent_weekly_elevation_m: dict[SportType, int | None] = field(default_factory=dict)


def _duration_hours(activity: IntervalsActivity) -> float:
    """Return an activity's duration in hours (0.0 when unknown).

    ``icu_recording_time`` is intervals.icu's timer duration, so it wins
    over ``moving_time``, which drops stops and reads systematically low
    on traffic-interrupted rides and transition-heavy sessions.
    """
    seconds = activity.icu_recording_time or activity.moving_time or 0
    return float(seconds) / 3600.0


def _median_weekly_hours(acts: list[IntervalsActivity], today: date) -> float:
    """Median training hours over the last complete ISO weeks.

    The week containing *today* is incomplete and excluded; zero-hour weeks
    count (the median is what makes this robust to sick weeks, not
    exclusion).

    Args:
        acts: Activities for one sport.
        today: Reference date.

    Returns:
        Median weekly hours over the window.
    """
    week_start = today - timedelta(days=today.weekday())
    window_start = week_start - timedelta(weeks=_HOURS_MEDIAN_WEEKS)
    buckets = [0.0] * _HOURS_MEDIAN_WEEKS
    for act in acts:
        day = activity_date(act)
        if day is not None and window_start <= day < week_start:
            buckets[(day - window_start).days // 7] += _duration_hours(act)
    return float(statistics.median(buckets))


def _median_weekly_elevation_m(
    acts: list[IntervalsActivity], today: date
) -> int | None:
    """Median metres climbed per week over the last complete ISO weeks.

    Shares `_median_weekly_hours`' window so the two read as the same
    typical week. An activity with no elevation figure contributes zero,
    but a history that records elevation nowhere is unmeasured, not flat,
    and reads ``None``.

    Args:
        acts: Activities for one sport.
        today: Reference date.

    Returns:
        Median weekly elevation gain in whole metres, or None when the
        window carries no elevation data at all.
    """
    week_start = today - timedelta(days=today.weekday())
    window_start = week_start - timedelta(weeks=_HOURS_MEDIAN_WEEKS)
    buckets = [0.0] * _HOURS_MEDIAN_WEEKS
    measured = False
    for act in acts:
        day = activity_date(act)
        gain = act.total_elevation_gain
        if gain is not None and day is not None and window_start <= day < week_start:
            measured = True
            buckets[(day - window_start).days // 7] += gain
    return round(statistics.median(buckets)) if measured else None


def _longest_recent_minutes(acts: list[IntervalsActivity], today: date) -> int:
    """Longest single-session duration (minutes) in the recent window."""
    window_start = today - timedelta(days=_LONGEST_WINDOW_DAYS)
    longest = 0.0
    for act in acts:
        day = activity_date(act)
        if day is not None and day >= window_start:
            longest = max(longest, _duration_hours(act) * 60.0)
    return round(longest)


def _sport_is_measured(acts: list[IntervalsActivity], today: date) -> bool:
    """Whether a sport's history is real evidence, not a stray activity.

    The bar is the measurement itself: ``_median_weekly_hours`` reads > 0
    only when at least half of the last ``_HOURS_MEDIAN_WEEKS`` complete
    ISO weeks contain training. A single old ride, or activities confined
    to the current incomplete week, measure nothing — treating them as
    "history" would silently seed the floored default and lock the
    questionnaire away for that sport.
    """
    return bool(acts) and _median_weekly_hours(acts, today) > 0.0


def _settings_by_sport(
    rows: list[SportSettingsRow],
) -> dict[SportType, SportSettingsRow]:
    """Map each planner sport to the first sport-settings row covering it.

    Load-only types (Hike/Walk) never claim a sport's row: a dedicated
    hiking row must not supply RUNNING thresholds.
    """
    by_sport: dict[SportType, SportSettingsRow] = {}
    for row in rows:
        for type_name in row.types or []:
            if type_name in LOAD_ONLY_ACTIVITY_TYPES:
                continue
            sport = SPORT_TYPE_MAP.get(type_name)
            if sport is not None and sport not in by_sport:
                by_sport[sport] = row
    return by_sport


def _thresholds_from_settings(
    rows: list[SportSettingsRow],
) -> tuple[dict[SportType, SportThresholds], dict[SportType, list[str]]]:
    """Read per-sport thresholds off the athlete's sport-settings rows.

    ``threshold_pace`` is metres per second on every row: run threshold
    pace is ``1000 / v`` sec/km and swim CSS is ``100 / v`` sec/100 m.
    A missing cycling LTHR is derived from the running one (flagged in
    the returned notes) so HR-targeted bike steps stay renderable.

    Args:
        rows: The athlete's ``sportSettings`` rows.

    Returns:
        ``(thresholds, notes)`` — both keyed by sport; ``notes`` only
        carries sports with a derived value.
    """
    by_sport = _settings_by_sport(rows)
    run_row = by_sport.get(SportType.RUNNING)
    run_lthr = run_row.lthr if run_row is not None else None

    thresholds: dict[SportType, SportThresholds] = {}
    notes: dict[SportType, list[str]] = {}
    for sport, row in by_sport.items():
        pace = row.threshold_pace
        lthr = row.lthr
        if sport is SportType.CYCLING and not lthr and run_lthr:
            lthr = derive_cycling_lthr_bpm(run_lthr)
            notes[sport] = [DERIVED_CYCLING_LTHR]
        thresholds[sport] = SportThresholds(
            ftp_watts=row.ftp if sport is SportType.CYCLING and row.ftp else None,
            lthr_bpm=lthr or None,
            threshold_pace_sec_per_km=(
                1000.0 / pace if sport is SportType.RUNNING and pace else None
            ),
            css_sec_per_100m=(
                100.0 / pace if sport is SportType.SWIMMING and pace else None
            ),
        )
    return thresholds, notes


def _state_thresholds(
    sport: SportType,
    thresholds: dict[SportType, SportThresholds],
) -> dict[str, float | None]:
    """Per-sport threshold kwargs for a :class:`SportState`."""
    found = thresholds.get(sport)
    return {
        "css_sec_per_100m": found.css_sec_per_100m if found else None,
        "ftp_watts": found.ftp_watts if found else None,
        "threshold_pace_sec_per_km": (
            found.threshold_pace_sec_per_km if found else None
        ),
    }


def _measured_state(
    sport: SportType,
    acts: list[IntervalsActivity],
    default_state: SportState,
    today: date,
    ctl_atl: dict[SportType, tuple[float, float] | None],
    thresholds: dict[SportType, SportThresholds],
) -> SportState:
    """Build a history-measured ``SportState`` for one sport.

    A sport whose activities carry no training load at all keeps
    ``ctl_0``/``atl_0`` at ``None`` — the hours are measured, the load is
    not, so the planner derives the fitness seed from the hours the way
    the self-report path does.
    """
    measured_load = ctl_atl.get(sport, (0.0, 0.0))
    ctl_0: float | None = measured_load[0] if measured_load is not None else None
    atl_0: float | None = measured_load[1] if measured_load is not None else None

    hours_floor = default_state.recent_weekly_hours * _HOURS_FLOOR_FRACTION
    weekly_hours = max(_median_weekly_hours(acts, today), hours_floor)

    family = _LONGEST_FAMILY[sport]
    longest_min = _longest_recent_minutes(acts, today)
    if longest_min <= 0:
        longest_min = default_state.longest_recent_min.get(family, 0)

    return SportState(
        recent_weekly_hours=weekly_hours,
        longest_recent_min={family: longest_min},
        ctl_0=ctl_0,
        atl_0=atl_0,
        **_state_thresholds(sport, thresholds),
    )


def _self_reported_state(
    sport: SportType,
    row: SelfAssessmentEntry,
    default_state: SportState,
    thresholds: dict[SportType, SportThresholds],
) -> SportState:
    """Build a ``SportState`` from a stored self-assessment answer.

    ``ctl_0``/``atl_0`` stay ``None`` — the athlete cannot report training
    load, so the planner seeds fitness from the reported hours instead.

    The reported hours are used verbatim: ``_HOURS_FLOOR_FRACTION`` exists
    to keep a *gappy measured history* from under-reading real training,
    and applying it here would silently override the athlete's own answer
    with the default tier the onboarding gate refuses.
    """
    family = _LONGEST_FAMILY[sport]
    longest_min = row.longest_session_min
    if longest_min <= 0:
        longest_min = default_state.longest_recent_min.get(family, 0)
    return SportState(
        recent_weekly_hours=row.weekly_hours,
        longest_recent_min={family: longest_min},
        ctl_0=None,
        atl_0=None,
        **_state_thresholds(sport, thresholds),
    )


def _recent_activities(client: IntervalsSource, today: date) -> list[IntervalsActivity]:
    """Fetch the history window, or ``[]`` when it fails the >= 5 gate."""
    activities = client.list_activities(
        today - timedelta(days=_LOOKBACK_DAYS),
        today,
    )
    return activities if len(activities) >= _MIN_ACTIVITIES else []


def _a_goal_type(goals: list[RaceGoal]) -> GoalType:
    """Return the goal type of the A-race (first goal when none is flagged)."""
    a_goal = next((goal for goal in goals if goal.priority == "A"), goals[0])
    return a_goal.goal_type


def history_measured_sports(client: IntervalsSource, today: date) -> set[SportType]:
    """Return the sports the athlete's intervals.icu history can measure.

    Args:
        client: The intervals.icu read client.
        today: Reference date.

    Returns:
        Sports whose recent history passes ``_sport_is_measured``; empty
        when the global >= 5-activity gate fails.
    """
    buckets = bucket_by_sport(_recent_activities(client, today))
    return {sport for sport, acts in buckets.items() if _sport_is_measured(acts, today)}


def resolve_from_intervals(
    client: IntervalsSource,
    today: date,
    goals: list[RaceGoal],
    self_assessment: Mapping[str, SelfAssessmentEntry] | None = None,
) -> ResolvedAthleteState:
    """Resolve every goal sport to the best available evidence.

    The sport set is the A-goal's default sport set
    (``default_profile_for``): sports the athlete trains but the goal
    doesn't need are dropped (a marathoner's bike commutes must not add
    bike sessions), and goal sports without evidence fall back through
    the self-assessment answers to the goal-default ``SportState``.

    Args:
        client: The intervals.icu read client.
        today: Reference date.
        goals: The plan's race goals (the A-race picks the sport set).
        self_assessment: Stored questionnaire answers keyed by sport value
            (``"running"``/``"cycling"``/``"swimming"``).

    Returns:
        The resolved state, the per-sport source labels, the thresholds
        read from sport settings, any derived-value notes, and each
        history-measured sport's typical weekly elevation gain.
    """
    profile = client.get_athlete()
    thresholds, notes = _thresholds_from_settings(profile.sport_settings or [])

    activities = _recent_activities(client, today)
    buckets = bucket_by_sport(activities)
    ctl_atl = per_sport_ctl_atl(activities, today)
    reported = self_assessment or {}

    sports: dict[SportType, SportState] = {}
    sources: dict[SportType, str] = {}
    elevation: dict[SportType, int | None] = {}
    for sport, default_state in default_profile_for(_a_goal_type(goals)).sports.items():
        acts = buckets.get(sport, [])
        row = reported.get(sport.value)
        if _sport_is_measured(acts, today):
            sports[sport] = _measured_state(
                sport, acts, default_state, today, ctl_atl, thresholds
            )
            sources[sport] = "history"
            elevation[sport] = _median_weekly_elevation_m(acts, today)
        elif row is not None:
            sports[sport] = _self_reported_state(sport, row, default_state, thresholds)
            sources[sport] = "self_report"
        else:
            sports[sport] = default_state
            sources[sport] = "default"

    return ResolvedAthleteState(
        athlete=AthleteState(sports=sports),
        sources=sources,
        thresholds=thresholds,
        notes=notes,
        upload_destinations=profile.upload_destinations(),
        recent_weekly_elevation_m=elevation,
    )
