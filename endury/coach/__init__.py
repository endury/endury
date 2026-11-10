"""Primary entrypoints for the conversational coaching runtime.

Import from this package when booting or testing the coach agent. It exposes
the long-lived runtime object and the per-conversation session wrapper.
"""

from endury.coach.runtime import CoachRuntime
from endury.coach.session import CoachSession

__all__ = ["CoachRuntime", "CoachSession"]
