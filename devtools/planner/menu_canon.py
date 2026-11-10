"""Canonical dump of every planner menu blueprint — the "nothing changed" oracle.

``menu_diff`` compares the *flattened* SessionSpecs, which is blind to slot
order, slot names, the athlete-facing ``why`` text, menu contracts and the
maintenance supplement menus. This tool dumps everything a menu author can
change — in authoring order — for the working tree and for a git rev, and
reports every goal whose canonical form differs. Engineering-only notes
(``Rationale.intent``/``citations``/``last_reviewed`` and a Progression's
rationale) are left out, so moving them into comments is not a change.

Usage:
    make run-devtool TOOL=planner_menu_canon ARGS='--rev main'
    make run-devtool TOOL=planner_menu_canon ARGS='--rev main --goal ironman'

Exit code 0 = identical, 1 = changed.
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

# Self-contained dump program, run with cwd at either the repo root or a
# ``git archive`` extraction so ``endury`` resolves from that directory and
# old revs need no devtools code. The canonical form is generic over the
# menu dataclasses, so a field added on one side shows up as a difference.
_DUMP_SNIPPET = (
    PACKAGE_PRELUDE
    + """
import dataclasses, enum, json, sys
from collections.abc import Mapping
_menus = _mod("planner.sessions.menus")
BLUEPRINTS, MAINTENANCE_MENUS = _menus.BLUEPRINTS, _menus.MAINTENANCE_MENUS

def key(k):
    if isinstance(k, tuple):
        return "/".join(str(canon(p)) for p in k)
    return str(canon(k))

def canon(value):
    kind = type(value).__name__
    if kind == "Progression":
        return value.name
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        out = {}
        for f in dataclasses.fields(value):
            v = getattr(value, f.name)
            if f.name == "rationale":
                out[f.name] = (v.athlete_why if v is not None else "") or ""
            else:
                out[f.name] = canon(v)
        return out
    if isinstance(value, Mapping):
        return {key(k): canon(v) for k, v in sorted(value.items(), key=lambda kv: key(kv[0]))}
    if isinstance(value, (frozenset, set)):
        return sorted((canon(v) for v in value), key=json.dumps)
    if isinstance(value, (tuple, list)):
        return [canon(v) for v in value]
    return value

dump = {"blueprint:" + key(g): canon(bp) for g, bp in BLUEPRINTS.items()}
dump.update({"maintenance:" + key(g): canon(specs) for g, specs in MAINTENANCE_MENUS.items()})
json.dump(dump, sys.stdout, sort_keys=True)
"""
)


def _run_dump(cwd: Path) -> dict[str, object]:
    """Run the dump snippet with ``endury`` resolving from *cwd*."""
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_SNIPPET],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"menu canon dump failed in {cwd}:\n{proc.stderr}")
    dump: dict[str, object] = json.loads(proc.stdout)
    return dump


def dump_worktree(repo_root: Path) -> dict[str, object]:
    """Canonical menus of the working tree."""
    return _run_dump(repo_root)


def dump_rev(repo_root: Path, rev: str) -> dict[str, object]:
    """Canonical menus at git *rev* via ``git archive``."""
    archive = subprocess.run(
        ["git", "-C", str(repo_root), "archive", rev, package_at(repo_root, rev)],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(
            f"git archive {rev!r} failed:\n{archive.stderr.decode(errors='replace')}"
        )
    tmp = Path(tempfile.mkdtemp(prefix="menu_canon_"))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        return _run_dump(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def diff_paths(old: object, new: object, path: str = "", limit: int = 8) -> list[str]:
    """Return up to *limit* ``path: old -> new`` lines where two values differ."""
    out: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            if len(out) >= limit:
                break
            if k not in old:
                out.append(f"{path}.{k}: + {json.dumps(new[k])[:120]}")
            elif k not in new:
                out.append(f"{path}.{k}: - {json.dumps(old[k])[:120]}")
            else:
                out.extend(diff_paths(old[k], new[k], f"{path}.{k}", limit - len(out)))
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append(f"{path}: length {len(old)} -> {len(new)}")
        for i, (a, b) in enumerate(zip(old, new, strict=False)):
            if len(out) >= limit:
                break
            out.extend(diff_paths(a, b, f"{path}[{i}]", limit - len(out)))
    elif old != new:
        out.append(f"{path}: {json.dumps(old)[:120]} -> {json.dumps(new)[:120]}")
    return out[:limit]


def compare(
    base: dict[str, object], head: dict[str, object], goal: str | None = None
) -> dict[str, list[str]]:
    """Map each differing dump key (optionally one goal's) to its diff lines."""
    changed: dict[str, list[str]] = {}
    for k in sorted(set(base) | set(head)):
        if goal is not None and k.split(":", 1)[1] != goal:
            continue
        if base.get(k) != head.get(k):
            changed[k] = diff_paths(base.get(k), head.get(k)) or [
                "(present on one side only)"
            ]
    return changed


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rev", default="HEAD", help="git rev to compare against")
    parser.add_argument("--goal", default=None, help="GoalType value to restrict to")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    changed = compare(
        dump_rev(repo_root, args.rev), dump_worktree(repo_root), args.goal
    )
    if args.json:
        print(json.dumps({"rev": args.rev, "changed": changed}, indent=2))
    else:
        for k, lines in changed.items():
            print(f"~ {k}")
            for line in lines:
                print(f"    {line}")
        print(f"{len(changed)} menu dump(s) differ from {args.rev}.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
