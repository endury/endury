# endury/planner

The planning engine. `generate_plan(PlanRequest) -> PlanResult` in
`pipeline.py` turns a race, an ambition and the athlete's state into
every session of every week, with no AI language model anywhere and no
I/O. Four layers run in turn, one subpackage each: `phase/` lays out the
weeks, `sessions/` fills each week from the goal's menu, `macro/` sizes
every session within its bounds with a quadratic program, and `days/`
puts the sessions on dates. `foundations/` holds the frozen contracts
they share, and `activity/` builds each session's workout card. The
menus are YAML under `sessions/menus/`; [AUTHORING.md](sessions/menus/AUTHORING.md)
says how to edit them, and [docs/specs/planner/](../../docs/specs/planner/)
holds each layer's contract.

| Module | What it does |
| --- | --- |
| `pipeline.py` | `generate_plan`, the composition root; the default profile and the CTL seed helpers. |
| `foundations/types.py` | The frozen dataclasses and enums every layer shares. |
| `foundations/request.py` | `PlanRequest`, `AthleteState`, `SportState`, `SchedulePreferences`, `SolverOptions`. |
| `foundations/ambition_tables.py` | The knobs per ambition level: ramp, recovery cadence, freshness, scaling. |
| `foundations/maintenance_tables.py` | Volume targets and block lengths per tier for the raceless maintenance goals. |
| `phase/phase_planner.py` | Layer 1: the week-by-week phase and mode skeleton from the goal list. |
| `phase/phase_config.py` | Layer 1 knobs: tune-up blend weights, the long-B-race threshold, recovery suppression near the taper. |
| `phase/phase_tables.py` | `GOAL_PROFILES` and the per-goal tables: horizons, sport sets. |
| `phase/sport_split_tables.py` | Phase-dependent sport splits for the multi-sport goals. |
| `sessions/session_generator.py` | Layer 2: every session from the menus, with progression, ambition scaling and capacity capping. |
| `sessions/test_injection.py` | Fitness-test placement between layers 2 and 3. |
| `sessions/why.py` | The athlete-facing "why" of each session. |
| `sessions/menus/` | One YAML blueprint per goal, the loader, the import-time contracts, the authoring helpers, and the blueprint-to-week resolver. |
| `macro/macro_planner.py` | Layer 3: the quadratic program that sizes each session, pricing its load by its own activity. |
| `macro/macro_config.py`, `macro/macro_tables.py` | Layer 3 knobs and lookup tables, the CTL and ATL decay constants among them. |
| `macro/capacity.py` | The hours ceiling per week. |
| `macro/tid_tables.py` | Training-intensity-distribution fractions per phase and mode. |
| `macro/session_pricing.py` | A session's TSS per hour from its own activity at its menu target. |
| `macro/tss_estimator.py` | The intensity-hours to TSS bridge for race events and a session-less opening week. |
| `days/day_assigner.py` | Layer 4: menu weekdays to calendar dates, with race-day eviction. |
| `activity/registry.py` | The activity registry and its semantic queries. |
| `activity/select.py` | A plain-language workout intent to one activity that builds to the length asked for. |
| `activity/capabilities.py` | Per-activity capability metadata for the selector and the materializer. |
| `activity/config.py` | Activity constants and resource tables. |
| `activity/materialize.py` | Hours to a structured workout for a placed session, with its brief. |
| `activity/sets.py` | Rotating sets: the alternative structures per card, and which one the n-th occurrence takes. |
| `activity/steps.py` | Flat steps and coaching notes for a structured workout. |
| `activity/catalog/{run,bike,swim}.py` | The activity definitions per sport. |
| `activity/shapes/{base,land,aquatic}.py` | The workout shape builders the catalog uses. |

Start reading:
- [pipeline.py](pipeline.py) — the entry point and composition root.
- [foundations/types.py](foundations/types.py) — the contracts everything else depends on.
- [docs/specs/planner/](../../docs/specs/planner/) — the contract per layer.
