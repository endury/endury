# endury/planning

The application layer around the one plan the athlete has. The planner
in `endury/planner/` decides what a plan should be; this package
generates it, stores it, pushes it to intervals.icu, replans and edits
it, and builds the calendar and debrief views. It owns no transport and
no raw SQL: the browser runtime calls in, and the repositories hold the
SQL.

## How a plan moves

- **Athlete state** comes from measured intervals.icu history first, a
  stored self-assessment second and the goal's default last. A goal sport
  with no measurement refuses with `athlete_data_required` until the
  athlete answers the questionnaire.
- **Generate** runs the planner and materialises the sessions into the
  stored shape. It refuses a tier no menu offers (`plan_not_offered`) and
  an Experimental plan until the athlete confirms (`experimental_plan`).
  A replan, and a race added to a stored plan, pass both gates.
- **Commit** stores the plan, pushes every session as a calendar event
  and refreshes the coach's plan brief. A failed push never rolls back
  the commit; the next push retries what is missing, replaced workouts it
  could not delete included.
- **Replan** regenerates from next Monday on the plan's own timeline.
  When the athlete edited pushed events by hand, it first answers with
  the readback diff and asks to confirm.
- **Compliance** is read live from intervals.icu on every calendar load.
  A session intervals.icu left unpaired is paired when exactly one
  activity of its sport fits its day, and never again once paired or
  marked by hand. An activity named after another open workout is left
  alone: the watch ran that session on a day it was not planned for.
- **Linking** an activity to a session moves the session onto the
  activity's day when it was planned for another, takes the activity off
  any session it completed before and pairs it on intervals.icu. A done
  session's sheet and the coach both link this way.

## The editing verbs

Each verb reaches one part of the calendar and no other.

| Verb | Module | Reach |
| --- | --- | --- |
| `adjust_current_week` | `adjust_week.py` | Today through this Sunday, rescaled. |
| `replan_active_plan` | `replan_plan.py` | Next Monday through race day. |
| `pause_training` | `pause_training.py` | Today through a date; the replan restarts on the resume day. |
| `set_plan_directives` | `plan_directives.py` | A future window, next week or later; never the current week. |
| `swap_planned_session` | `swap_session.py` | One future session, in either window; an optional date moves it within its week. |
| `schedule_workout` | `schedule_workout.py` | One extra session on one date. |
| `unschedule_workout` | `unschedule_workout.py` | The coach's own bookings only. |
| `set_session_status` | `session_status.py` | One session's status; skipping deletes its event, un-skipping re-pushes it, linking an activity moves the session to its day. |
| `skip_session` | `session_status.py` | The coach's skip: a preview first, the status write on Apply. |
| `move_planned_session` | `commit_plan.reschedule_session` | One session to a day in another week. |

A pause always starts today. A window that has not started yet is
refused with a redirect to `set_plan_directives`, and every applied pause
records its window as `unavailable` on the plan. Directives are what the
coach knows and the planner does not: a resume date, tests to suppress,
unavailable windows, a weekly-hours cap. They live on the plan's stored
request, so every later replan re-applies them.

An extra workout inside an active plan becomes a coach-extra plan row, so
week totals, compliance and the next replan see it. Its guards run in
order: refused inside an unavailable window; taper and race weeks only
with `allow_protected`; easy or recovery sessions by default, a hard,
long or brick one only with `allow_hard`; never within a day of another
hard session; at most a fraction more TSS than the week was sized for.
Both overrides reach the coach only through a previewing tool. Outside a
plan, the workout is a standalone calendar entry.

Every verb with no active plan answers the shared no-plan message, which
points at `create_plan`.

## Modules

