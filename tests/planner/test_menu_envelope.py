"""Catalog TSS windows MUST span the authored session-menu envelope.

The single enforced source of truth for the window-spans-menus invariant
(R-SESS-B-006/007/008): the catalog deliberately does not import the menus
at runtime (layering), so this test recomputes every activity's authored
menu envelope — all goals x phases x modes x ambitions plus
``MAINTENANCE_MENUS``, swim metres converted exactly as the generator does
(``_swim_minutes`` at ``DEFAULT_SWIM_CSS_SEC_PER_100M``) — and
checks, at the envelope extremes plus the generator's 5-minute rounding
headroom (+-2.5 min), that:

1. the duration-derived TSS lies inside the catalog ``[tss_min, tss_max]``
   window, so lattice projection never resizes a prescribed session; and
2. the materialized card duration tracks the solved duration to within the
   shape's own quantization (exact for continuous shapes, one rep's
   wall-clock for flexible-rep shapes, plus swim rounding).

Derivation runs on an unclamped copy of each activity because the window
check would otherwise be circular: ``duration_minutes_exact`` clamps at
``tss_max``, so a too-narrow window distorts the very inversion used to
detect it.

Fixed-protocol activities (``*_test``) are exempt by design: their
``tss_from_duration`` stays ``None`` and their cards never resize with
solved hours.
"""

import dataclasses

import pytest

from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.shapes.aquatic import (
    SwimCssIntervalActivity,
    SwimDrillSwimActivity,
    SwimLinearDistanceActivity,
)
from endury.planner.activity.shapes.land import IntervalPiecewiseActivity
from endury.planner.foundations.types import ActivityKey, AmbitionLevel, SportType
from endury.planner.sessions.menus import (
    BLUEPRINTS,
    MAINTENANCE_MENUS,
    get_session_menu,
)
from endury.planner.sessions.session_generator import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    _swim_minutes,
)
from endury.training.load import LoadMetricContext, pace_sec_per_100m_from_css

# The generator rounds each session's nominal minutes to the nearest 5
# (session_generator.py), so solved durations can overshoot the authored
# envelope by up to half a step.
_ROUNDING_HEADROOM_MIN = 2.5

# Same CSS for budgeting and rendering (R-SESS-B-006).
_RESOLVER = LoadMetricContext(swim_css_sec_per_100m=DEFAULT_SWIM_CSS_SEC_PER_100M)


def _authored_envelopes() -> dict[ActivityKey, tuple[float, float]]:
    """Min/max prescribable session minutes per activity across all menus.

    Mirrors the generator's sizing law: swim bounds are authored in metres
    and convert through ``_swim_minutes`` for the swim each week books, so a
    rotation spec converts its metres for every key in the rotation.
    """
    all_specs = []
    for goal, blueprint in BLUEPRINTS.items():
        for phase, mode_key in blueprint.phases:
            for ambition in AmbitionLevel:
                all_specs.extend(get_session_menu(goal, phase, mode_key, ambition))
    for specs in MAINTENANCE_MENUS.values():
        all_specs.extend(specs)

    css = DEFAULT_SWIM_CSS_SEC_PER_100M
    envelopes: dict[ActivityKey, tuple[float, float]] = {}
    for spec in all_specs:
        for key in spec.rotation or (spec.activity_key,):
            if spec.sport == SportType.SWIMMING:
                lo = _swim_minutes(key, spec.min_meters, css)
                hi = max(_swim_minutes(key, spec.max_meters, css, within=True), lo)
            else:
                lo, hi = float(spec.min_minutes), float(spec.max_minutes)
            prev = envelopes.get(key)
            envelopes[key] = (
                (lo, hi) if prev is None else (min(prev[0], lo), max(prev[1], hi))
            )
    return envelopes


ensure_registry_loaded()
_ENVELOPES = _authored_envelopes()
_MENU_KEYS = sorted(_ENVELOPES, key=lambda k: k.value)
_EXEMPT_FIXED_PROTOCOLS = {
    key
    for key in _MENU_KEYS
    if get_activity(key).tss_from_duration(60.0, resolver=_RESOLVER) is None
}
_DRIVEN_KEYS = [key for key in _MENU_KEYS if key not in _EXEMPT_FIXED_PROTOCOLS]


