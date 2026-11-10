# Day Assigner — Requirements

The planner layer-4 contract for mapping each QP-sized `PlacedSession` onto a
concrete calendar date. The menus author the calendar (`Slot.day` is mandatory
and the day-layout lints in `menus/contracts.py` enforce physiological spacing
at import time — R-SESS-025), so this layer is deliberately small: authored day
→ date, plus deterministic race-day eviction.

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### I/O contract
- `[x]` **R-DAY-001**: `assign_days` MUST take sized `list[PlacedSession]`,
  `training_start` (Monday of week 0), and an optional `Sequence[RaceEvent]`.
  It MUST return `list[ActivityAssignment]` sorted by `(date, slot_id)`.
- `[x]` **R-DAY-002**: Each output `ActivityAssignment` MUST carry the source
  `slot_id`, `activity_key`, and `target_hours` unchanged, with `date` =
  `training_start + week*7 + day_of_week`. The assigner MUST NOT resize sessions.
- `[x]` **R-DAY-003**: Empty input MUST return `[]`.
- `[x]` **R-DAY-004**: Totality: one assignment per input session, EXCEPT the
  documented race-week drops of R-DAY-006/R-DAY-007 — every drop MUST be logged
  at warning level with the slot_id and reason.

### Placement
- `[x]` **R-DAY-005**: Every session MUST be placed on its `day_of_week` (the
  menu-authored day; test injection presets it for TEST sessions). A session
  without a day (hand-built only — menu slots always carry one) falls back to
  Monday.
- `[x]` **R-DAY-006**: A non-TEST session landing on a `RaceEvent`'s
  `(week, day_of_week)` MUST be evicted to the nearest earlier day of the same
  week that is not a race day, hosts no same-sport session, and holds fewer
  than 2 sessions (the menu-lint per-day ceiling — an eviction MUST NOT stack
  a third session onto race-eve); with no such day it MUST be dropped (logged).
  Evictions resolve in input order (generator emission order) and update
  occupancy, so cascading collisions land on successively earlier days —
  fully deterministic.
- `[x]` **R-DAY-007**: In the A-race week (`RaceEvent.goal.priority == "A"` —
  the typed signal, not the `event_id` string), non-TEST sessions dated after
  the race day MUST be dropped (logged) — the plan ends at its goal race.
  B/C-race weeks MUST keep their post-race sessions (mid-season training).
- `[x]` **R-DAY-008**: `SessionOrigin.TEST` sessions MUST be pinned to their
  preset `day_of_week` — never evicted, even on a race day (an explicitly
  scheduled protocol wins; this preserves the old solver's pinning semantics,
  which also overrode race-day blocking).

### Boundaries (MUST NOT)
- `[x]` **R-DAY-009**: The assigner MUST NOT re-derive day layout — hard/easy
  alternation, brick co-location, per-day capacity, and weekend long-session
  placement are menu-authoring concerns enforced by the import-time day-layout
  lints (R-SESS-025), not runtime optimisation targets.
- `[x]` **R-DAY-010**: The assigner MUST be deterministic: identical inputs
  yield identical assignments (no solver, no randomness, no time budget).

## Map (orientation only)

| File | Owns |
|---|---|
| `planner/days/day_assigner.py` | `assign_days` — authored-day placement + race-day eviction. |
| `planner/foundations/types.py` | `PlacedSession`, `ActivityAssignment`, `RaceEvent`, `SessionOrigin`. |
| `planner/sessions/menus/contracts.py` | The day-layout lints that guarantee the authored calendar is sound (R-SESS-025). |

## Known violations

None.
