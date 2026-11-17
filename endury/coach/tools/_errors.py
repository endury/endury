"""Shared error response helpers for coach tools."""

from __future__ import annotations

import json
import logging

import httpx

from endury.common.exceptions import RepositoryError
from endury.intervals.client import IntervalsApiError, IntervalsAuthError

logger = logging.getLogger(__name__)


def tool_error_response(
    action: str,
    error: Exception,
) -> str:
    """Build a standard JSON error response for a tool failure.

    Logs the error and returns a user-friendly JSON string.  When the
    exception is a ``RepositoryError`` the log message includes a
    "Database error" prefix for easier triage.

    Args:
        action: Human-readable description of the failed action
            (e.g. ``"retrieve activities"``).
        error: The caught exception.

    Returns:
        JSON string with ``error`` and ``message`` keys.
    """
    is_db = isinstance(error, RepositoryError)
    prefix = "Database error" if is_db else "Error"
    logger.error("%s %s: %s", prefix, action, error)
    if isinstance(error, IntervalsAuthError):
        message = (
            "intervals.icu rejected Endury's access — connect again or check "
            "the key in Settings."
        )
    elif isinstance(error, (IntervalsApiError, httpx.HTTPError)):
        message = "intervals.icu could not be reached."
    else:
        db_note = "a database error" if is_db else "an error"
        message = f"There was {db_note}. Please try again later."
    return json.dumps(
        {
            "error": f"Failed to {action}.",
            "message": message,
        }
    )
