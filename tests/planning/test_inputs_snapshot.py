"""Tests for the planning-inputs snapshot's degrade-instead-of-raise wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from endury.intervals.client import IntervalsApiError, IntervalsAuthError
from endury.planning.wire.inputs import (
    AUTH_REJECTED_REASON,
    NOT_CONFIGURED_REASON,
    inputs_snapshot,
)

MODULE = "endury.planning.wire.inputs"


def _no_repos() -> Any:
    raise AssertionError("an unconfigured instance must not touch the repositories")


def _repos() -> SimpleNamespace:
    return SimpleNamespace(plan=SimpleNamespace(get_active=lambda: "active-plan"))


def test_unconfigured_reports_not_configured_without_reading_the_db() -> None:
    assert inputs_snapshot(None, _no_repos) == {
        "available": False,
        "reason": NOT_CONFIGURED_REASON,
    }


def test_configured_builds_the_payload_for_the_active_plan() -> None:
    client = MagicMock()
    repos = _repos()

    with patch(
        f"{MODULE}.build_inputs_payload", return_value={"available": True}
    ) as build:
        assert inputs_snapshot(client, lambda: repos) == {"available": True}

    build.assert_called_once_with(client, repos, "active-plan")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (IntervalsApiError(500, "boom"), "boom"),
        (IntervalsAuthError(401, "bad key"), AUTH_REJECTED_REASON),
        (httpx.ConnectError("no route to host"), "no route to host"),
    ],
)
def test_unreachable_or_rejected_degrades_to_unavailable(
    exc: Exception, reason: str
) -> None:
    with patch(f"{MODULE}.build_inputs_payload", side_effect=exc):
        payload = inputs_snapshot(MagicMock(), _repos)

    assert payload["available"] is False
    assert reason in payload["reason"]
