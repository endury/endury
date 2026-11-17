"""boot and dispatch: the envelope, the store revision check, saving."""

from __future__ import annotations

import httpx

from endury.browser import CRASH_MESSAGE, api, rpc
from endury.common import clock
from endury.database.sqlite.migrations import LATEST_VERSION
from endury.intervals.client import IntervalsClient
from endury.intervals.models import IntervalsActivity
from endury.store import chat as store_chat
from tests.browser.helpers import SETTINGS, app, boot, call, ok
from tests.doubles import FakeIntervals, FakeLLM
from tests.store.helpers import (
    SIM_TODAY,
    STORE_ID,
    decode_note,
    execute,
    query,
    sequences,
    tamper_state,
)


def _bump_remote_rev(fake: FakeIntervals) -> None:
    """Another device saved: the note moves one revision ahead."""
    tamper_state(fake, lambda state: state.update(rev=state["rev"] + 1))


# before boot


async def test_health_answers_before_boot():
    assert await call("health") == {
        "ok": True,
        "result": {"status": "ok", "ready": False, "coach_ready": False},
        "unsaved": False,
    }


async def test_methods_refuse_before_boot():
    envelope = await call("get_memory")
    assert envelope["ok"] is False
    assert envelope["error_code"] == "not_booted"
    assert envelope["unsaved"] is False


# boot


async def test_boot_on_an_empty_account_seeds_plan_ids(fake):
    # The seed is taken from the injectable clock, which the browser fixtures
    # pin, so measure "before" on that clock rather than on the wall.
    before = int(clock.now().timestamp())

    result = await boot(fake)

    assert result == {"coach_ready": False, "read_only": False, "rev": 0}
    assert sequences(app().repos.session)["plans"] >= before
    assert await ok("health") == {"status": "ok", "ready": True, "coach_ready": False}
    assert STORE_ID not in fake.events


async def test_boot_through_dispatch(fake, monkeypatch):
    def fake_client(*args, transport=None, **kwargs):
        assert transport is None  # the worker cannot hand one over
        return IntervalsClient(*args, transport=fake.transport(), **kwargs)

    monkeypatch.setattr(rpc, "IntervalsClient", fake_client)

    envelope = await rpc.dispatch("boot", {"settings": {**SETTINGS, "model": ""}})

    assert envelope == {
        "ok": True,
        "result": {"coach_ready": False, "read_only": False, "rev": 0},
        "unsaved": False,
        "read_only": False,
    }
    assert app().config.model == "anthropic/claude-haiku-5.5"


async def test_boot_refuses_unknown_parameters():
    envelope = await rpc.dispatch("boot", {"settings": SETTINGS, "colour": "red"})

    assert envelope["ok"] is False
    assert envelope["error_code"] == "invalid_request"
    assert envelope["unsaved"] is False


async def test_boot_without_an_intervals_key_is_invalid_settings(fake):
    envelope = await rpc.boot({"intervals_api_key": "  "}, transport=fake.transport())

    assert envelope["ok"] is False
    assert envelope["error_code"] == "invalid_settings"
    assert rpc._app is None


async def test_boot_on_a_connect_token_needs_no_key(fake):
    sent: list[str] = []
    account = fake.transport()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["authorization"])
        return account.handle_request(request)

    envelope = await rpc.boot(
        {"intervals_access_token": "oauth-token"},
        transport=httpx.MockTransport(handler),
    )

    assert envelope["ok"] is True, envelope
    assert set(sent) == {"Bearer oauth-token"}


async def test_a_failed_reboot_leaves_the_session_unbooted(fake):
    await boot(fake)

    envelope = await rpc.boot({}, transport=fake.transport())

    assert envelope["error_code"] == "invalid_settings"
    assert (await call("get_memory"))["error_code"] == "not_booted"


async def test_an_openrouter_key_turns_the_coach_on(fake):
    envelope = await rpc.boot(
        {**SETTINGS, "openrouter_api_key": "sk-or-v1-0123abcd"},
        transport=fake.transport(),
    )

    assert envelope["result"]["coach_ready"] is True
    assert app().coach is not None


