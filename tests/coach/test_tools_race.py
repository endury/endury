"""Tests for the race coach tools (upsert_race, list_races, add_race_to_plan).

``upsert_race``/``list_races`` call straight into the races repository (no
separate ``planning/`` use case, mirroring ``record_self_assessment``'s
tests); ``add_race_to_plan`` wraps a use case, monkeypatched here the same
way ``test_tools_plan_build.py`` does for ``create_plan``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from endury.coach.tools.race_tools import make_race_tools
from endury.domain.models.race import AthleteRace


def _get(tools, name):
    return next(t for t in tools if t.name == name)


def _noop_coach():
    return None


def _tools(repos=None, maybe_get_coach=_noop_coach, intervals_client=None):
    return make_race_tools(repos or MagicMock(), maybe_get_coach, intervals_client)


def _race(**overrides) -> AthleteRace:
    fields = {
        "id": 1,
        "name": "Berlin Marathon",
        "race_date": "2026-11-08",
        "goal_type": "running_marathon",
        "priority": "A",
        "status": "planned",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    fields.update(overrides)
    return AthleteRace(**fields)


# upsert_race


def test_upsert_race_creates_and_reports_created_true():
    repo = MagicMock()
    repo.races.get_by_natural_key.return_value = None
    repo.races.upsert.return_value = _race()
    tool = _get(_tools(repos=repo), "upsert_race")

    result = json.loads(
        tool.invoke({"name": "Berlin Marathon", "race_date": "2026-11-08"})
    )

    assert result["status"] == "ok"
    assert result["created"] is True
    assert result["race"]["name"] == "Berlin Marathon"
    repo.races.upsert.assert_called_once()


def test_upsert_race_puts_the_race_on_the_calendar(monkeypatch):
    """The athlete asking to "put the half marathon in the calendar" has one
    tool for it: the race is saved and lands on intervals.icu."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-26")
    repo = MagicMock()
    repo.races.get_by_natural_key.return_value = None
    repo.races.upsert.return_value = _race(
        id=4, name="Munich Half", race_date="2026-09-27", priority="B"
    )
    client = MagicMock()
    client.list_events.return_value = []
    tool = _get(_tools(repos=repo, intervals_client=client), "upsert_race")

    result = json.loads(
        tool.invoke({"name": "Munich Half", "race_date": "2026-09-27", "priority": "B"})
    )

    assert result["calendar"]["status"] == "ok"
    event = client.bulk_upsert_events.call_args.args[0][0]
    assert event["external_id"] == "coachie:race:4"
    assert event["category"] == "RACE_B"


def test_upsert_race_without_intervals_reports_the_calendar_skipped():
    repo = MagicMock()
    repo.races.get_by_natural_key.return_value = None
    repo.races.upsert.return_value = _race()
    tool = _get(_tools(repos=repo), "upsert_race")

    result = json.loads(
        tool.invoke({"name": "Berlin Marathon", "race_date": "2026-11-08"})
    )

    assert result["calendar"]["status"] == "skipped"


def test_upsert_race_by_id_reports_created_false():
    repo = MagicMock()
    repo.races.get.return_value = _race()
    repo.races.upsert.return_value = _race(name="Berlin Marathon (renamed)")
    tool = _get(_tools(repos=repo), "upsert_race")

    result = json.loads(
        tool.invoke(
            {
                "name": "Berlin Marathon (renamed)",
                "race_date": "2026-11-08",
                "race_id": 1,
            }
        )
    )

    assert result["created"] is False
    assert repo.races.upsert.call_args.kwargs["race_id"] == 1


def test_upsert_race_asks_the_reply_to_confirm_the_race_in_plain_words():
    # Seen live: the race was saved and on the calendar, and the reply
    # said neither and named add_race_to_plan instead.
    contract = " ".join(_get(_tools(), "upsert_race").reply_contract)
    assert "race_day" in contract
    assert "intervals.icu calendar" in contract
    assert "never a tool name" in contract


