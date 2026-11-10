# AGENTS.md

Endury is a free, open-source endurance coach: a deterministic planner, an intervals.icu adapter, a static web UI and an optional chat coach.
There is no server: the Python app in `endury/` runs unchanged in a Pyodide Web Worker in the athlete's browser tab, and natively under pytest.

Read [README.md](README.md) for what the product does and how to host it,
[docs/privacy.md](docs/privacy.md) for what it stores where, and
[docs/architecture.md](docs/architecture.md) for how the parts fit. Change a rule
at the owner named beside it instead of adding a competing one here.

## Working agreement

- **Simplicity.** Write the minimum code that solves the problem: no speculative features, no single-use abstractions, no handling for impossible cases. If 200 lines could be 50, rewrite them. When a simpler approach exists, say so and push back.
- **Surgical changes.** Touch only what the task needs and match the surrounding style: no drive-by refactors, reformatting or comment edits. Remove what your change orphaned; mention pre-existing dead code, never delete it unasked. Every changed line traces to the request.
- **Verify, don't assert.** Bug fix: write the failing test first, then make it pass. Refactor: green before and after. Run only the checks your change could affect, not the whole ladder: the focused slice for the touched area (`make test PYTEST_PATH=… PYTEST_COVERAGE=`), `make lint`, and for a planner change the fingerprints or the scorecard matrix. Skip the full `make test`, `make test-scorecards`, `make site` and the E2E run unless the change reaches what they check; they take minutes. Say plainly if a check fails, and name every gate you did not run.
- Run Python only through Poetry: `make` targets or `poetry run …`. Never call a bare `python`, `pytest`, `ruff`, `mypy` or `pip`.
- You need no keys. The test targets clear provider keys, and `tests/doubles.py` fakes intervals.icu and the AI model behind the chat; never add a test that reaches a real service or a paid AI model.
- Coaching knowledge is data: change the YAML menus and bands before the Python that reads them.
- Commit only when asked. Never rewrite git history: no rebase, amend, squash or force-push.
- Keep keys, `.env` files and personal training data out of commits, fixtures and logs. Reproduce a bug with the demo or synthetic data; a real athlete's key, chat or account data goes into an AI session or a replay only with their prior written consent.
- Endury stays non-commercial: no donations, sponsors, ads or affiliate links. Its position on product liability, the Cyber Resilience Act, trademarks and the provider's duties rests on that.
- Docs have two readers. This file, the planner specs in `docs/specs/planner/` and the rulebook are for agents and tests: dense, absolute, exhaustive. The README, `VISION.md`, `CONTRIBUTING.md`, `docs/architecture.md`, the package READMEs and the CHANGELOG are for people: each names its reader in its first lines, holds one idea per paragraph, and keeps a table cell to one short sentence.
- Update a human page with behaviour by rewriting the paragraph that changed, never by appending a dated note; the CHANGELOG and git hold the history. A package README is a paragraph on what the package is for, a module table and where to start reading. "Never", "only" and "the one place" are how this file instructs an agent; a human page states the behaviour and its reason instead. A number stays in prose only where the reader acts on it, and lives in one page. `devtools/core/check_docs.py` holds the ceilings and runs in `make lint`. Delete superseded docs.


## Plan quality

The planner exists to give athletes good training plans. Scorecards, the plan benchmark and baselines are instruments: judge a planner change by the plan the athlete gets, not by whether a gate turns green. A score that improves through a distortion is not an improvement; say so. Measure a number against the research before changing it, and when the benchmark and the research disagree, explain what each means for the athlete instead of chasing the rule. The behaviour lives in [behavioral.md](docs/specs/planner/behavioral.md) and the layer specs; check a change against:

- **Progression.** The first long session opens near the athlete's recent longest, not at the menu's start; long sessions grow about 10 % a week (running strictly), with no jump into a phase.
- **Recovery.** Recovery weeks keep the athlete training (not below about half the load week), and never sit directly before a race week.
- **Intensity.** No hard sessions of one sport on consecutive days (the rulebook's `hard_day_spacing`); a hard day in another sport may follow. Read intensity by time in zone, not by session labels.
- **Tune-up races.** A B or C race costs its own week, the A plan keeps its place in the phase, no hard run or ride follows in the next four days, and a long B race gets one easier week.
- **Taper.** Volume falls, intensity holds: keep the quality sessions and shorten the easy and long ones; arrive at the target race-day freshness (TSB / CTL).
- **Honest cards.** A card is the session the plan sized (length, metres, pace), and a session labelled threshold or intervals holds the work its label promises. A menu's units are the card's: the minutes or metres a menu books are what the athlete's card shows at their own thresholds, and every limit on a session (floor, ceiling, rounding, TSS window) is set in that unit.

## Invariants

Rules no single file reveals. Each names what enforces it or who owns it.

**Planning**

- `endury/planner/` is the only planning engine; no AI language model makes or changes a plan ([docs/specs/planner/](docs/specs/planner/)).
- The planner never imports `endury.domain`; shared primitives live in `endury/training/` (`planner-boundary`).
- The planner is deterministic and bit-identical in Pyodide: a change not meant to alter plans shows 0 differences in `planner_fingerprints` (with `--all`: every scenario) and `planner_menu_canon` against a git rev.
- A new plan is built only for a tier its menu offers (`offered:` in the menu YAML, read through `endury/planning/offered_plans.py`); a stored plan replans and a tune-up race keeps its type whatever is offered. The scorecard matrix, the card-honesty grid, the fingerprints, the maturity audit and the plan viewer's default cover the plans on offer, so a plan that is not offered holds no slow gate. Put a plan back on offer in its menu, after its review ([plan maturity](docs/plan_maturity.md)).
- `endury/planner/` (engine and menus) holds 100 % coverage (`[tool.coverage.report]` in `pyproject.toml`, enforced by `make test`).
- No scorecard composite may rise (`make test-scorecards`). Raising one is a reviewed diff to `devtools/quality/baselines/planner_matrix.json` in the same commit; never widen the tolerance.
- The rulebook in `bench/rulebook.yaml` judges plans: the scorecard gates Endury's plans on every rule in it, and the benchmark asks its `benchmark` rules of AI language models too. The planner never reads it.
- Booked → card has one conversion: the session generator sizes every bound through the card builder (`assignment_target_tss`, `activity.build`), a swim at the athlete's CSS, never through a second formula (R-SESS-011; `tests/planner/test_card_honesty.py` holds minutes and swim metres to the menu).
- One reading per intensity question (R-ACT-028): easy / middle / hard time is the planner's `tid_fractions` on each sport's own cut points (`TID_CUTS`), a hard day is the rulebook's, a card's zone number is the athlete's own zone (Endury's band spec without one), and a run's pace and heart-rate zones are one rung of the same ladder.

**Runtime**

- Nothing under `endury/` imports `js` or `pyodide`; only `web/worker/py_worker.js` knows it runs in Pyodide (`no-pyodide-import`).
- Nothing under `bench/` imports `endury`; Endury enters the benchmark through `devtools/bench/` (`bench-independence`).
- A runtime dependency must load in Pyodide: the worker installs a fixed package list (`PACKAGES` in `web/worker/py_worker.js`). That file is the one home of the list and of the Pyodide version: `make site` reads both from it, and `web/sw.js` repeats the version for its cache, so a bump changes both files.
- Boot loads neither the QP solver nor the AI SDK: cvxpy (with scipy) is imported inside the first solve and the openai SDK on the coach's first call, so a phone's cold start shows the calendar without them (`tests/architecture/test_lazy_imports.py`).
- The page loads nothing from a third party. `make site` stages Pyodide and the worker's packages into `site/pyodide/` (each wheel checked against Pyodide's lock), and the browser talks only to its own origin, intervals.icu and OpenRouter. On Cloudflare the Content Security Policy in `web/_headers` enforces it: a new host is a line there.
- The site is built from the files git knows about (tracked, or untracked and not ignored), so an ignored file never ships (`devtools/site/build_site.py`).
- Read time through the injectable clock in `endury/common/clock.py`, never `datetime.now()` (`bare-clock-usage`).
- No streaming anywhere: chat, generate and replan are single calls. The planner's progress frames are the one thing posted mid-call (`PROGRESS_METHODS` in `endury/browser/rpc.py`).
- The coach's analysis scripts run only in the sandbox worker (`web/worker/analysis_worker.js`: numpy and `endury/analysis/`, no key, its network removed once loaded and the Content Security Policy completing the seal), relayed by the page while the chat call waits; `run_local` runs them in-process for tests and devtools only. `endury/analysis/` imports numpy, the standard library and itself (`analysis-standalone`), and the sandbox reads the Pyodide version from `version.json`, so `py_worker.js` stays its one home. GPS reaches the sandbox only as metres from a point the run's files share (`_relative_positions` in `endury/coach/tools/analysis_tools.py`), never as coordinates.
- One AI model serves every coach turn (`CoachConfig.model` in `endury/wiring/config.py`); there are no model tiers.
- Every path in `web/` is relative, so the site works under a sub-path. The worker's `new URL('../../version.json', self.location)` and `new URL('../../pyodide/…', self.location)` are correct because it is served from `site/<build-id>/worker/`; do not "fix" them.

