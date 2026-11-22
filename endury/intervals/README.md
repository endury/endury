# endury/intervals

The intervals.icu adapter. intervals.icu is Endury's only source of
training data and its only durable store. This package reads activities,
wellness, thresholds and fitness from it, and writes calendar events
(plan sessions, races, the coach's bookings), the hidden notes the store
keeps, a completed activity's pairing and verdict, and the thresholds the
athlete saves. The verified API facts, the workout DSL grammar and the
external id scheme are in
[docs/reference/intervals_api.md](../../docs/reference/intervals_api.md).

| Module | What it does |
| --- | --- |
| `client.py` | `IntervalsClient`: httpx with basic auth, typed errors, bounded retries. |
| `models.py` | The pydantic subset of their payloads, the sport-type map, the upload-destination flags. |
| `trace.py` | An activity's streams into the bucketed intensity bars of the debrief. |
| `reps.py` | Recounts an activity's reps from the stream when intervals.icu's count misses the paired workout's. |
| `workout_dsl.py` | A `StructuredWorkout` into intervals.icu's text DSL, with threshold-relative targets. |
| `sport_settings.py` | Thresholds the athlete saves, written to every sport-settings row covering the sport. |
| `athlete_source.py` | intervals.icu data into the planner's `AthleteState`: measured history, then self-assessment, then the goal default. |
| `fitness.py` | Per-sport Banister CTL and ATL over daily TSS. |
| `fake.py` | `FakeIntervals`: an in-memory intervals.icu at the HTTP level, for the tests, the E2E harness and the demo athlete. |
| `push.py` | Plan sessions and races into calendar events: upsert and delete payloads, the readback diff. |
| `ownership.py` | Moves an API key's Endury events to a Connect intervals.icu sign-in. |

Start reading:
- [client.py](client.py) — every call to intervals.icu goes through it.
- [push.py](push.py) — how a plan becomes calendar events, and how drift is read back.