def test_upsert_race_asks_to_confirm_a_weekday_race_date():
    # Seen live: a year typed wrong in the memory put a Sunday race on
    # a Monday a year later, and the reply relayed "Monday" without a question.
    repo = MagicMock()
    repo.races.get_by_natural_key.return_value = None
    repo.races.upsert.return_value = _race(race_date="2027-12-06")
    tool = _get(_tools(repos=repo), "upsert_race")

    result = json.loads(
        tool.invoke({"name": "Berlin Marathon", "race_date": "2027-12-06"})
    )

    assert result["caveats"] == [
        "2027-12-06 is a Monday, and races are mostly at weekends: ask the "
        "athlete to confirm the date, year included."
    ]
    assert "weekday" in " ".join(tool.reply_contract)


def test_upsert_race_takes_a_weekend_or_past_race_date_as_given():
    repo = MagicMock()
    repo.races.get_by_natural_key.return_value = None
    tool = _get(_tools(repos=repo), "upsert_race")

    repo.races.upsert.return_value = _race()  # a Sunday
    sunday = json.loads(tool.invoke({"name": "Berlin", "race_date": "2026-11-08"}))
    repo.races.upsert.return_value = _race(race_date="2026-10-22", status="completed")
    raced = json.loads(tool.invoke({"name": "Berlin", "race_date": "2026-10-22"}))

    assert "caveats" not in sunday
    assert "caveats" not in raced


def test_upsert_race_without_a_status_keeps_an_existing_races_status():
    """Editing a cancelled race's details must not quietly un-cancel it."""
    repo = MagicMock()
    repo.races.get.return_value = _race(status="cancelled")
    repo.races.upsert.return_value = _race(status="cancelled")
    tool = _get(_tools(repos=repo), "upsert_race")

    tool.invoke({"name": "Berlin Marathon", "race_date": "2026-11-08", "race_id": 1})

    assert repo.races.upsert.call_args.kwargs["status"] == "cancelled"


def test_upsert_race_refuses_an_unparsable_date():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "upsert_race")
    result = json.loads(tool.invoke({"name": "X", "race_date": "not-a-date"}))
    assert result["status"] == "error"
    repo.races.upsert.assert_not_called()


def test_upsert_race_refuses_an_unknown_goal_type():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "upsert_race")
    result = json.loads(
        tool.invoke(
            {"name": "X", "race_date": "2026-11-08", "goal_type": "ultra_moonwalk"}
        )
    )
    assert result["status"] == "error"
    assert "options" not in result  # no options key; message names them
    assert "ultra_moonwalk" in result["message"]
    repo.races.upsert.assert_not_called()


def test_upsert_race_refuses_an_unknown_priority():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "upsert_race")
    result = json.loads(
        tool.invoke({"name": "X", "race_date": "2026-11-08", "priority": "D"})
    )
    assert result["status"] == "error"
    repo.races.upsert.assert_not_called()


def test_upsert_race_refuses_an_unknown_status():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "upsert_race")
    result = json.loads(
        tool.invoke({"name": "X", "race_date": "2026-11-08", "status": "retired"})
    )
    assert result["status"] == "error"
    repo.races.upsert.assert_not_called()


