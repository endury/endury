"""TSS-per-hour TID bridge for race events and a session-less opening week.

The planner plans in training hours; the L3 macro planner's Banister
CTL/ATL dynamics need TSS-dimensioned impulses instead. Sessions are priced
by their own activity (``session_pricing.py``); this bridge prices what has
no activity — a race event's fixed hours — and seeds CTL when the opening
week has no sessions. Each week's TID fractions are known before the QP runs
(looked up from ``tid_tables.py``), so ``tss_per_hour`` is a constant
coefficient rather than a QP variable — keeping the constraints affine.

TSS ≈ IF² x hours x 100 for any sport, so the weighted average across zones
is ``Σ_z (TID_fraction[z] x IF_midpoint[z]² x 100)``. The zone IF midpoints
(``ZONE_TSS_PER_HOUR``, normalised to threshold = 1.0; band boundaries are
``TID_LOW_IF_THRESHOLD`` / ``TID_HIGH_IF_THRESHOLD`` in
``foundations/types.py``) are stable across endurance sports when IF is
normalised to each sport's own threshold (FTP for cycling, LTHR/CSS
equivalents for running/swimming).
"""

from __future__ import annotations

from endury.planner.foundations.types import TIDFractions

# ---------------------------------------------------------------------------
# Zone TSS-per-hour constants
# ---------------------------------------------------------------------------

ZONE_TSS_PER_HOUR: dict[str, float] = {
    "z_low": 0.65**2 * 100,  # IF=0.65 → 42.25 TSS/h
    "z_mid": 0.875**2 * 100,  # IF=0.875 → 76.5625 TSS/h
    "z_high": 1.05**2 * 100,  # IF=1.05 → 110.25 TSS/h
}
"""Estimated TSS generated per hour in each intensity band.

Based on ``TSS = IF² x hours x 100`` with sport-agnostic IF midpoints.
"""


def tss_per_hour(fractions: TIDFractions) -> float:
    """Estimate TSS generated per training hour given a TID zone distribution.

    Used by L3 to convert race-event hours into TSS impulses for the
    Banister CTL/ATL state equations, and by the CTL seed when the opening
    week has no sessions.  The fractions are pre-computed from
    ``tid_tables.get_tid_fractions`` before the QP runs, making this a
    constant coefficient (not a variable) in each QP instantiation.

    Args:
        fractions: TID zone fractions.  Must sum to ≈ 1.0; any deviation is
            absorbed proportionally by the linear combination.

    Returns:
        Estimated TSS per training hour (typically 42-110 depending on
        intensity mix).
    """
    return (
        fractions.z_low * ZONE_TSS_PER_HOUR["z_low"]
        + fractions.z_mid * ZONE_TSS_PER_HOUR["z_mid"]
        + fractions.z_high * ZONE_TSS_PER_HOUR["z_high"]
    )


__all__ = [
    "ZONE_TSS_PER_HOUR",
    "tss_per_hour",
]
