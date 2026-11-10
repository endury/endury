# bench: a benchmark for long endurance training plans

The benchmark asks a contestant for a complete training plan, every week,
every session and every step, and grades it against Endury's house
rulebook. The rulebook is open to read, but Endury's author chose its rules
and Endury's plans are tuned against them, so read this as an internal
benchmark, not an independent ranking.
The bands come from the coaching and sports-science literature and from the
maintainer's judgement where no study sets the number, each benchmark rule
with an evidence grade and sources a reader can check. A source's `verified:
isbn` says the book exists, not that its content was read. Contestants are AI
language models called through OpenRouter and any planner that exports into
the same plan table. This package imports nothing from `endury`.
Endury is one contestant, adapted in
[`devtools/bench/`](../devtools/bench/).

The rulebook is also the rule file of Endury's own quality gate
([`devtools/quality/`](../devtools/quality/README.md)), which grades
Endury's plans on every rule in it. A rule marked `benchmark: false` reads
Endury's own plan structure, so the benchmark never asks it of a contestant.

## Status

The committed run under [`results/`](results/README.md) is graded on
rulebook version 6; the saved answers were regraded without a new call.
It covers Endury, the planner of 2026-10-11 graded on its workout cards,
on every cell; Gemini 3.5 Flash Lite at five samples on the two headline
cells and three elsewhere; and DeepSeek V4.1 Flash, Gemini 3.8 Flash,
Grok 4.7, GPT-6 Sol, Claude Opus 5.5 and Claude Sonnet 5 at one sample
per cell, except the 22-week Ironman, which only Endury has: the planner
now needs 22 weeks for an Ironman, and the 20-week cell went with the
answers it held. GPT-6 Astra was left out for cost. The Claude models ran as Claude Code
subagents on a subscription, not through OpenRouter, so their cost is an
estimate and their time is not comparable. One sample per AI
model is a first reading, not a ranking: Flash Lite's five draws on one
brief spread by five rules met. An answer that is not a complete plan,
through a missing week, broken JSON or an exhausted token budget, is
recorded with its defects and never graded. Not built yet: the replan
task and the chunked and validator-loop arms.

| Rulebook | What changed |
| --- | --- |
| 4 | From here the rulebook is also the rule file of Endury's quality gate. Taper volume, race-week volume, the half-Ironman's phase shares and the timing of its last long ride take house bands by goal; the taper-volume rule drops from grade A to D with them; the rest-day rule also grades triathlon plans. |
| 5 | A one-week taper is read together with its race week; every tier's recovery cadence is graded at 3:1; the trail 20K gets a race-week band of its own and is covered by the first-long-session and weekly-ramp rules. |
| 6 | The first long session is read on runs and rides only; a brick run counts as warmed up by its ride; monotony is read on average over the load weeks; a taper may hold up to 1.5 times the peak week's sessions and its quality minutes may fall as far as the trials cut volume; the half-Ironman, whose one taper week is read with its race week, takes the taper trials' floor of 0.40 and the long-course freshness ceiling. |

## What is compared

Fourteen plans for three race goals. Each is a fresh preparation for one
A race, with no tune-up races, no strength or mobility sessions and no
change part-way:

| Goal | Tier | Plans |
| --- | --- | --- |
| Marathon | moderate | 12, 18 and 24 weeks; the 18-week plan also for an athlete on a low and on a high training volume, and under a 5-hour weekly cap |
| Ironman | competitive | 22, 24, 28 and 32 weeks; the 32-week plan also for an athlete on a low and on a high training volume, and under a 12-hour weekly cap |
| Half-Ironman | moderate | 24 weeks |

An AI language model's plan is its answer to the brief. Endury's plan is
what its planner gives an athlete with the same race, tier, weeks and
recent training, read from the workout cards that athlete would get.

Nothing else is compared. The readings say nothing about the other tiers
of these goals, about the 5K, 10K, half marathon, sprint and olympic
triathlon or trail plans Endury also writes, or about a plan that is
changed after it starts.

## What is measured

| Piece | File | What it holds |
| --- | --- | --- |
| Plan table | `schema.py` | weeks of sessions; a session is a day, sport, type label, minutes (metres for swims), steps of `(zone, minutes)` and a note |
| Parser | `parse.py` | an AI model's answer into the table, lenient on labels, strict on arithmetic: steps that do not sum to the session are scaled and counted as defects |
| Load model | `load.py` | five zones at Coggan's power-zone midpoints; load is minutes at the zone's intensity factor squared; daily fitness, fatigue and form; Foster's monotony and strain |
| Readings | `readings.py` | every quantity a rule can grade, labelled and label-free, skeleton and step level |
| Rulebook | `rulebook.yaml` | 55 rules in 7 families. The 39 benchmark rules each name a reading of the plan table and carry bands, an evidence grade A to D (S for structural rules), sources with their verification and a `school_specific` flag. The 16 rules with `benchmark: false` read Endury's own plan structure; their bands are house bands the maintainer set. The file's header documents the band format. |
| Scoring | `rules.py`, `score.py` | distance from a band (0 inside, rising to 1 over the ramp), counts by grade and family, a composite with equal family weight, sample aggregation, the feasibility ceiling |
| Scenarios | `scenarios.py` | the matrix: two headline cells, a horizon sweep, athletes on a low and a high training volume, weekly caps |
| Brief | `brief.py` | the one request every AI model gets; the `bands` arm appends the targets |
| Runner | `run.py`, `report.py` | metered OpenRouter calls under a spend ceiling, raw answers saved before parsing and reused, results and a markdown report |

The rulebook was assembled from five research dossiers, one per family, in
[`docs/research/bench_dossiers/`](../docs/research/bench_dossiers/). Each
source was resolved through Crossref or a publisher's ISBN listing when
the dossier was written, and `verified` records which. The bands are the
benchmark's decisions. Where a band departs from a dossier card, the
rule's `note` says so.

## Read it right

- **Rules met by grade is the headline.** A and B rules rest on
  meta-analyses, trials, cohorts or validated models. C rules are
  agreement among coaching texts, D rules a single source or a
  convention, S rules the plan's own consistency. An AI model that
  follows another coaching school fails the `school_specific` rules; they
  are labelled so a reader can discount them.
- **Endury faces the same rulebook** on the same public load model, and
  its misses are in the table. Its own quality gate grades its plans on
  every rule here, so it is built to meet them: read the table as how close
  an AI language model comes to the plans this rulebook calls good.
- **A rule nobody meets is flagged, and still counted** (`rules_nobody_met`
  per scenario). The band is then reviewed.
- **Samples.** An AI model may be drawn several times per cell; the
  report shows the median, the range and the rules met in every sample.
  Endury is deterministic.
- **Registration.** `results.json` carries a hash of the rulebook, brief,
  scenarios, readings and load model that produced it, so a reading can
  be tied to the exact rules it was graded on.

## Run it

```bash
make run-devtool TOOL=bench_plans ARGS='--contestants endury --scenarios headline'
make run-devtool TOOL=bench_plans ARGS='--contestants endury,google/gemini-3.5-flash-lite --scenarios headline --n 5 --ceiling 3'
```

`--scenarios` takes labels or tags (`headline`, `horizon`, `athlete`,
`cap`). `--arm bands` appends the rulebook's targets to the brief. Calls
to an AI model need `OPENROUTER_API_KEY` in `.env`; without a key, or with
`--api-key ""`, only saved answers are graded. Every call the runner makes is
priced from OpenRouter's exact `usage.cost`, and a call whose worst case would cross
`--ceiling` is skipped rather than made. Raw answers are saved under
`bench/results/raw/` before parsing and reused on the next run, so a
regrade after a rulebook change makes no call and `--n` calls only for
the samples missing. Results and the report go to `bench/results/`;
`make run-devtool TOOL=bench_figure` redraws the README's figure from
them, and `TOOL=bench_plan_view ARGS='--scenarios ironman_32wk'` draws
every contestant's plan, week by week and session by session, as one HTML
page in the temp directory.

To add a contestant, pass a callable from a `Scenario` to a `Plan` as
`local=` to `bench.run.main`. `devtools/bench/endury_contestant.py` shows
the export, including how a workout card's targets map to the zones.
