"""Pytest configuration for Endury tests.

Shared fixtures: in-memory database session, repository container, and real
repository instances. Coach-specific fixtures live in tests/coach/conftest.py.
"""

from dataclasses import replace
from pathlib import PurePosixPath

import pytest

from endury.database.sqlite.session import DatabaseSession
from endury.planner.foundations.types import AmbitionLevel, GoalType, PlanMaturity
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planning.offered_plans import OFFERED_TIERS
from endury.wiring.container import RepositoryContainer

_DOMAIN_MARKER_BY_DIR = {
    "analysis": "analytics",
    "analytics": "analytics",
    "architecture": "architecture",
    "coach": "coach",
    "db": "db",
    "golden": "golden",
    "intervals": "intervals",
    "planner": "planning",
    "planning": "planning",
}

_LAYER_MARKER_BY_DIR = {
    "analysis": "unit",
    "architecture": "unit",
    "coach": "unit",
    "db": "unit",
    "intervals": "unit",
    "planner": "unit",
    "planning": "unit",
    "golden": "integration",
}


def _tests_relative_path(item: pytest.Item) -> PurePosixPath:
    """Return item path relative to the ``tests`` root when possible."""
    raw_path = getattr(item, "path", None)
    if raw_path is not None:
        path = PurePosixPath(raw_path.as_posix())
    else:  # pragma: no cover - compatibility fallback
        path = PurePosixPath(str(item.fspath).replace("\\", "/"))  # type: ignore[attr-defined]
    if "tests" not in path.parts:
        return path
    idx = path.parts.index("tests")
    return PurePosixPath(*path.parts[idx:])


def _apply_path_markers(item: pytest.Item) -> None:
    """Apply stable domain/layer markers using the test file path."""
    path = _tests_relative_path(item)
    if len(path.parts) < 2 or path.parts[0] != "tests":
        return

    top_dir = path.parts[1]
    domain = _DOMAIN_MARKER_BY_DIR.get(top_dir)
    if domain:
        item.add_marker(getattr(pytest.mark, domain))

    layer = _LAYER_MARKER_BY_DIR.get(top_dir)
    if layer:
        item.add_marker(getattr(pytest.mark, layer))

    stem = path.stem.lower()
    if "contract" in stem:
        item.add_marker(pytest.mark.contract)
    if "llm" in stem:
        item.add_marker(pytest.mark.llm)
    if "golden" in path.parts:
        item.add_marker(pytest.mark.golden)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Normalize marker coverage across tests without hand-marking each file."""
    for item in items:
        _apply_path_markers(item)


#: The goals holding the scorecard evidence for Alpha; each lacks only the
#: menu reviews ``docs/plan_maturity.md`` logs.
SCORECARD_CHECKED_GOALS = frozenset(
    {
        GoalType.IRONMAN,
        GoalType.HALF_IRONMAN,
        GoalType.RUNNING_MARATHON,
        GoalType.RUNNING_HALF_MARATHON,
        GoalType.SPRINT_TRIATHLON,
        GoalType.OLYMPIC_TRIATHLON,
        GoalType.TRAIL_50K,
        GoalType.RUNNING_10K,
        GoalType.RUNNING_MAINTENANCE,
        GoalType.TRIATHLON_MAINTENANCE,
    }
)


@pytest.fixture
def alpha_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Label the scorecard-checked goals Alpha, as once their menus are reviewed.

    No goal is Alpha until its menus are reviewed, so a test of the Alpha
    path, or of a plan Experimental for its conditions alone, runs on this.
    """
    for goal in SCORECARD_CHECKED_GOALS:
        monkeypatch.setitem(
            GOAL_PROFILES,
            goal,
            replace(GOAL_PROFILES[goal], maturity=PlanMaturity.ALPHA),
        )


@pytest.fixture(autouse=True)
def every_plan_offered(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offer every goal at every tier, as most of the suite needs.

    The menus offer only a few plans (``offered:``), and most tests build
    others. A test of the offer itself asks for ``offered_as_shipped``.
    """
    if "offered_as_shipped" in request.fixturenames:
        return
    for goal in GoalType:
        monkeypatch.setitem(OFFERED_TIERS, goal, frozenset(AmbitionLevel))


@pytest.fixture
def offered_as_shipped() -> None:
    """Keep the menus' own ``offered:`` sets; see ``every_plan_offered``."""


@pytest.fixture
def session() -> DatabaseSession:
    """Fresh in-memory DatabaseSession for each test."""
    s = DatabaseSession(":memory:")
    s.init_schema()
    return s


@pytest.fixture
def container() -> RepositoryContainer:
    """Fresh in-memory RepositoryContainer for each test."""
    return RepositoryContainer.in_memory()
