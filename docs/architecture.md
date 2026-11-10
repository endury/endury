# Architecture

For someone about to change the code. The [README](../README.md) says
what Endury does and how to host it; this page says how it is put
together and where each part lives. The detail is in the code and in the
package READMEs this page points at, and the rules a change must keep
are in [AGENTS.md](../AGENTS.md).

## The life of one plan

An athlete names a race in the wizard, or asks the coach for a plan. The
request, with the thresholds, fitness and recent volume read live from
intervals.icu, goes to the planner in `endury/planner/`, a pure function
from a request to a plan. It works in four layers. The phase planner lays
out the weeks: base, build, peak, taper and race, with a recovery week
every third or fourth. The session generator fills each week from the
goal's menu, a YAML template that says which session falls on which day,
how long it may be and why. A quadratic program sizes every session
within those bounds so that load ramps smoothly and the athlete arrives
fresh. The day assigner turns menu weekdays into dates and moves sessions
out of the way of a race that is not on a Sunday.

The planning service in `endury/planning/` turns that plan into the one
the athlete has. It builds each session's workout card from its booked
hours at the athlete's own thresholds, stores the plan in the worker's
SQLite database, and pushes every session to the intervals.icu calendar
as a structured workout under an external id of Endury's own. The
athlete's watch picks it up from there.

The athlete trains. Their device uploads the activity to intervals.icu,
which pairs it with the planned workout; where it does not, Endury pairs
the one activity of that sport that fits the day, unless the activity is
named after another planned workout. A session done on another day is
linked from the activity's sheet, which moves it onto that day. The
calendar and the debrief read the activity live, next to what was
planned. No training data is copied anywhere.

When life intervenes, the plan is regenerated from next Monday on the
plan's own timeline, so test weeks and session rotations keep their
place; the current week changes only when the athlete asks for it. The
chat coach can do the same through the planner's verbs, but a change that
rewrites the plan is only previewed in the chat. The athlete applies it
with a tap, and that apply is the one place a write is confirmed.

## Where it runs

There is no server. The Python package runs unchanged in two places: in a
Pyodide Web Worker inside the athlete's browser tab, and natively under
pytest. The page is a static site that any host serves.

```
web/data/api.js  (the exports the shells call)
  ──postMessage──▶ web/worker/py_worker.js   (Pyodide, one call at a time)
                     endury.browser.rpc.dispatch(method, params)
                       ├ store revision check (writes only)
                       ├ endury.browser.api.<method>(app, **params) → planning / coach / repositories
                       │    ├ IntervalsClient (httpx)       ⇄ intervals.icu
                       │    └ OpenRouterClient (openai SDK) ⇄ OpenRouter
                       └ save the store note when the database is dirty
  ◀── envelope {ok, result | error, error_code, unsaved, read_only}
```

Three consequences shape the code:

- **Nothing streams.** Chat, generate and replan are single calls, and
  the UI shows a spinner. The planner's progress frames are the one thing
  that arrives mid-call.
- **One writer per browser.** The page takes a `navigator.locks` writer
  lock; a second tab boots read-only and shows a banner. Every write first
  checks that no other device saved since this one loaded.
- **Only the worker knows it is in a browser.** Nothing under `endury/`
  imports `js` or `pyodide`. The worker installs a fixed package list, so
  a dependency outside Pyodide's distribution cannot be used.
- **The coach's own code runs apart.** For questions no fixed tool
  answers, the coach's AI model writes a short analysis script. It runs in
  a second worker, `web/worker/analysis_worker.js`, which loads numpy and
  `endury/analysis/` only, holds no key and removes its own network once
  loaded. The Python worker hands it each script through the page while
  the chat call waits, and the charts the script draws come back with the
  reply.

The RPC methods, their error codes and the boot sequence are in
[endury/browser/README.md](../endury/browser/README.md).

## Where state lives

intervals.icu is the only durable store. Training data is read from it
live and never mirrored; Endury's own state is a snapshot of its database
tables in a hidden note on the athlete's calendar.

