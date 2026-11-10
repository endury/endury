"""Tests for create_plan (the coach's front door onto run_generate/run_commit)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from endury.planner.foundations.types import PlanMaturity
from endury.planning import generate_plan as generate_plan_module
from endury.planning.create_plan import create_plan
from endury.planning.plan_maturity import TIER_NOTES
from tests.planning.test_replan import _base_repos

# The unconfirmed builds below stand for goals whose menus are reviewed.
pytestmark = pytest.mark.usefixtures("alpha_goals")

_TODAY = date(2026, 5, 19)  # a Tuesday: the default start is this week
_MONDAY = "2026-05-18"  # this week's Monday
_RACE = "2026-09-06"  # a Sunday, 16 weeks out: a horizon the scorecard checks


def _races(
    race: str = _RACE, goal: str = "running_marathon", priority: str = "A"
) -> list[dict[str, str]]:
    return [{"goal_type": goal, "race_date": race, "priority": priority}]


def _repos(active: SimpleNamespace | None = None):
    repos = _base_repos()
    repos.plan.get_active.return_value = active
    return repos


class TestTrainingStart:
    def test_defaults_to_this_weeks_monday_up_to_tuesday(self) -> None:
        # The opening test sits on Tuesday: it is still ahead on a Tuesday.
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["status"] == "preview"
        assert result["plan_start"] == _MONDAY

    def test_defaults_to_next_monday_from_wednesday(self) -> None:
        # A start this week would open the plan without its week-1 test.
        wednesday = date(2026, 5, 20)
        result = create_plan(repos=_repos(), races=_races(), today=wednesday)
        assert result["status"] == "preview"
        assert result["plan_start"] == "2026-05-25"

    def test_accepts_an_explicit_monday(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), training_start=_MONDAY, today=_TODAY
        )
        assert result["status"] == "preview"
        assert result["plan_start"] == _MONDAY

    def test_refuses_a_non_monday_start(self) -> None:
        result = create_plan(
            repos=_repos(),
            races=_races(),
            training_start="2026-05-19",
            today=_TODAY,
        )
        assert result["status"] == "error"
        assert _MONDAY in result["message"]

    def test_refuses_a_start_before_this_week(self) -> None:
        # drop_past only trims a start inside the current week; an earlier
        # one would push sessions the athlete never had a chance to do.
        result = create_plan(
            repos=_repos(), races=_races(), training_start="2026-05-11", today=_TODAY
        )
        assert result["status"] == "error"
        assert _MONDAY in result["message"]

    def test_refuses_an_unparsable_start(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), training_start="not-a-date", today=_TODAY
        )
        assert result["status"] == "error"


class TestRaceValidation:
    def test_refuses_an_empty_list(self) -> None:
        result = create_plan(repos=_repos(), races=[], today=_TODAY)
        assert result["status"] == "error"

    def test_refuses_a_non_list(self) -> None:
        result = create_plan(
            repos=_repos(), races={"goal_type": "running_marathon"}, today=_TODAY
        )
        assert result["status"] == "error"

    def test_refuses_a_non_object_race(self) -> None:
        result = create_plan(repos=_repos(), races=["oops"], today=_TODAY)
        assert result["status"] == "error"

    def test_refuses_an_unknown_goal_type(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(goal="ultra_moonwalk"), today=_TODAY
        )
        assert result["status"] == "error"
        assert "options" in result

    def test_refuses_an_invalid_priority(self) -> None:
        result = create_plan(repos=_repos(), races=_races(priority="D"), today=_TODAY)
        assert result["status"] == "error"

    def test_missing_a_goal_is_the_phase_planners_own_refusal(self) -> None:
        # No pre-check duplicates the phase planner's "exactly one A-goal"
        # rejection — it is relayed verbatim.
        result = create_plan(repos=_repos(), races=_races(priority="B"), today=_TODAY)
        assert result["status"] == "error"
        assert "this one has none" in result["message"]


class TestAmbitionValidation:
    def test_refuses_an_unknown_ambition(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), ambition_level="extreme", today=_TODAY
        )
        assert result["status"] == "error"
        assert "options" in result


class TestWeeklyHoursCap:
    def test_refuses_a_non_positive_cap(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), weekly_hours_cap=0, today=_TODAY
        )
        assert result["status"] == "error"
        # Curated, not pydantic's raw validation-error text — the cap is
        # checked (via parse_directives) before GenerateRequest is ever built.
        assert result["message"] == "weekly_hours_cap must be greater than 0."

    def test_refuses_a_negative_cap(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), weekly_hours_cap=-5, today=_TODAY
        )
        assert result["status"] == "error"
        assert result["message"] == "weekly_hours_cap must be greater than 0."


class TestDirectiveArgs:
    def test_resume_date_filters_the_previewed_sessions(self) -> None:
        # Inside the previewed ten days, so the filter is visible there:
        # the days before it are gone, the days from it survive.
        baseline = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert any(day["date"] < "2026-05-22" for day in baseline["first_days"])

        result = create_plan(
            repos=_repos(), races=_races(), resume_date="2026-05-22", today=_TODAY
        )
        assert result["status"] == "preview"
        assert result["first_days"]
        assert all(day["date"] >= "2026-05-22" for day in result["first_days"])

    def test_suppress_tests_until_is_persisted_on_confirm(self) -> None:
        repos = _repos()
        repos.plan.create.return_value = 3
        result = create_plan(
            repos=repos,
            races=_races(),
            suppress_tests_until="2026-07-01",
            confirm=True,
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "ok"
        stored = json.loads(repos.plan.create.call_args.kwargs["solve_result_json"])
        assert stored["request"]["directives"] == {"suppress_tests_until": "2026-07-01"}

    def test_refuses_directives_that_leave_no_sessions(self) -> None:
        # Confirming this would archive the active plan for an empty one.
        result = create_plan(
            repos=_repos(), races=_races(), resume_date="2026-12-01", today=_TODAY
        )
        assert result["status"] == "error"
        assert "no sessions at all" in result["message"]

    def test_refuses_an_invalid_resume_date(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), resume_date="not-a-date", today=_TODAY
        )
        assert result["status"] == "error"

    def test_weekly_hours_cap_is_carried_on_the_directives_object_too(self) -> None:
        repos = _repos()
        repos.plan.create.return_value = 4
        result = create_plan(
            repos=repos,
            races=_races(),
            weekly_hours_cap=8.0,
            confirm=True,
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "ok"
        stored = json.loads(repos.plan.create.call_args.kwargs["solve_result_json"])
        assert stored["request"]["weekly_hours_cap"] == 8.0
        assert stored["request"]["directives"] == {"weekly_hours_cap": 8.0}


class TestDirectivesPreviewEcho:
    def test_no_directives_is_an_empty_object(self) -> None:
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["directives"] == {}

    def test_given_directives_are_echoed(self) -> None:
        result = create_plan(
            repos=_repos(),
            races=_races(),
            resume_date="2026-05-22",
            today=_TODAY,
        )
        assert result["directives"] == {"resume_date": "2026-05-22"}

    def test_confirmed_plan_still_persists_the_directives_unchanged(self) -> None:
        # The echo is preview-only decoration; it must not change what a
        # confirm persists.
        repos = _repos()
        repos.plan.create.return_value = 9
        result = create_plan(
            repos=repos,
            races=_races(),
            unavailable='[["2026-06-01", "2026-06-05"]]',
            confirm=True,
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "ok"
        stored = json.loads(repos.plan.create.call_args.kwargs["solve_result_json"])
        assert stored["request"]["directives"] == {
            "unavailable": [["2026-06-01", "2026-06-05"]]
        }


class TestTrailBandBackstop:
    _TRAIL_RACE = "2026-09-06"  # ~16 weeks out from _TODAY's Monday — clears
    # both trail_20k's (8-20 wk) and trail_50k's (12-24 wk) horizons.

    def _stored(self, **overrides: object) -> SimpleNamespace:
        base = SimpleNamespace(
            name="Challenge Walchsee",
            race_date=self._TRAIL_RACE,
            distance_km=25.0,
            climb_m=900.0,
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_refuses_a_mismatched_band(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored()
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            today=_TODAY,
        )
        assert result["status"] == "error"
        assert result["error_code"] == "trail_band_mismatch"
        assert result["suggested_goal_type"] == "trail_20k"
        assert "Challenge Walchsee" in result["message"]
        assert "34" in result["message"]  # 25 km + 900 m / 100 = 34 km-effort
        assert "accept_band_mismatch=true" in result["message"]
        repos.races.get_by_date.assert_called_once_with(self._TRAIL_RACE)

    def test_refuses_a_race_longer_than_any_trail_plan(self) -> None:
        """170 km-effort or more has no Endury plan: no band to suggest."""
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored(
            distance_km=160.0, climb_m=9000.0
        )
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            today=_TODAY,
        )
        assert result["error_code"] == "trail_band_mismatch"
        assert result["suggested_goal_type"] is None
        assert "longer than any Endury trail plan" in result["message"]
        assert "250 km-effort" in result["message"]
        assert "accept_band_mismatch=true" in result["message"]

    def test_a_matching_band_is_not_refused(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored()
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_20k"),
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"

    def test_no_stored_race_on_that_date_skips_the_check(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = None
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"

    def test_a_stored_race_missing_climb_skips_the_check(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored(climb_m=None)
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"

    def test_a_stored_race_missing_distance_skips_the_check(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored(distance_km=None)
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"

    def test_a_non_trail_goal_never_looks_up_a_stored_race(self) -> None:
        repos = _repos()
        result = create_plan(repos=repos, races=_races(), today=_TODAY)
        assert result["status"] == "preview"
        repos.races.get_by_date.assert_not_called()

    def test_accept_band_mismatch_builds_the_requested_band_with_a_caveat(self) -> None:
        repos = _repos()
        repos.races.get_by_date.return_value = self._stored()
        result = create_plan(
            repos=repos,
            races=_races(race=self._TRAIL_RACE, goal="trail_50k"),
            accept_band_mismatch=True,
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"
        assert result["goal"] == "Trail 50K"
        assert any("Challenge Walchsee" in caveat for caveat in result["caveats"])
        assert any("anyway" in caveat for caveat in result["caveats"])


class TestActivePlanGuard:
    _ACTIVE = SimpleNamespace(id=1, name="Marathon Block", goal_date="2026-10-01")

    def test_refuses_when_a_plan_is_active(self) -> None:
        result = create_plan(repos=_repos(self._ACTIVE), races=_races(), today=_TODAY)
        assert result["status"] == "error"
        assert result["replaces"] == {
            "plan_id": 1,
            "name": "Marathon Block",
            "goal_date": "2026-10-01",
        }

    def test_replace_active_names_what_it_replaces_in_the_preview(self) -> None:
        result = create_plan(
            repos=_repos(self._ACTIVE),
            races=_races(),
            replace_active=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"
        assert result["replaces"]["plan_id"] == 1


class TestPreview:
    def test_preview_is_lean_and_names_the_phases(self) -> None:
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["weeks"] > 0
        # The plan's own week count: the phase runs must add up to it.
        assert result["weeks"] == sum(phase["weeks"] for phase in result["phases"])
        assert "sessions" not in result  # never the full session list
        assert all(
            {"phase", "from", "through", "weeks"} <= set(phase)
            for phase in result["phases"]
        )
        assert all(
            {"week", "from", "hours", "tss", "by_sport"} <= set(week)
            for week in result["weekly"]
        )
        assert result["athlete_state_sources"]["running"] == "self_report"

    def test_preview_lists_only_the_first_ten_days(self) -> None:
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        start = date.fromisoformat(result["plan_start"])
        dates = [date.fromisoformat(day["date"]) for day in result["first_days"]]
        assert dates == sorted(dates)
        assert all(start <= day < start + timedelta(days=10) for day in dates)
        assert len(result["first_days"]) < sum(
            week["hours"] for week in result["weekly"][:2]
        )  # sanity: far fewer than the full plan


class TestGenerateErrorRelay:
    def test_relays_athlete_data_required(self) -> None:
        # half_ironman needs run+bike+swim; _base_repos only answers running.
        result = create_plan(
            repos=_repos(), races=_races(goal="half_ironman"), today=_TODAY
        )
        assert result["status"] == "error"
        assert result["error_code"] == "athlete_data_required"
        assert "missing_sports" in result

    def test_relays_horizon_too_short_with_the_week_counts(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(race="2026-07-12"), today=_TODAY
        )
        assert result["status"] == "error"
        assert result["error_code"] == "horizon_too_short"
        assert result["min_weeks"] == 12
        assert result["actual_weeks"] == 8
        assert result["deficit_weeks"] == 4

    def test_relays_experimental_plan_with_its_reasons(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(goal="running_5k"), today=_TODAY
        )
        assert result == {
            "status": "error",
            "message": (
                "This plan type is built from training research, and its "
                "sessions have not yet been reviewed one by one. "
                "You can still build it. Confirm to go ahead."
            ),
            "error_code": "experimental_plan",
            "reasons": ["goal_experimental"],
        }

    def test_a_trail_band_mismatch_is_asked_before_the_experimental_plan(
        self,
    ) -> None:
        """The band decides which plan is built; its maturity comes second."""
        repos = _repos()
        repos.races.get_by_date.return_value = SimpleNamespace(
            name="Challenge Walchsee",
            race_date="2026-09-06",
            distance_km=25.0,
            climb_m=900.0,
        )
        result = create_plan(repos=repos, races=_races(goal="trail_50k"), today=_TODAY)
        assert result["error_code"] == "trail_band_mismatch"

    def test_relays_an_infeasible_solve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from endury.planner.macro.macro_planner import QPInfeasibleError

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise QPInfeasibleError("Session-menu QP infeasible (status=...).")

        monkeypatch.setattr(generate_plan_module, "generate_plan", _raise)
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["status"] == "error"
        assert "ambition level" in result["message"]


class TestExperimentalPlan:
    def test_an_alpha_plan_previews_its_tier_and_says_so(self) -> None:
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["status"] == "preview"
        assert result["maturity"] == "alpha"
        assert result["caveats"] == [TIER_NOTES[PlanMaturity.ALPHA]]

    def test_a_confirmed_experimental_plan_carries_the_sentence_as_a_caveat(
        self,
    ) -> None:
        result = create_plan(
            repos=_repos(),
            races=_races(goal="running_5k"),
            accept_experimental=True,
            today=_TODAY,
        )
        assert result["status"] == "preview"
        assert result["maturity"] == "experimental"
        assert (
            "This plan type is built from training research, and its sessions "
            "have not yet been reviewed one by one."
        ) in result["caveats"]


class TestConfirm:
    def test_confirm_commits_through_run_commit(self) -> None:
        repos = _repos()
        repos.plan.create.return_value = 42
        result = create_plan(
            repos=repos, races=_races(), confirm=True, today=_TODAY, client=None
        )
        assert result["status"] == "ok"
        assert result["plan_id"] == 42
        assert result["plan_start"] == _MONDAY
        assert result["weeks"] > 0
        # No INTERVALS_API_KEY in this fixture -> push is skipped, and that
        # is relayed as a caveat rather than a silent "ok".
        assert result["push"]["status"] == "skipped"
        assert result["caveats"]

    def test_confirm_with_a_working_push_carries_no_caveat(self) -> None:
        class _Client:
            def __init__(self) -> None:
                self.events: list[object] = []

            def bulk_upsert_events(self, events: list[object]) -> None:
                self.events = events

        repos = _repos()
        repos.plan.create.return_value = 11
        client = _Client()
        result = create_plan(
            repos=repos, races=_races(), confirm=True, today=_TODAY, client=client
        )
        assert result["push"]["status"] == "ok"
        assert result["caveats"] == []
        assert client.events

    def test_confirm_reports_a_failed_push(self) -> None:
        class _FailingClient:
            def bulk_upsert_events(self, events: list[object]) -> None:
                raise RuntimeError("upstream exploded")

        repos = _repos()
        repos.plan.create.return_value = 7
        result = create_plan(
            repos=repos,
            races=_races(),
            confirm=True,
            today=_TODAY,
            client=_FailingClient(),
        )
        assert result["status"] == "ok"
        assert result["push"]["status"] == "error"
        assert any("intervals.icu" in caveat for caveat in result["caveats"])


class TestUnavailableArg:
    def test_json_string_filters_the_previewed_sessions(self) -> None:
        result = create_plan(
            repos=_repos(),
            races=_races(),
            unavailable='[["2026-05-20", "2026-05-21"]]',
            today=_TODAY,
        )
        assert result["status"] == "preview"
        assert all(
            day["date"] not in ("2026-05-20", "2026-05-21")
            for day in result["first_days"]
        )

    def test_a_parsed_list_is_also_accepted(self) -> None:
        result = create_plan(
            repos=_repos(),
            races=_races(),
            unavailable=[["2026-05-20", "2026-05-21"]],
            today=_TODAY,
        )
        assert result["status"] == "preview"

    def test_refuses_unparsable_json(self) -> None:
        result = create_plan(
            repos=_repos(), races=_races(), unavailable="not json", today=_TODAY
        )
        assert result["status"] == "error"
        assert "unavailable" in result["message"]

    def test_stored_on_confirm(self) -> None:
        repos = _repos()
        repos.plan.create.return_value = 5
        result = create_plan(
            repos=repos,
            races=_races(),
            unavailable='[["2026-06-01", "2026-06-05"]]',
            confirm=True,
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "ok"
        stored = json.loads(repos.plan.create.call_args.kwargs["solve_result_json"])
        assert stored["request"]["directives"] == {
            "unavailable": [["2026-06-01", "2026-06-05"]]
        }


class TestInputHashFreshness:
    def test_preview_carries_an_input_hash(self) -> None:
        result = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        assert result["status"] == "preview"
        assert isinstance(result["input_hash"], str)
        assert result["input_hash"]

    def test_confirm_with_a_matching_hash_commits_normally(self) -> None:
        preview = create_plan(repos=_repos(), races=_races(), today=_TODAY)
        repos = _repos()
        repos.plan.create.return_value = 55
        result = create_plan(
            repos=repos,
            races=_races(),
            confirm=True,
            expected_input_hash=preview["input_hash"],
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "ok"
        assert result["plan_id"] == 55

    def test_confirm_with_a_stale_hash_commits_nothing_and_returns_a_fresh_preview(
        self,
    ) -> None:
        preview = create_plan(repos=_repos(), races=_races(), today=_TODAY)

        repos = _repos()
        result = create_plan(
            repos=repos,
            races=_races(),
            confirm=True,
            expected_input_hash=preview["input_hash"] + "stale",
            today=_TODAY,
            client=None,
        )
        assert result["status"] == "preview"
        assert "changed" in result["caveats"][0].lower()
        repos.plan.create.assert_not_called()

    def test_confirm_without_expected_input_hash_skips_the_freshness_check(
        self,
    ) -> None:
        repos = _repos()
        repos.plan.create.return_value = 6
        result = create_plan(
            repos=repos, races=_races(), confirm=True, today=_TODAY, client=None
        )
        assert result["status"] == "ok"
        assert result["plan_id"] == 6
