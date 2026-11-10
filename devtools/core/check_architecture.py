#!/usr/bin/env python3
"""Architecture fitness checks for active runtime boundaries.

This script enforces the structural constraints that keep the codebase
maintainable.  It is run automatically as part of ``make lint`` and on the
pre-push hook, in addition to being available as a standalone devtool.

**1. Package boundaries**
The standalone ``endury/planner/`` engine must never import from
``endury.domain`` — it depends only on ``endury.training`` for shared
training-science primitives.

**2. Service-function size and complexity budgets**
The 120-line function-length budget and the cyclomatic-complexity budget
(15) apply to all of ``endury/planning/`` (including the wire serializers in
``endury/planning/wire/``), ``endury/browser/`` and ``endury/store/``
(``SERVICE_PATH_PREFIXES``). Functions already over budget when the scope
widened are dated in ``FUNCTION_BUDGET_WAIVERS`` (waived only up to their
recorded count — growing past it still fails).

These budgets keep individual functions from growing into unreadable
monoliths.

**3. Single-home rules**
Zone band-specs, athlete SQL, and the injectable clock each have exactly one
home (``zone-spec-single-home``, ``athlete-sql-gateway``,
``bare-clock-usage``), and no I/O runs inside a database block
(``db-block-io``).

**4. Store boundaries**
``endury/store/`` (the intervals.icu-backed snapshot of the durable tables)
imports only ``endury.database``, ``endury.intervals`` and
``endury.common`` (``store-imports``), and only ``endury/browser/`` may
import it (``store-boundary``): the store is the browser runtime's concern,
never the planner's or the coach's.

**5. Pyodide bridge isolation**
``endury/`` runs both natively (tests, devtools) and inside the Pyodide
worker; nothing under it may import ``js`` or ``pyodide`` (``no-pyodide-
import``) — that bridge belongs to ``web/worker/py_worker.js`` alone.

**6. Analysis sandbox**
``endury/analysis/`` imports numpy, the standard library and itself only
(``analysis-standalone``): the sandbox worker that runs the coach's analysis
scripts loads nothing else.

Exit code is non-zero when any violation is found.

Usage::

    make run-devtool TOOL=check_architecture
    make lint                               # also runs this check
    make check-arch                         # shortcut alias
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Function-length and function-complexity budgets cover all of
# endury/planning/, endury/browser/ and endury/store/ — most of the service
# surface grew here without either check ever seeing it. Pre-existing
# violations the widening surfaced are dated in FUNCTION_BUDGET_WAIVERS below.
SERVICE_PATH_PREFIXES: tuple[str, ...] = (
    "endury/planning/",
    "endury/browser/",
    "endury/store/",
)

MAX_FUNCTION_LINES = 120
MAX_FUNCTION_COMPLEXITY = 15

# Dated waivers for functions already over MAX_FUNCTION_LINES and/or
# MAX_FUNCTION_COMPLEXITY when the function-length/complexity checks were
# widened to the same scope as SERVICE_PATH_PREFIXES. A
# waived function still fails once it grows past its waived count on either
# axis, and the "waivers point at real code" test catches a waiver whose
# path or function disappears — this table is debt tracking, not a blanket
# exemption. Entry: "path::function" -> (waived lines or None, waived
# complexity or None, date, reason).
FUNCTION_BUDGET_WAIVERS: dict[str, tuple[int | None, int | None, str, str]] = {
    "endury/planning/coach_brief.py::build_coach_brief": (
        None,
        20,
        "2026-09-14",
        "older than the budget",
    ),
    "endury/planning/commit_plan.py::reschedule_session": (
        None,
        17,
        "2026-09-14",
        "older than the budget",
    ),
    "endury/planning/get_active_plan.py::build_active_render_result": (
        None,
        19,
        "2026-09-14",
        "older than the budget",
    ),
    "endury/planning/load_planner_metrics.py::get_planner_load_context": (
        None,
        19,
        "2026-09-14",
        "older than the budget",
    ),
    "endury/planning/replan_plan.py::run_replan": (
        152,
        None,
        "2026-09-23",
        "a linear composition: the race refusal, the fresh retry,"
        " persistence and the splice are their own functions",
    ),
    "endury/planning/swap_session.py::plan_session_swap": (
        None,
        18,
        "2026-09-20",
        "older than the budget, plus the one"
        " branch the optional same-week re-date adds",
    ),
}

# The planner is host-free: it must never import the shared domain models.
PLANNER_PREFIX = "endury/planner/"
PLANNER_BANNED_ROOTS: tuple[str, ...] = ("endury.domain",)

# The store's whole dependency surface inside endury, and the one package
# allowed to depend on the store.
STORE_PREFIX = "endury/store/"
STORE_MODULE = "endury.store"
STORE_ALLOWED_ROOTS: tuple[str, ...] = (
    "endury.database",
    "endury.intervals",
    "endury.common",
    STORE_MODULE,
)
STORE_IMPORTER_PREFIXES: tuple[str, ...] = ("endury/browser/", STORE_PREFIX)


@dataclass(frozen=True)
class Violation:
    """One architecture violation."""

    path: str
    line: int
    rule: str
    detail: str


class _ComplexityVisitor(ast.NodeVisitor):
    """Approximate cyclomatic complexity for one function body."""

    def __init__(self) -> None:
        self.score = 1

    def _bump(self, amount: int = 1) -> None:
        self.score += amount

    def visit_If(self, node: ast.If) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        # One branch for each except/else/finally path.
        self._bump(max(len(node.handlers), 1))
        if node.orelse:
            self._bump()
        if node.finalbody:
            self._bump()
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # a and b and c introduces additional branch points.
        if len(node.values) > 1:
            self._bump(len(node.values) - 1)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._bump()
        self.generic_visit(node)


@dataclass(frozen=True)
class ModuleCheckConfig:
    """Computed classification flags for one module path."""

    is_service_module: bool


def _relative(path: Path) -> str:
    """Return path relative to repository root in posix form."""
    return path.relative_to(REPO_ROOT).as_posix()


def _classify(path: Path) -> ModuleCheckConfig:
    """Classify a path for relevant architecture checks."""
    rel = _relative(path)
    return ModuleCheckConfig(is_service_module=rel.startswith(SERVICE_PATH_PREFIXES))


def _iter_python_files() -> list[Path]:
    """Return all Python files under endury, devtools and bench."""
    roots = [REPO_ROOT / "endury", REPO_ROOT / "devtools", REPO_ROOT / "bench"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.py")))
    return files


def _imported_modules(node: ast.AST) -> list[tuple[str, int]]:
    """Extract absolute module import targets from one AST node."""
    modules: list[tuple[str, int]] = []
    if isinstance(node, ast.Import):
        modules.extend((alias.name, node.lineno) for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        modules.append((node.module, node.lineno))
    return modules


def _check_planner_boundaries(tree: ast.Module, rel: str) -> list[Violation]:
    """Keep planner free of host imports (endury.domain).

    planner is the standalone session-menu engine; its only shared
    dependency for training-science primitives is endury.training. It must
    not reach back into the host domain layer.
    """
    if not rel.startswith(PLANNER_PREFIX):
        return []

    return [
        Violation(
            path=rel,
            line=lineno,
            rule="planner-boundary",
            detail=(
                f"planner modules must not import '{root}'; "
                "use endury.training for shared training-science primitives"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        for root in PLANNER_BANNED_ROOTS
        if module_name == root or module_name.startswith(root + ".")
    ]


def _check_bench_independence(tree: ast.Module, rel: str) -> list[Violation]:
    """Keep the benchmark package free of endury imports.

    ``bench/`` grades any planner that exports into its plan table; Endury is
    one contestant, adapted under ``devtools/bench``. An endury import inside
    ``bench/`` would make the benchmark Endury's own again.
    """
    if not rel.startswith("bench/"):
        return []
    return [
        Violation(
            path=rel,
            line=lineno,
            rule="bench-independence",
            detail=(
                f"bench modules must not import '{module_name}'; Endury enters the "
                "benchmark through devtools/bench"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        if module_name == "endury" or module_name.startswith("endury.")
    ]


def _is_module_under(module_name: str, root: str) -> bool:
    """Whether an imported module name is *root* or a submodule of it."""
    return module_name == root or module_name.startswith(root + ".")


def _check_store_imports(tree: ast.Module, rel: str) -> list[Violation]:
    """Keep the store's endury imports to database, intervals and common.

    The store snapshots the durable tables into an intervals.icu note; it
    must not know the planner, the coach, the domain models or the wire
    shapes, or every one of them becomes a store change.
    """
    if not rel.startswith(STORE_PREFIX):
        return []

    return [
        Violation(
            path=rel,
            line=lineno,
            rule="store-imports",
            detail=(
                f"store modules must not import '{module_name}'; the store "
                "imports only endury.database, endury.intervals and "
                "endury.common"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        if _is_module_under(module_name, "endury")
        and not any(_is_module_under(module_name, root) for root in STORE_ALLOWED_ROOTS)
    ]


def _check_store_boundary(tree: ast.Module, rel: str) -> list[Violation]:
    """Only the browser runtime (and the store itself) imports endury.store."""
    if not rel.startswith("endury/") or rel.startswith(STORE_IMPORTER_PREFIXES):
        return []

    return [
        Violation(
            path=rel,
            line=lineno,
            rule="store-boundary",
            detail=(
                f"'{module_name}' may only be imported from endury/browser/ — "
                "the store is the browser runtime's concern"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        if _is_module_under(module_name, STORE_MODULE)
    ]


#: The analysis library and its runner load alone in the sandbox worker.
ANALYSIS_PREFIX = "endury/analysis/"
#: The third-party modules the sandbox worker loads.
ANALYSIS_ALLOWED_THIRD_PARTY: tuple[str, ...] = ("numpy",)


def _check_analysis_standalone(tree: ast.Module, rel: str) -> list[Violation]:
    """Keep endury/analysis/ to numpy, the standard library and itself.

    The sandbox worker (web/worker/analysis_worker.js) runs the coach's
    analysis scripts with numpy and this package only; an import of the
    rest of Endury, or of a package the worker does not load, would fail
    there and nowhere else.
    """
    if not rel.startswith(ANALYSIS_PREFIX):
        return []
    stdlib = sys.stdlib_module_names
    return [
        Violation(
            path=rel,
            line=lineno,
            rule="analysis-standalone",
            detail=(
                f"analysis modules must not import '{module_name}'; they "
                "import numpy, the standard library and endury.analysis only"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        if not _is_module_under(module_name, "endury.analysis")
        and module_name.split(".")[0] not in stdlib
        and module_name.split(".")[0] not in ANALYSIS_ALLOWED_THIRD_PARTY
        and module_name != "__future__"
    ]


#: The Pyodide bridge modules endury/ must never import directly.
_PYODIDE_BRIDGE_ROOTS: tuple[str, ...] = ("js", "pyodide")


def _check_no_pyodide_imports(tree: ast.Module, rel: str) -> list[Violation]:
    """Ban ``import js`` / ``import pyodide`` anywhere under endury/.

    endury/ runs both natively (tests, devtools) and inside the Pyodide
    worker; the only place that talks to the Pyodide/JS bridge is
    ``web/worker/py_worker.js``. An import here would break native execution
    and couple the engine to one runtime.
    """
    if not rel.startswith("endury/"):
        return []

    return [
        Violation(
            path=rel,
            line=lineno,
            rule="no-pyodide-import",
            detail=(
                f"'{module_name}' must not be imported under endury/ — the "
                "browser worker is the only Pyodide/JS bridge"
            ),
        )
        for node in ast.walk(tree)
        for module_name, lineno in _imported_modules(node)
        for root in _PYODIDE_BRIDGE_ROOTS
        if module_name == root or module_name.startswith(root + ".")
    ]


def _check_service_function_budgets(tree: ast.Module, rel: str) -> list[Violation]:
    """Check function length and complexity budgets for service modules.

    A waived function (see FUNCTION_BUDGET_WAIVERS) is allowed up to its
    waived count on whichever axis it was waived for — but no further:
    growing past the waived count still fails, same as an unwaived function
    over budget.
    """
    path = REPO_ROOT / rel
    flags = _classify(path)
    if not flags.is_service_module:
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        waiver = FUNCTION_BUDGET_WAIVERS.get(f"{rel}::{node.name}")
        waived_lines = waiver[0] if waiver else None
        waived_complexity = waiver[1] if waiver else None

        if node.end_lineno is None:
            continue
        fn_len = node.end_lineno - node.lineno + 1
        length_budget = max(MAX_FUNCTION_LINES, waived_lines or 0)
        if fn_len > length_budget:
            violations.append(
                Violation(
                    path=rel,
                    line=node.lineno,
                    rule="function-size",
                    detail=(
                        f"function '{node.name}' has {fn_len} lines "
                        f"(max {length_budget})"
                    ),
                )
            )

        visitor = _ComplexityVisitor()
        visitor.visit(node)
        complexity_budget = max(MAX_FUNCTION_COMPLEXITY, waived_complexity or 0)
        if visitor.score > complexity_budget:
            violations.append(
                Violation(
                    path=rel,
                    line=node.lineno,
                    rule="function-complexity",
                    detail=(
                        f"function '{node.name}' complexity is {visitor.score} "
                        f"(max {complexity_budget})"
                    ),
                )
            )

    return violations


# Paths exempt from the bare-clock ban (the clock module itself).
_CLOCK_EXEMPT_PATHS: tuple[str, ...] = (
    "endury/common/clock.py",
    "endury/clock.py",
)

# AST attribute patterns that indicate bare clock usage:
#   date.today()     -> Attribute(attr="today", value=Name(id="date"))
#   datetime.now()   -> Attribute(attr="now", value=Name(id="datetime"))
#   datetime.today() -> Attribute(attr="today", value=Name(id="datetime"))
#   datetime.utcnow()-> Attribute(attr="utcnow", value=Name(id="datetime"))
_BARE_CLOCK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("date", "today"),
    ("datetime", "now"),
    ("datetime", "today"),
    ("datetime", "utcnow"),
)


def _check_bare_clock_usage(tree: ast.Module, rel: str) -> list[Violation]:
    """Disallow bare date.today()/datetime.now() outside clock.py.

    All production code must use ``endury.common.clock.today()`` and
    ``endury.common.clock.now()`` to support the devtools app simulator.
    """
    if not rel.startswith("endury/"):
        return []
    if any(rel.startswith(exempt) or rel == exempt for exempt in _CLOCK_EXEMPT_PATHS):
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        for obj_name, attr_name in _BARE_CLOCK_PATTERNS:
            if node.value.id == obj_name and node.attr == attr_name:
                violations.append(
                    Violation(
                        path=rel,
                        line=node.lineno,
                        rule="bare-clock-usage",
                        detail=(
                            f"use clock.{'today' if attr_name == 'today' else 'now'}() "
                            f"instead of {obj_name}.{attr_name}()"
                        ),
                    )
                )
    return violations


#: Files allowed to run SQL against the athlete_profile table (the
#: repository is the only gateway; the DDL and migrations create it).
_ATHLETE_SQL_ALLOWED = {
    "endury/database/sqlite/repositories/athlete.py",
    "endury/database/sqlite/session.py",
    "endury/database/sqlite/migrations.py",
}

#: SQL phrases that name the table as a table (prose mentioning the
#: update_athlete_profile TOOL must not match).
_ATHLETE_SQL_PHRASES = (
    "from athlete_profile",
    "into athlete_profile",
    "update athlete_profile",
    "table athlete_profile",
)


def _check_zone_spec_single_home(tree: ast.Module, rel: str) -> list[Violation]:
    """One canonical band table per metric.

    Every ``*_BAND_SPECS`` assignment must live in
    ``endury/training/zones.py`` — a second copy of the zone geometry is a
    silent-divergence hazard (the pre-pivot TID-cutpoint drift).
    """
    if not rel.startswith("endury/") or rel == "endury/training/zones.py":
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        violations.extend(
            Violation(
                path=rel,
                line=node.lineno,
                rule="zone-spec-single-home",
                detail=(
                    f"{target.id} assigned outside "
                    "endury/training/zones.py — the canonical band "
                    "tables live there"
                ),
            )
            for target in targets
            if isinstance(target, ast.Name) and target.id.endswith("_BAND_SPECS")
        )
    return violations


def _check_athlete_sql_gateway(tree: ast.Module, rel: str) -> list[Violation]:
    """athlete_profile SQL only through its repository.

    Flags SQL-shaped string literals touching the ``athlete_profile`` table
    outside the repository, the DDL, and migrations. Tool names like
    ``update_athlete_profile`` are not SQL and do not match.
    """
    if not rel.startswith("endury/") or rel in _ATHLETE_SQL_ALLOWED:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value.lower()
        if any(phrase in text for phrase in _ATHLETE_SQL_PHRASES):
            violations.append(
                Violation(
                    path=rel,
                    line=node.lineno,
                    rule="athlete-sql-gateway",
                    detail=(
                        "SQL against athlete_profile outside its repository "
                        "— go through AthleteProfileRepository"
                    ),
                )
            )
    return violations


#: Context-manager methods on ``DatabaseSession`` that hold the session's
#: reentrant lock for the whole block (see ad30462b).
_DB_BLOCK_METHODS = frozenset({"cursor", "transaction"})

#: Module roots whose calls are always network or process I/O.
_IO_MODULE_ROOTS = frozenset({"httpx", "requests", "urllib", "socket", "subprocess"})

#: (module, attribute) pairs that are I/O on an otherwise-harmless module.
_IO_CALL_PATTERNS: tuple[tuple[str, str], ...] = (("time", "sleep"),)

#: Builtins that touch the filesystem.
_IO_BUILTINS = frozenset({"open"})

#: IntervalsClient methods — every one is an HTTP round trip. Matched by
#: name on a receiver (``client.get_activity(...)``) and never as a bare
#: call, so the planner's pure ``get_activity(activity_key)`` registry
#: helper does not match.
_INTERVALS_CLIENT_METHODS = frozenset(
    {
        "get_athlete",
        "get_activity",
        "list_activities",
        "list_wellness",
        "list_events",
        "bulk_upsert_events",
        "bulk_delete_events",
    }
)


def _attribute_root(node: ast.Attribute) -> str | None:
    """Return the base Name of a dotted attribute chain, if it has one."""
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _io_detail(node: ast.AST) -> str | None:
    """Describe why ``node`` is I/O, or None when it is not one we detect."""
    if isinstance(node, ast.Await):
        return "await suspends while the session lock is held"
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        name = node.func.id
        return f"{name}() is filesystem I/O" if name in _IO_BUILTINS else None
    if not isinstance(node.func, ast.Attribute):
        return None

    attr = node.func.attr
    root = _attribute_root(node.func)
    if root in _IO_MODULE_ROOTS:
        return f"{root}.{attr}() is network/process I/O"
    if (root, attr) in _IO_CALL_PATTERNS:
        return f"{root}.{attr}() blocks the session lock"
    if attr in _INTERVALS_CLIENT_METHODS:
        return f"{attr}() is an intervals.icu HTTP round trip"
    return None


def _is_db_block(node: ast.With | ast.AsyncWith) -> bool:
    """Whether ``node`` is a ``with session.cursor()/transaction()`` block."""
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr in _DB_BLOCK_METHODS
        for item in node.items
    )


def _check_db_block_io(tree: ast.Module, rel: str) -> list[Violation]:
    """No I/O inside a ``cursor()``/``transaction()`` block.

    Since ad30462b those blocks hold a process-wide reentrant lock, so every
    other thread's DB work waits on whatever happens inside one. Network
    calls, file access, sleeps and awaits therefore belong outside the
    block: read/compute first, then open the block for the writes.

    Detected: ``open()``; calls rooted at ``httpx``/``requests``/``urllib``/
    ``socket``/``subprocess``; ``time.sleep()``; ``IntervalsClient`` methods
    called on a receiver; and any ``await``.

    NOT detected (deliberately — this is a syntactic scan with no type
    resolution or call-graph): I/O reached through a helper function called
    from inside the block; logging (ubiquitous and cheap enough to live
    with); ``Path``/file-object methods such as ``read_text()``; imports
    aliased away from their module name (``from httpx import get``); any
    HTTP client other than the two named modules; and anything outside
    ``endury/``.

    Args:
        tree: Parsed module AST.
        rel: Repo-relative module path.

    Returns:
        One violation per detected I/O node.
    """
    if not rel.startswith("endury/"):
        return []

    violations: list[Violation] = []
    seen: set[int] = set()
    for block in ast.walk(tree):
        if not isinstance(block, (ast.With, ast.AsyncWith)) or not _is_db_block(block):
            continue
        for statement in block.body:
            for node in ast.walk(statement):
                detail = _io_detail(node)
                if detail is None or id(node) in seen:
                    continue
                seen.add(id(node))
                violations.append(
                    Violation(
                        path=rel,
                        line=getattr(node, "lineno", 1),
                        rule="db-block-io",
                        detail=(
                            f"{detail} — do it outside the cursor()/transaction() block"
                        ),
                    )
                )
    return violations


def run_checks() -> list[Violation]:
    """Run all architecture checks and return violations."""
    violations: list[Violation] = []

    for path in _iter_python_files():
        rel = _relative(path)
        content = path.read_text(encoding="utf-8")

        # Skip vendored/generated-style caches if ever present.
        if rel.endswith("__pycache__"):
            continue

        try:
            tree = ast.parse(content, filename=rel)
        except SyntaxError as exc:
            violations.append(
                Violation(
                    path=rel,
                    line=exc.lineno or 1,
                    rule="parse-error",
                    detail=str(exc),
                )
            )
            continue

        violations.extend(_check_planner_boundaries(tree, rel))
        violations.extend(_check_bench_independence(tree, rel))
        violations.extend(_check_store_imports(tree, rel))
        violations.extend(_check_store_boundary(tree, rel))
        violations.extend(_check_no_pyodide_imports(tree, rel))
        violations.extend(_check_analysis_standalone(tree, rel))
        violations.extend(_check_service_function_budgets(tree, rel))
        violations.extend(_check_bare_clock_usage(tree, rel))
        violations.extend(_check_zone_spec_single_home(tree, rel))
        violations.extend(_check_athlete_sql_gateway(tree, rel))
        violations.extend(_check_db_block_io(tree, rel))

    return violations


def main() -> int:
    """CLI entrypoint."""
    violations = run_checks()
    if not violations:
        print("architecture checks passed")
        return 0

    print("architecture checks failed:")
    for v in sorted(violations, key=lambda item: (item.path, item.line, item.rule)):
        print(f"- {v.path}:{v.line} [{v.rule}] {v.detail}")
    print(f"total violations: {len(violations)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
