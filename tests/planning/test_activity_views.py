"""Tests for the in-app activity debrief and intensity-trace views."""

from __future__ import annotations

import pytest

from endury.coach.tools.activity_tools import detail_view
from endury.intervals.client import IntervalsApiError
from endury.intervals.models import ActivityStream, IntervalsActivityDetail
from endury.planning.activity_views import activity_debrief, activity_trace_view


class _Client:
    """Stub intervals client whose get_activity/get_activity_streams responses are scripted."""

    def __init__(
        self,
        detail: IntervalsActivityDetail,
        streams: list[ActivityStream] | None = None,
        streams_exc: Exception | None = None,
    ) -> None:
        self._detail = detail
        self._streams = streams or []
        self._streams_exc = streams_exc
        self.detail_calls: list[str] = []
        self.streams_calls: list[tuple[str, tuple[str, ...]]] = []

    def get_activity(self, activity_id: str) -> IntervalsActivityDetail:
        self.detail_calls.append(activity_id)
        return self._detail

    def get_activity_streams(
        self, activity_id: str, types: list[str]
    ) -> list[ActivityStream]:
        self.streams_calls.append((activity_id, tuple(types)))
        if self._streams_exc is not None:
            raise self._streams_exc
        return self._streams


def test_debrief_is_the_coach_tools_detail_view() -> None:
    """The athlete's debrief and the coach's are the same projection."""
    detail = IntervalsActivityDetail(id="i123", type="Run", moving_time=1800)
    client = _Client(detail)

    assert activity_debrief(client, "i123") == detail_view(detail)
    assert client.detail_calls == ["i123"]


def test_trace_fetches_the_sports_streams() -> None:
    detail = IntervalsActivityDetail(
        id="i123", type="Ride", icu_ftp=250, moving_time=100
    )
    client = _Client(
        detail, streams=[ActivityStream(type="watts", data=[100.0, 200.0])]
    )

    trace = activity_trace_view(client, "i123")

    assert trace["source"] == "streams"
    assert trace["metric"] == "power"
    assert client.streams_calls == [("i123", ("watts", "heartrate", "altitude"))]


def test_trace_of_a_pool_swim_skips_the_streams_call() -> None:
    detail = IntervalsActivityDetail(id="i124", type="Swim", pool_length=25.0)
    client = _Client(detail)

    assert activity_trace_view(client, "i124") == {
        "activity_id": "i124",
        "source": None,
    }
    assert client.streams_calls == []


def test_trace_lets_a_streams_error_propagate() -> None:
    detail = IntervalsActivityDetail(id="i123", type="Ride", icu_ftp=250)
    client = _Client(detail, streams_exc=IntervalsApiError(500, "streams boom"))

    with pytest.raises(IntervalsApiError, match="streams boom"):
        activity_trace_view(client, "i123")
