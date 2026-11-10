"""Tests for the PlanRepository (plan + plan_sessions tables)."""

import json
from dataclasses import replace

import pytest

from endury.database.sqlite.repositories.plan import PlanRow, PlanSessionRow
from endury.database.sqlite.repositories.write_models import SessionInsertRow
from endury.wiring.container import RepositoryContainer


@pytest.fixture
def repos():
    return RepositoryContainer.in_memory()


@pytest.fixture
def plan_repo(repos):
    return repos.plan


def _make_sessions(n: int = 2) -> list[SessionInsertRow]:
    return [
        SessionInsertRow(
            slot_id=f"w00_d{i}_s0",
            date=f"2026-03-0{i + 1}",
            week_index=0,
            phase_type="base",
            sport="running",
            activity_key="run_easy",
            family="easy",
            target_tss=50.0,
            achieved_tss=48.0,
            duration_minutes=60.0,
            workout_json=json.dumps({"steps": []}),
        )
        for i in range(n)
    ]


class TestPlanCRUD:
    def test_create_returns_id(self, plan_repo):
        pid = plan_repo.create(
            name="Test Plan",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="abc123",
        )
        assert isinstance(pid, int)
        assert pid > 0

    def test_get_active(self, plan_repo):
        assert plan_repo.get_active() is None

        plan_repo.create(
            name="Active Plan",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="abc",
        )
        active = plan_repo.get_active()
        assert active is not None
        assert isinstance(active, PlanRow)
        assert active.name == "Active Plan"
        assert active.status == "active"

    def test_get_by_id(self, plan_repo):
        pid = plan_repo.create(
            name="By ID",
            goal_type="half_ironman",
            goal_date="2026-09-01",
            plan_start="2026-04-01",
            solve_result_json='{"ok":true}',
            solver_version="0.2.0",
            input_hash="xyz",
        )
        row = plan_repo.get_by_id(pid)
        assert row is not None
        assert row.id == pid
        assert row.goal_type == "half_ironman"
        assert row.solver_version == "0.2.0"

    def test_get_by_id_missing(self, plan_repo):
        assert plan_repo.get_by_id(999) is None

    def test_create_archives_existing(self, plan_repo):
        pid1 = plan_repo.create(
            name="Plan 1",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="a",
        )
        pid2 = plan_repo.create(
            name="Plan 2",
            goal_type="running_marathon",
            goal_date="2026-07-01",
            plan_start="2026-04-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="b",
        )
        assert plan_repo.get_by_id(pid1).status == "archived"
        assert plan_repo.get_by_id(pid2).status == "active"
        assert plan_repo.get_active().id == pid2

    def test_archive(self, plan_repo):
        pid = plan_repo.create(
            name="To Archive",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="c",
        )
        plan_repo.archive(pid)
        assert plan_repo.get_active() is None
        assert plan_repo.get_by_id(pid).status == "archived"

    def test_create_with_optional_json(self, plan_repo):
        pid = plan_repo.create(
            name="Full",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="d",
            athlete_state_json='{"ctl_run":40}',
        )
        row = plan_repo.get_by_id(pid)
        assert row.athlete_state_json == '{"ctl_run":40}'


