"""Per-session hours→TSS pricing for the macro QP and the CTL seed.

A session's card is priced by its own activity: the materializer asks the
activity for the target TSS its hours imply (``assignment_target_tss``) and
the card carries what the built structure achieves (``achieved_tss``). The
QP's Banister state must carry that same load, so each session's hours are
priced here by the same two calls rather than by a week-wide TID average.
"""

from __future__ import annotations

from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import ActivityKey


def session_tss_per_hour(activity_key: ActivityKey, hours: float) -> float:
    """TSS per hour this activity realises when sized to *hours*.

    This is the linearisation of the activity's card price at *hours*: the
    QP evaluates it once at each session's menu target and uses it as a
    frozen coefficient on that session's hours, so the Banister recursion
    stays affine (R-MACRO-020). The resolver is ``None`` because the
    pipeline carries no athlete thresholds and must stay pure and
    deterministic. A zero-length session carries no load, so ``hours <= 0``
    prices at 0.0.

    Args:
        activity_key: The session's activity.
        hours: Session duration the price is evaluated at.

    Returns:
        Achieved TSS divided by *hours*, or 0.0 when *hours* is not positive.
    """
    if hours <= 0:
        return 0.0
    ensure_registry_loaded()
    act = get_activity(activity_key)
    return (
        act.achieved_tss(assignment_target_tss(act, hours, None), resolver=None) / hours
    )


__all__ = ["session_tss_per_hour"]
