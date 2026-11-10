"""Session materialization primitives + devtools-only synthetic fixtures.

The synthetic athlete resolver (``build_synthetic_resolver``) materializes
report, benchmark and scorecard workouts without a real athlete, and
``card_text`` writes a built workout as its card reads. Built on by the
report sweep (``report_sweep``), the benchmark adapter (``devtools.bench``)
and the scorecard (``devtools.quality.planner``); the materialization
primitives are re-exported from ``endury.planner.activity.materialize``.
"""

from __future__ import annotations

from endury.common import clock
from endury.domain.analysis.zones import compute_all_zones
from endury.planner.activity.materialize import (
    assignment_target_tss,
    materialized_swim_meters,
    workout_total_meters,
)
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    FlatStep,
    fmt_distance,
    fmt_seconds,
)
from endury.training.load import LoadMetricContext

__all__ = [
    "assignment_target_tss",
    "build_synthetic_resolver",
    "card_text",
    "materialized_swim_meters",
    "workout_total_meters",
]

# Synthetic athlete profile: fixed thresholds so catalog/report
# materialization doesn't need a real athlete record.
_SYNTHETIC_RUNNING_THRESHOLD_PACE_MIN_PER_KM = 4.2
_SYNTHETIC_RUNNING_LTHR_BPM = 166
_SYNTHETIC_RUNNING_FTP_WATTS = 303.0
_SYNTHETIC_CYCLING_FTP_WATTS = 303.0
_SYNTHETIC_CYCLING_LTHR_BPM = 158
# The planner's DEFAULT_SWIM_CSS_SEC_PER_100M by construction: scenarios
# don't set an athlete CSS, so swim hours are budgeted at that default — the
# render resolver has to swim at the same CSS or materialized distances
# drift from the authored menu metres. ``steps`` re-exports the constant
# from ``endury/planner/activity/config.py``.
_SYNTHETIC_SWIM_CSS_SEC_PER_100M = DEFAULT_SWIM_CSS_SEC_PER_100M


def build_synthetic_resolver() -> LoadMetricContext:
    """Return a synthetic athlete resolver for catalog materialization."""
    training_zones = compute_all_zones(
        lthr_bpm=_SYNTHETIC_RUNNING_LTHR_BPM,
        cycling_ftp_watts=int(_SYNTHETIC_CYCLING_FTP_WATTS),
        running_power_ftp=int(_SYNTHETIC_RUNNING_FTP_WATTS),
        threshold_pace_min_per_km=_SYNTHETIC_RUNNING_THRESHOLD_PACE_MIN_PER_KM,
        css_sec_per_100m=_SYNTHETIC_SWIM_CSS_SEC_PER_100M,
        cycling_lthr_bpm=_SYNTHETIC_CYCLING_LTHR_BPM,
        source="manual",
    )
    return LoadMetricContext(
        running_threshold_pace_min_per_km=_SYNTHETIC_RUNNING_THRESHOLD_PACE_MIN_PER_KM,
        running_lthr_bpm=_SYNTHETIC_RUNNING_LTHR_BPM,
        running_ftp_watts=_SYNTHETIC_RUNNING_FTP_WATTS,
        cycling_ftp_watts=_SYNTHETIC_CYCLING_FTP_WATTS,
        cycling_lthr_bpm=_SYNTHETIC_CYCLING_LTHR_BPM,
        swim_css_sec_per_100m=_SYNTHETIC_SWIM_CSS_SEC_PER_100M,
        training_zones=training_zones,
        threshold_updated_at=clock.today(),
    )


def _card_step(row: FlatStep) -> str:
    size = (
        fmt_distance(row.distance_m) if row.distance_m else fmt_seconds(row.duration_s)
    )
    return f"{size} {row.primary_label}"


def card_text(rows: list[FlatStep]) -> str:
    """A built workout as its card reads, a repeat block as ``Nx (a / b)``."""
    parts: list[str] = []
    i = 0
    while i < len(rows):
        n = rows[i].iterations
        j = i + 1
        while n > 1 and j < len(rows) and rows[j].iterations == n:
            j += 1
        block = " / ".join(_card_step(r) for r in rows[i:j])
        parts.append(f"{n}x ({block})" if n > 1 else block)
        i = j
    return " → ".join(parts)