**State**

- intervals.icu is the only durable store; the worker's SQLite database is an in-memory cache restored from the store note at boot ([endury/store/README.md](endury/store/README.md)).
- Training history, thresholds and wellness are read live from intervals.icu, never mirrored. Chat history and the coach's activity takes never reach the store note: the last 50 messages live in their own `coachie:chat` note and the takes in `coachie:takes`, both outside the store's revision ([endury/store/README.md](endury/store/README.md)).
- The store note is a snapshot of the durable tables, never a rebuild from the plan request: a rebuild would change stored plans whenever a menu changes.
- A plan write never touches an intervals.icu event dated before today: replacing, archiving or deleting a plan clears its sessions and races from today on, so a finished block keeps its workouts and their paired activities (`superseded_plan_events` in `endury/planning/commit_plan.py`). The calendar shows a former plan's finished sessions from those events, never from the store, which drops archived plans (`calendar_only` in `endury/planning/calendar_projection.py`).
- Only `endury/browser/` imports `endury.store`, and the store imports only `endury.{database,intervals,common}` (`store-boundary`, `store-imports`).
- The legacy `coachie` identifiers on the wire find every athlete's existing data; never rename them: the external ids `coachie:{plan_id}:…`, `coachie:race:…`, `coachie:adhoc:…`, `coachie:store`, `coachie:chat` and `coachie:takes`, the ```` ```coachie-state ```` fence. The event tag is `endury` (`EVENT_TAG` in `endury/intervals/push.py`); nothing reads it, so events pushed before 2026-10-21 keep `coachie` until their next push.
- No I/O inside a database block (`db-block-io`).

**Coach**

- Plan-changing writes go through Apply cards, and so does cancelling or re-dating a race: a tool with a `confirm` parameter only previews inside a turn, and `apply_proposal` is the one place `confirm=True` is passed. `set_session_status`, `move_planned_session`, `schedule_workout`, `upsert_race`, `update_memory`, `update_athlete_profile` and `record_self_assessment` write at once by design ([endury/coach/proposals.py](endury/coach/proposals.py)).
- Athlete-facing text (menu `why:` lines, plan briefs, tool replies the coach relays) is self-contained coaching language: no citations, section references, author names or dev-speak. Engineering reasoning goes in an `# intent:` comment beside it, research citations in YAML comments.
- Endury is training software, not a medical product. No athlete-facing text claims to prevent, detect or treat an injury or illness, and the prompt keeps its health boundaries: no diagnosis, a doctor's clearance after illness or injury, emergencies, a mental-health crisis, under-fuelling, athletes under 18 (`endury/coach/prompt.py`, pinned by `tests/coach/test_prompt.py`). It does not ask about pregnancy or other health conditions unprompted.
- The coach says it is an AI: "AI coach" in the chat header of both shells, "an AI model" in its welcome line (`CoachConfig.welcome_message`), when sincerely asked (`endury/coach/prompt.py`), and "AI" on saved takes and briefs, which also carry `generated_by` and `data-ai-generated`.
- A chart is a spec the page draws (`endury/analysis/charts.py`, checked again by `take_charts`), never markup from the model. It is stored with its reply as `%%CHART%%` lines, and the model rereads it as its title only (`charts_for_model`). A turn that ran `run_analysis` writes its reply in a round without tools when the model says DONE or drafted beside a tool call (`endury/coach/turn.py`).
- Nothing goes to OpenRouter before the athlete agrees: Settings refuses an OpenRouter key without the chat consent (`chat_consent` in `web/data/settings.js`), and every call asks for providers that neither store nor train on it (`CoachConfig.openrouter_data_collection`).

**Privacy and terms**

