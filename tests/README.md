# tests

The offline suite: deterministic, free, no keys and no network. Pytest
collects from `tests/` only (`testpaths` in `pyproject.toml`); the
devtools under `devtools/` are run by hand, never collected. The fakes of
intervals.icu and of the AI model stand in for every service, so no test
reaches a real account or a paid model.

## Layout

Each folder mirrors the package it covers.

| Folder | Covers |
| --- | --- |
| `planner/` | `endury/planner`: the engine, its menus, the quality canaries. |
| `planning/` | `endury/planning`: generate, commit, replan and the editing verbs. |
| `coach/` | `endury/coach`, against a fake AI model. |
| `analysis/` | `endury/analysis`: the helpers, the runner and the charts. |
| `browser/` | `endury/browser`: every RPC method, run natively. |
| `store/` | `endury/store`: the note format, chunking, revisions, dirty triggers, and a device-A to device-B round trip. |
| `intervals/` | `endury/intervals`: the client, the workout DSL, the push. |
| `db/` | `endury/database`: the schema, the migrations, the repositories. |
| `analytics/` | `endury/domain/analysis`: zone sets and the race prediction. |
| `bench/` | `bench/`: the parser, the load model, the readings, the scoring. |
| `devtools/` | The devtools, the scorecard baseline gate and the docs checks. |
| `golden/` | Conversation replay fixtures. |
| `architecture/` | The import boundaries and the banned-reference-name scan. |
| `fixtures/` | The golden conversation payloads. |

## Running it

| Command | Runs |
| --- | --- |
| `make test` | Everything offline, with the coverage gate: `endury/planner/` at 100 %, the rest measured. |
| `make test-scorecards` | The scorecard baseline gate and the card-honesty grid, marked `slow`. |
| `make test-planner-fast` | The planner and planning tests without coverage. |
| `make test-durations` | A serial timing profile without coverage. |

`make test` is the gate before each commit or push; `make test-scorecards`
runs in the pre-push hook. The browser end-to-end run is a devtool, not
a pytest target: `make run-devtool TOOL=e2e_lifecycle` builds the site
and drives Chromium against a fake intervals.icu and a fake OpenRouter on
local ports. Pyodide's release is downloaded once and cached.

## Slicing

`make test` passes through a path, a marker expression and extra
arguments. A slice fails the planner coverage floor unless it drops
coverage with `PYTEST_COVERAGE=`.

```bash
make test PYTEST_PATH=tests/browser
make test PYTEST_PATH=tests/coach/test_turn.py PYTEST_COVERAGE=
make test PYTEST_PATH=tests/coach/test_turn.py::test_tool_loop_executes_and_continues PYTEST_ARGS='-vv'
make test PYTEST_MARKERS='coach and not slow'
make test PYTEST_COVERAGE= PYTEST_ARGS='-k planner -vv'
```

## Markers

`tests/conftest.py` applies markers from each test's path and filename;
`poetry run pytest --markers` lists them.

| Kind | Markers |
| --- | --- |
| Layer | `unit`, `integration` |
| Domain | `coach`, `planning`, `db`, `intervals`, `analytics`, `architecture`, `golden` |
| Heuristic | `contract`, `llm`, `golden` |
| Runtime | `slow`, applied by hand and excluded from `make test` |

## Fixtures and doubles

| Where | What |
| --- | --- |
| `conftest.py` | The shared database and container fixtures. |
| `coach/conftest.py` | The coach's repositories, tools and fake-model fixtures. |
| `browser/conftest.py` | A pinned clock, cleared keys, an unbooted runtime and a `FakeIntervals` account per test; `browser/helpers.py` boots it. |
| `doubles.py` | `FakeLLM`, `CapturingLLM` and the other reusable doubles. |
| `endury/intervals/fake.py` | `FakeIntervals`, an in-memory intervals.icu at the HTTP level: `fake.client()` drives a real `IntervalsClient` over an httpx mock transport. The E2E harness and the demo athlete use it too. |

Keep a new test in the folder that mirrors the module under test, and
prefer a deterministic fixture over anything live.
