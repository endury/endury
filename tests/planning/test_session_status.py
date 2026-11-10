"""Tests for the calendar-aware session status write.

A skip is not only a local mark: the workout must also leave the athlete's
intervals.icu calendar, or their watch keeps telling them to do it. These
tests pin both halves — the local row and the remote effect — plus the
un-skip that puts the event back.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.session_status import set_session_status

_PLAN = 7
_SLOT = "w05_tempo_run_0"
_EXTERNAL_ID = f"coachie:{_PLAN}:{_SLOT}"


class _Client:
    """The slice of ``IntervalsClient`` a status write uses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[list[str]] = []
        self.upserted: list[list[dict]] = []

    def bulk_delete_events(self, external_ids: list[str]) -> int:
        if self.fail:
            raise RuntimeError("upstream exploded")
        self.deleted.append(list(external_ids))
        return len(external_ids)

    def bulk_upsert_events(self, events: list[dict]) -> list[object]:
        if self.fail:
            raise RuntimeError("upstream exploded")
        self.upserted.append(events)
        return []


def _event(day: str = "2026-02-10") -> dict:
    return {
        "category": "WORKOUT",
        "external_id": _EXTERNAL_ID,
        "start_date_local": f"{day}T00:00:00",
        "type": "Run",
        "name": "Tempo Run",
        "description": "prose\n\n- Tempo 20m",
        "tags": ["coachie"],
    }


def _row(
    *,
    status: str = "pending",
    matched: str | None = None,
    pushed: dict | None = None,
    day: str = "2026-02-10",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=501,
        plan_id=_PLAN,
        slot_id=_SLOT,
        date=day,
        status=status,
        matched_activity_id=matched,
        pushed_event_json=json.dumps(pushed) if pushed else None,
    )


def _repos(row: object | None = None, *, active: bool = True) -> MagicMock:
    repos = MagicMock()
    repos.plan.get_active.return_value = SimpleNamespace(id=_PLAN) if active else None
    repos.plan.get_sessions.return_value = [] if row is None else [row]
    repos.session.transaction.return_value = nullcontext()
    return repos


class TestValidation:
    def test_an_unknown_status_is_refused_before_any_read(self) -> None:
        repos = _repos()
        out = set_session_status(
            repos=repos, client=_Client(), session_id=501, status="bogus"
        )
        assert "Invalid status" in out["error"]
        assert out["options"] == ["completed", "pending", "skipped", "unlinked"]
        repos.plan.get_active.assert_not_called()

    def test_without_an_active_plan(self) -> None:
        out = set_session_status(
            repos=_repos(active=False),
            client=_Client(),
            session_id=501,
            status="skipped",
        )
        assert out == {"message": NO_ACTIVE_PLAN_MESSAGE}

    def test_a_session_outside_the_active_plan(self) -> None:
        repos = _repos(_row())
        out = set_session_status(
            repos=repos, client=_Client(), session_id=999, status="skipped"
        )
        assert "not part of the active plan" in out["error"]
        repos.plan.update_session_status.assert_not_called()


class TestActivityLink:
    def test_omitting_the_activity_preserves_an_existing_link(self) -> None:
        repos = _repos(_row(matched="act-9"))
        out = set_session_status(
            repos=repos, client=_Client(), session_id=501, status="completed"
        )
        assert out["activity_id"] == "act-9"
        repos.plan.update_session_status.assert_called_once_with(
            501, "completed", "act-9"
        )

    def test_unlinked_clears_the_link_even_when_one_is_supplied(self) -> None:
        repos = _repos(_row(matched="act-9"))
        out = set_session_status(
            repos=repos,
            client=_Client(),
            session_id=501,
            status="unlinked",
            activity_id="act-9",
        )
        assert out["activity_id"] is None
        repos.plan.update_session_status.assert_called_once_with(501, "unlinked", None)