async def test_boot_restores_the_store_and_the_conversation(fake):
    await boot(fake, llm=FakeLLM(["Yes, easy."]))
    await ok("put_memory", content="- Runs before work.")
    await ok("chat", message="Can I run tomorrow?")
    created_at = query(
        app().repos.session, "SELECT created_at FROM conversation_history ORDER BY id"
    )

    result = await boot(fake, llm=FakeLLM())

    assert result == {"coach_ready": True, "read_only": False, "rev": 1}
    assert (await ok("get_memory"))["content"] == "- Runs before work."
    assert (await ok("conversation_recent"))["messages"] == [
        {"role": "user", "content": "Can I run tomorrow?"},
        {"role": "assistant", "content": "Yes, easy."},
    ]
    assert (
        query(
            app().repos.session,
            "SELECT created_at FROM conversation_history ORDER BY id",
        )
        == created_at
    )


async def test_boot_with_a_coach_starts_clean(fake):
    await boot(fake, llm=FakeLLM())

    assert (await call("flush"))["unsaved"] is False
    assert STORE_ID not in fake.events


async def test_a_corrupt_store_refuses_boot_until_the_athlete_starts_fresh(fake):
    for external_id in (STORE_ID, f"{STORE_ID}:2"):
        fake.events[external_id] = {
            "id": len(fake.events) + 1,
            "external_id": external_id,
            "category": "NOTE",
            "start_date_local": "2099-12-31T00:00:00",
            "description": "hand-edited",
        }

    envelope = await rpc.boot(SETTINGS, transport=fake.transport())
    assert envelope["ok"] is False
    assert envelope["error_code"] == "store_corrupt"

    result = await boot(fake, start_fresh=True)

    assert result["rev"] == 0
    assert STORE_ID not in fake.events
    assert f"{STORE_ID}:2" not in fake.events


async def test_start_fresh_on_an_empty_account_deletes_nothing(fake):
    await boot(fake, start_fresh=True)

    assert ("PUT", "/athlete/0/events/bulk-delete") not in fake.calls


# the chat note


async def test_a_chat_turn_saves_its_own_note_without_moving_the_store(fake):
    await boot(fake, llm=FakeLLM())

    envelope = await call("chat", message="hi there")

    assert envelope["ok"] is True
    assert envelope["unsaved"] is False
    assert app().rev == 0
    assert STORE_ID not in fake.events
    assert store_chat.CHAT_ID in fake.events


async def test_a_failed_chat_upload_stays_unsaved_until_flush(fake):
    await boot(fake, llm=FakeLLM())
    fake.fail_next(400, "POST")

    envelope = await call("chat", message="hi there")

    assert envelope["ok"] is True
    assert envelope["unsaved"] is True
    assert store_chat.CHAT_ID not in fake.events

    retry = await call("flush")

    assert retry["unsaved"] is False
    assert store_chat.CHAT_ID in fake.events


async def test_reset_chat_wipes_the_note_to_the_marker(fake):
    await boot(fake, llm=FakeLLM())
    await ok("chat", message="hi there")

    await ok("reset_chat")

    assert [row["role"] for row in store_chat.load(fake.client())] == ["reset"]

    result = await boot(fake, llm=FakeLLM())

    assert result["coach_ready"] is True
    assert (await ok("conversation_recent"))["messages"] == []


async def test_a_read_only_boot_never_writes_the_chat_note(fake):
    await boot(fake, llm=FakeLLM())
    await ok("chat", message="hi there")
    result = await boot(fake, llm=FakeLLM(), read_only=True)
    assert result["read_only"] is True
    note_before = fake.events[store_chat.CHAT_ID]["description"]
    # A local row beyond what the note holds, as if the coach had chatted
    # in this read-only tab (it cannot: `chat` refuses under read_only) —
    # even so, flush must never write it.
    execute(
        app().repos.session,
        "INSERT INTO conversation_history (role, content, created_at) "
        "VALUES ('user', 'sneaky', '2026-10-07T12:00:00')",
    )

    flushed = await call("flush")

    assert flushed["unsaved"] is False
    assert fake.events[store_chat.CHAT_ID]["description"] == note_before


