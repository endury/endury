# Contributing to Endury

For a first change. Endury is a small codebase with firm conventions and
mechanical guardrails, and this page is the short path through them.
[docs/architecture.md](docs/architecture.md) says how the parts fit,
[AGENTS.md](AGENTS.md) holds the rules a change must keep, and every
package under `endury/` has a README that says where to start reading.

## Coding agents

Endury is made with coding agents, AI language models that write and
test software. They write the code, the tests, the session menus and the
docs, from the training research in `docs/research/` and under the rules
in [AGENTS.md](AGENTS.md). The maintainer decides what to build and what
a good plan looks like, reads every plan on offer session by session,
reviews what ships and answers for it. The gates below are what make
that workable: a change passes the tests, the lint and the plan scorecard
before it lands, whoever wrote it.

Pull requests written with an agent are welcome and are reviewed like any
other. Point the agent at AGENTS.md, run the gates, and make sure you
understand what the change does and can say what it changes for the
athlete: you answer for what you submit. Naming the agent in the
pull request is a courtesy, not a requirement.

## Setup

```bash
make install         # Poetry installs everything, dev tools included
make test            # the offline suite: free, no keys, no network
make lint            # ruff, mypy, the architecture checks, the generated docs
```

- Run Python only through Poetry: the `make` targets or `poetry run …`,
  never a bare `python`, `pytest`, `ruff`, `mypy` or `pip`.
- You need no keys. The tests clear provider keys and run the planner,
  the coach and the store against in-memory fakes of intervals.icu and
  the AI model (`tests/doubles.py`). Only the native tools that call an
  AI model read a `.env`; copy `.env.example` if you need one.
- To try a change in a browser: `make site && make serve`, then open
  <http://localhost:8080> with your own intervals.icu key. Rebuild after
  every edit to `web/` or `endury/`.
- `make install-hooks` turns on the pre-push hook, which runs `make lint`,
  `make test`, `make test-scorecards` and `make site`. Without it, run
  them by hand.

The tracked `.claude/` folder holds a session hook for Claude Code's
remote environments; other editors can ignore it.

## Where your change goes

Most of Endury's coaching knowledge is data, not code.

| To change… | Edit | Read first |
| --- | --- | --- |
| Which sessions a plan prescribes, on which days, and how long | `endury/planner/sessions/menus/<goal>.yaml` | [AUTHORING.md](endury/planner/sessions/menus/AUTHORING.md) |
| The "why" an athlete reads on a session card | that slot's `why:` in the same file | AUTHORING.md, "Rationale policy" |
| What a good plan looks like: the rules and their target bands | `bench/rulebook.yaml` | [bench/README.md](bench/README.md), [the scorecard](devtools/quality/planner/README.md) |
| How a workout is built: warm-up, reps, targets | `endury/planner/activity/catalog/{run,bike,swim}.py` | `docs/specs/planner/activity_library.md` |
| Phase lengths, taper, recovery cadence, ambition knobs | `endury/planner/{phase,foundations,macro}/` | `docs/specs/planner/` |
| How the coach talks and what its tools do | `endury/coach/` | [endury/coach/README.md](endury/coach/README.md) |
| The UI | `web/` | [web/README.md](web/README.md) |

The training science the menus and bands rest on lives in
`docs/research/`. Cite it in YAML comments, never in text an athlete
reads.

**Session menus.** One YAML file per goal prescribes every session of
every week: for each phase and week type, the slots of a weekly template
with their day, activity, per-ambition duration bounds and the
athlete-facing `why`. The loader rejects an unknown key or a malformed
value with its exact path, and the menu contracts run on every load, so a
broken file fails the first test that imports the planner. Some mistakes
still pass; [AUTHORING.md](endury/planner/sessions/menus/AUTHORING.md)
lists them and the check that catches each.

**Rules and bands.** `bench/rulebook.yaml` says what a good plan looks
like: every rule with its band, its evidence grade and its sources. The
scorecard grades Endury's plans on all of them, and the benchmark asks
the ones that can be read off any plan of AI language models too. The
planner never reads the rulebook: it judges plans, it does not make them.
A new band needs at least two independent published sources, a `reads`
line saying what the rule measures, a one-line `why`, and a re-recorded
baseline in the same commit. [docs/quality](docs/quality/README.md) maps
what checks a plan and lists every rule by race goal.

