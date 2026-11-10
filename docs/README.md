# docs

What is here: the pages people read first, then the research and the
specs the agents and the tests work from.

| Page | What it holds |
| --- | --- |
| [architecture.md](architecture.md) | How Endury's pieces fit together. |
| [privacy.md](privacy.md) | What Endury stores where, what leaves the browser when you chat, and how to remove all of it. |
| [plan_maturity.md](plan_maturity.md) | The Experimental, Alpha and Beta labels, the evidence each needs, and the log of completed preparations. |
| [quality/](quality/README.md) | What checks a plan, what each check rests on, what a human has reviewed, and every rule by race goal in the generated [rules.md](quality/rules.md). |
| [reference/intervals_api.md](reference/intervals_api.md) | Verified intervals.icu API facts, the workout DSL grammar and the `external_id` scheme. |
| [readme/](readme/) | The README's figures, a light and a dark file each. |

## Research

The sport science the planner and its menus cite.

| Page | Covers |
| --- | --- |
| [endurance_methodology.md](research/endurance_methodology.md) | Endurance training methodology, sport-agnostic. |
| [triathlon.md](research/triathlon.md) | Triathlon training methodology. |
| [short_course.md](research/short_course.md) | Short-course triathlon: sprint and Olympic distance. |
| [triathlon_long_course.md](research/triathlon_long_course.md) | Long-course triathlon: half-Ironman and Ironman. |
| [trail_running.md](research/trail_running.md) | Trail and ultramarathon: 20K, 50K, 100K. |
| [bench_dossiers/](research/bench_dossiers/README.md) | The rule dossiers and band reviews behind the benchmark in [bench/](../bench/README.md). |

## Planner specs

The planner's contracts, one per layer, written for the agents and the
tests.

| Spec | Holds |
| --- | --- |
| [behavioral.md](specs/planner/behavioral.md) | The behavioural requirements: the defect-driven checklist. |
| [foundations.md](specs/planner/foundations.md) | Frozen contracts, enums, the ambition policy, the load model. |
| [phase_planner.md](specs/planner/phase_planner.md) | Layer 1, the phase planner. |
| [session_generator.md](specs/planner/session_generator.md) | Layer 2, the session generator. |
| [macro_planner.md](specs/planner/macro_planner.md) | Layer 3, the macro planner. |
| [day_assigner.md](specs/planner/day_assigner.md) | Layer 4, the day assigner. |
| [activity_library.md](specs/planner/activity_library.md) | The activity library that builds the workout cards. |