| Module | What it does |
| --- | --- |
| `athlete_state.py` | The per-sport `AthleteState`: measured history, then self-assessment, then the goal default. |
| `generate_plan.py` | `run_generate` and `run_commit`; the short-notice join, the preview's warnings, the persisted request and its hash. |
| `plan_maturity.py` | A plan's effective tier and the sentences the athlete reads; the gate on an unconfirmed Experimental plan. |
| `offered_plans.py` | The tiers each menu offers; a new plan outside them is refused. |
| `create_plan.py` | The coach's `create_plan`, previewed as an Apply card; refuses a stale preview and a trail-band mismatch. |
| `plan_preview.py` | The lean preview of a generated plan for the coach's card. |
| `trail_band_guard.py` | Flags a trail race whose stored distance and climb name a different band than requested. |
| `commit_plan.py` | Persists a previewed plan, pushes it and updates the coach context; `reschedule_session`; the push retry. |
| `session_status.py` | `set_session_status`, mirrored onto the calendar. |
| `replan_window.py` | Where a replan cuts, how many prior weeks it keeps, which pushed events go stale. |
| `replan_plan.py` | `run_replan` from next Monday or a resume date, with the readback diff. |
| `adjust_week.py` | `adjust_current_week`. |
| `swap_session.py` | `swap_planned_session`. |
| `pause_training.py` | `pause_training`, and `trim_first_week` for the week after a pause. |
| `schedule_workout.py`, `plan_extra_session.py` | `schedule_workout`; the coach-extra row and its guards. |
| `unschedule_workout.py` | Removes the coach's own bookings; `pause_without_plan`. |
| `plan_directives.py` | `PlanDirectives`: parse, apply and merge the windows. |
| `materialize_plan.py` | A planner result into the stored session shape: steps, notes, loads. |
| `brief/` | The brief sheet for a coming session or race: form, proven efforts, fuel, kit, routes, a race time range, the success rubric. |
| `projections.py` | Daily CTL and TSB projections for a plan. |
| `race_book.py` | Syncs a plan's races into `athlete_goals` on every commit and replan, never overwriting a name. |
| `race_calendar.py` | Keeps a race-table race on the intervals.icu calendar: on save, and at boot for one the calendar lost; a cancelled one comes off. |
| `race_day.py` | Finds the activity that was a race, and marks a race the athlete has run as completed at boot. |
| `add_race.py` | `add_race_to_plan`: a dry-run replan with the race appended; persist and replan on apply. |
| `trail_band.py` | The km-effort table behind the trail bands. |
| `get_active_plan.py` | The active plan with compliance stats and the payload the shells boot from. |
| `load_planner_metrics.py` | The read-only planner reads: load context with the athlete's own zones, macro weeks. |
| `sync_status.py` | Per-session push state against intervals.icu, and what "Sync now" re-pushes. |
| `calendar_data.py` | The calendar payload: planned sessions, completion, phases, races, and the workouts that live only on intervals.icu. |
| `pair_activity.py` | Links an activity with a session (`link_activity`, `unlink_activity`) or pairs it with an event; `pair_unambiguous` on every calendar load. |
| `activity_match.py` | Which session an activity was: the workout title its name carries, then length and day. |
| `calendar_projection.py` | Calendar readback: activities, completion overlays, and the coach's extras and a former plan's finished sessions as rows with absolute targets. |
| `activity_views.py` | The debrief and intensity-trace views, read live. |
| `coach_brief.py` | The plan summary in the coach's system context after a commit. |
| `commit_note.py` | The memory note a commit leaves. |
| `intervals_provider.py` | An `IntervalsClient`, or `None` without a key, so every flow degrades instead of raising. |
| `no_plan.py` | The shared no-active-plan message. |
| `wire/` | The pydantic request models and the serialisers: sessions, steps, weeks, macro, inputs, races. |

Start reading:
- [generate_plan.py](generate_plan.py) — how a plan is created and committed.
- [replan_plan.py](replan_plan.py) — how a plan is regenerated mid-preparation.
- [adjust_week.py](adjust_week.py) and [swap_session.py](swap_session.py) — how the coach steers a live plan without replanning.