class TestTrailSuggestion:
    def test_suggests_without_storing_when_goal_type_is_omitted(self):
        """Never auto-store a goal_type the athlete/coach did not give."""
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(
            name="Mont Blanc", goal_type=None, distance_km=55, climb_m=3200
        )
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "Mont Blanc",
                    "race_date": "2026-08-30",
                    "distance_km": 55,
                    "climb_m": 3200,
                }
            )
        )

        assert repo.races.upsert.call_args.kwargs["goal_type"] is None
        assert result["suggested_trail_goal_type"] == "trail_50k"
        assert result["km_effort"] == 87.0
        assert "caveats" not in result

    def test_keeps_an_explicit_trail_goal_that_disagrees_with_a_caveat(self):
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(
            name="Mont Blanc", goal_type="trail_20k", distance_km=55, climb_m=3200
        )
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "Mont Blanc",
                    "race_date": "2026-08-30",
                    "goal_type": "trail_20k",
                    "distance_km": 55,
                    "climb_m": 3200,
                }
            )
        )

        assert repo.races.upsert.call_args.kwargs["goal_type"] == "trail_20k"
        assert result["suggested_trail_goal_type"] == "trail_50k"
        assert any("trail_50k" not in c and "Trail 50K" in c for c in result["caveats"])
        assert any("kept Trail 20K" in c for c in result["caveats"])

    def test_a_race_longer_than_any_trail_plan_has_no_suggestion(self):
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(
            name="UTMB", goal_type="trail_100k", distance_km=171, climb_m=10000
        )
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "UTMB",
                    "race_date": "2026-08-28",
                    "goal_type": "trail_100k",
                    "distance_km": 171,
                    "climb_m": 10000,
                }
            )
        )

        assert result["suggested_trail_goal_type"] is None
        assert result["km_effort"] == 271.0
        assert any(
            "longer than any Endury trail plan" in c and "kept Trail 100K" in c
            for c in result["caveats"]
        )

    def test_no_caveat_when_the_explicit_goal_matches_the_suggestion(self):
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(
            name="Mont Blanc", goal_type="trail_50k", distance_km=55, climb_m=3200
        )
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "Mont Blanc",
                    "race_date": "2026-08-30",
                    "goal_type": "trail_50k",
                    "distance_km": 55,
                    "climb_m": 3200,
                }
            )
        )

        assert "caveats" not in result

    def test_no_caveat_for_an_explicit_non_trail_goal_type(self):
        """distance_km/climb_m on a non-trail race never second-guesses it."""
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(
            name="A trail-adjacent ultra",
            goal_type="running_marathon",
            distance_km=55,
            climb_m=3200,
        )
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "A trail-adjacent ultra",
                    "race_date": "2026-08-30",
                    "goal_type": "running_marathon",
                    "distance_km": 55,
                    "climb_m": 3200,
                }
            )
        )

        assert repo.races.upsert.call_args.kwargs["goal_type"] == "running_marathon"
        assert "caveats" not in result
        assert result["suggested_trail_goal_type"] == "trail_50k"

    def test_no_suggestion_without_both_fields(self):
        repo = MagicMock()
        repo.races.get_by_natural_key.return_value = None
        repo.races.upsert.return_value = _race(distance_km=55)
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke({"name": "X", "race_date": "2026-08-30", "distance_km": 55})
        )

        assert "km_effort" not in result
        assert "suggested_trail_goal_type" not in result
        assert repo.races.upsert.call_args.kwargs["goal_type"] is None

    def test_refuses_a_zero_distance_with_climb_given(self):
        repo = MagicMock()
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "X",
                    "race_date": "2026-08-30",
                    "distance_km": 0,
                    "climb_m": 100,
                }
            )
        )

        assert result["status"] == "error"
        repo.races.upsert.assert_not_called()

    def test_refuses_a_negative_climb_with_distance_given(self):
        repo = MagicMock()
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke(
                {
                    "name": "X",
                    "race_date": "2026-08-30",
                    "distance_km": 50,
                    "climb_m": -10,
                }
            )
        )

        assert result["status"] == "error"
        repo.races.upsert.assert_not_called()

    def test_refuses_a_zero_distance_given_alone(self):
        """The `> 0` guard must fire even when only one field is given, not
        just when both distance_km and climb_m are present."""
        repo = MagicMock()
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke({"name": "X", "race_date": "2026-08-30", "distance_km": 0})
        )

        assert result["status"] == "error"
        repo.races.upsert.assert_not_called()

    def test_refuses_a_negative_climb_given_alone(self):
        repo = MagicMock()
        tool = _get(_tools(repos=repo), "upsert_race")

        result = json.loads(
            tool.invoke({"name": "X", "race_date": "2026-08-30", "climb_m": -5})
        )

        assert result["status"] == "error"
        repo.races.upsert.assert_not_called()


