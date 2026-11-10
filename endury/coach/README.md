# endury/coach

The chat coach: a plain async tool loop, no framework. `turn.py` runs one
turn. It builds the system context, calls the model once per round with
tools until the model stops calling them, and returns a `TurnResult`: the
reply, the tools used, the Apply cards those tools previewed, the charts
its analyses drew, and whether the plan changed. A turn that ran an
analysis writes its reply in a last round without tools, because the
model drafted answers beside its tool calls and then closed on a note
about a message the athlete never saw.

Two rules hold the package together. The coach never confirms a plan
write: a tool with a `confirm` parameter runs as a preview inside a turn,
the preview becomes a card beside the reply, and `apply_proposal` is the
one place `confirm=True` is passed, with the arguments the athlete saw.
And memory is one markdown document, read whole every turn and rewritten
whole by the coach, which the athlete can edit in the same words; nothing
is inferred from the transcript.

## Talking to OpenRouter

OpenRouter is the one way to a model, and it is optional: without a key
the planner works and chat answers `chat_disabled`. The key and the model
id come from Settings into `CoachConfig` at boot.

- One model serves every turn, `CoachConfig.model`; the default is
  `anthropic/claude-haiku-5.5`.
- `reasoning_effort` defaults to `"low"`. The default model reasons at
  length when the field is omitted, and its reasoning counts against the
  reply's token limit. Never `"none"`, which some models reject.
- `openrouter_data_collection` defaults to `"deny"` and `openrouter_zdr`
  to true, so a turn goes only to endpoints OpenRouter lists as not
  collecting it and as keeping no data. A model with no such endpoint gets a
  clear error. The page sends nothing until the athlete ticks the chat
  consent in Settings.
- The system message carries a prompt-cache breakpoint on its context
  block.
- `llm.py` strips the openai SDK's `x-stainless-*` headers, which
  OpenRouter's CORS rejects, and imports the SDK on the first call, so a
  session that never chats never loads it. The client is the same
  natively and in Pyodide.
- Native tools and tests read `.env` through `get_coach_config()`;
  `.env.example` names the variables.

## Modules

| Module | What it does |
| --- | --- |
| `turn.py` | The turn loop. Offers the plan tools only while a plan is active, and attaches each tool's reply contract to its result. |
| `proposals.py` | Apply cards: `run_tool` previews, `apply_proposal` writes or re-previews a stale card, `refresh_proposal` updates an open card after another one wrote. |
| `card_summary.py` | The card's one line per tool, in the athlete's language. |
| `session.py` | One conversation: `chat`, apply, refresh and dismiss; persisting turns; resuming the recent window on start. |
| `runtime.py` | The app-scoped runtime: repositories, the tool registry, the clients, the cached plan context. |
| `llm.py` | The OpenRouter client: one-shot completions with bounded retries. |
| `messages.py` | Windowing and pruning of the message list, tool-result digests, and each replayed message stamped with its send time on the way to the model. |
| `context.py` | The system context: profile, memory document, wellness, form, plan brief, today's and tomorrow's sessions, the coach's bookings, the races, the app facts, the day table. |
| `dates.py` | The day names the coach reads instead of computing, and "today" or "tomorrow" resolved before a tool runs. |
| `prompt.py` | The persona, the template and `build_system_prompt`. |
| `wellness_context.py` | Wellness rows into the snapshot, recovery flags and trends. |
| `form_context.py` | Fitness, fatigue and form from completed sessions: the dated "Form today" line and the load tool read the same numbers. |
| `memory_cleanup.py` | "Tidy up": one model call over the document, validated, returned as a proposal and never stored here. |
| `welcome.py` | The welcome line and the conversation starters. |
| `workout_outline.py` | The outline of an ad-hoc workout, which the coach quotes verbatim. |
| `reply/` | Cleaning the reply: echoes, the one-question rule, leaked tool calls; then the final reply and its profile side effects. |
| `tools/registry.py` | The `@coach_tool` registry: schema from the signature, description from the docstring, hidden parameters kept out of both. |
| `tools/activity_tools.py` | Completed sessions: the recent list, the debrief, the weekly summary. |
| `tools/load_tools.py` | Form (CTL, ATL, TSB), readiness advice, wellness trends, a race-time prediction. |
| `tools/brief_tools.py` | `get_brief`: the brief sheet for a coming session or race, the same one the page shows. |
| `tools/analysis_tools.py` | `run_analysis`: a script on the athlete's data in the sandbox, and the charts it draws under the reply. |
| `tools/plan_tools.py` | The planner reads, including a completed test's thresholds. |
| `tools/plan_edit_tools.py` | The plan writes: status and move at once; adjust, swap, pause and replan previewed; `schedule_workout` books on the call and its overrides preview. |
| `tools/plan_build_tools.py` | `create_plan`, previewed as a card, and `record_self_assessment`. |
| `tools/plan_directive_tools.py` | `set_plan_directives`, for an away window next week or later. |
| `tools/race_tools.py` | Races in the `athlete_goals` table and on the calendar; `add_race_to_plan` dry-runs the replan first. |
| `tools/profile_tools.py` | The profile and `update_memory`. |
| `tools/workout_tools.py` | `build_workout`: an ad-hoc structured workout, never persisted by itself. |
| `tools/zone_tools.py` | Thresholds and zones from intervals.icu; `set_athlete_thresholds` previews; a threshold estimate from a race time. |
| `tools/threshold_readout.py` | A completed fitness test into suggested thresholds. |
| `tools/prescription_summary.py` | A planned session's targets in prose. |

Start reading:
- [turn.py](turn.py) — the turn loop; everything else supports it.
- [proposals.py](proposals.py) — how a write becomes a card, and a card a write.
- [tools/registry.py](tools/registry.py) — how tools are declared and dispatched.
