"""Database repositories for Endury.

Each repository encapsulates SQLite queries for a single domain.
All repositories accept a ``DatabaseSession`` from
``endury.database.sqlite.session`` as their constructor argument.

Repositories:
    AthleteProfileRepository: Athlete profile CRUD.
    AthleteMemoryRepository: The durable athlete-memory document.
    ConversationHistoryRepository: Chat message persistence.
    SelfAssessmentRepository: Self-reported per-sport training volume.
    PlanRepository: Training plans and sessions (planner).
"""

from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)
from endury.database.sqlite.repositories.conversation_history import (
    ConversationHistoryRepository,
)
from endury.database.sqlite.repositories.plan import PlanRepository
from endury.database.sqlite.repositories.self_assessment import (
    SelfAssessmentRepository,
    SelfAssessmentRow,
)

__all__ = [
    "AthleteMemoryRepository",
    "AthleteProfileRepository",
    "ConversationHistoryRepository",
    "PlanRepository",
    "SelfAssessmentRepository",
    "SelfAssessmentRow",
]