def _duration_tolerance_minutes(act) -> float:
    """Return the shape's intrinsic duration quantization in minutes.

    Continuous shapes invert exactly; flexible-rep shapes quantize by one
    rep's wall-clock (R-SESS-B-008 acceptance bound); swim shapes add the
    R-DU-003 rounding absorber (the cooldown shifts by up to +-50 m so the
    total lands on a 100 m boundary, and linear swims round to 100 m).
    """
    css = DEFAULT_SWIM_CSS_SEC_PER_100M
    if isinstance(act, IntervalPiecewiseActivity):
        return act.rep_work_max + act.rep_rest_min
    if isinstance(act, SwimCssIntervalActivity):
        rep_seconds = (
            act.rep_meters_max * pace_sec_per_100m_from_css(css, act.if_work) / 100.0
            + act.rep_rest_seconds
        )
        cooldown_shift_seconds = pace_sec_per_100m_from_css(css, act.if_cooldown)
        return (rep_seconds + cooldown_shift_seconds) / 60.0
    if isinstance(act, SwimDrillSwimActivity):
        rep_seconds = (
            act.drill_meters * pace_sec_per_100m_from_css(css, act.if_drill)
            + act.swim_meters * pace_sec_per_100m_from_css(css, act.if_swim)
        ) / 100.0
        cooldown_shift_seconds = pace_sec_per_100m_from_css(css, act.if_cooldown)
        return (rep_seconds + cooldown_shift_seconds) / 60.0
    if isinstance(act, SwimLinearDistanceActivity):
        return pace_sec_per_100m_from_css(css, act.intensity_factor) / 60.0
    return 0.01


def test_every_menu_activity_is_duration_driven_or_fixed_protocol() -> None:
    """Only the fixed test protocols are exempt from duration-first sizing."""
    assert {
        ActivityKey.BIKE_FTP_TEST,
        ActivityKey.RUN_THRESHOLD_TEST,
        ActivityKey.SWIM_CSS_TEST,
    } == _EXEMPT_FIXED_PROTOCOLS


@pytest.mark.parametrize("activity_key", _DRIVEN_KEYS)
def test_window_spans_authored_menu_envelope(activity_key: ActivityKey) -> None:
    """Derived TSS at the envelope extremes stays inside the catalog window."""
    act = get_activity(activity_key)
    free = dataclasses.replace(act, tss_min=0.0, tss_max=10_000.0)
    lo, hi = _ENVELOPES[activity_key]
    for requested in (
        max(lo - _ROUNDING_HEADROOM_MIN, 1.0),
        hi + _ROUNDING_HEADROOM_MIN,
    ):
        derived = free.tss_from_duration(requested, resolver=_RESOLVER)
        assert act.tss_min - 1e-9 <= derived <= act.tss_max + 1e-9, (
            f"{activity_key.value}: {requested:.1f} min derives {derived:.2f} TSS, "
            f"outside window [{act.tss_min}, {act.tss_max}] — projection would "
            f"resize the prescribed session (widen the catalog window)"
        )


@pytest.mark.parametrize("activity_key", _DRIVEN_KEYS)
def test_card_duration_tracks_solved_duration(activity_key: ActivityKey) -> None:
    """R-SESS-B-008: the built card realizes the solved duration.

    The realized duration must match the requested duration clamped to the
    shape's own realizable span (menus may author beyond the rep lattice or
    below the warm-up + cool-down scaffold floor), within the shape's
    quantization tolerance.
    """
    act = get_activity(activity_key)
    free = dataclasses.replace(act, tss_min=0.0, tss_max=10_000.0)
    lo, hi = _ENVELOPES[activity_key]
    tolerance = _duration_tolerance_minutes(act)
    probes = (max(lo - _ROUNDING_HEADROOM_MIN, 1.0), hi + _ROUNDING_HEADROOM_MIN)
    span = sorted(
        free.duration_minutes_exact(
            free.tss_from_duration(requested, resolver=_RESOLVER), resolver=_RESOLVER
        )
        for requested in probes
    )
    for requested in probes:
        derived = act.tss_from_duration(requested, resolver=_RESOLVER)
        projected = act.project_target_tss(derived, resolver=_RESOLVER)
        realized = act.duration_minutes_exact(projected, resolver=_RESOLVER)
        expected = min(max(requested, span[0]), span[1])
        assert abs(realized - expected) <= tolerance + 1e-6, (
            f"{activity_key.value}: solved {requested:.1f} min renders a "
            f"{realized:.1f} min card (expected {expected:.1f} +- {tolerance:.1f})"
        )
