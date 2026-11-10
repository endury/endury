"""Planner-output fingerprints for every scenario — the "plans unchanged" oracle.

Runs ``generate_plan`` for the registered scenarios, for the working tree
and for a git rev, and hashes each plan's sessions (slot, week, activity,
hours) plus its calendar days. Any refactor that must not change planner
behaviour — menus moving to YAML, code moving between packages — has to
leave every fingerprint identical.

By default only the scenarios whose plan a menu offers (``offered:``) are
compared; ``--all`` compares every scenario.

Usage:
    make run-devtool TOOL=planner_fingerprints ARGS='--rev main'
    make run-devtool TOOL=planner_fingerprints ARGS='--rev main --goal ironman'
    make run-devtool TOOL=planner_fingerprints ARGS='--rev main --all'

Exit code 0 = identical, 1 = changed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from devtools.planner._shared.revs import PACKAGE_PRELUDE, package_at
from devtools.planner._shared.scenarios import SCENARIOS
from endury.planner.sessions.menus import BLUEPRINTS

# Self-contained dump program (see menu_canon for why it is a subprocess).
# PLAN_FP_ONLY limits the run to a comma list of scenario names.
_DUMP_SNIPPET = (
    PACKAGE_PRELUDE
    + """
import hashlib, json, sys
generate_plan = _mod("planner").generate_plan
from devtools.planner._shared.scenarios import SCENARIOS

only = [s for s in os.environ.get("PLAN_FP_ONLY", "").split(",") if s]
out = {}
for name, scenario in SCENARIOS.items():
    if only and name not in only:
        continue
    result = generate_plan(scenario.to_request())
    rows = sorted(
        (s.slot_id, s.week, str(getattr(s.activity_key, "value", s.activity_key)), s.target_hours)
        for s in result.sessions
    )
    days = sorted((a.slot_id, a.date.isoformat()) for a in result.assignments)
    canon = json.dumps({"rows": [(a, b, c, round(d, 4)) for a, b, c, d in rows], "days": days})
    out[name] = {
        "goal": scenario.goals[0].goal_type.value,
        "n": len(rows),
        "sha": hashlib.sha256(canon.encode()).hexdigest()[:16],
        "hours": {f"{a}@{b}": d for a, b, c, d in rows},
    }
json.dump(out, sys.stdout)
"""
)


def offered_scenarios() -> list[str]:
    """Names of the scenarios whose A-goal plan the working tree's menus offer."""
    return [
        name
        for name, scenario in SCENARIOS.items()
        if scenario.ambition in BLUEPRINTS[scenario.goals[0].goal_type].offered
    ]


def _run_dump(cwd: Path, only: list[str] | None) -> dict[str, dict]:
    """Run the dump snippet with ``endury`` resolving from *cwd*.

    Args:
        cwd: The tree to fingerprint.
        only: Scenario names to limit the run to; ``None`` runs them all.
    """
    scope = {} if only is None else {"PLAN_FP_ONLY": ",".join(only)}
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_SNIPPET],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "", **scope},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"fingerprint dump failed in {cwd}:\n{proc.stderr}")
    dump: dict[str, dict] = json.loads(proc.stdout)
    return dump


def dump_worktree(repo_root: Path, only: list[str] | None = None) -> dict[str, dict]:
    """Fingerprint the working tree's plans (*only* these scenarios, if given)."""
    return _run_dump(repo_root, only)


def dump_rev(
    repo_root: Path, rev: str, only: list[str] | None = None
) -> dict[str, dict]:
    """Fingerprint the plans at git *rev* via ``git archive``."""
    archive = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "archive",
            rev,
            package_at(repo_root, rev),
            "devtools",
        ],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(
            f"git archive {rev!r} failed:\n{archive.stderr.decode(errors='replace')}"
        )
    tmp = Path(tempfile.mkdtemp(prefix="plan_fp_"))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        return _run_dump(tmp, only)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare(
    base: dict[str, dict], head: dict[str, dict], goal: str | None = None
) -> dict[str, str]:
    """Map each differing scenario (optionally one goal's) to a one-line reason."""
    changed: dict[str, str] = {}
    for name in sorted(set(base) | set(head)):
        b, h = base.get(name), head.get(name)
        if goal is not None and (b or h or {}).get("goal") != goal:
            continue
        if b is None or h is None:
            changed[name] = "present on one side only"
        elif b["sha"] != h["sha"]:
            slots = set(b["hours"]) ^ set(h["hours"])
            common = set(b["hours"]) & set(h["hours"])
            worst = max(
                (abs(b["hours"][k] - h["hours"][k]) for k in common), default=0.0
            )
            changed[name] = (
                f"n {b['n']} -> {h['n']}, {len(slots)} slot(s) differ, "
                f"max |Δ| {60 * worst:.3f} min"
            )
    return changed


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rev", default="HEAD", help="git rev to compare against")
    parser.add_argument("--goal", default=None, help="GoalType value to restrict to")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--all",
        action="store_true",
        help="compare every scenario, not only the plans a menu offers",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    only = None if args.all else offered_scenarios()
    head = dump_worktree(repo_root, only)
    changed = compare(dump_rev(repo_root, args.rev, only), head, args.goal)
    if args.json:
        print(
            json.dumps(
                {"rev": args.rev, "scenarios": len(head), "changed": changed}, indent=2
            )
        )
    else:
        for name, why in changed.items():
            print(f"~ {name}: {why}")
        print(f"{len(changed)} of {len(head)} scenario plan(s) differ from {args.rev}.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
