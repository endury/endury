"""Tests for endury.coach.tools._errors."""

from __future__ import annotations

import json

import httpx

from endury.coach.tools._errors import tool_error_response
from endury.common.exceptions import RepositoryError
from endury.intervals.client import IntervalsApiError, IntervalsAuthError


class TestToolErrorResponse:
    def test_an_auth_error_points_to_settings(self) -> None:
        body = json.loads(
            tool_error_response("fetch data", IntervalsAuthError(401, "bad key"))
        )
        assert body["message"] == (
            "intervals.icu rejected Endury's access — connect again or check "
            "the key in Settings."
        )

    def test_an_api_error_reports_unreachable(self) -> None:
        body = json.loads(
            tool_error_response("fetch data", IntervalsApiError(500, "boom"))
        )
        assert body["message"] == "intervals.icu could not be reached."

    def test_a_transport_error_reports_unreachable(self) -> None:
        body = json.loads(
            tool_error_response("fetch data", httpx.ConnectError("no route"))
        )
        assert body["message"] == "intervals.icu could not be reached."

    def test_a_database_error_keeps_the_generic_message(self) -> None:
        body = json.loads(tool_error_response("save plan", RepositoryError("db down")))
        assert body["message"] == "There was a database error. Please try again later."

    def test_any_other_error_keeps_the_generic_message(self) -> None:
        body = json.loads(tool_error_response("do thing", ValueError("bad")))
        assert body["message"] == "There was an error. Please try again later."