class TestSessionCRUD:
    def test_bulk_insert_and_get(self, plan_repo):
        pid = plan_repo.create(
            name="S",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="e",
        )
        sessions = _make_sessions(3)
        count = plan_repo.bulk_insert_sessions(pid, sessions)
        assert count == 3

        rows = plan_repo.get_sessions(pid)
        assert len(rows) == 3
        assert all(isinstance(r, PlanSessionRow) for r in rows)
        assert rows[0].slot_id == "w00_d0_s0"
        assert rows[0].sport == "running"

    def test_get_sessions_by_date_range(self, plan_repo):
        pid = plan_repo.create(
            name="DR",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="f",
        )
        plan_repo.bulk_insert_sessions(pid, _make_sessions(2))

        rows = plan_repo.get_sessions_by_date_range(pid, "2026-03-01", "2026-03-01")
        assert len(rows) == 1
        assert rows[0].date == "2026-03-01"

    def test_update_session_status(self, plan_repo):
        pid = plan_repo.create(
            name="US",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="g",
        )
        plan_repo.bulk_insert_sessions(pid, _make_sessions(1))
        rows = plan_repo.get_sessions(pid)
        sid = rows[0].id

        plan_repo.update_session_status(sid, "completed", matched_activity_id=42)
        updated = plan_repo.get_sessions(pid)
        assert updated[0].status == "completed"
        assert updated[0].matched_activity_id == 42

    def test_bulk_update_session_matches(self, plan_repo):
        pid = plan_repo.create(
            name="Bulk Match",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="bulk-match",
        )
        plan_repo.bulk_insert_sessions(pid, _make_sessions(2))
        rows = plan_repo.get_sessions(pid)

        updated_count = plan_repo.bulk_update_session_matches(
            [
                {
                    "session_id": rows[0].id,
                    "matched_activity_id": 100,
                    "status": "completed",
                },
                {
                    "session_id": rows[1].id,
                    "matched_activity_id": None,
                    "status": "pending",
                },
            ]
        )
        assert updated_count == 2

        refreshed = plan_repo.get_sessions(pid)
        assert refreshed[0].matched_activity_id == 100
        assert refreshed[0].status == "completed"
        assert refreshed[1].matched_activity_id is None
        assert refreshed[1].status == "pending"

    def test_bulk_update_session_matches_empty_input(self, plan_repo):
        assert plan_repo.bulk_update_session_matches([]) == 0

    def test_update_pushed_events_records_json_per_slot(self, plan_repo):
        pid = plan_repo.create(
            name="Pushed",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="push",
        )
        plan_repo.bulk_insert_sessions(pid, _make_sessions(2))
        assert all(row.pushed_event_json is None for row in plan_repo.get_sessions(pid))

        updated = plan_repo.update_pushed_events(
            pid, {"w00_d0_s0": '{"external_id":"coachie:1:w00_d0_s0"}'}
        )
        assert updated == 1

        rows = plan_repo.get_sessions(pid)
        assert json.loads(rows[0].pushed_event_json) == {
            "external_id": "coachie:1:w00_d0_s0"
        }
        assert rows[1].pushed_event_json is None

    def test_update_pushed_events_empty_input(self, plan_repo):
        assert plan_repo.update_pushed_events(1, {}) == 0


class TestUpdateSessionPrescription:
    """The week-adjustment write: prescription only, never the plan intent."""

    def _seed(self, plan_repo):
        pid = plan_repo.create(
            name="Trim",
            goal_type="running_marathon",
            goal_date="2026-06-01",
            plan_start="2026-03-01",
            solve_result_json="{}",
            solver_version="0.1.0",
            input_hash="trim",
        )
        rows = _make_sessions(2)
        rows[0] = replace(rows[0], duration_target_minutes=60.0)
        plan_repo.bulk_insert_sessions(pid, rows)
        plan_repo.update_pushed_events(pid, {"w00_d0_s0": '{"external_id":"x"}'})
        plan_repo.update_session_status(
            plan_repo.get_sessions(pid)[0].id, "pending", "act-9"
        )
        return pid

    def test_writes_only_the_five_prescription_columns(self, plan_repo):
        pid = self._seed(plan_repo)
        before = plan_repo.get_sessions(pid)[0]

        assert (
            plan_repo.update_session_prescription(
                before.id,
                pid,
                target_tss=30.0,
                achieved_tss=29.0,
                duration_minutes=36.0,
                workout_json=json.dumps({"steps": [], "coaching_intent": "Trimmed."}),
                tid_fractions_json=json.dumps(
                    {"z_low": 1.0, "z_mid": 0.0, "z_high": 0.0}
                ),
            )
            is True
        )

        after = plan_repo.get_sessions(pid)[0]
        assert (after.target_tss, after.achieved_tss) == (30.0, 29.0)
        assert after.duration_minutes == 36.0
        assert json.loads(after.workout_json)["coaching_intent"] == "Trimmed."
        assert json.loads(after.tid_fractions_json)["z_low"] == 1.0
        # The planner's hours-space intent and every identity/state column
        # survive byte-identical: they are what a later restore scales from.
        assert after.duration_target_minutes == before.duration_target_minutes == 60.0
        assert (after.date, after.week_index) == (before.date, before.week_index)
        assert (after.status, after.matched_activity_id) == ("pending", "act-9")
        assert after.pushed_event_json == before.pushed_event_json

    def test_tid_fractions_may_be_cleared(self, plan_repo):
        pid = self._seed(plan_repo)
        row = plan_repo.get_sessions(pid)[0]
        plan_repo.update_session_prescription(
            row.id,
            pid,
            target_tss=10.0,
            achieved_tss=10.0,
            duration_minutes=12.0,
            workout_json="{}",
            tid_fractions_json=None,
        )
        assert plan_repo.get_sessions(pid)[0].tid_fractions_json is None

    def test_a_foreign_plan_id_writes_nothing(self, plan_repo):
        pid = self._seed(plan_repo)
        row = plan_repo.get_sessions(pid)[0]
        assert (
            plan_repo.update_session_prescription(
                row.id,
                pid + 999,
                target_tss=1.0,
                achieved_tss=1.0,
                duration_minutes=1.0,
                workout_json="{}",
                tid_fractions_json=None,
            )
            is False
        )
        assert plan_repo.get_sessions(pid)[0].target_tss == row.target_tss
