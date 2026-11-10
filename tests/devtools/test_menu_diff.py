"""Tests for the menu_diff fingerprint-diff logic (pure part, no git)."""

from __future__ import annotations

from devtools.planner.menu_diff import diff_cells


def _fp(**overrides):
    fp = {
        "activity_key": "run_easy",
        "min_minutes": 30,
        "max_minutes": 45,
        "day_hint": 2,
        "phase_fraction_start": 0.0,
        "phase_fraction_end": 1.0,
        "slot_group": None,
        "progressive": False,
    }
    fp.update(overrides)
    return fp


def test_identical_cells_produce_empty_diff():
    cells = {"ironman/base/load/basic": [_fp(), _fp(activity_key="bike_long")]}
    assert diff_cells(cells, cells) == {}


def test_bounds_change_reports_field_delta_on_matched_slot():
    base = {"c": [_fp()]}
    work = {"c": [_fp(max_minutes=60)]}
    diff = diff_cells(base, work)
    assert list(diff) == ["c"]
    assert diff["c"] == ["~ run_easy @wed  max_minutes 45→60"]


def test_added_and_removed_slots_are_reported():
    base = {"c": [_fp()]}
    work = {"c": [_fp(), _fp(activity_key="swim_long", day_hint=4)]}
    diff = diff_cells(base, work)
    assert any(line.startswith("+ swim_long @fri") for line in diff["c"])
    diff = diff_cells(work, base)
    assert "- swim_long @fri" in diff["c"]


def test_duplicate_identities_match_by_multiplicity():
    base = {"c": [_fp(), _fp()]}
    work = {"c": [_fp(), _fp(max_minutes=60)]}
    diff = diff_cells(base, work)
    assert diff["c"] == ["~ run_easy @wed  max_minutes 45→60"]


def test_missing_day_hint_from_old_rev_does_not_crash():
    # A rev whose SessionSpec lacks a fingerprint field dumps it as None
    # (getattr fallback); the differ must report, not raise.
    diff = diff_cells({"c": [_fp()]}, {"c": [_fp(day_hint=None)]})
    assert any("run_easy @any" in line for line in diff["c"])
