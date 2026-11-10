"""Tests for endury.planner.sessions.menus.session_spec."""

from __future__ import annotations

import pytest

from endury.planner.foundations.types import ActivityKey, SportType
from endury.planner.sessions.menus.session_spec import (
    SessionSpec,
)


def _run(min_minutes: int = 30, max_minutes: int = 45) -> SessionSpec:
    return SessionSpec(
        activity_key=ActivityKey.RUN_EASY,
        sport=SportType.RUNNING,
        min_minutes=min_minutes,
        max_minutes=max_minutes,
    )


def _swim(min_meters: int = 1000, max_meters: int = 1500) -> SessionSpec:
    return SessionSpec(
        activity_key=ActivityKey.SWIM_ENDURANCE,
        sport=SportType.SWIMMING,
        min_meters=min_meters,
        max_meters=max_meters,
    )


def test_run_spec_accepts_minute_bounds():
    spec = _run(30, 45)
    assert spec.min_minutes == 30
    assert spec.max_minutes == 45


def test_run_spec_rejects_metre_fields_on_non_swim():
    with pytest.raises(ValueError, match="must not"):
        SessionSpec(
            activity_key=ActivityKey.RUN_EASY,
            sport=SportType.RUNNING,
            min_minutes=30,
            max_minutes=45,
            min_meters=100,
            max_meters=200,
        )


def test_run_spec_rejects_zero_minute_bounds():
    with pytest.raises(ValueError, match="positive min_minutes"):
        SessionSpec(
            activity_key=ActivityKey.RUN_EASY,
            sport=SportType.RUNNING,
            min_minutes=0,
            max_minutes=0,
        )


def test_swim_spec_accepts_metre_bounds():
    spec = _swim(1000, 1500)
    assert spec.min_meters == 1000
    assert spec.max_meters == 1500


def test_swim_spec_rejects_minute_fields():
    with pytest.raises(ValueError, match="must use"):
        SessionSpec(
            activity_key=ActivityKey.SWIM_ENDURANCE,
            sport=SportType.SWIMMING,
            min_minutes=10,
            max_minutes=20,
            min_meters=1000,
            max_meters=1500,
        )


def test_swim_spec_rejects_zero_metre_bounds():
    with pytest.raises(ValueError, match="positive min_meters"):
        SessionSpec(
            activity_key=ActivityKey.SWIM_ENDURANCE,
            sport=SportType.SWIMMING,
            min_meters=0,
            max_meters=0,
        )
