"""One account, one owner: a Connect session takes over an API key's events.

intervals.icu matches an upsert or a delete by ``external_id`` only among
the events the caller created. An account written with an API key and then
through Connect intervals.icu would hold two copies of the store note and
of every session, each device reading the other's. So the account moves to
Connect once any device uses it: that session re-creates the key's Endury
events as its own and deletes the originals by server id.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from endury.intervals.client import IntervalsClient
from endury.intervals.models import IntervalsEvent

#: Every event Endury writes carries this ``external_id`` prefix.
_PREFIX = "coachie:"
#: The store notes sit on the last day any event is written to.
_LAST_DAY = date(2099, 12, 31)
#: The fields Endury sets on an event, minus a test's ``icu_training_load``:
#: the server prices every other session itself, and the next push sets it.
_FIELDS = frozenset(
    {
        "category",
        "external_id",
        "start_date_local",
        "type",
        "name",
        "description",
        "tags",
        "hide_from_athlete",
    }
)


def _payload(event: IntervalsEvent) -> dict[str, Any]:
    """Build the event as Endury would push it."""
    return event.model_dump(include=set(_FIELDS), exclude_none=True)


def take_over_key_events(client: IntervalsClient, today: date) -> int:
    """Make this Connect session the owner of the key's Endury events.

    Events dated before ``today`` stay as they are: nothing writes them
    again, and a finished session keeps its paired activity.

    Args:
        client: A client signed in with an OAuth token.
        today: The athlete's current date.

    Returns:
        How many key-owned events were deleted.
    """
    events = [
        event
        for event in client.list_events(today, _LAST_DAY)
        if (event.external_id or "").startswith(_PREFIX)
    ]
    key_owned = [event for event in events if event.oauth_client_id is None]
    if not key_owned:
        return 0
    owned = {event.external_id for event in events if event.oauth_client_id}
    missing = [_payload(e) for e in key_owned if e.external_id not in owned]
    if missing:
        client.bulk_upsert_events(missing)
    return client.delete_events_by_id([e.id for e in key_owned if e.id is not None])