class TestSkipDeletesTheEvent:
    def test_a_skip_deletes_the_sessions_calendar_event(self) -> None:
        repos = _repos(_row(pushed=_event()))
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="skipped"
        )

        assert out["new_status"] == "skipped"
        assert out["push"] == {
            "status": "ok",
            "errors": [],
            "message": None,
            "deleted": 1,
        }
        assert client.deleted == [[_EXTERNAL_ID]]

    def test_the_external_id_is_derived_not_read_from_the_stored_event(self) -> None:
        # A hand-mangled pushed payload must not misdirect the delete.
        repos = _repos(_row(pushed={"external_id": "coachie:99:other"}))
        client = _Client()

        set_session_status(repos=repos, client=client, session_id=501, status="skipped")

        assert client.deleted == [[_EXTERNAL_ID]]

    def test_a_never_pushed_session_is_still_deleted_by_id(self) -> None:
        # intervals.icu ignores an unknown external_id, so the blind delete
        # is cheaper than tracking whether the push ever landed.
        repos = _repos(_row())
        client = _Client()

        set_session_status(repos=repos, client=client, session_id=501, status="skipped")

        assert client.deleted == [[_EXTERNAL_ID]]

    def test_re_skipping_makes_no_remote_call(self) -> None:
        repos = _repos(_row(status="skipped", pushed=_event()))
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="skipped"
        )

        assert out["push"] is None
        assert client.deleted == []

    def test_completed_never_touches_the_calendar(self) -> None:
        repos = _repos(_row(status="skipped", pushed=_event()))
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="completed"
        )

        assert out["push"] is None
        assert (client.deleted, client.upserted) == ([], [])

    def test_a_failed_delete_is_reported_not_raised(self) -> None:
        repos = _repos(_row(pushed=_event()))

        out = set_session_status(
            repos=repos,
            client=_Client(fail=True),
            session_id=501,
            status="skipped",
        )

        assert out["new_status"] == "skipped"
        assert out["push"]["status"] == "error"
        assert out["push"]["errors"] == ["upstream exploded"]
        assert "intervals.icu" in out["push"]["message"]
        repos.plan.update_session_status.assert_called_once()

    def test_without_a_client_the_remote_side_is_skipped(self) -> None:
        out = set_session_status(
            repos=_repos(_row(pushed=_event())),
            client=None,
            session_id=501,
            status="skipped",
        )
        assert out["push"] == {
            "status": "skipped",
            "errors": [],
            "message": PUSH_SKIPPED_MESSAGE,
            "deleted": 0,
        }


class TestUnSkipRestoresTheEvent:
    def test_un_skipping_re_pushes_the_event_on_the_rows_date(self) -> None:
        # The event was deleted on the way in, so the stored payload is
        # re-dated to wherever the session sits now.
        repos = _repos(
            _row(status="skipped", pushed=_event("2026-02-10"), day="2026-02-12")
        )
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="pending"
        )

        assert out["push"] == {
            "status": "ok",
            "errors": [],
            "message": None,
            "pushed": 1,
        }
        event = client.upserted[0][0]
        assert event["start_date_local"] == "2026-02-12T00:00:00"
        assert event["description"] == "prose\n\n- Tempo 20m"
        # A payload pushed under the former tag comes back as #endury.
        assert event["tags"] == ["endury"]
        repos.plan.update_pushed_events.assert_called_once_with(
            _PLAN, {_SLOT: json.dumps(event)}
        )

    def test_unlinked_also_restores_the_event(self) -> None:
        repos = _repos(_row(status="skipped", pushed=_event()))
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="unlinked"
        )

        assert out["push"]["pushed"] == 1
        assert client.upserted

    def test_a_never_pushed_session_points_at_sync_now(self) -> None:
        repos = _repos(_row(status="skipped"))
        client = _Client()

        out = set_session_status(
            repos=repos, client=client, session_id=501, status="pending"
        )

        assert out["push"]["status"] == "skipped"
        assert "Sync now" in out["push"]["message"]
        assert client.upserted == []

    def test_a_failed_restore_is_reported_not_raised(self) -> None:
        repos = _repos(_row(status="skipped", pushed=_event()))

        out = set_session_status(
            repos=repos,
            client=_Client(fail=True),
            session_id=501,
            status="pending",
        )

        assert out["new_status"] == "pending"
        assert out["push"]["status"] == "error"
        assert out["push"]["errors"] == ["upstream exploded"]
        repos.plan.update_pushed_events.assert_not_called()

    def test_without_a_client_the_restore_is_skipped(self) -> None:
        out = set_session_status(
            repos=_repos(_row(status="skipped", pushed=_event())),
            client=None,
            session_id=501,
            status="pending",
        )
        assert out["push"] == {
            "status": "skipped",
            "errors": [],
            "message": PUSH_SKIPPED_MESSAGE,
            "pushed": 0,
        }
