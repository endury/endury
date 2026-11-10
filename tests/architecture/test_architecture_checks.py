"""Tests for architecture fitness checks script."""

import ast

from devtools.core.check_architecture import (
    FUNCTION_BUDGET_WAIVERS,
    MAX_FUNCTION_LINES,
    REPO_ROOT,
    _check_analysis_standalone,
    _check_db_block_io,
    _check_service_function_budgets,
    _check_store_boundary,
    _check_store_imports,
    run_checks,
)


def test_architecture_checks_pass() -> None:
    """Repository should satisfy architecture checks."""
    violations = run_checks()
    assert violations == []


def _function_waiver_pointer_errors(waivers: dict[str, tuple]) -> list[str]:
    """Return one message per waiver key whose file+function do not exist."""
    errors = []
    for key in waivers:
        rel, _, fn_name = key.partition("::")
        path = REPO_ROOT / rel
        if not path.is_file():
            errors.append(f"waived path missing: {rel}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if fn_name not in names:
            errors.append(f"waived function missing: {key}")
    return errors


def test_function_budget_waivers_point_at_real_code() -> None:
    """Every function waiver names a file+function that still exist."""
    assert _function_waiver_pointer_errors(FUNCTION_BUDGET_WAIVERS) == []


def test_stale_function_waiver_fails_the_pointer_test() -> None:
    """A waiver whose function no longer exists in its file fails the check."""
    stale = {
        "endury/planning/adjust_week.py::not_a_real_function": (
            1,
            None,
            "2026-09-14",
            "synthetic stale entry for the test",
        )
    }

    assert _function_waiver_pointer_errors(stale) != []


def _make_function_source(name: str, body_lines: int) -> str:
    """Build a module source with one function of the given body length."""
    body = "\n".join(f"    x = {n}" for n in range(body_lines))
    return f"def {name}():\n{body}\n"


def test_widened_scope_catches_over_budget_function_outside_activities() -> None:
    """A new over-budget function under endury/planning/ now fails.

    Function-length/complexity budgets used to be scoped to a narrow
    nominated set of modules; they were widened to all of endury/planning/,
    endury/browser/ and endury/store/.
    """
    rel = "endury/planning/some_new_module.py"
    assert f"{rel}::over_budget" not in FUNCTION_BUDGET_WAIVERS
    source = _make_function_source("over_budget", MAX_FUNCTION_LINES + 1)

    violations = _check_service_function_budgets(ast.parse(source), rel)

    assert len(violations) == 1
    assert violations[0].rule == "function-size"


def test_waived_function_within_its_count_passes() -> None:
    """A waived function is allowed up to its recorded line/complexity count."""
    key, (waived_lines, _complexity, _date, _reason) = next(
        (k, v) for k, v in FUNCTION_BUDGET_WAIVERS.items() if v[0] is not None
    )
    rel, _, fn_name = key.partition("::")
    assert waived_lines is not None
    source = _make_function_source(fn_name, waived_lines - 1)

    violations = _check_service_function_budgets(ast.parse(source), rel)

    assert not any(v.rule == "function-size" for v in violations)


def test_waived_function_over_its_count_still_fails() -> None:
    """A waived function still fails once it grows past its waived count."""
    key, (waived_lines, _complexity, _date, _reason) = next(
        (k, v) for k, v in FUNCTION_BUDGET_WAIVERS.items() if v[0] is not None
    )
    rel, _, fn_name = key.partition("::")
    assert waived_lines is not None
    source = _make_function_source(fn_name, waived_lines + 1)

    violations = _check_service_function_budgets(ast.parse(source), rel)

    assert any(v.rule == "function-size" for v in violations)


_DB_BLOCK_IO_SOURCE = """
import httpx
import time


def save(repos, client):
    with repos.session.transaction():
        repos.plan.archive(1)
        client.bulk_upsert_events([])
        httpx.get("https://example.invalid")
        time.sleep(0.1)
        with open("notes.txt") as handle:
            handle.read()


async def save_async(repos, client):
    with repos.session.cursor() as cursor:
        cursor.execute("SELECT 1")
        await client.push()
"""

_PURE_DB_BLOCK_SOURCE = """
def save(repos, payload):
    events = json.dumps(payload)
    with repos.session.transaction():
        repos.plan.create(events)
        # bare get_activity() is the planner's pure registry lookup, not the
        # intervals client method of the same name.
        repos.plan.bulk_insert_sessions(get_activity("run_easy"))
        logger.info("saved %s", events)
"""


def test_io_inside_a_db_block_is_detected() -> None:
    """Network, filesystem, sleep and await calls inside a DB block all fail."""
    tree = ast.parse(_DB_BLOCK_IO_SOURCE)

    violations = _check_db_block_io(tree, "endury/planning/fake.py")

    assert {v.rule for v in violations} == {"db-block-io"}
    details = " ".join(v.detail for v in violations)
    assert "bulk_upsert_events()" in details
    assert "httpx.get()" in details
    assert "time.sleep()" in details
    assert "open()" in details
    assert "await" in details
    assert len(violations) == 5


def test_pure_db_block_and_non_endury_modules_are_clean() -> None:
    """Pure DB work passes, and the rule is scoped to endury/."""
    assert (
        _check_db_block_io(ast.parse(_PURE_DB_BLOCK_SOURCE), "endury/planning/fake.py")
        == []
    )
    assert _check_db_block_io(ast.parse(_DB_BLOCK_IO_SOURCE), "devtools/fake.py") == []


# ---------------------------------------------------------------------------
# Store boundaries — the store's imports, and who may import the store
# ---------------------------------------------------------------------------


def test_store_may_import_only_database_intervals_and_common() -> None:
    source = (
        "from endury.database.sqlite.session import DatabaseSession\n"
        "from endury.intervals.client import IntervalsClient\n"
        "from endury.common import clock\n"
        "from endury.store.note import encode\n"
        "import endury.planner\n"
        "from endury.coach.turn import run_turn\n"
        "from endury.planning.wire import types\n"
    )

    violations = _check_store_imports(ast.parse(source), "endury/store/snapshot.py")

    assert [v.rule for v in violations] == ["store-imports"] * 3
    assert [v.line for v in violations] == [5, 6, 7]


def test_analysis_imports_only_numpy_stdlib_and_itself() -> None:
    # The sandbox worker loads numpy and endury.analysis alone.
    source = (
        "from __future__ import annotations\n"
        "import json\n"
        "import numpy as np\n"
        "from endury.analysis.charts import make_spec\n"
        "from endury.coach.turn import run_turn\n"
        "import pydantic\n"
    )

    violations = _check_analysis_standalone(
        ast.parse(source), "endury/analysis/library.py"
    )

    assert [v.rule for v in violations] == ["analysis-standalone"] * 2
    assert [v.line for v in violations] == [5, 6]
    assert _check_analysis_standalone(ast.parse(source), "endury/coach/x.py") == []


def test_store_import_rule_is_scoped_to_the_store() -> None:
    source = "from endury.coach.turn import run_turn\n"
    assert _check_store_imports(ast.parse(source), "endury/planning/x.py") == []


def test_only_the_browser_runtime_imports_the_store() -> None:
    source = "from endury.store import Store\nimport endury.store.note\n"

    assert _check_store_boundary(ast.parse(source), "endury/browser/api.py") == []
    assert _check_store_boundary(ast.parse(source), "endury/store/inner.py") == []
    assert _check_store_boundary(ast.parse(source), "devtools/one_off.py") == []

    violations = _check_store_boundary(ast.parse(source), "endury/coach/runtime.py")
    assert [v.rule for v in violations] == ["store-boundary"] * 2


def test_store_boundary_ignores_lookalike_modules() -> None:
    source = "import endury.storefront\n"
    assert _check_store_boundary(ast.parse(source), "endury/coach/runtime.py") == []


def test_bench_must_not_import_endury() -> None:
    from devtools.core.check_architecture import _check_bench_independence

    tree = ast.parse("import json\nfrom endury.planner import pipeline\n")
    violations = _check_bench_independence(tree, "bench/readings.py")
    assert [v.rule for v in violations] == ["bench-independence"]
    assert _check_bench_independence(tree, "devtools/bench/endury_contestant.py") == []
