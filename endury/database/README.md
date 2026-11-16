# endury/database

SQLite for the one thing Endury owns: plan and conversation state.
Training history, thresholds and wellness live on intervals.icu and are
never mirrored here. Single user, `athlete_id = 1`. In the browser the
database is in memory, a per-session cache that `endury/store/` restores
from the store note at boot and saves back when a durable table changes.
Native tools can open a file instead (`ENDURY_DB_PATH`).

| Table | Holds |
| --- | --- |
| `athlete_profile` | Name, preferences, coach style. |
| `athlete_self_assessment` | Self-reported weekly hours and longest session per sport. |
| `plans`, `plan_sessions`, `macro_plans` | The plan. `plan_sessions.pushed_event_json` is the last payload pushed, which a replan's readback diff compares against. |
| `athlete_goals` | Races: name, date, goal type, priority, status. A cancelled race is a status, never a deleted row. |
| `athlete_memory`, `athlete_memory_history` | The memory document and its earlier versions. |
| `conversation_history` | The chat. It never enters the store note; its last 50 rows go to the chat note. |

| Module | What it does |
| --- | --- |
| `sqlite/session.py` | The injectable `DatabaseSession`: `cursor()` and `transaction()` blocks, schema bootstrap, the default path. |
| `sqlite/migrations.py` | Ordered schema migrations versioned by `PRAGMA user_version`. |
| `sqlite/repositories/plan.py` | Plans, sessions and macro plans: create, archive, status, link, schedule, swap. |
| `sqlite/repositories/athlete.py` | The athlete profile row. |
| `sqlite/repositories/athlete_memory.py` | The memory document and its history snapshots. |
| `sqlite/repositories/conversation_history.py` | Chat rows and the `reset` markers "New Chat" writes. |
| `sqlite/repositories/self_assessment.py` | Self-reported weekly volume per sport. |
| `sqlite/repositories/athlete_goals.py` | Races, matched by id, by date and goal type, or by date alone. |
| `sqlite/repositories/write_models.py` | The dataclass payloads the plan repository accepts. |

Two architecture rules police this package: athlete SQL lives only in the
athlete repository (`athlete-sql-gateway`), and no I/O runs inside a
`cursor()` or `transaction()` block (`db-block-io`).

Start reading:
- [sqlite/session.py](sqlite/session.py) — the session every repository is built on.
- [sqlite/migrations.py](sqlite/migrations.py) — the schema, version by version.
