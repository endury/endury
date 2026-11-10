"""Re-export coach fixtures needed by golden replay tests."""

from tests.coach.conftest import (
    coach_tools,
    mock_athlete_repo,
    mock_history_repo,
    mock_memory_repo,
    stub_intervals,
)

__all__ = [
    "coach_tools",
    "mock_athlete_repo",
    "mock_history_repo",
    "mock_memory_repo",
    "stub_intervals",
]
