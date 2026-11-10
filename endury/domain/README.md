# endury/domain

Shared domain models and the zone analysis built on them. The planner
does not import this package; what both need lives in `endury/training/`.

| Module | What it does |
| --- | --- |
| `models/athlete.py` | `AthleteProfile`: name, sport, experience, coach-style preferences. |
| `models/memory.py` | The athlete-memory document and the chat-history message models. |
| `models/race.py` | `AthleteRace`, one `athlete_goals` row, with its priority and status literals. |
| `models/zones.py` | `ZoneSet` for heart rate, power, pace and swim pace, with the per-sport fallback chain. |
| `analysis/zones.py` | Zone sets from an athlete's thresholds, or from the zone bounds intervals.icu holds. |
| `analysis/race_prediction.py` | Riegel race-time prediction for running. |

Start reading: [models/zones.py](models/zones.py), then [analysis/zones.py](analysis/zones.py).
