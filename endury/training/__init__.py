"""Host-free training-science kernel shared by the planner and the domain layer.

This package holds the pure primitives that have no athlete identity and no
host coupling (no database, no HTTP client, no I/O):

- :mod:`endury.training.load` — TSS/load formulas, threshold-scale constants,
  pace/power/CSS conversions, and the ``ResolverProtocol`` athlete-threshold
  interface.
- :mod:`endury.training.zones` — the declarative ``ZoneBandSpec`` and the
  canonical intensity-factor band tables.
- :mod:`endury.training.workout` — the Pydantic structured-workout schema
  (``StructuredWorkout``/``WorkoutStep``/``TargetDef``/``DurationDef``).

Both :mod:`endury.domain` and :mod:`endury.planner` depend on this package;
it depends on neither (stdlib + pydantic only), keeping the dependency graph a
clean DAG.
"""
