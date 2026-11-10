"""Dependency injection container for Endury repositories.

All repositories sharing a single DatabaseSession are wired here.
Use RepositoryContainer.from_path() for production and
RepositoryContainer.in_memory() for tests.
"""

from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_goals import (
    AthleteGoalsRepository,
)
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)
from endury.database.sqlite.repositories.conversation_history import (
    ConversationHistoryRepository,
)
from endury.database.sqlite.repositories.plan import PlanRepository
from endury.database.sqlite.repositories.self_assessment import (
    SelfAssessmentRepository,
)
from endury.database.sqlite.session import DatabaseSession
from endury.intervals.client import IntervalsClient
from endury.wiring.config import get_coach_config


class RepositoryContainer:
    """Wires all repositories to a single shared DatabaseSession."""

    def __init__(self, session: DatabaseSession) -> None:
        self.session = session
        self.athlete = AthleteProfileRepository(session)
        self.memory = AthleteMemoryRepository(session)
        self.history = ConversationHistoryRepository(session)
        self.plan = PlanRepository(session)
        self.self_assessment = SelfAssessmentRepository(session)
        self.races = AthleteGoalsRepository(session)
        self._intervals_client: IntervalsClient | None = None

    @property
    def intervals_client(self) -> IntervalsClient:
        """Return the intervals.icu client, building it on first use.

        Construction is deferred so an instance without a configured API
        key still boots cleanly; the clear ``ValueError`` from
        ``CoachConfig.get_intervals_api_key`` only surfaces once a caller
        actually needs intervals.icu data.
        """
        if self._intervals_client is None:
            config = get_coach_config()
            self._intervals_client = IntervalsClient(
                api_key=config.get_intervals_api_key(),
                athlete_id=config.intervals_athlete_id,
                base_url=config.intervals_base_url,
            )
        return self._intervals_client

    def bind_intervals_client(self, client: IntervalsClient) -> None:
        """Serve ``client`` instead of building one from the configured key.

        Lets a harness or test pin a scripted client so nothing read through
        this container reaches the real intervals.icu account.

        Args:
            client: The client every ``intervals_client`` read should return.
        """
        self._intervals_client = client

    @classmethod
    def from_path(cls, db_path: str) -> "RepositoryContainer":
        """Create a container backed by a file-based SQLite database."""
        session = DatabaseSession(db_path)
        session.init_schema()
        return cls(session)

    @classmethod
    def in_memory(cls) -> "RepositoryContainer":
        """Create an isolated in-memory container (for testing)."""
        session = DatabaseSession(":memory:")
        session.init_schema()
        return cls(session)