async def test_boot_drops_a_maintenance_block_stored_as_a_race(fake):
    # Older commits wrote a maintenance block's end into the race table.
    await boot(fake)
    execute(
        app().repos.session,
        "INSERT INTO athlete_goals (name, race_date, goal_type, created_at, "
        "updated_at) VALUES ('Triathlon maintenance', '2026-12-20', "
        "'triathlon_maintenance', '', ''), ('Roth', '2027-07-04', 'ironman', '', '')",
    )
    await ok("flush")

    await boot(fake)

    rows = query(app().repos.session, "SELECT name FROM athlete_goals")
    assert rows == [{"name": "Roth"}]


async def test_boot_puts_a_planned_race_the_calendar_lost_back_on(fake):
    await boot(fake)
    execute(
        app().repos.session,
        "INSERT INTO athlete_goals (name, race_date, goal_type, priority, created_at, "
        "updated_at) VALUES ('Ridge Marathon', '2027-03-06', 'trail_50k', 'A', '', '')",
    )
    await ok("flush")
    race_id = query(app().repos.session, "SELECT id FROM athlete_goals")[0]["id"]
    assert fake.event(f"coachie:race:{race_id}") is None

    await boot(fake, read_only=True)
    assert fake.event(f"coachie:race:{race_id}") is None

    await boot(fake)
    event = fake.event(f"coachie:race:{race_id}")
    assert event["category"] == "RACE_A"
    assert event["start_date_local"].startswith("2027-03-06")


async def test_boot_completes_a_race_the_athlete_has_run(fake):
    fake.activities.append(
        IntervalsActivity(
            id="i9", type="TrailRun", start_date_local=f"{SIM_TODAY}T10:00:00"
        )
    )
    await boot(fake)
    app().repos.races.upsert(
        name="Ridge Marathon", race_date=SIM_TODAY.isoformat(), goal_type="trail_50k"
    )
    await ok("flush")

    await boot(fake, read_only=True)
    assert app().repos.races.list()[0].status == "planned"

    await boot(fake)
    await ok("flush")
    await boot(fake, read_only=True)
    assert app().repos.races.list()[0].status == "completed"


# dispatch


async def test_a_durable_write_saves_the_next_revision(fake):
    await boot(fake)

    envelope = await call("put_memory", content="- Likes hills.")

    assert envelope["ok"] is True
    assert envelope["unsaved"] is False
    assert app().rev == 1
    memory, state = decode_note(fake.events[STORE_ID]["description"])
    assert "- Likes hills." in memory
    assert state["rev"] == 1


async def test_a_read_saves_nothing(fake):
    await boot(fake)

    envelope = await call("get_memory")

    assert envelope["unsaved"] is False
    assert STORE_ID not in fake.events


async def test_a_write_after_another_device_saved_is_a_conflict(fake):
    await boot(fake)
    await ok("put_memory", content="- First.")
    _bump_remote_rev(fake)

    envelope = await call("put_memory", content="- Second.")

    assert envelope["ok"] is False
    assert envelope["error_code"] == "store_conflict"
    assert (await ok("get_memory"))["content"] == "- First."


async def test_a_save_that_conflicts_answers_the_conflict(fake):
    await boot(fake)
    await ok("put_memory", content="- First.")
    _bump_remote_rev(fake)
    execute(app().repos.session, "UPDATE athlete_memory SET content = 'x'")

    envelope = await call("flush")

    # The call itself succeeded; the refused save rides along as its code.
    assert envelope["ok"] is True
    assert envelope["error_code"] == "store_conflict"
    assert envelope["unsaved"] is True


