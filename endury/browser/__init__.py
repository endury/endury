"""The browser runtime: Endury's Python side inside a Pyodide Web Worker.

The page's ``web/data/api.js`` keeps its exported names; each call becomes
one :func:`endury.browser.rpc.dispatch` in the worker, which answers an
envelope. The database lives in memory, a cache of the store note on the
athlete's intervals.icu calendar — this package is the only one that
imports :mod:`endury.store`.

Modules:
    api: One method per ``api.js`` export, over the booted session.
    demo: The demo athlete: an in-memory account and a plan four weeks in.
    rpc: ``boot`` and ``dispatch`` — the method table, the envelope, the
        store-revision check before writes and the save after every call.

This module holds what both share: the booted session and the error a
method refuses with.
"""

from __future__ import annotations

from dataclasses import dataclass

from endury.coach.runtime import CoachRuntime
from endury.coach.session import CoachSession
from endury.common.exceptions import EnduryError
from endury.intervals.client import IntervalsClient
from endury.wiring.config import CoachConfig
from endury.wiring.container import RepositoryContainer

#: Said by every method that needs the coach when no OpenRouter key is set.
CHAT_DISABLED_MESSAGE = (
    "Coach chat is disabled: add an OpenRouter API key in Settings to use it."
)

#: Said for a failure Endury did not foresee; the exception itself is logged.
CRASH_MESSAGE = "Something went wrong in Endury. Reload and try again."


class RpcError(EnduryError):
    """A method refused its call; ``code`` is the envelope's ``error_code``."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class BrowserApp:
    """One booted browser session.

    Attributes:
        config: The settings the page booted with.
        repos: The in-memory database, a cache of the store note.
        client: The athlete's intervals.icu client.
        coach: The conversation session, or None without an OpenRouter key.
        rev: The store revision this session last loaded or saved.
        read_only: Whether a newer bundle wrote the store, so nothing may
            change or be saved.
        chat_saved: The newest ``conversation_history`` id the chat note
            already holds.
        takes: The coach's saved takes on activities, oldest first, read
            from their note on first use (None until then).
        takes_unsaved: Whether a take waits to reach that note.
    """

    config: CoachConfig
    repos: RepositoryContainer
    client: IntervalsClient
    coach: CoachSession | None = None
    rev: int = 0
    read_only: bool = False
    connect_owned: bool = False
    chat_saved: int = 0
    takes: list[dict[str, str]] | None = None
    takes_unsaved: bool = False

    @property
    def runtime(self) -> CoachRuntime | None:
        """Return the coach runtime, or None without a coach."""
        return self.coach.runtime if self.coach is not None else None

    def maybe_get_coach(self) -> CoachSession | None:
        """Return the coach session, or None (the planning commands' accessor)."""
        return self.coach

    def get_repos(self) -> RepositoryContainer:
        """Return the repository container (the planning commands' factory)."""
        return self.repos

    def require_coach(self) -> CoachSession:
        """Return the coach session, refusing when chat is disabled."""
        if self.coach is None:
            raise RpcError(CHAT_DISABLED_MESSAGE, "chat_disabled")
        return self.coach


__all__ = ["CHAT_DISABLED_MESSAGE", "BrowserApp", "RpcError"]