| State | Home |
| --- | --- |
| Activities, thresholds, wellness | intervals.icu, read live on every use |
| Plan sessions, races, coach bookings | intervals.icu calendar events, under `coachie:` external ids |
| Plans, races, profile, memory, self-assessment | One hidden note, `coachie:store`: a snapshot of the durable tables |
| The last 50 chat messages | A second hidden note, `coachie:chat`, outside the store's revision |
| The coach's takes and briefs | A third hidden note, `coachie:takes` |
| The SQLite database | The worker, in memory: a cache restored from the store note at boot |
| Keys and the model choice | `localStorage`, sent only to their own host |
| The theme | `localStorage`, never sent |

The identifiers carry the project's former name, `coachie`. They are how
every existing athlete's data is found, so they never change. The store
protocol is in [endury/store/README.md](../endury/store/README.md), the
external id scheme in [reference/intervals_api.md](reference/intervals_api.md).

## Boundaries

A few rules give the packages their shape. `make lint` enforces them
through `devtools/core/check_architecture.py`, and AGENTS.md lists each
with what enforces it.

- The planner is pure: it takes a request and returns a plan, does no
  I/O, and never imports `endury.domain`. What it shares with the rest
  lives in `endury/training/`.
- Only `endury/browser/` imports the store, and the store imports only
  the database, the intervals adapter and `endury.common`.
- Time comes from the injectable clock in `endury/common/clock.py`.
- One home per fact: zone bands in `endury/training/zones.py`, race data
  in the `athlete_goals` table, athlete-profile SQL in its repository.
- Functions in `endury/planning/`, `endury/browser/` and `endury/store/`
  stay under 120 lines and complexity 15.

## Codemap

Each package README opens with what the package is for and ends with
where to start reading.

| Package | Owns | Start at |
| --- | --- | --- |
| [endury/planner/](../endury/planner/README.md) | The planning engine and the activity library that builds the workout cards | `pipeline.generate_plan` |
| [endury/planning/](../endury/planning/README.md) | The stored plan: generate, commit, push, replan, the editing verbs, the calendar and debrief views | `generate_plan.run_generate` |
| [endury/coach/](../endury/coach/README.md) | The chat coach: turn loop, tools, Apply cards, prompt, memory | `turn.run_turn` |
| [endury/analysis/](../endury/analysis/README.md) | The library the coach's analysis scripts call, their runner and their charts | `library.overview` |
| [endury/browser/](../endury/browser/README.md) | The worker's entry point: boot, dispatch, one method per `api.js` export | `rpc.dispatch` |
| [endury/store/](../endury/store/README.md) | The durable tables to and from the store note | `sync.load` |
| [endury/intervals/](../endury/intervals/README.md) | The intervals.icu client, the workout DSL, calendar push, fitness | `client.IntervalsClient` |
| [endury/database/](../endury/database/README.md) | The SQLite schema, migrations and repositories | `sqlite/session.py` |
| [endury/domain/](../endury/domain/README.md) | Shared models: athlete, memory, races, zones | `models/` |
| [endury/training/](../endury/training/README.md) | The training-science kernel: load math, zone bands, the workout schema | `load.py` |
| [endury/common/](../endury/common/README.md), [endury/wiring/](../endury/wiring/README.md) | The clock and the exceptions; `CoachConfig` and the repository container | `clock.py`, `config.py` |
| [web/](../web/README.md) | The single-page app, the worker, the service worker, the legal pages | `main.js` |
| `devtools/` | The site build, the planner reports, the scorecard, the E2E harness, the architecture checks | the `Makefile` registry |
| [bench/](../bench/README.md) | The rulebook and the plan benchmark; imports nothing from `endury` | `rulebook.yaml` |

## The planner

`generate_plan(PlanRequest) -> PlanResult` in `endury/planner/pipeline.py`
runs the four layers, `phase/`, `sessions/`, `macro/` and `days/`, with
the fitness tests placed between the sessions and the sizing. The
pipeline is deterministic and bit-identical in Pyodide, which is what
lets the fingerprints pin its output.

- **Goals.** Road running from 5K to marathon, trail 20K, 50K and 100K,
  triathlon from sprint to full distance, and a maintenance block per sport that
  converges on a volume instead of building to a race and can hold a tune-up
  race. A new plan is built only for a tier its menu offers.
