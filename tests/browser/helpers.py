"""Shared helpers for the browser-runtime tests: boot, one-line calls, a plan.

Every test drives :func:`endury.browser.rpc.dispatch` the way the worker
does, against a :class:`FakeIntervals` account behind a real
``IntervalsClient`` and, for the coach, a :class:`FakeLLM`.
"""

from __future__ import annotations

from typing import Any

from endury.browser import BrowserApp, rpc
from tests.doubles import FakeIntervals

SETTINGS = {"intervals_api_key": "fake-key"}

#: A 10K plan the fake account can commit once running volume is on file.
#: The 10K is Experimental, so the request carries the athlete's confirmation.
TEN_K = {
    "races": [{"goal_type": "running_10k", "race_date": "2026-12-20"}],
    "training_start": "2026-10-05",
    "ambition_level": "basic",
    "accept_experimental": True,
}
RUNNING_VOLUME = {
    "sports": {"running": {"weekly_hours": 5, "longest_session_min": 100}}
}


async def boot(fake: FakeIntervals, llm: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Boot against ``fake`` and return the boot result."""
    envelope = await rpc.boot(SETTINGS, transport=fake.transport(), llm=llm, **kwargs)
    assert envelope["ok"] is True, envelope
    return envelope["result"]


async def call(method: str, **params: Any) -> dict[str, Any]:
    """Dispatch one method and return its envelope."""
    return await rpc.dispatch(method, params)


async def ok(method: str, **params: Any) -> Any:
    """Dispatch one method that must succeed and return its result."""
    envelope = await call(method, **params)
    assert envelope["ok"] is True, envelope
    return envelope["result"]


def app() -> BrowserApp:
    """The booted session."""
    assert rpc._app is not None
    return rpc._app


async def commit_ten_k() -> int:
    """Store running volume, then generate and commit the 10K plan."""
    await ok("put_self_assessment", **RUNNING_VOLUME)
    event = await ok("generate_plan", **TEN_K)
    assert event["type"] == "result"
    assert event["status"] == "ok", event
    preview = {k: v for k, v in event.items() if k not in ("type", "source")}
    outcome = await ok(
        "planner_commit",
        name="Autumn 10K",
        generate_request=TEN_K,
        generate_result=preview,
    )
    assert outcome["status"] == "ok", outcome
    return int(outcome["plan_id"])
