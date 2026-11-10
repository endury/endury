"""Unit tests for the coach-extra guards: unavailable, adjacent-hard, budget."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.planner.activity import ensure_registry_loaded
from endury.planning.plan_extra_session import (
    adjacent_hard_refusal,
    budget_refusal,
    unavailable_window_refusal,
)


@pytest.fixture(autouse=True)
def _registry() -> None:
    ensure_registry_loaded()


def _plan(directives: dict | None = None) -> SimpleNamespace:
    request: dict[str, object] = {"training_start": "2026-03-09"}
    if directives is not None:
        request["directives"] = directives
    return SimpleNamespace(solve_result_json=json.dumps({"request": request}))


class TestUnavailableWindowRefusal:
    def test_no_directives_at_all(self) -> None:
        assert unavailable_window_refusal(_plan(), date(2026, 5, 21)) is None

    def test_a_malformed_stored_range_is_treated_as_no_directives(self) -> None:
        active = _plan({"unavailable": ["not-a-pair"]})
        assert unavailable_window_refusal(active, date(2026, 5, 21)) is None

    def test_a_date_outside_every_range_is_clear(self) -> None:
        active = _plan({"unavailable": [["2026-06-01", "2026-06-05"]]})
        assert unavailable_window_refusal(active, date(2026, 5, 21)) is None

    def test_a_date_inside_a_range_is_refused_naming_it(self) -> None:
        active = _plan({"unavailable": [["2026-05-20", "2026-05-24"]]})
        result = unavailable_window_refusal(active, date(2026, 5, 21))
        assert result is not None
        assert result["status"] == "error"
        assert "2026-05-21" in result["message"]
        assert "2026-05-20..2026-05-24" in result["message"]
        assert "set_plan_directives" in result["message"]

    def test_a_date_on_a_range_boundary_is_refused(self) -> None:
        active = _plan({"unavailable": [["2026-05-20", "2026-05-24"]]})
        assert unavailable_window_refusal(active, date(2026, 5, 24)) is not None


def _row(activity_key: str, day: str, *, status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(activity_key=activity_key, date=day, status=status)


class TestAdjacentHardRefusal:
    def _repos(self, rows: list[SimpleNamespace]) -> MagicMock:
        repos = MagicMock()
        repos.plan.get_sessions_by_date_range.side_effect = (
            lambda _plan_id, start, end: [
                row for row in rows if start <= str(row.date) <= end
            ]
        )
        return repos

    def test_nothing_nearby_is_clear(self) -> None:
        repos = self._repos([])
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "Threshold Run") is (
            None
        )

    def test_a_hard_session_the_day_before_blocks(self) -> None:
        rows = [_row("run_threshold", "2026-05-20")]
        repos = self._repos(rows)
        result = adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "Threshold Run")
        assert result is not None
        assert result["status"] == "error"
        assert "2026-05-20" in result["message"]
        assert "a hard session" in result["message"]
        assert "Threshold Run" in result["message"]

    def test_a_long_session_the_day_after_blocks_and_names_it_long(self) -> None:
        rows = [_row("run_long", "2026-05-22")]
        repos = self._repos(rows)
        result = adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "Threshold Run")
        assert result is not None
        assert "a long session" in result["message"]

    def test_a_hard_session_the_same_day_blocks(self) -> None:
        rows = [_row("run_threshold", "2026-05-21")]
        repos = self._repos(rows)
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "x") is not None

    def test_a_skipped_hard_session_does_not_block(self) -> None:
        rows = [_row("run_threshold", "2026-05-21", status="skipped")]
        repos = self._repos(rows)
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "x") is None

    def test_an_easy_neighbour_does_not_block(self) -> None:
        rows = [_row("run_easy", "2026-05-21")]
        repos = self._repos(rows)
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "x") is None

    def test_a_brick_neighbour_blocks_although_its_family_is_easy(self) -> None:
        """The same hard_extra_label notion the allow_hard gate uses — a
        brick is family "easy" but still needs allow_hard, so it still
        blocks an adjacent hard extra."""
        rows = [_row("run_brick_short", "2026-05-20")]
        repos = self._repos(rows)
        result = adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "Threshold Run")
        assert result is not None
        assert "a hard session" in result["message"]

    def test_an_unknown_activity_key_is_ignored_not_raised(self) -> None:
        rows = [_row("not_a_real_activity", "2026-05-21")]
        repos = self._repos(rows)
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "x") is None

    def test_two_weeks_away_is_clear(self) -> None:
        rows = [_row("run_threshold", "2026-05-07")]
        repos = self._repos(rows)
        assert adjacent_hard_refusal(repos, 1, date(2026, 5, 21), "x") is None


def _sess_row(tss: float, slot_id: str, *, status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(target_tss=tss, slot_id=slot_id, status=status)


class TestBudgetRefusal:
    def test_it_fits_under_the_allowance(self) -> None:
        rows = [_sess_row(100.0, "menu:a")]
        assert budget_refusal(rows, date(2026, 5, 18), 10.0) is None

    def test_an_empty_week_gets_the_natural_wording(self) -> None:
        result = budget_refusal([], date(2026, 5, 18), 10.0)
        assert result is not None
        assert "has no planned sessions to add an extra to" in result["message"]

    def test_a_week_of_only_skipped_rows_is_still_sized_by_them(self) -> None:
        # The solver sized this week at 200 load and a pause hollowed it out.
        # It is empty of load, not empty of plan — the allowance is measured
        # against what it was sized at.
        rows = [_sess_row(200.0, "menu:a", status="skipped")]
        assert budget_refusal(rows, date(2026, 5, 18), 10.0) is None

    def test_a_skipped_row_sizes_the_week_but_carries_nothing(self) -> None:
        rows = [
            _sess_row(100.0, "menu:a"),
            _sess_row(100.0, "menu:b", status="skipped"),
            _sess_row(5.0, "x1:extra_2026-05-21_run_easy"),
            _sess_row(999.0, "x2:extra_2026-05-21_run_easy", status="skipped"),
        ]
        # sized = 200 (both menu rows), carrying = 105 (the skipped extra
        # counts for nothing): the ceiling is 230, so 105 + 120 = 225 fits.
        assert budget_refusal(rows, date(2026, 5, 18), 120.0) is None
        over = budget_refusal(rows, date(2026, 5, 18), 130.0)
        assert over is not None
        assert "sized at 200.0 load" in over["message"]
        assert "still carrying 105.0 load" in over["message"]
        assert "125.0 load of room" in over["message"]

    def test_the_pause_hollowed_week_of_the_2026_09_20_incident(self) -> None:
        # Sessions 8 and 9 skipped by an illness pause, session 10 pending;
        # the solver sized the week at 102.8 load. A 15.1 load recovery run is
        # nowhere near the edge of that week's allowance.
        rows = [
            _sess_row(21.5, "menu:8", status="skipped"),
            _sess_row(65.0, "menu:9", status="skipped"),
            _sess_row(16.3, "menu:10"),
        ]
        assert budget_refusal(rows, date(2026, 9, 21), 15.1) is None

    def test_an_optional_session_neither_sizes_nor_carries_the_week(self) -> None:
        # R-SESS-037: outside the plan's load, so no room to add against.
        rows = [_sess_row(100.0, "menu:a"), _sess_row(100.0, "menu:b")]
        rows[1].optional = True
        assert budget_refusal(rows, date(2026, 5, 18), 14.0) is None
        over = budget_refusal(rows, date(2026, 5, 18), 16.0)
        assert over is not None
        assert "sized at 100.0 load" in over["message"]

    def test_over_budget_names_the_alternative(self) -> None:
        rows = [_sess_row(60.0, "menu:a")]
        result = budget_refusal(rows, date(2026, 5, 18), 45.0)
        assert result is not None
        assert "swap_planned_session" in result["message"]
