"""The menu_canon oracle: worktree dump shape, diff reporting, CLI exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from devtools.planner import menu_canon
from endury.planner.sessions.menus import BLUEPRINTS

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dump() -> dict:
    return menu_canon.dump_worktree(REPO)


def test_dump_covers_every_blueprint_and_maintenance_goal(dump: dict) -> None:
    blueprints = {k for k in dump if k.startswith("blueprint:")}
    assert blueprints == {f"blueprint:{g.value}" for g in BLUEPRINTS}
    assert any(k.startswith("maintenance:") for k in dump)


def test_dump_keeps_slot_order_names_and_athlete_why(dump: dict) -> None:
    blueprint = BLUEPRINTS[next(iter(BLUEPRINTS))]
    (phase, mode), menu = next(iter(blueprint.phases.items()))
    canon_menu = dump[f"blueprint:{blueprint.goal.value}"]["phases"][
        f"{phase.value}/{mode}"
    ]
    assert [s["name"] for s in canon_menu["slots"]] == [s.name for s in menu.slots]
    first = menu.slots[0]
    expected_why = first.rationale.athlete_why if first.rationale else ""
    assert canon_menu["slots"][0]["rationale"] == expected_why


def test_compare_reports_differing_path_and_goal_filter() -> None:
    base = {"blueprint:a": {"x": [1, 2]}, "blueprint:b": {"y": 1}}
    head = {"blueprint:a": {"x": [1, 3]}, "blueprint:b": {"y": 1, "z": 2}}
    changed = menu_canon.compare(base, head)
    assert changed["blueprint:a"] == [".x[1]: 2 -> 3"]
    assert changed["blueprint:b"] == [".z: + 2"]
    assert list(menu_canon.compare(base, head, goal="a")) == ["blueprint:a"]


def test_diff_paths_reports_length_removal_and_one_sided_keys() -> None:
    assert menu_canon.diff_paths({"k": 1}, {}) == [".k: - 1"]
    assert menu_canon.diff_paths([1], [1, 2]) == [": length 1 -> 2"]
    assert menu_canon.compare({"blueprint:a": 1}, {}) == {
        "blueprint:a": [": 1 -> null"]
    }


def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(menu_canon, "dump_rev", lambda _root, _rev: {"blueprint:a": 1})
    monkeypatch.setattr(menu_canon, "dump_worktree", lambda _root: {"blueprint:a": 1})
    assert menu_canon.main(["--rev", "main"]) == 0
    monkeypatch.setattr(menu_canon, "dump_worktree", lambda _root: {"blueprint:a": 2})
    assert menu_canon.main(["--rev", "main", "--json"]) == 1
    assert '"blueprint:a"' in capsys.readouterr().out