def test_upsert_race_swallows_unexpected_errors():
    repo = MagicMock()
    repo.races.upsert.side_effect = RuntimeError("boom")
    tool = _get(_tools(repos=repo), "upsert_race")
    result = json.loads(tool.invoke({"name": "X", "race_date": "2026-11-08"}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# list_races


def test_list_races_returns_every_race_and_a_count():
    repo = MagicMock()
    repo.races.list.return_value = [_race(), _race(id=2, race_date="2026-12-01")]
    tool = _get(_tools(repos=repo), "list_races")

    result = json.loads(tool.invoke({}))

    assert result["count"] == 2
    assert [race["id"] for race in result["races"]] == [1, 2]


def test_list_races_passes_the_window_through():
    repo = MagicMock()
    repo.races.list.return_value = []
    tool = _get(_tools(repos=repo), "list_races")
    tool.invoke({"include_past_days": 90})
    repo.races.list.assert_called_once_with(include_past_days=90)


def test_list_races_swallows_unexpected_errors():
    repo = MagicMock()
    repo.races.list.side_effect = RuntimeError("boom")
    tool = _get(_tools(repos=repo), "list_races")
    result = json.loads(tool.invoke({}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# add_race_to_plan


def _add_race_tool(monkeypatch, result, **tool_args):
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.race_tools.run_add_race_to_plan", spy)
    return _get(_tools(**tool_args), "add_race_to_plan"), spy


def test_add_race_to_plan_relays_a_fit_preview(monkeypatch):
    preview = {
        "status": "preview",
        "confirm_required": True,
        "fits_as": "B",
        "reason": "Fits as a B race without disrupting the A-race build.",
        "races": [],
        "weeks": 25,
        "caveats": [],
        "message": "fits as priority B",
    }
    tool, spy = _add_race_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(tool.invoke({"race_id": 1}))
    assert result == preview
    assert spy.call_args.kwargs["race_id"] == 1
    assert spy.call_args.kwargs["priority"] == "B"
    assert spy.call_args.kwargs["confirm"] is False


def test_add_race_to_plan_relays_a_no_fit_result(monkeypatch):
    no_fit = {
        "status": "preview",
        "confirm_required": False,
        "fits_as": None,
        "reason": "B/C windows exceed 40% of pre-taper weeks.",
        "races": [],
        "weeks": None,
        "caveats": [],
        "message": '"City 10K" does not fit this plan.',
    }
    tool, _ = _add_race_tool(monkeypatch, no_fit, repos=MagicMock())
    result = json.loads(tool.invoke({"race_id": 1, "priority": "C"}))
    assert result["fits_as"] is None
    assert "40%" in result["reason"]


def test_add_race_to_plan_confirm_passes_through(monkeypatch):
    ok = {"status": "ok", "action": "replanned", "fits_as": "B"}
    repo = MagicMock()
    client = MagicMock()
    coach_accessor = _noop_coach
    tool, spy = _add_race_tool(
        monkeypatch,
        ok,
        repos=repo,
        maybe_get_coach=coach_accessor,
        intervals_client=client,
    )
    result = json.loads(tool.invoke({"race_id": 1, "confirm": True}))
    assert result["status"] == "ok"
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert kwargs["confirm"] is True


def test_add_race_to_plan_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.race_tools.run_add_race_to_plan",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(repos=MagicMock()), "add_race_to_plan")
    result = json.loads(tool.invoke({"race_id": 1}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


def test_upsert_race_refuses_a_cancellation_and_a_new_date():
    repo = MagicMock()
    repo.races.get.return_value = _race()
    tool = _get(_tools(repos=repo), "upsert_race")
    for args in (
        {"name": "Berlin Marathon", "race_date": "2026-11-08", "status": "cancelled"},
        {"name": "Berlin Marathon", "race_date": "2030-01-05", "race_id": 1},
    ):
        result = json.loads(tool.invoke(args))
        assert result["status"] == "refused"
    repo.races.upsert.assert_not_called()


def test_cancelling_a_race_previews_until_confirmed():
    repo = MagicMock()
    repo.races.get.return_value = _race()
    repo.races.upsert.return_value = _race(status="cancelled")
    tool = _get(_tools(repos=repo), "cancel_or_move_race")

    preview = json.loads(tool.invoke({"race_id": 1, "cancel": True}))
    assert preview["status"] == "preview"
    repo.races.upsert.assert_not_called()

    applied = json.loads(tool.invoke({"race_id": 1, "cancel": True, "confirm": True}))
    assert applied["status"] == "ok"
    assert repo.races.upsert.call_args.kwargs["status"] == "cancelled"


def test_moving_a_race_needs_one_change_named():
    repo = MagicMock()
    repo.races.get.return_value = _race()
    tool = _get(_tools(repos=repo), "cancel_or_move_race")
    assert json.loads(tool.invoke({"race_id": 1}))["status"] == "error"
    moved = json.loads(tool.invoke({"race_id": 1, "new_date": "2030-01-05"}))
    assert moved["status"] == "preview"
    assert moved["new_date"] == "2030-01-05"
