"""The plan_fingerprints oracle: real dump of one scenario, compare, CLI exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from devtools.planner import plan_fingerprints

REPO = Path(__file__).resolve().parents[2]


def test_worktree_dump_fingerprints_a_scenario() -> None:
    dump = plan_fingerprints.dump_worktree(REPO, ["5k_beginner"])
    assert list(dump) == ["5k_beginner"]
    entry = dump["5k_beginner"]
    assert entry["goal"] == "running_5k"
    assert entry["n"] == len(entry["hours"]) > 0
    assert len(entry["sha"]) == 16


def _entry(sha: str, hours: dict[str, float], goal: str = "running_5k") -> dict:
    return {"goal": goal, "n": len(hours), "sha": sha, "hours": hours}


def test_compare_reports_hour_delta_missing_side_and_goal_filter() -> None:
    base = {"a": _entry("1", {"s@0": 1.0}), "b": _entry("2", {"s@0": 1.0}, "ironman")}
    head = {"a": _entry("9", {"s@0": 1.5, "t@1": 1.0}), "c": _entry("3", {})}
    changed = plan_fingerprints.compare(base, head)
    assert changed["a"] == "n 1 -> 2, 1 slot(s) differ, max |Δ| 30.000 min"
    assert changed["b"] == changed["c"] == "present on one side only"
    assert list(plan_fingerprints.compare(base, head, goal="ironman")) == ["b"]


def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    same = {"a": _entry("1", {"s@0": 1.0})}
    scopes: list[list[str] | None] = []

    def dump_rev(_root: Path, _rev: str, only: list[str] | None) -> dict:
        scopes.append(only)
        return same

    monkeypatch.setattr(plan_fingerprints, "dump_rev", dump_rev)
    monkeypatch.setattr(plan_fingerprints, "dump_worktree", lambda _root, _only: same)
    assert plan_fingerprints.main(["--rev", "main"]) == 0
    assert "0 of 1 scenario plan(s) differ" in capsys.readouterr().out
    assert plan_fingerprints.main(["--rev", "main", "--all"]) == 0
    assert scopes == [plan_fingerprints.offered_scenarios(), None]
    monkeypatch.setattr(
        plan_fingerprints,
        "dump_worktree",
        lambda _root, _only: {"a": _entry("2", {"s@0": 2.0})},
    )
    assert plan_fingerprints.main(["--rev", "main", "--json"]) == 1


def test_only_the_scenarios_of_plans_on_offer_are_compared_by_default() -> None:
    assert plan_fingerprints.offered_scenarios() == [
        "hm_moderate",
        "half_ironman_moderate",
        "ironman_moderate",
        "ironman_a_oly_b",
        "ironman_a_half_b",
        "trail_20k_basic",
        "trail_returner_20k",
        "trail_maintenance_basic",
        "triathlon_maintenance_moderate",
        "triathlon_maintenance_moderate_tune_up",
    ]
