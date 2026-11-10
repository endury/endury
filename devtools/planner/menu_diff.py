"""Blast-radius diff for the planner session-menu blueprints.

Flattens every ``(goal, phase, mode_key, ambition)`` menu cell into a
multiset of SessionSpec fingerprints — once for the working tree and
once for a git rev (via ``git archive``) — and reports every changed
cell with per-field deltas in menu units (minutes / metres).

An empty diff is the acceptance gate for structural menu refactors: it
proves the flattened prescription is byte-identical even when the
authoring representation changed. Exit code 0 = identical, 1 = changed.

Usage:
    make run-devtool TOOL=planner_menu_diff                # vs HEAD
    make run-devtool TOOL=planner_menu_diff ARGS='--rev HEAD~3 --goal ironman'
    make run-devtool TOOL=planner_menu_diff ARGS='--json'
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from devtools.planner._shared.revs import PACKAGE_PRELUDE, package_at

# Self-contained dump program run via ``python -c`` with cwd at either
# the repo root (working tree) or a ``git archive`` extraction (base
# rev), so ``endury`` always resolves from that directory. Keeping it
# a subprocess for BOTH sides means the fingerprint definition exists
# exactly once and old revs need no devtools code.
_DUMP_SNIPPET = (
    PACKAGE_PRELUDE
    + """
import json, sys
AmbitionLevel = _mod("planner.foundations.types").AmbitionLevel
_menus = _mod("planner.sessions.menus")
BLUEPRINTS, get_session_menu = _menus.BLUEPRINTS, _menus.get_session_menu

FIELDS = (
    "activity_key", "min_minutes", "max_minutes", "min_meters", "max_meters",
    "day_hint", "phase_fraction_start", "phase_fraction_end", "progressive",
    "progression_key", "is_brick_pair", "rotation",
    "slot_group", "race_sim", "eases", "cadence_weeks",
)

def norm(value):
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return [norm(v) for v in value]
    return value

cells = {}
for goal, blueprint in BLUEPRINTS.items():
    for phase, mode_key in blueprint.phases:
        for ambition in AmbitionLevel:
            specs = get_session_menu(goal, phase, mode_key, ambition)
            if specs:
                key = "/".join((goal.value, phase.value, mode_key, ambition.value))
                cells[key] = [
                    {f: norm(getattr(s, f, None)) for f in FIELDS} for s in specs
                ]
json.dump(cells, sys.stdout)
"""
)

# Fields that identify "the same slot" across the two sides; everything
# else (bounds, flags, progression) counts as a change on a matched slot.
_IDENTITY_FIELDS = (
    "activity_key",
    "day_hint",
    "slot_group",
    "phase_fraction_start",
    "phase_fraction_end",
)

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _run_dump(cwd: Path) -> dict[str, list[dict]]:
    """Run the dump snippet with ``endury`` resolving from *cwd*."""
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_SNIPPET],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"menu dump failed in {cwd}:\n{proc.stderr}")
    dump: dict[str, list[dict]] = json.loads(proc.stdout)
    return dump


def dump_worktree(repo_root: Path) -> dict[str, list[dict]]:
    """Fingerprint the working tree's menus."""
    return _run_dump(repo_root)


def dump_rev(repo_root: Path, rev: str) -> dict[str, list[dict]]:
    """Fingerprint the menus at git *rev* via ``git archive``."""
    archive = subprocess.run(
        ["git", "-C", str(repo_root), "archive", rev, package_at(repo_root, rev)],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(
            f"git archive {rev!r} failed:\n{archive.stderr.decode(errors='replace')}"
        )
    tmp = Path(tempfile.mkdtemp(prefix="menu_diff_"))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        return _run_dump(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _identity(fp: dict) -> str:
    """Stable identity key for pairing fingerprints across sides."""
    return json.dumps([fp.get(f) for f in _IDENTITY_FIELDS])


def _slot_label(fp: dict) -> str:
    """Human-readable one-liner for a fingerprint."""
    day_hint = fp.get("day_hint")
    day = _DAYS[day_hint] if isinstance(day_hint, int) and 0 <= day_hint <= 6 else "any"
    return f"{fp['activity_key']} @{day}"


def diff_cells(
    base: dict[str, list[dict]], work: dict[str, list[dict]]
) -> dict[str, list[str]]:
    """Diff two cell→fingerprints maps.

    Args:
        base: Fingerprints at the base rev.
        work: Fingerprints in the working tree.

    Returns:
        Mapping of changed cell key → human-readable change lines
        (``~`` field deltas, ``+`` added, ``-`` removed). Empty when
        the flattened menus are identical.
    """
    out: dict[str, list[str]] = {}
    for cell in sorted(base.keys() | work.keys()):
        lines: list[str] = []
        base_by_id: dict[str, list[dict]] = {}
        for fp in base.get(cell, []):
            base_by_id.setdefault(_identity(fp), []).append(fp)
        for identity in base_by_id:
            base_by_id[identity].sort(key=json.dumps)
        for fp in sorted(work.get(cell, []), key=json.dumps):
            candidates = base_by_id.get(_identity(fp))
            if not candidates:
                lines.append(f"+ {_slot_label(fp)} {json.dumps(fp, sort_keys=True)}")
                continue
            old = candidates.pop(0)
            deltas = [
                f"{f} {old[f]!r}→{fp[f]!r}" for f in fp if old.get(f) != fp.get(f)
            ]
            if deltas:
                lines.append(f"~ {_slot_label(fp)}  {', '.join(deltas)}")
        lines.extend(
            f"- {_slot_label(fp)}"
            for candidates in base_by_id.values()
            for fp in candidates
        )
        if lines:
            out[cell] = lines
    return out


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Args:
        argv: Optional argv override; defaults to ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(
        prog="devtools.planner.menu_diff",
        description="Diff flattened session menus: working tree vs a git rev.",
    )
    parser.add_argument("--rev", default="HEAD", help="Base rev (default: HEAD).")
    parser.add_argument("--goal", default=None, help="Only diff one goal slug.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    base = dump_rev(repo_root, args.rev)
    work = dump_worktree(repo_root)
    if args.goal:
        base = {k: v for k, v in base.items() if k.startswith(args.goal + "/")}
        work = {k: v for k, v in work.items() if k.startswith(args.goal + "/")}
        if not base and not work:
            sys.exit(f"error: --goal {args.goal!r} matches no menu cells")

    changed = diff_cells(base, work)
    unchanged = len(base.keys() | work.keys()) - len(changed)
    if args.as_json:
        print(json.dumps({"changed": changed, "unchanged_cells": unchanged}, indent=2))
    else:
        for cell, lines in changed.items():
            print(cell)
            for line in lines:
                print(f"  {line}")
        print(f"{len(changed)} cells changed, {unchanged} unchanged (vs {args.rev}).")
    sys.exit(1 if changed else 0)


if __name__ == "__main__":
    main()
