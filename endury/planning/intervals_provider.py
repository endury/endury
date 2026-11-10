"""Optional access to an intervals.icu client.

``RepositoryContainer.intervals_client`` raises when no API key is
configured — the right behaviour for reads that cannot proceed without the
data. Flows that must degrade instead of fail (calendar push, planning
inputs, calendar compliance) resolve the client through here: no key means
``None``, never an exception. The client is memoized per configuration, so
repeated calls share one connection pool.
"""

from __future__ import annotations

from functools import lru_cache

from endury.intervals.client import IntervalsClient
from endury.wiring.config import CoachConfig, get_coach_config


@lru_cache(maxsize=1)
def _build_client(api_key: str, athlete_id: str, base_url: str) -> IntervalsClient:
    """Build (and memoize) the client for one configuration triple."""
    return IntervalsClient(api_key, athlete_id=athlete_id, base_url=base_url)


def get_intervals_client(config: CoachConfig | None = None) -> IntervalsClient | None:
    """Return the shared intervals.icu client, or ``None`` without a key.

    Args:
        config: Coach configuration; loaded from the environment when
            omitted.

    Returns:
        The shared client, or ``None`` when this instance has no usable
        ``INTERVALS_API_KEY`` — callers treat that as "skipped", never an
        error.
    """
    config = config or get_coach_config()
    if not config.intervals_api_key:
        return None
    return _build_client(
        config.intervals_api_key,
        config.intervals_athlete_id,
        config.intervals_base_url,
    )


__all__ = ["get_intervals_client"]
