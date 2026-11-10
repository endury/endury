"""Tests for plan directives: what the coach knows, applied at generation time."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from endury.planner.sessions import session_generator, test_injection
from endury.planning.commit_plan import build_solve_result_payload
from endury.planning.generate_plan import (
    SOLVER_VERSION,
    build_input_hash,
    build_request_payload,
    request_from_payload,
    run_generate,
)
from endury.planning.plan_directives import (
    TEST_ACTIVITY_KEYS,
    PlanDirectives,
    apply_directives,
    merge_unavailable_ranges,
    parse_directives,
)
from endury.planning.replan_plan import run_replan
from endury.planning.wire.requests import GenerateRequest, RaceGoalModel
from tests.planning.test_replan import _base_repos, _StatefulPlanRepo

_START = "2026-03-09"  # a Monday
_RACE = "2026-11-08"


def _session(day: str, activity_key: str = "run_easy") -> dict:
    return {
        "date": day,
        "activity_key": activity_key,
        "slot_id": f"s:{day}:{activity_key}",
    }


def _request(directives: dict | None = None) -> GenerateRequest:
    return GenerateRequest(
        races=[RaceGoalModel(goal_type="running_marathon", race_date=_RACE)],
        training_start=_START,
        accept_experimental=True,  # beyond the checked horizons
        directives=directives,
    )


class TestParseDirectives:
    def test_none_payload_is_none(self) -> None:
        assert parse_directives(None) is None

    def test_empty_payload_is_none(self) -> None:
        assert parse_directives({}) is None

    def test_all_falsy_field_values_normalize_to_none(self) -> None:
        assert parse_directives({"resume_date": "", "unavailable": []}) is None

    def test_valid_resume_date(self) -> None:
        result = parse_directives({"resume_date": "2026-06-01"})
        assert result == PlanDirectives(resume_date=date(2026, 6, 1))

    def test_invalid_resume_date(self) -> None:
        assert isinstance(parse_directives({"resume_date": "not-a-date"}), str)

    def test_invalid_suppress_tests_until(self) -> None:
        result = parse_directives({"suppress_tests_until": "not-a-date"})
        assert isinstance(result, str)

    def test_unavailable_round_trips(self) -> None:
        result = parse_directives({"unavailable": [["2026-06-01", "2026-06-05"]]})
        assert result == PlanDirectives(
            unavailable=((date(2026, 6, 1), date(2026, 6, 5)),)
        )

    def test_unavailable_rejects_a_malformed_entry(self) -> None:
        assert isinstance(parse_directives({"unavailable": ["not-a-pair"]}), str)

    def test_unavailable_rejects_a_bad_start_date(self) -> None:
        result = parse_directives({"unavailable": [["bad", "2026-06-05"]]})
        assert isinstance(result, str)

    def test_unavailable_rejects_a_bad_end_date(self) -> None:
        result = parse_directives({"unavailable": [["2026-06-01", "bad"]]})
        assert isinstance(result, str)

    def test_unavailable_rejects_an_inverted_range(self) -> None:
        result = parse_directives({"unavailable": [["2026-06-05", "2026-06-01"]]})
        assert isinstance(result, str)

    def test_weekly_hours_cap_rejects_non_numeric(self) -> None:
        assert isinstance(parse_directives({"weekly_hours_cap": "lots"}), str)

    def test_weekly_hours_cap_rejects_non_positive(self) -> None:
        assert isinstance(parse_directives({"weekly_hours_cap": 0}), str)

    def test_weekly_hours_cap_accepts_a_positive_value(self) -> None:
        result = parse_directives({"weekly_hours_cap": 8.0})
        assert result == PlanDirectives(weekly_hours_cap=8.0)


class TestMergeUnavailableRanges:
    """The coalescing a repeated pause leans on (pause_training)."""

    def test_disjoint_ranges_are_kept_and_sorted(self) -> None:
        assert merge_unavailable_ranges(
            [
                (date(2026, 6, 10), date(2026, 6, 12)),
                (date(2026, 6, 1), date(2026, 6, 3)),
            ]
        ) == [
            (date(2026, 6, 1), date(2026, 6, 3)),
            (date(2026, 6, 10), date(2026, 6, 12)),
        ]

    def test_an_identical_range_collapses_to_one(self) -> None:
        window = (date(2026, 6, 1), date(2026, 6, 5))
        assert merge_unavailable_ranges([window, window]) == [window]

    def test_an_adjacent_range_merges(self) -> None:
        assert merge_unavailable_ranges(
            [(date(2026, 6, 1), date(2026, 6, 3)), (date(2026, 6, 4), date(2026, 6, 6))]
        ) == [(date(2026, 6, 1), date(2026, 6, 6))]

    def test_a_contained_range_never_shortens_the_one_holding_it(self) -> None:
        assert merge_unavailable_ranges(
            [
                (date(2026, 6, 1), date(2026, 6, 20)),
                (date(2026, 6, 5), date(2026, 6, 7)),
            ]
        ) == [(date(2026, 6, 1), date(2026, 6, 20))]

    def test_a_one_day_gap_is_not_bridged(self) -> None:
        ranges = [
            (date(2026, 6, 1), date(2026, 6, 3)),
            (date(2026, 6, 5), date(2026, 6, 6)),
        ]
        assert merge_unavailable_ranges(ranges) == ranges


class TestApplyDirectives:
    def test_no_directives_leaves_the_session_list_identical(self) -> None:
        sessions = [_session("2026-06-01"), _session("2026-06-02")]
        assert apply_directives(sessions, None) == sessions
        assert apply_directives(sessions, {}) == sessions

    def test_resume_date_drops_earlier_sessions(self) -> None:
        sessions = [
            _session("2026-06-01"),
            _session("2026-06-05"),
            _session("2026-06-10"),
        ]
        result = apply_directives(sessions, {"resume_date": "2026-06-05"})
        assert [s["date"] for s in result] == ["2026-06-05", "2026-06-10"]

    def test_suppress_tests_until_drops_only_test_sessions(self) -> None:
        sessions = [
            _session("2026-06-01", "run_threshold_test"),
            _session("2026-06-01", "run_easy"),
            _session("2026-06-10", "run_threshold_test"),
        ]
        result = apply_directives(sessions, {"suppress_tests_until": "2026-06-05"})
        remaining = [(s["date"], s["activity_key"]) for s in result]
        assert ("2026-06-01", "run_threshold_test") not in remaining
        assert ("2026-06-01", "run_easy") in remaining
        assert ("2026-06-10", "run_threshold_test") in remaining

    def test_unavailable_range_is_inclusive_on_both_ends(self) -> None:
        sessions = [
            _session("2026-06-04"),
            _session("2026-06-05"),
            _session("2026-06-06"),
            _session("2026-06-07"),
        ]
        result = apply_directives(
            sessions, {"unavailable": [["2026-06-05", "2026-06-06"]]}
        )
        assert [s["date"] for s in result] == ["2026-06-04", "2026-06-07"]

    def test_past_dated_directives_are_inert(self) -> None:
        # Nothing before today is ever materialized in the first place, so
        # a resume_date already behind us drops nothing among what remains.
        sessions = [_session("2026-06-10")]
        assert apply_directives(sessions, {"resume_date": "2020-01-01"}) == sessions

    def test_tolerates_an_invalid_stored_payload(self) -> None:
        # A payload that fails validation (should never happen for anything
        # this module persisted) is treated as no directives, not raised.
        sessions = [_session("2026-06-10")]
        result = apply_directives(sessions, {"resume_date": "not-a-date"})
        assert result == sessions


class TestTestKeyDriftGuard:
    def test_test_key_set_matches_the_planner(self) -> None:
        from_generator = {key.value for key in session_generator._TEST_KEYS}
        from_injection = {
            key.value for key in test_injection._TEST_ACTIVITY_KEY.values()
        }
        assert from_generator == TEST_ACTIVITY_KEYS
        assert from_injection == TEST_ACTIVITY_KEYS


class TestRequestPayloadRoundTrip:
    def test_no_directives_leaves_the_input_hash_identical(self) -> None:
        bare = _request(directives=None)
        empty = _request(directives={})
        assert "directives" not in build_request_payload(bare)
        assert build_request_payload(bare) == build_request_payload(empty)
        assert build_input_hash(bare, {}, "v1") == build_input_hash(empty, {}, "v1")

    def test_directives_round_trip_through_the_request_payload(self) -> None:
        request = _request(directives={"resume_date": "2026-06-01"})
        payload = build_request_payload(request)
        assert payload["directives"] == {"resume_date": "2026-06-01"}
        rebuilt = request_from_payload(payload, request.races)
        assert rebuilt.directives == {"resume_date": "2026-06-01"}


class TestRunGenerateAppliesDirectives:
    def test_resume_date_filters_the_generated_plan(self) -> None:
        request = _request(directives={"resume_date": "2026-04-06"})
        result = run_generate(request, repos=_base_repos(), progress=lambda p: None)
        assert result.status == "ok"
        assert all(s["date"] >= "2026-04-06" for s in result.sessions)

    def test_suppress_tests_until_filters_real_test_sessions(self) -> None:
        # A real marathon plan from _START places run_threshold_test at
        # 2026-06-24 / 2026-07-22 / 2026-08-19 (verified against the live
        # pipeline before writing this assertion).
        request = _request(directives={"suppress_tests_until": "2026-07-01"})
        result = run_generate(request, repos=_base_repos(), progress=lambda p: None)
        assert result.status == "ok"
        tests = [s for s in result.sessions if s["activity_key"] in TEST_ACTIVITY_KEYS]
        assert all(s["date"] >= "2026-07-01" for s in tests)
        assert any(s["date"] == "2026-07-22" for s in tests)


class TestReplanReappliesDirectives:
    def test_replan_reapplies_the_stored_directives(self) -> None:
        request = _request(directives={"suppress_tests_until": "2026-07-01"})
        result = run_generate(request, repos=_base_repos(), progress=lambda p: None)
        assert result.status == "ok"
        solve = build_solve_result_payload(result)
        solve["request"] = build_request_payload(request)
        active = SimpleNamespace(
            id=1,
            name="Marathon Block",
            goal_type="running_marathon",
            goal_date=_RACE,
            plan_start=_START,
            solve_result_json=json.dumps(solve),
            solver_version=SOLVER_VERSION,
            athlete_state_json=json.dumps(result.athlete_state),
        )
        repos = _base_repos()
        repos.plan = _StatefulPlanRepo(active, None)

        replanned = run_replan(
            repos=repos,
            progress=lambda p: None,
            maybe_get_coach=lambda: None,
            today=date(2026, 5, 20),
        )
        assert replanned["status"] == "ok"
        sessions = replanned["plan_result"]["sessions"]
        tests = [s for s in sessions if s["activity_key"] in TEST_ACTIVITY_KEYS]
        assert all(s["date"] >= "2026-07-01" for s in tests)
        assert any(s["date"] == "2026-07-22" for s in tests)
