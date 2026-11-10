"""Fixtures for the browser runtime: a pinned clock and a fake account."""

from __future__ import annotations

import pytest

from endury.browser import rpc
from tests.doubles import FakeIntervals
from tests.store.helpers import SIM_TODAY, run_athlete


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    """Pin the clock, drop real keys, and start every test unbooted."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", SIM_TODAY.isoformat())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.setattr(rpc, "_app", None)


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals(athlete=run_athlete())
