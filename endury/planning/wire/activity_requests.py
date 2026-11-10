"""Pydantic request models for pairing an activity with a planned workout."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PairBySessionRequest(BaseModel):
    """Pair a completed activity with a local plan session row.

    The row's intervals.icu event is resolved by external_id readback.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: int


class PairByEventRequest(BaseModel):
    """Pair a completed activity with an intervals.icu event id directly.

    Coach-scheduled ad-hoc workouts exist only on intervals.icu, so their
    event id is all the client has for them.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: int


#: Params of the ``pair_activity`` RPC method. "Exactly one target" is
#: structural rather than a validator: each shape forbids the other's key,
#: so params carrying both — or neither — match neither member and the call
#: fails validation (``invalid_request``).
PairActivityRequest = PairBySessionRequest | PairByEventRequest
