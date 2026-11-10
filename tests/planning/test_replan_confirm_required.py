"""Tests for the replan's hand-edited-events confirmation gate."""

from __future__ import annotations

from endury.planning.replan_plan import replan_or_confirm_required


def test_no_drift_lets_the_replan_run() -> None:
    assert replan_or_confirm_required([]) is None


def test_drifted_events_require_confirmation() -> None:
    diff = [
        {
            "external_id": "coachie:1:w05_d2_s0",
            "date": "2026-06-02",
            "name": "Tempo Run",
            "changed_fields": ["description"],
        },
        {
            "external_id": "coachie:1:w05_d4_s0",
            "date": "2026-06-04",
            "name": "Long Run",
            "changed_fields": ["start_date_local"],
        },
    ]

    assert replan_or_confirm_required(diff) == {
        "status": "error",
        "error_code": "confirm_required",
        "error_message": (
            "2 upcoming workout(s) were edited on intervals.icu and would be "
            "overwritten by this replan."
        ),
        "diff": diff,
    }
