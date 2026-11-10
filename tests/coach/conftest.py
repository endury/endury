"""Coach-specific test fixtures (mock repos, stub intervals client, tools)."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from endury.coach.tools import make_tools
from endury.domain.models.athlete import AthleteProfile
from endury.domain.models.memory import (
    AthleteMemory,
    ConversationHistoryEntry,
)
from endury.intervals.models import (
    ActivityStream,
    IntervalRow,
    IntervalsActivity,
    IntervalsActivityDetail,
    SportSettingsRow,
    WellnessDay,
)
from endury.intervals.models import (
    AthleteProfile as IntervalsAthleteProfile,
)
from tests.doubles import CapturingLLM, FakeLLM

# Stub intervals.icu client


class StubIntervalsClient:
    """Offline stand-in for ``IntervalsClient`` (same method signatures).

    Every read is served from in-memory lists; date windows are honored so
    tests can assert on windowing. Any ``*_error`` flag makes the matching
    read raise, exercising the tools' error paths.
    """

    def __init__(
        self,
        *,
        athlete: IntervalsAthleteProfile | None = None,
        activities: list[IntervalsActivity] | None = None,
        details: list[IntervalsActivityDetail] | None = None,
        wellness: list[WellnessDay] | None = None,
        streams: dict[str, list[ActivityStream]] | None = None,
        athlete_error: Exception | None = None,
        activities_error: Exception | None = None,
        detail_error: Exception | None = None,
        wellness_error: Exception | None = None,
    ) -> None:
        self.athlete = athlete or IntervalsAthleteProfile(id="i1", sportSettings=[])
        self.activities = activities or []
        self.details = {d.id: d for d in details or []}
        self.wellness = wellness or []
        self.streams = streams or {}
        self.athlete_error = athlete_error
        self.activities_error = activities_error
        self.detail_error = detail_error
        self.wellness_error = wellness_error
        self.activity_windows: list[tuple[date, date]] = []
        self.detail_requests: list[str] = []
        self.wellness_windows: list[tuple[date, date]] = []

    def get_athlete(self) -> IntervalsAthleteProfile:
        if self.athlete_error:
            raise self.athlete_error
        return self.athlete

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        self.activity_windows.append((oldest, newest))
        if self.activities_error:
            raise self.activities_error
        return [
            act
            for act in self.activities
            if act.start_date_local
            and oldest <= date.fromisoformat(act.start_date_local[:10]) <= newest
        ]

    def get_activity(self, activity_id: str) -> IntervalsActivityDetail:
        self.detail_requests.append(activity_id)
        if self.detail_error:
            raise self.detail_error
        if activity_id in self.details:
            return self.details[activity_id]
        # Fall back to the listed activity, with no interval breakdown.
        listed = next((a for a in self.activities if a.id == activity_id), None)
        if listed is None:
            raise KeyError(activity_id)
        return IntervalsActivityDetail.model_validate(listed.model_dump())

    def get_activity_streams(
        self, activity_id: str, types: list[str]
    ) -> list[ActivityStream]:
        return [s for s in self.streams.get(activity_id, []) if s.type in types]

    def list_wellness(self, oldest: date, newest: date) -> list[WellnessDay]:
        self.wellness_windows.append((oldest, newest))
        if self.wellness_error:
            raise self.wellness_error
        return [
            row
            for row in self.wellness
            if row.id and oldest <= date.fromisoformat(row.id) <= newest
        ]


def make_activity(
    day: str,
    sport_type: str = "Run",
    *,
    activity_id: str = "a1",
    moving_time: int = 3600,
    load: float = 60.0,
    distance: float | None = 10000.0,
    compliance: float | None = None,
) -> IntervalsActivity:
    """Build one intervals.icu activity for tests."""
    return IntervalsActivity(
        id=activity_id,
        start_date_local=f"{day}T07:00:00",
        type=sport_type,
        moving_time=moving_time,
        icu_training_load=load,
        distance=distance,
        compliance=compliance,
    )


def make_activity_detail(
    day: str,
    sport_type: str = "Run",
    *,
    activity_id: str = "a1",
    intervals: list[IntervalRow] | None = None,
    groups: list[IntervalRow] | None = None,
    **fields: object,
) -> IntervalsActivityDetail:
    """Build one intervals.icu activity detail (with reps/groups) for tests."""
    return IntervalsActivityDetail(
        id=activity_id,
        start_date_local=f"{day}T07:00:00",
        type=sport_type,
        icu_intervals=intervals or [],
        icu_groups=groups or [],
        **fields,
    )


def make_wellness(
    day: str,
    *,
    sleep_secs: int | None = 28800,
    hrv: float | None = 60.0,
    resting_hr: int | None = 50,
) -> WellnessDay:
    """Build one intervals.icu wellness row for tests."""
    return WellnessDay(id=day, sleepSecs=sleep_secs, hrv=hrv, restingHR=resting_hr)


@pytest.fixture
def stub_intervals():
    """A stub intervals client with a run history and wellness rows."""
    return StubIntervalsClient(
        athlete=IntervalsAthleteProfile(
            id="i1",
            sportSettings=[
                SportSettingsRow(
                    types=["Run"], lthr=165, max_hr=190, threshold_pace=3.5
                ),
                SportSettingsRow(types=["Ride"], ftp=260, lthr=155),
            ],
        ),
        activities=[
            make_activity("2026-01-05", activity_id="a1"),
            make_activity("2026-01-07", "Ride", activity_id="a2", load=90.0),
        ],
        wellness=[make_wellness("2026-01-07")],
    )


# Mock repositories


@pytest.fixture
def mock_athlete_repo():
    """Mock AthleteProfileRepository with sample data."""
    repo = MagicMock()
    _profile = AthleteProfile(
        name="Alex",
        experience_level="intermediate",
        primary_sport="running",
        communication_style="balanced",
    )
    repo.get.return_value = _profile
    repo.update.side_effect = lambda data: _profile.model_copy(
        update={k: v for k, v in data.items() if v is not None}
    )
    return repo


@pytest.fixture
def mock_memory_repo():
    """Mock AthleteMemoryRepository holding one document."""
    repo = MagicMock()
    repo.get.return_value = AthleteMemory(
        content="## Preferences\n- Prefers morning workouts",
        updated_at="2024-01-01T08:00:00",
    )
    repo.set.return_value = None
    return repo


@pytest.fixture
def mock_history_repo():
    """Mock ConversationHistoryRepository."""
    repo = MagicMock()
    repo.add.return_value = 11
    repo.get_by_date_range.return_value = [
        ConversationHistoryEntry(
            id=11,
            role="user",
            content="Hello coach",
            created_at="2024-01-02T10:00:00",
        ),
    ]
    return repo


# Coach tools


@pytest.fixture
def coach_tools(mock_athlete_repo, mock_memory_repo, stub_intervals):
    """Create tools with injected mock repositories and stub client."""
    return make_tools(
        athlete_repo=mock_athlete_repo,
        memory_repo=mock_memory_repo,
        repos=MagicMock(),
        intervals_client=stub_intervals,
    )


@pytest.fixture
def fake_llm():
    """Create a fake LLM for testing."""
    return FakeLLM(["Test response"])


@pytest.fixture
def capturing_llm():
    """Create a CapturingLLM with a single default response."""
    return CapturingLLM(["Test response"])