- What leaves the browser is told in three places that change together: [docs/privacy.md](docs/privacy.md), the hosted site's `web/privacy.html`, and the consent text in `web/components/settings_panel.js`.
- `web/impressum.html` and `web/privacy.html` name the person who hosts the site, but only in `site/`: `make site` fills their marked places from `~/.config/endury/legal.json`, never commit a host's details, and `make deploy` refuses blank pages ([README.md](README.md)).
- Garmin-sourced activity data shown to the athlete is credited to Garmin, as the intervals.icu API terms require (`garminLabel` in `web/data/adapters.js` on sheets and agenda cards, `GARMIN_NOTE` on mixed figures, [intervals_api.md](docs/reference/intervals_api.md)).

**Structure**

- Functions in `endury/planning/`, `endury/browser/` and `endury/store/` stay within 120 lines and complexity 15; the dated exceptions in `devtools/core/check_architecture.py` may shrink, never grow.
- One home per fact: zone bands in `endury/training/zones.py` (`zone-spec-single-home`), `athlete_profile` SQL in its repository (`athlete-sql-gateway`), race data in the `athlete_goals` table.
- Google-style docstrings on production code and devtools (ruff's `D` rules in `pyproject.toml`).

## Commands

| Command | Use |
| --- | --- |
| `make install` | Install every dependency with Poetry, dev tools included. |
| `make install-hooks` | Enable `.githooks/pre-push` (`make lint`, `make test`, `make test-scorecards`, then `make site`). Per clone: `git config --get core.hooksPath` reads `.githooks` when it is on. |
| `make test` | The offline gate: free, no keys, no network, with the planner coverage floor. |
| `make test-scorecards` | The scorecard baseline and the card-honesty grid (`slow`, excluded from `make test`). |
| `make test-planner-fast` | Planner and planning tests without coverage. |
| `make lint` | `ruff check --fix`, `ruff format --check`, mypy, `check_architecture`, `check_docs` and `make docs`. |
| `make format` | `ruff format`. |
| `make docs` | Regenerate the generated docs from their committed sources: the rule page `docs/quality/rules.md` and the benchmark figure. Never edit those by hand. |
| `make site` / `make serve` | Build the static site into `site/` / serve it at <http://localhost:8080>. Rebuild after editing `web/` or `endury/`. The first build downloads Pyodide into `.pyodide/`. |
| `make deploy` | Build the site and upload it to Cloudflare (`wrangler.jsonc`). Only when asked. |
| `make run-devtool TOOL=<name> [ARGS='…']` | Run a registered devtool. Without `TOOL=` it prints the registry (`DEVTOOLS_ALL` in the `Makefile`); `make help` prints examples. |

A focused slice fails the 100 % planner floor unless it drops coverage with `PYTEST_COVERAGE=`:

```bash
make test PYTEST_PATH=tests/coach/test_session.py PYTEST_COVERAGE= PYTEST_ARGS='-vv'
make test PYTEST_PATH=tests/browser PYTEST_COVERAGE= PYTEST_ARGS='-k chat'
make test PYTEST_COVERAGE= PYTEST_MARKERS='coach and not slow'
```

- Planner output unchanged: `make run-devtool TOOL=planner_fingerprints ARGS='--rev main'` (the plans on offer; `--all` for every scenario), then `TOOL=planner_menu_canon` (offline, seconds). Menu and band edits run the full validation ladder in `CONTRIBUTING.md`.
- Look at whole plans: `TOOL=planner_sweep_site [ARGS='--goals ironman']` writes the plans on offer (or the goals and tiers named) into one interactive HTML page: totals, weekly hours by sport and load, and any week's sessions day by day with their workout cards; athletes and horizons are set in its configuration section; `TOOL=bench_plan_view ARGS='--scenarios ironman_32wk'` draws Endury's plan beside every saved AI model's answer, graded by the bench rulebook. Both write to the temp dir; judge a planner change by these plans, not only by the gates.
- Frontend changes: `TOOL=static_adapters_smoke` (Node, no browser) and `TOOL=e2e_lifecycle` (Chromium through Playwright against local fakes; the site build downloads Pyodide once into `.pyodide/`, and `poetry run playwright install chromium` runs once).
- Response headers: `make serve` sends none. `npx wrangler dev` serves `site/` with `web/_headers` applied; check a Content Security Policy change there, with the browser console open.

## Where things live

| Path | Owns | Guide |
| --- | --- | --- |
| `endury/planner/` | The planning engine: phases, session menus, QP sizing, calendar days, the activity library | [README](endury/planner/README.md), [docs/specs/planner/](docs/specs/planner/) |
| `endury/planner/sessions/menus/` | One YAML blueprint per goal: every session, day, duration bound and `why` | [AUTHORING.md](endury/planner/sessions/menus/AUTHORING.md) |
| `endury/planning/` | Generate, commit, push, replan and edit the stored plan; the plan-editing verbs | [README](endury/planning/README.md) |
| `endury/coach/` | The chat coach: turn loop, tools, Apply cards, prompt, memory document | [README](endury/coach/README.md) |
| `endury/analysis/` | The `ana` library the coach's analysis scripts call, their runner and the chart spec | [README](endury/analysis/README.md) |
| `endury/browser/` | The worker's entry point: `rpc.dispatch`, `METHODS`, the envelope and its error codes | [README](endury/browser/README.md) |
| `endury/store/` | The durable tables ↔ the intervals.icu store note | [README](endury/store/README.md) |
| `endury/intervals/` | The intervals.icu client, workout DSL, calendar push, fitness | [README](endury/intervals/README.md), [intervals_api.md](docs/reference/intervals_api.md) |
| `endury/database/` | The SQLite schema, its versions and the repositories | [README](endury/database/README.md) |
| `endury/{domain,training,common,wiring}/` | Shared models, the training-science kernel, the clock, config and container | each package's `README.md` |
| `web/` | The SPA (plain ES modules, no bundler), the Pyodide worker, the hosted site's legal pages and response headers | [web/README.md](web/README.md) |
| `devtools/quality/` | The plan-quality scorecard and its committed baseline; its rules and bands are the rulebook in `bench/` | [README](devtools/quality/README.md), [rulebook](bench/rulebook.yaml) |
| `bench/`, `devtools/bench/` | The rulebook (every rule and band a plan is graded on, with its evidence) and the plan benchmark: plans with steps graded on the rulebook's `benchmark` rules; Endury is one contestant, adapted under `devtools/bench/` | [README](bench/README.md), [dossiers](docs/research/bench_dossiers/README.md) |
| `devtools/{planner,frontend,site,core}/` | Planner reports and oracles, the E2E harness and fakes, the site build, the architecture and docs checks | `Makefile` registry, [docs/architecture.md](docs/architecture.md) |
| `tests/` | The offline suite; `tests/<area>` mirrors `endury/<package>` | [tests/README.md](tests/README.md) |
| `docs/quality/` | What checks a plan, what each check rests on and what a human has reviewed; the generated rule page | [README](docs/quality/README.md) |
| `docs/` | Architecture, planner specs, API reference, training research | [docs/README.md](docs/README.md) |

## Read when relevant

Read the matching guide in full before changing its area.

- **Architecture walkthrough:** [docs/architecture.md](docs/architecture.md).
- **Planner menus:** [AUTHORING.md](endury/planner/sessions/menus/AUTHORING.md), then the validation ladder in [CONTRIBUTING.md](CONTRIBUTING.md).
- **Planner layer behaviour:** [docs/specs/planner/](docs/specs/planner/), the per-layer contracts plus `behavioral.md`; check each spec's known violations before "fixing" one.
- **What checks a plan, and what a human has reviewed:** [docs/quality/README.md](docs/quality/README.md); every rule by race goal in [docs/quality/rules.md](docs/quality/rules.md).
- **Scorecard rules, bands and baselines:** [bench/README.md](bench/README.md) (the rulebook), [devtools/quality/planner/README.md](devtools/quality/planner/README.md), [devtools/quality/baselines/README.md](devtools/quality/baselines/README.md).
- **intervals.icu API, workout DSL, external ids:** [docs/reference/intervals_api.md](docs/reference/intervals_api.md).
- **Store note protocol:** [endury/store/README.md](endury/store/README.md).
- **RPC methods and error codes:** `endury/browser/rpc.py` (`METHODS` is authoritative).
- **Coach tools and Apply cards:** [endury/coach/README.md](endury/coach/README.md).
- **Frontend layout:** [web/README.md](web/README.md).
- **Training science:** [docs/research/](docs/research/); cite it in code and YAML comments, never in athlete-facing text.
- **Contributor path, gates and pull requests:** [CONTRIBUTING.md](CONTRIBUTING.md).
- **Test taxonomy, markers and slicing:** [tests/README.md](tests/README.md).
- **Product and hosting:** [README.md](README.md); **privacy:** [docs/privacy.md](docs/privacy.md); **the hosted site's notices:** `web/privacy.html` and `web/impressum.html`.
