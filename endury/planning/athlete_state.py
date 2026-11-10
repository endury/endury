"""Resolve a planner ``AthleteState`` for plan generation.

Thin delegators to :mod:`endury.intervals.athlete_source` — intervals.icu
is the source of truth for activity history and thresholds — adapting a
goal-type-only call shape to that resolver's
``(client, today, goals, self_assessment)`` contract.

Both entry points degrade rather than raise: no API key or an
intervals.icu outage reads as "no measured history" and falls through to
self-assessment, then the goal defaults (which onboarding refuses).
"""

from __future__ import annotations

import logging
from datetime import date

from endury.common import clock
from endury.intervals.athlete_source import (
    IntervalsSource,
    ResolvedAthleteState,
    resolve_from_intervals,
)
from endury.intervals.athlete_source import (
    history_measured_sports as _intervals_history_measured_sports,
)
from endury.intervals.models import AthleteProfile, IntervalsActivity
from endury.planner.foundations.types import (
    GoalType,
    RaceGoal,
    SportType,
)
from endury.planning.load_planner_metrics import PlannerRepos

logger = logging.getLogger(__name__)

#: Reason reported when this instance has no usable intervals.icu key.
NOT_CONFIGURED_REASON = (
    "The intervals.icu API key is not configured — add it in Settings."
)


class _OfflineSource:
    """An empty intervals.icu source: no profile, no activities.

    Substituted whenever the real client is missing or failing, so the
    resolver runs its normal fallback chain instead of propagating a
    transport error into a 500.
    """

    def get_athlete(self) -> AthleteProfile:
        """Return an empty profile (no sport settings, no thresholds)."""
        return AthleteProfile()

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        """Return no activities, whatever the window."""
        del oldest, newest
        return []


class _ReadOnce:
    """An intervals.icu source that answers each distinct read only once.

    The planner bootstrap asks for the measured sports and then resolves
    every goal's measured hours from the same history; in the browser each
    repeated read is a visible round-trip.
    """

    def __init__(self, client: IntervalsSource) -> None:
        self._client = client
        self._athlete: AthleteProfile | None = None
        self._activities: dict[tuple[date, date], list[IntervalsActivity]] = {}

    def get_athlete(self) -> AthleteProfile:
        """Return the athlete profile, read on first use."""
        if self._athlete is None:
            self._athlete = self._client.get_athlete()
        return self._athlete

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        """Return the activities in the window, read once per window."""
        window = (oldest, newest)
        if window not in self._activities:
            self._activities[window] = self._client.list_activities(oldest, newest)
        return self._activities[window]


def _intervals_source(repos: PlannerRepos) -> IntervalsSource | None:
    """Return the container's client, or ``None`` when unconfigured."""
    try:
        return repos.intervals_client
    except ValueError as exc:  # no INTERVALS_API_KEY on this instance
        logger.info("intervals.icu history unavailable: %s", exc)
        return None


def read_once(repos: PlannerRepos) -> IntervalsSource | None:
    """The container's client behind a cache, for callers that read repeatedly.

    Args:
        repos: Repository container (intervals_client).

    Returns:
        A source that reads intervals.icu once per distinct request, or
        ``None`` when no client is configured.
    """
    client = _intervals_source(repos)
    return _ReadOnce(client) if client is not None else None


def measured_sports_with_reason(
    repos: PlannerRepos,
    *,
    today: date | None = None,
    source: IntervalsSource | None = None,
) -> tuple[set[SportType], str | None]:
    """Measured sports plus why the answer may be empty.

    Args:
        repos: Repository container (intervals_client).
        today: Reference date (defaults to the app clock).
        source: The intervals.icu source to read (defaults to the
            container's client).

    Returns:
        ``(sports, reason)`` — the measured sports, and ``None`` when
        intervals.icu actually answered, otherwise a user-facing reason
        the history could not be read.
    """
    client = source or _intervals_source(repos)
    if client is None:
        return set(), NOT_CONFIGURED_REASON
    try:
        return _intervals_history_measured_sports(client, today or clock.today()), None
    except Exception as exc:  # an outage must not break the wizard
        logger.warning("intervals.icu history read failed: %s", exc)
        return set(), f"Could not reach intervals.icu: {exc}"


def history_measured_sports(
    repos: PlannerRepos, *, today: date | None = None
) -> set[SportType]:
    """Return the sports the athlete's intervals.icu history can measure.

    Thin delegator to
    :func:`endury.intervals.athlete_source.history_measured_sports`.

    Args:
        repos: Repository container (intervals_client).
        today: Reference date (defaults to the app clock).

    Returns:
        Sports whose recent history passes the intervals.icu evidence bar;
        empty when the global >= 5-activity gate fails, when no API key is
        configured, or when intervals.icu is unreachable.
    """
    return measured_sports_with_reason(repos, today=today)[0]


def resolve_athlete_state(
    repos: PlannerRepos,
    goal_type: GoalType,
    *,
    today: date | None = None,
    source: IntervalsSource | None = None,
) -> ResolvedAthleteState:
    """Resolve every goal sport to the best available evidence.

    Thin delegator to
    :func:`endury.intervals.athlete_source.resolve_from_intervals`,
    wrapping ``goal_type`` in a single A-priority goal before passing
    through. Precedence per sport: measured intervals.icu history, then
    stored self-assessment answers, then the goal-type default — a last
    resort describing an athlete who already trains a lot, so callers
    enforcing onboarding refuse to build a plan on it.

    Args:
        repos: Repository container (intervals_client, self_assessment).
        goal_type: The A-goal the plan is built for.
        today: Reference date (defaults to the app clock).
        source: The intervals.icu source to read (defaults to the
            container's client).

    Returns:
        The resolved state, the per-sport source labels, the thresholds
        read from intervals.icu sport settings, and any derived-value
        notes.
    """
    today = today or clock.today()
    goals = [RaceGoal(goal_type=goal_type, race_date=today, priority="A")]
    reported = repos.self_assessment.get_all()
    client = source or _intervals_source(repos)
    if client is not None:
        try:
            return resolve_from_intervals(client, today, goals, reported)
        except Exception as exc:  # degrade to the self-report/default chain
            logger.warning("intervals.icu state read failed: %s", exc)
    return resolve_from_intervals(_OfflineSource(), today, goals, reported)


def describe_missing_data(sports: list[SportType]) -> str:
    """User-facing prompt naming the sports the athlete must answer for."""
    names = [sport.value for sport in sports]
    joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return (
        "I don't know enough about your recent training to size this plan. "
        f"Answer a few questions about your {joined} training to continue."
    )


def log_resolved_state(resolved: ResolvedAthleteState, goal_type: GoalType) -> None:
    """Log what the plan about to be sized is actually seeded from."""
    if any(source == "history" for source in resolved.sources.values()):
        logger.info(
            "Athlete state derived from history: %s",
            {
                sport.value: {
                    "weekly_hours": round(state.recent_weekly_hours, 1),
                    "ctl_0": None if state.ctl_0 is None else round(state.ctl_0, 1),
                    "atl_0": None if state.atl_0 is None else round(state.atl_0, 1),
                }
                for sport, state in resolved.athlete.sports.items()
            },
        )
    else:
        logger.info(
            "Athlete state seeded without history for %s: %s",
            goal_type.value,
            {sport.value: source for sport, source in resolved.sources.items()},
        )
