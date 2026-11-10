# endury/training

The training-science kernel: pure primitives with no athlete identity and
no I/O, on the standard library and pydantic alone. The planner, the
domain models and the intervals adapter depend on it, and it depends on
none of them.

| Module | What it does |
| --- | --- |
| `load.py` | The TSS formulas per sport, the pace, power and CSS conversions, and the athlete-threshold interface. |
| `zones.py` | `ZoneBandSpec` and the intensity-factor band tables, the one home of zone bands. |
| `workout.py` | The structured-workout schema: `StructuredWorkout`, `WorkoutStep`, `TargetDef`, `DurationDef`. |

Start reading: [load.py](load.py).
