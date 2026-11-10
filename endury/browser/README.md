# endury/browser

The worker's entry point: Endury's Python side inside the Pyodide Web
Worker. Each `web/data/api.js` export becomes one `rpc.dispatch` call,
which answers an envelope. This is the only package that imports
`endury.store`.

| Module | What it does |
| --- | --- |
| `rpc.py` | `boot`, `dispatch`, `METHODS` and `MUTATING`; the envelope; the store-revision check before a write; the save after a call that left the database dirty. |
| `api.py` | One method per `api.js` export, with the parameters and the JSON shape each expects. |
| `demo.py` | The demo athlete: Sam's in-memory intervals.icu account and a half-distance plan four weeks in, nothing persisted. |
| `__init__.py` | `BrowserApp`, the booted session, and `RpcError`, a refused call whose `code` is the envelope's `error_code`. |

## Boot

`rpc.boot(settings)` builds `CoachConfig` from the settings panel, creates
the intervals.icu client and an in-memory repository container, restores
the store note and then the chat note, starts the coach runtime when an
OpenRouter key is present, and takes the dirty-tracking baseline. It
answers `{coach_ready, read_only, rev}`. A missing intervals.icu key
answers `invalid_settings`; `demo` in the settings boots the demo athlete
instead.

## The envelope

Every answer is `{ok, result, unsaved, read_only}` or
`{ok: false, error, error_code, unsaved, read_only}`. `unsaved` means the
store note still has to be uploaded; the next call, or `flush`, retries.

| `error_code` | Meaning |
| --- | --- |
| `not_booted` | A call other than `health` before `boot`. |
| `unknown_method` | The method is not in `METHODS`. |
| `invalid_request` | Bad parameters or a failed request model. |
| `invalid_settings` | No usable intervals.icu key at boot. |
| `not_found` | The addressed activity or session does not exist. |
| `refused` | A planning `ValueError`: an athlete-readable refusal, not a crash. |
| `chat_disabled` | A chat call without an OpenRouter key. |
| `intervals_auth` | intervals.icu rejected the key. |
| `upstream_error` | intervals.icu or the network failed. |
| `store_conflict` | Another device saved since this one loaded. |
| `store_read_only` | A newer Endury wrote the note; saving over it would drop columns. |
| `store_corrupt` | The note exists but cannot be decoded; the boot screen offers "Start fresh". |
| `internal_error` | Anything else, logged. |

## Methods

`METHODS` in `rpc.py` is the authoritative list; this table says what
each is for.

| Method | What it does |
| --- | --- |
| `health()`, `flush()` | Readiness, which answers before boot too; a no-op that lets a pending save retry. |
| `planner_bootstrap()` | The goal and ambition catalogues plus athlete-data readiness. |
| `put_self_assessment(sports)` | Self-reported volume for a goal sport intervals.icu history cannot measure. |
| `generate_plan(races, training_start, ambition_level, …)` | The deterministic preview. Refuses a plan no menu offers, and an Experimental plan until `accept_experimental`. |
| `planner_commit(…)` | Persist the preview and macro, push to intervals.icu, update the coach context. |
| `planner_push()` | Retry a failed or partial push of the active plan's events. |
| `replan(confirm?)` | Replan from next Monday; answers `confirm_required` with the readback diff when remote events were edited by hand. |
| `planner_delete()` | Archive the active plan and delete its remote events from today on. |
| `planner_active()`, `planner_macro()` | The active plan with compliance stats; the lean macro weeks. |
| `planner_sync_status()` | Per-session push state, coach bookings included, plus leftover replaced workouts. |
| `reschedule_session(session_id, date)` | Move a planned session to a new date. |
| `delete_ad_hoc_workout(external_id)` | Remove one coach-scheduled workout. |
| `calendar_data(start_date, end_date)` | The calendar payload, with compliance pairing. |
| `activity_debrief(activity_id)`, `activity_trace(activity_id)` | The in-app debrief, with its place in the plan; the intensity trace. |
| `pair_activity(activity_id, session_id or event_id)` | Link a completed activity with a planned session (moved to its day) or pair it with a workout. |
| `unlink_activity(activity_id)` | Undo an activity's link to its planned session. |
| `put_activity_verdict(activity_id, feel, icu_rpe)` | Write the athlete's own feel (1-5) and effort (1-10) on a completed activity. |
| `get_inputs()`, `get_races()` | The planning-inputs snapshot; the upcoming races for the wizard's chips. |
| `welcome()`, `conversation_recent(limit)`, `reset_chat()` | The welcome line; the recent conversation; "New Chat". |
| `chat(message, isolated)`, `apply_proposal(proposal)`, `dismiss_proposal(proposal)` | A coach turn (isolated: outside the conversation, as a take or a brief runs) and the two answers to an Apply card. |
| `refresh_proposal(proposal)` | Bring an open card up to date after another card wrote the plan; never writes. |
| `get_memory()`, `get_memory_history()`, `put_memory(content)`, `cleanup_memory()` | The memory document, its history, an athlete edit, "Tidy up". |
| `activity_takes(…)`, `save_activity_take(…)` | The coach's saved takes on activities and briefs on coming sessions and races. |
| `session_brief(session_id or race_date)` | The deterministic brief sheet for a coming session or race. |

Start reading:
- [rpc.py](rpc.py) — boot, dispatch and the envelope.
- [api.py](api.py) — what each method expects and returns.