async def test_a_failed_upload_stays_unsaved_until_the_next_call(fake):
    await boot(fake)
    fake.fail_next(400, "POST")

    envelope = await call("put_memory", content="- Keep me.")

    assert envelope["ok"] is True
    assert envelope["unsaved"] is True
    assert STORE_ID not in fake.events

    retry = await call("flush")

    assert retry == {
        "ok": True,
        "result": {"status": "ok"},
        "unsaved": False,
        "read_only": False,
    }
    assert app().rev == 1
    assert "- Keep me." in fake.events[STORE_ID]["description"]


async def test_a_store_from_a_newer_bundle_is_read_only(fake):
    await boot(fake)
    await ok("put_memory", content="- Written by the future.")
    tamper_state(fake, lambda state: state.update(db_user_version=LATEST_VERSION + 1))
    note_before = fake.events[STORE_ID]["description"]

    result = await boot(fake)
    envelope = await call("put_memory", content="- Overwrite.")
    execute(app().repos.session, "UPDATE athlete_memory SET content = 'local'")
    flushed = await call("flush")

    assert result["read_only"] is True
    assert envelope["error_code"] == "store_read_only"
    assert envelope["read_only"] is True
    # Read-only never saves, so it never reports anything as pending upload.
    assert flushed["unsaved"] is False
    assert fake.events[STORE_ID]["description"] == note_before


async def test_unknown_methods_are_refused(fake):
    await boot(fake)

    envelope = await call("drop_tables")

    assert envelope["ok"] is False
    assert envelope["error_code"] == "unknown_method"


async def test_unknown_parameters_are_refused(fake):
    await boot(fake)

    envelope = await call("get_memory", verbose=True)

    assert envelope["error_code"] == "invalid_request"
    assert "Bad parameters" in envelope["error"]


async def test_validation_errors_are_invalid_requests(fake):
    await boot(fake)

    envelope = await call("put_self_assessment", sports={})

    assert envelope["error_code"] == "invalid_request"
    assert "Answer at least one sport" in envelope["error"]


async def test_an_intervals_auth_failure_has_its_own_code(fake):
    await boot(fake)
    fake.fail_next(401, "GET")

    envelope = await call("put_memory", content="- x")

    assert envelope["error_code"] == "intervals_auth"


async def test_an_intervals_failure_is_an_upstream_error(fake):
    await boot(fake)
    fake.fail_next(400, "GET")

    envelope = await call("put_memory", content="- x")

    assert envelope["error_code"] == "upstream_error"


async def test_a_planning_refusal_is_refused_not_internal(fake):
    await boot(fake)

    envelope = await call("replan", confirm=True)

    assert envelope["ok"] is False
    assert envelope["error_code"] == "refused"


async def test_an_unexpected_failure_is_an_internal_error(fake, monkeypatch, caplog):
    await boot(fake)

    def boom(**_kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(api, "build_bootstrap_payload", boom)

    envelope = await call("planner_bootstrap")

    # The athlete reads a plain sentence; the exception goes to the log.
    assert envelope == {
        "ok": False,
        "error": CRASH_MESSAGE,
        "error_code": "internal_error",
        "unsaved": False,
        "read_only": False,
    }
    assert "kaput" in caplog.text


def test_the_method_table_names_every_mutating_method():
    assert set(rpc.METHODS) >= rpc.MUTATING


async def test_a_read_only_boot_never_saves(fake):
    await boot(fake)
    await ok("put_memory", content="- From the writer tab.")
    note_before = fake.events[STORE_ID]["description"]

    result = await boot(fake, read_only=True)
    execute(app().repos.session, "UPDATE athlete_memory SET content = 'derived'")
    flushed = await call("flush")
    refused = await call("put_memory", content="- From the second tab.")

    assert result["read_only"] is True
    assert flushed["unsaved"] is False
    assert refused["error_code"] == "store_read_only"
    assert fake.events[STORE_ID]["description"] == note_before