- **Ambition.** Basic, moderate and competitive set the ramp, the
  recovery cadence and the session bounds; race-day freshness is one
  TSB/CTL ratio for all three.
- **Fitness.** A per-sport Banister model prices every session in TSS by
  its own workout card, so a card is the session the plan sized.
- **Replans** continue the plan's own timeline: the weeks already trained
  are rebuilt and cut, so the remaining weeks keep their test weeks and
  rotations.
- **Menus** are one YAML file per goal under `sessions/menus/`, loaded and
  linted on import. How to edit them:
  [AUTHORING.md](../endury/planner/sessions/menus/AUTHORING.md).
- **Contracts.** [docs/specs/planner/](specs/planner/) holds one contract
  per layer as numbered rules, written for the agents and the tests.

## The planning service

`endury/planning/` wraps the engine around the one stored plan. The
athlete's state comes from measured intervals.icu history first, a stored
self-assessment second and the goal's default last; a goal sport with no
measurement is refused until the athlete answers the questionnaire. A
commit stores the plan and pushes it. A failed push never rolls back the
commit, and the next push retries what is missing. The editing verbs
split the calendar between them: the current week through Sunday,
everything from next Monday, one session, one extra workout. Their reach
and guards are in [endury/planning/README.md](../endury/planning/README.md).

## The coach

The coach is a plain async tool loop in `endury/coach/turn.py`: build the
system context, then one completion per round with tools until the model
stops calling them. Every message is an OpenAI chat-completions dict, and
one model serves every turn through OpenRouter.

- **Context.** Each turn carries the profile, the memory document, a
  wellness snapshot, today's fitness, fatigue and form with the date they
  hold for, the plan brief, today's and tomorrow's sessions, the
  races and a table of day names, so the coach never works out a date.
- **Apply cards.** A tool with a `confirm` parameter only previews inside
  a turn, and the preview becomes a card beside the reply.
  `apply_proposal` re-runs it with the arguments the athlete saw, or
  re-previews when the plan changed since.
- **Memory.** One markdown document, rewritten in full by the coach and
  editable by the athlete. Every constraint in it carries a review date,
  after which the coach asks instead of advising from it.
- **Chat history** lives in its own note, outside the store's revision,
  so chatting never conflicts with a write from another device.

The module map and the OpenRouter settings are in
[endury/coach/README.md](../endury/coach/README.md).

## Build and hosting

`make site` copies `web/` into `site/<build-id>/`, zips the Python
package into a hashed bundle named by `site/version.json`, and stages
Pyodide and the worker's packages into `site/pyodide/` from Pyodide's
release, each wheel checked against its lock. The page therefore loads
nothing from a third party, and the Content Security Policy in
`web/_headers` allows this origin, intervals.icu and OpenRouter alone.
The service worker caches the pinned runtime and the hashed bundle
cache-first and the site's own files network-first, and a worker served
from a stale page refuses a newer bundle and asks for a reload. Every
path is relative, so the site works under a sub-path.

## Checks

`make test` is the offline gate: the planner, the coach and the store run
natively against in-memory fakes of intervals.icu and the AI model, with
`endury/planner/` held at 100 % coverage. `make lint` runs ruff, mypy,
the architecture checks, the docs check and the generated docs. `make test-scorecards`
holds the plans on offer to the rulebook's bands. The fingerprints pin
the planner's output across a change that should not move it, and the
browser E2E run drives the built site through Chromium against local
fakes. What each check catches, and what a human reviews, is in
[docs/quality](quality/README.md); the test layout and markers in
[tests/README.md](../tests/README.md); the commands in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Documentation conventions

- Each page names its reader in its first lines. The writing rules are in
  AGENTS.md.
- A package README opens with what the package is for and ends with where
  to start reading.
- Contracts a single package owns live under `docs/specs/<package>/` as
  numbered rules with a known-violations section, written for the agents
  and the tests. Cross-cutting contracts ship as `check_architecture`
  rules plus tests, not prose.
- Sport science lives in [docs/research/](research/), cited from code
  and menu YAML comments, never from athlete-facing text. Verified
  external facts live in [docs/reference/](reference/).
- Docstrings are Google style, enforced by ruff.
- A superseded page is deleted, not archived. Git keeps it.