## The validation ladder

Run it after every menu or band edit, top to bottom, and stop at the
first surprise. Every step is offline and takes seconds unless noted.

| # | Command | Answers |
| --- | --- | --- |
| 1 | `make test PYTEST_PATH=tests/planner/menus PYTEST_COVERAGE=` | The loader, contracts, day-layout lints and brick pairing hold. |
| 2 | `make run-devtool TOOL=planner_menu_diff` | Exactly which menu cells (goal × phase × mode × tier) changed, against `HEAD`. |
| 3 | `make run-devtool TOOL=planner_menu_canon` | Whether slot order, names, `why` text or contracts changed. |
| 4 | `make run-devtool TOOL=planner_fingerprints` | Which scenario plans changed at all: those of the plans on offer, or all of them with `ARGS='--all'`. |
| 5 | `make run-devtool TOOL=planner_score_quality ARGS='--matrix --json'` | Whether any scorecard rule regressed at any horizon of a plan on offer. |
| 6 | `make run-devtool TOOL=planner_scenario_report ARGS='--scenario <name> --no-plots'` | What a changed plan now looks like, week by week (`--list` names the scenarios). |
| 7 | `make test`, `make lint`, `make test-scorecards` | Nothing else broke (a few minutes). |

Steps 2 to 4 compare against `HEAD` by default; `ARGS='--rev <rev>'`
compares against another commit, and `--goal <goal>` narrows them. A pure
refactor, such as reformatting YAML or moving code, must show empty diffs
at all three. A coaching change will show diffs: say in the pull request
which cells and scenarios moved and why. The scorecard matrix grades the
plans on offer; for a goal that is not offered, steps 1 to 4 and 6 are
the net. To look at whole plans rather than numbers,
`make run-devtool TOOL=planner_sweep_site` draws every plan on offer
week by week with its workout cards.

## Gates

- **`make test`**: the offline suite. `endury/planner/`, engine and
  menus, is held at 100 % coverage; everything else you add ships with
  its tests.
- **`make lint`**: ruff, mypy, `devtools/core/check_architecture.py`, the docs check,
  and the generated docs.
- **`make test-scorecards`**: the planner-matrix baseline in
  `devtools/quality/baselines/planner_matrix.json`. No composite may
  rise. Lowering one is free; raising one is a deliberate, reviewed
  change to that file in the same commit, with the trade explained in
  the commit message. Never widen the tolerance to absorb a regression.
- **Frontend changes**: `make run-devtool TOOL=static_adapters_smoke`
  (Node, no browser) and `make run-devtool TOOL=e2e_lifecycle`, which
  drives Chromium through Playwright against the built site and local
  fakes of intervals.icu and OpenRouter. Run
  `poetry run playwright install chromium` once. The site build downloads
  Pyodide once into `.pyodide/`, so the harness is not part of `make test`.

## Pull requests

- Start with an issue, and write code once the maintainer has agreed to
  the change there. It saves you work on a change that would not be
  merged. Name the issue in the pull request (`Closes #12`); a pull
  request without an agreed issue may be closed.
- The maintainer approves and merges every pull request.
- One topic per pull request, with a description of what changes for the
  athlete.
- For a menu or band change, paste what steps 2 to 5 of the ladder
  reported.
- `make test`, `make lint` and `make test-scorecards` green.
- The conventions a change must keep, from the planner's boundaries to
  the clock and the athlete-facing text, are in [AGENTS.md](AGENTS.md).
  `make lint` enforces most of them.

## License

Endury is licensed under the GNU Affero General Public License, version 3 or
any later version (AGPL-3.0-or-later). By contributing, you agree that your
contribution is licensed under that same licence, the later-version option
included. Sign off each commit (`git commit -s`), which certifies the
[Developer Certificate of Origin](https://developercertificate.org): you have
the right to submit the change under that licence.

Copy no training plan, book, or third-party code beyond what its licence allows.
Never put an API key, a real athlete's data or a chat in an issue or pull request;
nobody here will ask for them. Reproduce a bug with the demo or invented data.
