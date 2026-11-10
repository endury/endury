"""The in-app activity debrief and intensity-trace views, read live.

The debrief shares its projection with the coach's ``get_activity_detail``
tool, so the athlete and the coach see the same numbers. The trace is a
separate projection the LLM never sees.

Both views let intervals.icu client errors propagate; mapping them to a
response is the caller's job.
"""

from __future__ import annotations

from typing import Any

from endury.coach.tools.activity_tools import detail_view
from endury.intervals.client import IntervalsClient
from endury.intervals.reps import read_activity_detail
from endury.intervals.trace import activity_trace, trace_stream_types


def activity_debrief(client: IntervalsClient, activity_id: str) -> dict[str, Any]:
    """Return the debrief projection for one intervals.icu activity.

    Args:
        client: The intervals.icu read client.
        activity_id: intervals.icu activity id (e.g. ``"i176644752"``).

    Returns:
        Overview, time in zones, interval groups, and per-rep splits.
    """
    return detail_view(*read_activity_detail(client, activity_id))


def activity_trace_view(client: IntervalsClient, activity_id: str) -> dict[str, Any]:
    """Return the debrief's intensity-trace projection for one activity.

    A pool swim fetches no streams (it is drawn from its interval rows) —
    see ``endury.intervals.trace`` for the bucketing/fallback rules.

    Args:
        client: The intervals.icu read client.
        activity_id: intervals.icu activity id (e.g. ``"i176644752"``).

    Returns:
        The wire shape from ``endury.intervals.trace.activity_trace``, or
        ``{"activity_id": ..., "source": None}`` when nothing is drawable.
    """
    detail = client.get_activity(activity_id)
    types = trace_stream_types(detail)
    streams = client.get_activity_streams(activity_id, types) if types else []
    return activity_trace(detail, streams)
