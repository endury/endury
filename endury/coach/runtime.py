"""Coach runtime: app-scoped repositories, tools, LLM clients, and context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from endury.coach.context import (
    build_latest_session_line,
    build_scheduled_workouts_line,
)
from endury.coach.form_context import build_form_line
from endury.coach.llm import create_llm
from endury.coach.tools import make_tools
from endury.coach.wellness_context import build_wellness_summary, fetch_wellness
from endury.common import clock
from endury.database.sqlite.session import get_default_db_path
from endury.domain.models.athlete import AthleteProfile
from endury.intervals.client import IntervalsClient
from endury.planning.commit_plan import load_plan_context_summary
from endury.planning.generate_plan import SOLVER_VERSION
from endury.wiring.config import CoachConfig, get_coach_config
from endury.wiring.container import RepositoryContainer

if TYPE_CHECKING:
    from endury.coach.session import CoachSession
    from endury.coach.tools.analysis_tools import Executor

logger = logging.getLogger(__name__)


def _resolve_intervals_client(repos: RepositoryContainer) -> IntervalsClient | None:
    """Return the container's intervals.icu client, or None when unavailable.

    The container builds the client lazily and raises when no API key is
    configured — that is the "not connected yet" state, not an error: the
    coach simply runs without its activity, threshold, and load tools.

    Args:
        repos: The runtime's repository container.

    Returns:
        The client, or None when intervals.icu is not configured.
    """
    try:
        return repos.intervals_client
    except Exception as exc:
        logger.info("intervals.icu is not configured for the coach: %s", exc)
        return None


class CoachRuntime:
    """Application-scoped coach runtime state and dependencies."""

    def __init__(
        self,
        config: CoachConfig | None = None,
        *,
        llm: Any = None,
        verbose: int = 0,
        repos: RepositoryContainer | None = None,
        intervals_client: IntervalsClient | None = None,
        stamp_last_session: bool = True,
        analysis_executor: Executor | None = None,
    ) -> None:
        self.config = config or get_coach_config()
        # The browser store uploads every durable write; a session timestamp
        # is not worth an upload per boot and per "New chat".
        self._stamp_sessions = stamp_last_session
        self.verbose = verbose
        self._llm = llm

        self.athlete_profile: AthleteProfile | None = None
        self.recent_health_summary: str | None = None
        self.form_summary: str | None = None
        self.latest_session_summary: str | None = None
        self.scheduled_workouts_summary: str | None = None
        self.active_plan_summary: str | None = None
        self.macro_plan_summary: str | None = None
        self._coach: CoachSession | None = None

        self._repos = repos or RepositoryContainer.from_path(get_default_db_path())
        self._athlete_repo = self._repos.athlete
        self._memory_repo = self._repos.memory
        self._intervals = intervals_client or _resolve_intervals_client(self._repos)

        self.tools = make_tools(
            athlete_repo=self._athlete_repo,
            memory_repo=self._memory_repo,
            repos=self._repos,
            intervals_client=self._intervals,
            maybe_get_coach=self.maybe_get_coach,
            analysis_executor=analysis_executor,
        )

    @property
    def repos(self) -> RepositoryContainer:
        """Return the runtime repository container."""
        return self._repos

    @property
    def athlete_repo(self) -> Any:
        """Return the athlete repository."""
        return self._athlete_repo

    @property
    def memory_repo(self) -> Any:
        """Return the memory repository."""
        return self._memory_repo

    @property
    def intervals_client(self) -> IntervalsClient | None:
        """Return the intervals.icu client, or None when unconfigured."""
        return self._intervals

    def coach_llm(self) -> Any:
        """Return the LLM client for this runtime.

        An injected test ``llm`` is used when present; otherwise a real
        OpenRouter client is created from config (requires an API key).
        """
        if self._llm is not None:
            return self._llm
        return create_llm(self.config)

    def create_session(self) -> CoachSession:
        """Create a new conversation session bound to this runtime."""
        from endury.coach.session import CoachSession

        session = CoachSession(runtime=self)
        self._coach = session
        return session

    def maybe_get_coach(self) -> CoachSession | None:
        """Return the runtime's bound coach session, or None if none exists yet.

        Passed to the planner replan tool so it can refresh the coach's
        plan-context summary after a replan.
        """
        return self._coach

    def run_initializer(self) -> None:
        """Load the athlete profile and stamp this session's start time."""
        self.athlete_profile = self._load_athlete_profile()
        if self._stamp_sessions:
            self._stamp_last_session()

    def _load_athlete_profile(self) -> AthleteProfile | None:
        try:
            return self._athlete_repo.get()
        except Exception as exc:
            logger.warning("Could not load athlete profile: %s", exc)
            return None

    def _stamp_last_session(self) -> None:
        try:
            now_ts = clock.now().isoformat()
            self._athlete_repo.update_last_session_at(now_ts)
            if self.athlete_profile:
                self.athlete_profile = self.athlete_profile.model_copy(
                    update={"last_session_at": now_ts}
                )
        except Exception as exc:
            logger.warning("Could not update last_session_at: %s", exc)

    def load_plan_context(self) -> None:
        """Rebuild the plan context a commit wrote, so a restart keeps it.

        The full coach_brief plus weekly lines for the active plan, and the
        macro plan's headline — what ``build_system_context`` renders as the
        plan sections. Fault tolerant: either half failing to load leaves
        its summary as it was.
        """
        try:
            self.active_plan_summary = load_plan_context_summary(
                self._repos, SOLVER_VERSION
            )
            if self.active_plan_summary is not None:
                logger.info("Loaded active plan context for coach.")
        except Exception:
            logger.debug("Could not load active plan context", exc_info=True)

        try:
            macro = self._repos.plan.get_active_macro()
            if macro is not None:
                self.macro_plan_summary = (
                    f"Goal: {macro.goal_type}, {macro.total_weeks} weeks total\n"
                    f"Ambition: {macro.ambition_level}\n"
                    f"Brief: {macro.coaching_brief[:300]}"
                )
                logger.info("Loaded active macro plan context for coach.")
        except Exception:
            logger.debug("Could not load coach macro plan context", exc_info=True)

    def enrich_context(self) -> None:
        """Load recent wellness, today's form and the latest session for context.

        Fault tolerant by design: with no intervals.icu key (or an
        unreachable API) the summaries stay None instead of failing a turn.
        """
        self.recent_health_summary = build_wellness_summary(
            fetch_wellness(self._intervals)
        )
        self.form_summary = build_form_line(self._intervals)
        self.latest_session_summary = build_latest_session_line(
            self._intervals, self._repos
        )
        self.scheduled_workouts_summary = build_scheduled_workouts_line(self._intervals)

    def refresh_athlete_profile(self) -> AthleteProfile | None:
        """Reload and store the current athlete profile."""
        profile = self._athlete_repo.get()
        self.athlete_profile = profile
        return profile

    def set_athlete_profile(self, profile: AthleteProfile | None) -> None:
        """Replace the in-memory athlete profile."""
        self.athlete_profile = profile

    def update_language(self, language: str) -> None:
        """Update the in-memory athlete language so the next turn sees it."""
        if self.athlete_profile is None:
            return
        self.athlete_profile = self.athlete_profile.model_copy(
            update={"language": language}
        )
