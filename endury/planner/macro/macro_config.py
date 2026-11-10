"""Tunable scalar parameters for the L3 macro planner.

These are policy knobs - change them to adjust Banister dynamics, ramp limits,
and solver behaviour without touching algorithm code.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Banister decay constants (weekly granularity)
#
# Coggan's practical 42-day/7-day time constants for the fitness/fatigue
# impulse-response model (docs/research/endurance_methodology.md, The
# Banister Impulse-Response Model).
# ---------------------------------------------------------------------------

# CTL (fitness): 42-day time constant → exp(-7/42)
CTL_DECAY_WEEKLY: float = math.exp(-7 / 42)  # ≈ 0.8465

# ATL (fatigue): 7-day time constant → exp(-7/7)
ATL_DECAY_WEEKLY: float = math.exp(-7 / 7)  # ≈ 0.3679

# The same two time constants at DAY resolution (``*_WEEKLY == *_DAILY ** 7``).
# The QP's Banister state is weekly, but a race falls on one specific weekday,
# so the freshness term steps the week-entry state forward day by day to the
# race-day instant (see ``race_day_carryover`` / ``race_day_session_weights``
# in ``macro_planner``).
CTL_DECAY_DAILY: float = math.exp(-1 / 42)  # ≈ 0.9764
ATL_DECAY_DAILY: float = math.exp(-1 / 7)  # ≈ 0.8669

# ---------------------------------------------------------------------------
# Load-shape limits (L3 owns these; same defaults as anchor_config)
# ---------------------------------------------------------------------------

RECOVERY_CEILING_FACTOR: float = 0.60  # recovery ceiling as fraction of last LOAD
POST_RECOVERY_BOUNCE_BACK: float = 1.05  # max rebound after recovery (x LOAD ref)
TAPER_DECAY_FACTOR: float = 0.85  # LOAD → TAPER entry step-down
RACE_RECOVERY_CEILING_FACTOR: float = 0.60  # base factor for graduated ramp-back

# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

SOLVER_NAME: str = "CLARABEL"  # Interior-point conic solver (handles QP natively)
SOLVER_VERBOSE: bool = False

# ---------------------------------------------------------------------------
# Quadratic penalty weights (QP objective)
# ---------------------------------------------------------------------------

# Freshness band miss: λ_fresh · Σ (max(0, floor - TSB) + max(0, TSB - ceiling))²
# Units: per (daily-TSB)².  The QP builds the freshness expression on the
# COMBINED (unweighted-sum) PMC divided by DAILY_TSB_DIVISOR: a miss of the
# TSB / CTL floor is measured as the daily TSB points it leaves short, the
# same language the athlete's trends view uses.
#
# The floor keeps the athlete out of sustained negative TSB (see
# docs/research/endurance_methodology.md, TSB Zones and The Overtraining
# Continuum). A 3 TSB/d miss costs 90 in penalty, well above the ~0.4
# tracking cost of the race-week volume that would close it, so freshness
# leads while the session bounds still cap how far the menu prescription
# can bend.
LAMBDA_FRESH: float = 10.0

# Weekly-TSS → daily-TSB divisor.  The Banister state inside the QP is
# weekly-TSS dimensioned; the freshness targets, the literature TSB zones
# (endurance_methodology.md "TSB Zones"), and the app's trends view are all
# daily.  Every freshness comparison converts through this constant.
#
# It is also the impulse bridge in the day-stepped race-day expression: one
# day's TSS raises the weekly-TSS state by ``7 · (1 - decay) · tss`` because
# the state is 7x its daily counterpart.
DAILY_TSB_DIVISOR: float = 7.0

# Width of the race-day freshness band above the floor
# (``macro_tables.RACE_DAY_FRESHNESS_RATIO``), in TSB / CTL. 0.27 + 0.22 puts
# the ceiling at 0.49, the upper edge of the band measured at
# the race-day instant (docs/research/endurance_methodology.md, TSB Zones).
# The ceiling matters: without it the freshness term is floor-only and
# unbounded above, letting plans over-taper into detraining while every
# floor-only rule still reads green.
FRESHNESS_CEILING_MARGIN: float = 0.22

# Ceiling half of the freshness band, weighted separately and far softer
# than LAMBDA_FRESH. The two errors are not symmetric: arriving BELOW the
# floor means racing fatigued (a genuine coaching failure worth bending the
# plan for), while arriving above the ceiling means merely over-rested.
# The only lever the QP has for shedding freshness is adding volume near the
# race, and the race week itself is menu-pinned (min == max == target on
# every taper session), so any volume a stronger ceiling forces in comes
# from one week further out, where it buys far less freshness per TSS. A
# ceiling weight near LAMBDA_FRESH's magnitude inflates taper sessions
# until the longest long ride/run migrates out of PEAK, breaking the
# requirement that it fall 4-6 weeks before the race
# (`docs/specs/planner/behavioral.md`, R-PP-002). The over-freshness that
# remains above the band is a taper-shape question for the menus to own,
# not something the QP should distort the prescription to chase.
LAMBDA_FRESH_CEILING: float = 0.01

# Volume smoothing: λ_smooth · Σ_k Σ_s (H[k+1,s] - H[k,s])²
# Units: per hours².  Week-to-week deltas are typically 0-2 h on the menu.
# Tuned to leave the menu's natural LOAD→LOAD ramp (~1 h) alone (cost ≈ 1)
# while punishing larger spikes (Δ=2 h → cost ≈ 4 per sport).  Must be
# small relative to the tracking term so the menu prescription is preserved.
LAMBDA_SMOOTH: float = 0.5

# Weekly-hours cap overshoot: λ_cap · Σ_k max(0, H[k] - cap), a LINEAR
# hinge over LOAD/RECOVERY weeks.  Units: per hour.  Linear, not squared,
# because the athlete named a number: the first hour over must already
# cost more than what the rest of the objective is willing to pay to keep
# it.  Soft only in that the session floors are hard BOUNDS the penalty
# cannot buy — a cap under them is absorbed, not made infeasible.
#
# Sized against the term that actually fights it, which is not tracking
# but FRESHNESS. Shedding volume drains race-day CTL, and TSB ≈ CTL at
# the race-day instant (ATL has decayed), so a lower plan reads as *less*
# fresh and λ_fresh = 10 pushes back with marginal cost ≈ 19·deficit per
# hour — far above tracking's ≈ 1 per hour — so λ_cap has to out-bid that.
# At this weight the residual overshoot is one rounding step: the QP lands
# on the cap and `_round_hours` nudges a session back up by up to 5 min.
LAMBDA_CAP: float = 100.0

# CTL tiebreaker weight: ε · Σ_s w_s · CTL_s[k_race], subtracted from the
# tracking-first minimisation so that among equivalent tracking solutions
# the solver prefers the one with higher race-week fitness.
#
# Sized so the tiebreaker never overrides a meaningful tracking deviation.
# A typical race-week CTL sum (Σ w_s · CTL_s) is 150-250 weekly-TSS and a
# single session's tracking cost at 0.25 h deviation is 0.0625; at EPS=1e-3
# the tiebreaker is ~0.2 per session vs. 0.0625 - small enough that the
# tracking term decides, large enough to break ties cleanly.
EPS_CTL: float = 1e-3


__all__ = [
    "ATL_DECAY_DAILY",
    "ATL_DECAY_WEEKLY",
    "CTL_DECAY_DAILY",
    "CTL_DECAY_WEEKLY",
    "DAILY_TSB_DIVISOR",
    "EPS_CTL",
    "FRESHNESS_CEILING_MARGIN",
    "LAMBDA_CAP",
    "LAMBDA_FRESH",
    "LAMBDA_FRESH_CEILING",
    "LAMBDA_SMOOTH",
    "POST_RECOVERY_BOUNCE_BACK",
    "RACE_RECOVERY_CEILING_FACTOR",
    "RECOVERY_CEILING_FACTOR",
    "SOLVER_NAME",
    "SOLVER_VERBOSE",
    "TAPER_DECAY_FACTOR",
]
