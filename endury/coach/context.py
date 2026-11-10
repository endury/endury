"""System context builder for the coach agent.

Builds the LLM system prompt from athlete profile, the athlete-memory
document, recent wellness, the latest completed session, the active plan,
a static app-facts section (planner horizon policy, short-notice join
floors, tune-up race rules, the controls the athlete actually sees), and
— for a trail or maintenance goal — the sport-doctrine block that states
the plan's own design rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.coach.dates import build_day_table
from endury.coach.prompt import build_system_prompt
from endury.coach.tools.activity_tools import delivered_view
from endury.common import clock
from endury.common.memory_notes import due_check_ins, due_reviews
from endury.domain.models.athlete import AthleteProfile
from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import activity_date, activity_start_clock
from endury.intervals.push import AD_HOC_EXTERNAL_ID_PREFIX
from endury.planner.foundations.maintenance_tables import MAINTENANCE_WEEKLY_HOURS
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PlanMaturity,
)
from endury.planner.macro.macro_tables import RACE_DAY_FRESHNESS_RATIO
from endury.planner.phase.phase_config import (
    B_RACE_RECOVERY_MIN_HOURS,
    BC_EARLIEST_WEEK,
    BC_PROXIMITY_TO_A_RACE_WEEKS,
    BC_WINDOW_FRACTION,
    MAX_HORIZON_WEEKS,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planning.activity_match import link_view
from endury.planning.generate_plan import min_join_weeks
from endury.planning.offered_plans import offered_names
from endury.planning.trail_band import band_thresholds_prose
from endury.wiring.config import CoachConfig

logger = logging.getLogger(__name__)

#: How far back the "latest completed session" line looks.
LATEST_SESSION_WINDOW_DAYS = 7

#: How far ahead the "scheduled workouts" line looks.
SCHEDULED_WORKOUTS_WINDOW_DAYS = 14

#: How many assistant turns the profile-intake nudge survives. Past that the
#: coach has had its chances; asking again mid-conversation reads as an
#: interrogation.
PROFILE_NUDGE_MAX_TURNS = 6

#: Jargon level to pitch at when the experience level is unknown — read off
#: the plan's ambition tier instead of asking the athlete for it.
_PITCH_BY_AMBITION: dict[str, str] = {
    "basic": "beginner",
    "moderate": "intermediate",
    "competitive": "advanced",
}

#: The pitch used when no plan (or no readable tier) says otherwise.
DEFAULT_EXPERIENCE_PITCH = "intermediate"

# Language instructions

_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "en": "Respond in English.",
    "de": "Antworte auf Deutsch. Kommuniziere ausschließlich auf Deutsch.",
    "fr": "Réponds en français. Communique exclusivement en français.",
    "es": "Responde en español. Comunícate exclusivamente en español.",
    "it": "Rispondi in italiano. Comunica esclusivamente in italiano.",
    "pt": "Responde em português. Comunica exclusivamente em português.",
    "nl": "Antwoord in het Nederlands. Communiceer uitsluitend in het Nederlands.",
    "pl": "Odpowiadaj po polsku. Komunikuj się wyłącznie po polsku.",
    "sv": "Svara på svenska. Kommunicera uteslutande på svenska.",
    "da": "Svar på dansk. Kommuniker udelukkende på dansk.",
    "no": "Svar på norsk. Kommuniser utelukkende på norsk.",
    "fi": "Vastaa suomeksi. Kommunikoi yksinomaan suomeksi.",
    "zh": "用中文回复。只用中文交流。",
    "ja": "日本語で返答してください。日本語のみで会話してください。",
    "ko": "한국어로 답변하세요. 한국어로만 소통하세요.",
    "ru": "Отвечай на русском языке. Общайся исключительно на русском.",
    "ar": "أجب باللغة العربية. تواصل باللغة العربية حصراً.",
    "tr": "Türkçe yanıt ver. Yalnızca Türkçe iletişim kur.",  # noqa: RUF001
    "cs": "Odpovídej česky. Komunikuj výhradně česky.",
    "hu": "Válaszolj magyarul. Kizárólag magyarul kommunikálj.",
}


# App facts


def _maturity_marker(maturity: PlanMaturity) -> str:
    """Return the parenthesised marker for an Experimental goal, else empty."""
    return f" ({maturity.value})" if maturity is PlanMaturity.EXPERIMENTAL else ""


def _maintenance_volumes() -> str:
    """Render the per-tier maintenance weekly-hour targets as one clause.

    Generated off ``MAINTENANCE_WEEKLY_HOURS`` the same way the horizon
    spans are generated off ``GOAL_PROFILES``, so the coach can never
    quote a volume the block was not built to. Goals sharing a tier row
    (running and trail) are grouped rather than repeated.

    Returns:
        The clause, e.g. ``"running/trail 3/4.5/6 h, triathlon 5/8.5/10 h"``.
    """
    grouped: dict[tuple[float, ...], list[str]] = {}
    for goal, tiers in MAINTENANCE_WEEKLY_HOURS.items():
        grouped.setdefault(tuple(tiers.values()), []).append(
            goal.value.removesuffix("_maintenance")
        )
    return ", ".join(
        f"{'/'.join(names)} {'/'.join(f'{hours:g}' for hours in row)} h"
        for row, names in grouped.items()
    )


def build_app_knowledge() -> str:
    """Build the static "how this app plans" section of the system prompt.

    Gives the coach the planner's own horizon policy — per-goal minimum and
    recommended-maximum prep weeks, read straight off ``GOAL_PROFILES`` so
    the coach's race-timing advice always matches what the plan wizard
    enforces and warns about. An Experimental goal is marked, so the coach
    can caveat it honestly instead of guessing which ones those are. The
    short-notice join floors (``min_join_weeks``) and the B/C tune-up window rules are
    read off the same config the wizard validates against, so the coach
    can tell an athlete whether a race fits as a B or only as a C.

    It closes with what the athlete actually sees — the phone's plan pill
    and the desktop's read-only label, the wizard's own fields and each
    shell's real button labels — plus two non-obvious behaviours: a plan
    starts on *this* week's Monday with the elapsed days dropped, and a
    maintenance block holds its tier's volume rather than ramping. It also
    states plainly that there is no reminder mechanism to promise.

    Returns:
        The section body (no heading).
    """
    spans = ", ".join(
        f"{goal.value.removeprefix('running_').replace('_', ' ')} "
        f"{profile.min_horizon_weeks}-{profile.max_recommended_weeks}"
        f"{_maturity_marker(profile.maturity)}"
        for goal, profile in GOAL_PROFILES.items()
        if profile.max_recommended_weeks is not None
    )
    blocks = ", ".join(
        f"{goal.value.removesuffix('_maintenance')}{_maturity_marker(profile.maturity)}"
        for goal, profile in GOAL_PROFILES.items()
        if profile.max_recommended_weeks is None
    )
    floors = ", ".join(
        f"{goal.value.removeprefix('running_').replace('_', ' ')} "
        f"{min_join_weeks(goal)}"
        for goal, profile in GOAL_PROFILES.items()
        if profile.max_recommended_weeks is not None
    )
    return (
        "You build a plan yourself with create_plan — it previews, and the "
        "athlete applies the preview from its card; the wizard runs the "
        "exact same pipeline by hand. You cannot delete a plan. A new plan "
        f"can be built only for these: {offered_names()}. create_plan "
        "refuses any other goal or tier and the wizard does not show it, so "
        "never promise one: say what is on offer. A plan the athlete "
        "already has keeps working and replans, and a B or C race may be of "
        "any type. Sensible "
        "prep horizons per goal, in weeks (full-preparation minimum to "
        f"recommended maximum): {spans}. Maintenance blocks "
        f"({blocks}) run 4-16 weeks. A goal marked (experimental) is built "
        "from training research and its sessions have not yet been "
        "reviewed one by one, and building its plan needs the "
        "athlete's confirmation — say so if the athlete asks how solid "
        "their plan is. A plan can be Experimental for its conditions even "
        "when its goal is not (a part-way join, an unusual horizon, or "
        "recent training below what was checked); create_plan reports that "
        "with experimental_plan. When an athlete's race sits further out "
        "than the recommended maximum, advise starting with a maintenance block (or "
        "a B-race season) and building the race plan once inside the "
        "window — a single continuous prep loses its edge beyond it, and "
        f"one plan can span at most {MAX_HORIZON_WEEKS} weeks. "
        "A race closer than its minimum is NOT refused: create_plan "
        "(accept_short_horizon) and the wizard both offer a "
        "short-notice plan that joins the full preparation part-way "
        "through (skipping the early base weeks, sized from the athlete's "
        "own recent hours) once the athlete applies it. Below the goal's "
        "join floor it refuses outright and the race can only be a tune-up "
        f"under a later goal. Join floors, in weeks to race day: {floors}. "
        "Tune-up races: a plan has exactly one A race; B and C races must "
        f"fall before it, not in its first {BC_EARLIEST_WEEK} weeks, not "
        f"within {BC_PROXIMITY_TO_A_RACE_WEEKS} weeks of it, and not inside "
        "its peak/taper. A tune-up takes only its own race week out of the "
        "A-race build: the week before it is a normal training week, and so "
        "is the week after, except after a B race longer than "
        f"{B_RACE_RECOVERY_MIN_HOURS:g} hours (a marathon, a trail 20K or "
        "longer, a half- or full-distance triathlon), which is followed by one easier "
        "recovery week. A B race week is a short race-week taper so the athlete "
        "races it reasonably fresh; a C race week is a lighter training "
        "week with the race in it. Two tune-ups may not share a week, and "
        "all tune-up race weeks together may use at most "
        f"{BC_WINDOW_FRACTION:.0%} of the pre-taper weeks. "
        "A maintenance block takes a tune-up race in any of its weeks from "
        "next week on: a B race week is that race's own mini-taper, a C "
        "race week the block's lighter week with the race in it; add it "
        "with add_race_to_plan, never a hand-made taper. "
        "A plan tapers to arrive at its A race with form at least "
        f"{RACE_DAY_FRESHNESS_RATIO['A']:.0%} of fitness and at a B race "
        f"at least {RACE_DAY_FRESHNESS_RATIO['B']:.1%}; a C race is trained "
        "through. "
        # The athlete-visible control names, so the coach never points at a
        # button that does not exist.
        "What the athlete sees: on the phone the app bar's plan pill "
        '(top-left) shows the goal or "Create a plan" and opens the plan '
        "panel; on desktop that top-left text is a read-only label "
        '("No active plan" when there is none) and the panel opens from the '
        'plan card\'s "Edit" (or "Create") button. The panel is titled '
        '"Choose a plan" or "Edit plan". The wizard '
        "picks a family (Triathlon / Running / Trail Running), a goal and a "
        "tier (Basic / Moderate / Competitive; only the plans on offer "
        "appear), then an A-race date, or a 4-16-week block length for a "
        'maintenance goal, plus optional tune-up races ("+ Add": a race type, '
        'a date, and B or C). A trail race can add an optional "Race profile" '
        '("distance km", "climb m") suggesting a band by km-effort (km + '
        f"climb m / 100; {band_thresholds_prose()}). The button reads "
        '"Preview <goal> plan" / '
        '"Preview N-week maintenance block"; the preview offers "Discard" '
        'and "Apply plan" ("Apply" on the phone), and applying pushes to '
        'intervals.icu. An active plan\'s panel offers "Delete plan", with '
        '"Replan from next Monday" on the phone and a "Replan" button on the '
        'desktop plan card; an ended plan offers "Start a new plan" and '
        '"Archive plan". '
        "There is no pause button — pausing goes through you. "
        "A plan made on a Monday or Tuesday starts on THIS week's Monday "
        "with the elapsed days dropped; from Wednesday on it starts next "
        "Monday, so week 1 keeps its Tuesday test. "
        "Maintenance blocks converge on a fixed weekly volume "
        f"({_maintenance_volumes()}, for Basic / Moderate / Competitive): an "
        "athlete at or above it trains at it from the first weeks (a little "
        "under while the long sessions grow into their range), one below it "
        "is ramped up to it and then held — so pick the tier against "
        "measured hours. "
        "Session targets come from the athlete's intervals.icu sport "
        "settings: pace targets appear only once a threshold pace is set "
        "there (Settings → Sports → Running), heart-rate bands need an LTHR, "
        "power needs an FTP — with none set a step shows its zone number and "
        'RPE only, so "why is there no pace?" is answered by setting one. '
        "You can set one from the chat with set_athlete_thresholds (an Apply "
        "card): a recent race time (estimate_threshold_from_race) or the "
        "lactate threshold the athlete's watch shows is enough, and the "
        "booked test is a check, not a prerequisite. "
        "You have no reminder or notification mechanism and cannot ping the "
        "athlete later: say so, and put the date in the athlete-memory "
        'document instead, as a "- Next check-in YYYY-MM-DD: <why>" bullet '
        'under "## Notes". That line is surfaced back to you on the first '
        "turn on or after that date — it is the only reminder there is."
    )


# Sport doctrine

#: The trail core every UTMB-category plan is built on — the rules the coach
#: must not talk an athlete out of. Sources are cited per rule cluster below;
#: the athlete-visible text carries no citations.
_TRAIL_CORE: tuple[str, ...] = (
    # Long run — docs/research/trail_running.md, The Long Run — Time on Feet
    # (prescribed in time, not distance; sized off recent load, not race
    # distance; 1-2/month near the ceiling; the 4 h fuelling floor; gut
    # training rides on the long run).
    "- Long runs are prescribed in time on feet, never in kilometres — grade "
    "makes distance meaningless.",
    "- Size the long run off the athlete's recent training load, not off race "
    "distance. Only 1-2 a month sit near their ceiling; the rest run 50-75% "
    "of it.",
    "- A session of 4 h or more is the fuelling-rehearsal floor: race foods, "
    "race carry, something in the gut every ~45 min.",
    "- Under 4 h the long run is durability work — do not grade it on nutrition.",
    # Vertical gain — docs/research/trail_running.md, Vertical Gain (~80% of
    # quality work belongs on the climb, effort not pace; vert adaptation is
    # easy-come-easy-go, with the final 8 weeks as the window).
    "- Steer every climb by RPE, HR, cadence and form. Pace on grade is noise.",
    "- Never judge an uphill by min/km, and never let a slow climbing split "
    "read to the athlete as a bad session.",
    "- Power-hiking steep grade is a trained race skill, not a failure — above "
    "the walk/run crossover it is the faster choice.",
    # Walk/run crossover — docs/research/trail_running.md, The walk/run
    # crossover: ~15.8 degrees (about 28% grade) at ordinary paces, moving
    # lower as pace slows. Stated so the coach quotes the measured figure
    # instead of inventing a grade.
    "- That crossover sits near 28% grade (about 16 degrees) at ordinary "
    "paces, and it moves lower the slower the athlete runs. Quote that number; "
    "never guess one.",
    # Back-to-backs — docs/research/trail_running.md, Back-to-Back Long Runs
    # (dosing; the thinnest evidence base in the document, hence the
    # caution).
    "- Back-to-back weekends: day 1 is the longer, harder run; day 2 is 25-50% "
    "shorter and deliberately easy.",
    "- At least 2 easy days follow the pair, and day 2 is where injuries "
    "happen — stay hypervigilant through it.",
    "- Pairs are never weekly. The plan spaces them 2-3 weeks apart on "
    "purpose; asked for one sooner, refuse and say why — this is the "
    "highest-risk, thinnest-evidenced structure in trail training.",
    # Descent / eccentric conditioning — docs/research/trail_running.md,
    # Downhill and Eccentric Conditioning (strength and architecture
    # stimulus with VO2max unchanged; the repeated-bout window — 5 days
    # adds nothing, more than 9 weeks washes out; recovery and cadence).
    "- Downhill sessions are eccentric strength work, not aerobic volume: "
    "4-5 days to full function, DOMS peaking 48-72 h out, and no aerobic gain "
    "at all. Budget them against the week's hard work.",
    "- At least 2 weeks between descent bouts — a 5-day gap buys damage and no "
    "extra protection.",
    "- At least 2-3 easy days after each bout, the last bout 3-4 weeks before "
    "race day, and none at all in the taper.",
    "- One bout's protection lasts ~9 weeks, so the few the plan schedules ARE "
    "the dose — never add descent work ad hoc.",
    # Taper — docs/research/trail_running.md, Taper: What is preserved
    # (intensity preserved, volume of intensity cut, terrain and vertical
    # specificity unchanged — vert decays fastest of anything the athlete
    # has built).
    "- The taper cuts volume, not terrain. Climb touches and trail surface "
    "stay in every taper week — a flat road taper strips the fastest-decaying "
    "adaptation the athlete owns.",
    "- Taper keeps the zone and cuts the minutes at it.",
    # Where "more" should go — docs/research/trail_running.md, Where
    # periodization sits in the priority stack (total volume outranks every
    # other lever; the hard-work budget is small and already spent).
    "- When the athlete wants more, spend it on easy volume and vert — not an "
    "extra descent bout, an extra pair, or a harder climb session.",
)


#: Athlete-facing name for each ambition tier — the wizard's ``basic`` is
#: "recreational" in docs/research/trail_running.md's per-tier tables.
_TIER_LABELS: dict[AmbitionLevel, str] = {
    AmbitionLevel.BASIC: "recreational",
    AmbitionLevel.MODERATE: "moderate",
    AmbitionLevel.COMPETITIVE: "competitive",
}


@dataclass(frozen=True)
class _TrailCategory:
    """One UTMB category's doctrine inputs.

    Attributes:
        label: Athlete-facing category name, e.g. ``"Trail 100K"``.
        vert_bands: Weekly vertical-gain band at peak per ambition tier.
        rules: The category-specific rules, rendered after the shared core.
    """

    label: str
    vert_bands: dict[AmbitionLevel, str]
    rules: tuple[str, ...]


@dataclass(frozen=True)
class _MaintenanceBlock:
    """One maintenance goal's doctrine inputs.

    Attributes:
        label: Athlete-facing family name, e.g. ``"Trail running"``.
        rules: The family-specific rules, rendered between the shared
            volume rules and the shared closing rules.
    """

    label: str
    rules: tuple[str, ...]


def _vert_line(category: _TrailCategory, ambition: AmbitionLevel | None) -> str:
    """Render the weekly-vert line for one category at one ambition tier.

    With a known tier only that tier's band is named, so the coach cannot
    quote a band the athlete's plan was never built to. Without one, every
    band is labelled with its tier — misattribution then takes a misreading,
    not a guess.

    Args:
        category: The trail category whose bands to render.
        ambition: The active plan's ambition tier, if it could be read.

    Returns:
        The single athlete-visible vert line.
    """
    if ambition is not None:
        return (
            f"- Weekly vert target at your tier: {category.vert_bands[ambition]} "
            "at peak. Never quote another tier's band."
        )
    tiers = ", ".join(
        f"{_TIER_LABELS[level]} {band}" for level, band in category.vert_bands.items()
    )
    return f"- Weekly vert at peak by tier: {tiers} — quote only the athlete's tier."


def _trail_doctrine(category: _TrailCategory, ambition: AmbitionLevel | None) -> str:
    """Compose one trail goal's doctrine: shared core plus its own rules.

    Args:
        category: The trail category to render.
        ambition: The active plan's ambition tier, if it could be read.

    Returns:
        The rendered doctrine block.
    """
    header = (
        f"{category.label} — these are the plan's own design rules. Do not "
        "advise against them, and do not add work the plan deliberately "
        "withholds."
    )
    return "\n".join(
        (header, *_TRAIL_CORE, _vert_line(category, ambition), *category.rules)
    )


#: Per-goal doctrine. Goals with no entry contribute nothing to the prompt —
#: a road or triathlon athlete pays zero tokens for this.
#: Weekly vertical bands are the per-category derived table in
#: docs/research/trail_running.md, Weekly vertical gain (m/week at peak);
#: back-to-back and descent cadences are its Back-to-back long runs and
#: downhill session cadence table.
SPORT_DOCTRINE: dict[GoalType, _TrailCategory] = {
    GoalType.TRAIL_20K: _TrailCategory(
        label="Trail 20K",
        vert_bands={
            AmbitionLevel.BASIC: "800-1,200 m",
            AmbitionLevel.MODERATE: "1,200-1,800 m",
            AmbitionLevel.COMPETITIVE: "1,800-2,500 m",
        },
        rules=(
            "- No back-to-backs at this distance — the plan authors none, and "
            "adding one is not an upgrade.",
            "- The race lasts ~3 h, so nothing here reaches the 4 h fuelling "
            "floor: gut training is a non-issue at 20K.",
            "- Intensity and climbing strength are the limiters, not volume. "
            "1-2 descent bouts in the final 8 weeks.",
        ),
    ),
    GoalType.TRAIL_50K: _TrailCategory(
        label="Trail 50K",
        vert_bands={
            AmbitionLevel.BASIC: "1,500-2,000 m",
            AmbitionLevel.MODERATE: "2,000-2,800 m",
            AmbitionLevel.COMPETITIVE: "2,800-3,500 m",
        },
        rules=(
            "- Back-to-backs are optional here, not necessary — if the athlete "
            "wants one it replaces a long run, it never sits on top of one.",
            "- Longest single run 3-4 h; the session that reaches 4 h is the "
            "one fuelling rehearsal worth protecting.",
            "- 2 descent bouts in the final 8 weeks, spaced ~3 weeks.",
        ),
    ),
    GoalType.TRAIL_100K: _TrailCategory(
        label="Trail 100K",
        vert_bands={
            AmbitionLevel.BASIC: "2,500-3,500 m",
            AmbitionLevel.MODERATE: "3,500-4,500 m",
            AmbitionLevel.COMPETITIVE: "4,500-6,000 m",
        },
        rules=(
            "- Back-to-backs are the defining structure: Saturday 3-4 h at "
            "race-matched climbing density, Sunday 2-2.5 h at RPE 4-5.",
            "- 2-3 pairs across the cycle, every 2-3 weeks, the biggest 4-5 "
            "weeks out; longest single run 4-5 h.",
            "- 2-3 descent bouts in the final 8-12 weeks, spaced 2-3 weeks, "
            "the last one 3-4 weeks out.",
        ),
    ),
}


#: Per-maintenance-goal doctrine. The block deliberately withholds
#: progression, so a coach that does not know its design rules advises the
#: athlete straight out of it. Sources: the three ``maintenance_*`` menu
#: blueprints and ``MAINTENANCE_WEEKLY_HOURS``.
MAINTENANCE_DOCTRINE: dict[GoalType, _MaintenanceBlock] = {
    GoalType.RUNNING_MAINTENANCE: _MaintenanceBlock(
        label="Running",
        rules=(
            "- One quality touch a week — fartlek, tempo, threshold and "
            "VO2max in rotation — is the whole intensity budget.",
        ),
    ),
    GoalType.TRAIL_MAINTENANCE: _MaintenanceBlock(
        label="Trail running",
        rules=(
            "- One quality touch a week, hills and sustained climbing "
            "leading the rotation, is the whole intensity budget.",
            "- The long session runs on trails: time on feet over real "
            "terrain, hike the climbs, steer by effort — the terrain sets "
            "the numbers, not pace.",
            # Same bands the plan's own coach_brief prints
            # (planning/coach_brief.py, _TRAIL_WEEKLY_VERT_M) —
            # roughly half the 20K race bands. Never quote another number.
            "- Weekly climb by tier: Basic 450-750 m, Moderate 700-1,100 m, "
            "Competitive 900-1,500 m — quote only the athlete's tier.",
        ),
    ),
    GoalType.TRIATHLON_MAINTENANCE: _MaintenanceBlock(
        label="Triathlon",
        rules=(
            "- Every sport keeps a weekly touch and one quality session of "
            "its own — swim technique, bike sweet-spot or threshold, run "
            "quality. Losing a sport for a week is what this block exists "
            "to prevent.",
        ),
    ),
}


def _maintenance_hours_line(goal: GoalType, ambition: AmbitionLevel | None) -> str:
    """Render the weekly-volume line for one maintenance goal and tier.

    Mirrors ``_vert_line``: with a known tier only that tier's target is
    named, so the coach cannot hold the athlete to a volume their block
    was never built to. Without one, every tier is labelled with the
    wizard's own name for it.

    Args:
        goal: The maintenance goal whose targets to render.
        ambition: The active plan's ambition tier, if it could be read.

    Returns:
        The single athlete-visible volume line.
    """
    tiers = MAINTENANCE_WEEKLY_HOURS[goal]
    if ambition is not None:
        return (
            f"- Weekly volume at your tier: {tiers[ambition]:g} h. The block "
            "converges on it — ramping up from below, a little under it "
            "while the long sessions grow into their range — and then holds; "
            "never quote another tier's target."
        )
    labelled = ", ".join(
        f"{level.value.capitalize()} {hours:g} h" for level, hours in tiers.items()
    )
    return f"- Weekly volume by tier: {labelled} — quote only the athlete's tier."


def _maintenance_doctrine(
    goal: GoalType, block: _MaintenanceBlock, ambition: AmbitionLevel | None
) -> str:
    """Compose one maintenance goal's doctrine block.

    Args:
        goal: The maintenance goal being rendered.
        block: Its family-specific doctrine inputs.
        ambition: The active plan's ambition tier, if it could be read.

    Returns:
        The rendered doctrine block.
    """
    header = (
        f"{block.label} maintenance block — these are the plan's own design "
        "rules. Do not advise against them, and do not add work the plan "
        "deliberately withholds."
    )
    return "\n".join(
        (
            header,
            _maintenance_hours_line(goal, ambition),
            "- Nothing here builds toward a race. Once at the tier's volume "
            "the load is flat week over week by design — not a plateau to "
            "break out of; the only ramp is the approach to it.",
            *block.rules,
            "- The long session stays moderate. Where it starts below the "
            "block's range it grows a step a week up to it, then holds; it "
            "keeps durability and is not growing toward a race.",
            "- The fitness retest — one per sport — is the block's "
            "checkpoint, on legs rested by a recovery week, in that week or "
            'the one after. It answers "did I hold?", not "did I improve?".',
            "- When the athlete wants to progress, the answer is a race plan "
            "through the wizard, not more maintenance volume.",
        )
    )


def _plan_ambition(repos: Any, plan: Any) -> AmbitionLevel | None:
    """Read the active plan's ambition tier.

    The ``plans`` row carries no ambition column — the wizard's
    ``ambition_level`` is persisted on the linked ``macro_plans`` row.

    Args:
        repos: Repository container with ``plan`` access.
        plan: The active plan row.

    Returns:
        The ambition tier, or None when no macro row backs the plan or its
        level is unreadable.
    """
    try:
        macro = repos.plan.get_macro_for_plan(plan)
        if macro is None:
            return None
        return AmbitionLevel(str(macro.ambition_level))
    except Exception:
        logger.debug("Could not resolve the active plan's ambition", exc_info=True)
        return None


def build_experience_fallback(repos: Any) -> str:
    """Derive the jargon level to pitch at when the experience level is unknown.

    The plan's ambition tier is the closest honest proxy the app already
    holds, so the coach can pitch instead of opening with an intake question.

    Args:
        repos: Repository container with ``plan`` access.

    Returns:
        "beginner", "intermediate", or "advanced" — ``DEFAULT_EXPERIENCE_PITCH``
        when there is no active plan or no readable ambition tier.
    """
    try:
        active = repos.plan.get_active()
    except Exception:
        logger.debug("Could not read the active plan for the pitch", exc_info=True)
        return DEFAULT_EXPERIENCE_PITCH
    if active is None:
        return DEFAULT_EXPERIENCE_PITCH
    ambition = _plan_ambition(repos, active)
    if ambition is None:
        return DEFAULT_EXPERIENCE_PITCH
    return _PITCH_BY_AMBITION[ambition.value]


def build_sport_doctrine(repos: Any) -> str | None:
    """Build the doctrine block for the active plan's goal family.

    Gives the coach the design rules the plan itself was built on, so it
    cannot advise against them. Only goals with a ``SPORT_DOCTRINE`` or
    ``MAINTENANCE_DOCTRINE`` entry render anything — every other goal (and
    every athlete with no plan) contributes nothing to the prompt.

    Args:
        repos: Repository container with ``plan`` access.

    Returns:
        The doctrine block, or None when there is no active plan, no
        readable goal type, or no doctrine for that goal.
    """
    try:
        active = repos.plan.get_active()
        if active is None:
            return None
        goal = GoalType(str(active.goal_type))
    except Exception:
        logger.debug("Could not resolve the active plan's goal type", exc_info=True)
        return None
    category = SPORT_DOCTRINE.get(goal)
    if category is not None:
        return _trail_doctrine(category, _plan_ambition(repos, active))
    block = MAINTENANCE_DOCTRINE.get(goal)
    if block is None:
        return None
    return _maintenance_doctrine(goal, block, _plan_ambition(repos, active))


# System context builder

#: The "Active Training Plan" section when no plan is active. Without it the
#: section is simply absent, and a plan note left in the athlete memory reads
#: as a plan still running.
NO_ACTIVE_PLAN = (
    "None: the athlete has no active plan, so no plan sessions are on the "
    "calendar. A plan note in the athlete memory describes an earlier plan, "
    "not a current one."
)


def build_no_plan_note(repos: Any) -> str | None:
    """Say there is no active plan, when there is none.

    Args:
        repos: Repository container with ``plan`` access.

    Returns:
        :data:`NO_ACTIVE_PLAN`, or None while a plan is active (or the read
        fails, so the coach is never told a plan is gone when it is not).
    """
    try:
        return NO_ACTIVE_PLAN if repos.plan.get_active() is None else None
    except Exception:
        logger.debug("Could not read the active plan", exc_info=True)
        return None


def build_plan_anchor(repos: Any) -> str | None:
    """Build the one-line temporal anchor for the active plan.

    Grounds the coach in "where in the plan is the athlete TODAY" —
    calendar week number (1-based, matching the plan UI), current phase,
    and today's planned session(s) or rest day. Reads the materialized
    ``plan_sessions`` rows, so it stays correct after reschedules.

    Args:
        repos: Repository container with ``plan`` access.

    Returns:
        The anchor line, or None when there is no readable active plan.
    """
    try:
        active = repos.plan.get_active()
        if active is None:
            return None
        plan_start = date.fromisoformat(str(active.plan_start))
        goal_date = date.fromisoformat(str(active.goal_date))
        today = clock.today()
        today_str = today.isoformat()
        today_label = f"{today.strftime('%A')} {today_str}"
        if today < plan_start:
            return f"Today is {today_label} — the plan starts {plan_start.isoformat()}."
        if today > goal_date:
            return (
                f"Today is {today_label} — the plan's goal date "
                f"{goal_date.isoformat()} has passed."
            )

        week0_monday = plan_start - timedelta(days=plan_start.weekday())
        week_no = (today - week0_monday).days // 7 + 1
        monday = today - timedelta(days=today.weekday())
        # Two weeks on, so a Sunday's tomorrow and the next key session
        # are in the read too.
        rows = repos.plan.get_sessions_by_date_range(
            active.id, monday.isoformat(), (monday + timedelta(days=14)).isoformat()
        )
    except Exception:
        logger.debug("Could not build plan anchor", exc_info=True)
        return None

    this_week = [
        row for row in rows if str(row.date) < (monday + timedelta(days=7)).isoformat()
    ]
    phase = str(this_week[0].phase_type).replace("_", " ") if this_week else None
    todays = [row for row in rows if str(row.date) == today_str]
    if todays:
        parts = []
        for row in todays:
            # Mark delivered sessions so the coach never prescribes
            # adjustments to a workout the athlete already logged today.
            done = (
                str(getattr(row, "status", "")) == "completed"
                or getattr(row, "matched_activity_id", None) is not None
            )
            done_note = " — ALREADY COMPLETED" if done else ""
            parts.append(f"{_session_label(row)}{done_note}")
        session_note = "today's session(s): " + "; ".join(parts)
    else:
        session_note = "today is a rest day"
    phase_note = f", {phase} phase" if phase else ""
    anchor = f"Today is {today_label} — plan week {week_no}{phase_note}; {session_note}"
    tomorrow = today + timedelta(days=1)
    if tomorrow <= goal_date:
        # "What's on tomorrow?" is the question most often answered from
        # today's session; naming tomorrow's here answers it outright.
        tomorrows = [
            _session_label(row) for row in rows if str(row.date) == tomorrow.isoformat()
        ]
        when = f"tomorrow ({tomorrow.strftime('%A')} {tomorrow.isoformat()})"
        anchor += (
            f"; {when}: " + "; ".join(tomorrows)
            if tomorrows
            else f"; {when} is a rest day"
        )
    key = next((row for row in rows if _is_coming_key(row, today_str)), None)
    if key is not None:
        day = date.fromisoformat(str(key.date))
        anchor += (
            f"; next key session: {day:%A} {day.isoformat()}, {_session_label(key)}"
        )
    return anchor + "."


#: Families of the sessions the plan builds a week around.
_KEY_FAMILIES = frozenset(
    family.value
    for family in ActivityFamily
    if family not in (ActivityFamily.EASY, ActivityFamily.RECOVERY)
)


def _is_coming_key(row: Any, today: str) -> bool:
    """Whether a session row is a key session still ahead, from today on."""
    return (
        str(row.date) >= today
        and str(getattr(row, "family", "")) in _KEY_FAMILIES
        and not getattr(row, "optional", False)
        and str(getattr(row, "status", "")) not in ("skipped", "completed")
        and getattr(row, "matched_activity_id", None) is None
    )


def _session_label(row: Any) -> str:
    """Render a plan session row as ``Sport — Title (N min)``.

    A skipped row is marked as such: a pause or a skip already deleted its
    intervals.icu event, so describing it as still planned briefs the coach
    on a session that no longer exists.
    """
    duration = (
        f" ({int(float(row.duration_minutes))} min)" if row.duration_minutes else ""
    )
    title = str(row.activity_key).replace("_", " ").title()
    skipped = " — SKIPPED" if str(getattr(row, "status", "")) == "skipped" else ""
    optional = " — optional" if getattr(row, "optional", False) else ""
    return f"{str(row.sport).title()} — {title}{duration}{optional}{skipped}"


def _planned_note(repos: Any, day: date) -> str:
    """Describe what the active plan had scheduled on ``day``."""
    try:
        active = repos.plan.get_active()
        if active is None:
            return "no active plan"
        rows = repos.plan.get_sessions_by_date_range(
            active.id, day.isoformat(), day.isoformat()
        )
    except Exception:
        logger.debug("Could not read planned sessions for %s", day, exc_info=True)
        return "plan unavailable"
    if not rows:
        return "nothing planned that day"
    return "planned that day: " + "; ".join(_session_label(row) for row in rows)


def build_latest_session_line(
    client: IntervalsClient | None, repos: Any, days: int = LATEST_SESSION_WINDOW_DAYS
) -> str | None:
    """Build the "latest completed session" context.

    Lets the coach open a debrief unprompted: every intervals.icu activity
    on the most recent active day in the last ``days`` days — one line per
    activity (a run-plus-ride double is two lines), each with its headline
    numbers, intervals.icu's interval recap, what the plan had scheduled
    that day, and its own splits pointer.

    Args:
        client: intervals.icu client, or None when not configured.
        repos: Repository container with ``plan`` access.
        days: Look-back window, inclusive of today.

    Returns:
        The line(s), or None when nothing was completed in the window (or
        the API is unreachable — never fails a turn).
    """
    if client is None:
        return None
    today = clock.today()
    try:
        activities = client.list_activities(today - timedelta(days=days - 1), today)
    except Exception as exc:
        logger.warning("Could not load latest intervals.icu activity: %s", exc)
        return None
    dated: list[tuple[date, Any]] = []
    for act in activities:
        act_day = activity_date(act)
        if act_day is not None:
            dated.append((act_day, act))
    if not dated:
        return None
    day = max(act_day for act_day, _ in dated)
    todays = sorted(
        (act for act_day, act in dated if act_day == day),
        key=lambda act: act.start_date_local or "",
    )
    planned = _planned_note(repos, day)
    return "\n".join(
        _activity_line(day, act, planned + _link_note(repos, day, act))
        for act in todays
    )


def _link_note(repos: Any, day: date, activity: Any) -> str:
    """Say which plan session the activity completes, or which its name says."""
    try:
        active = repos.plan.get_active()
        rows = repos.plan.get_sessions(active.id) if active is not None else []
    except Exception:
        logger.debug("Could not read the plan for the link note", exc_info=True)
        return ""
    if not rows or not activity.id:
        return ""
    link = link_view(
        rows,
        str(activity.id),
        day=day,
        activity_type=activity.type,
        name=activity.name,
        minutes=(activity.moving_time or 0) / 60.0,
    )
    named = next(
        (c for c in link["candidates"] if c["session_id"] == link["looks_like"]), None
    )
    note = ""
    if link["linked"] is not None:
        linked = link["linked"]
        note = f"; it completes {linked['title']} (session_id {linked['session_id']})"
    if named is not None:
        when = date.fromisoformat(named["date"])
        note += (
            f"; its name says it was {named['title']} planned {when:%A} "
            f"{when.isoformat()} (session_id {named['session_id']})"
        )
    return note


def _activity_line(day: date, latest: Any, planned: str) -> str:
    """Render one completed activity as a self-contained debrief pointer."""
    view = delivered_view(latest)

    headline = [
        f"{view['duration_minutes']} min",
        f"{view['distance_km']} km" if "distance_km" in view else None,
        f"avg HR {view['avg_hr']}" if "avg_hr" in view else None,
        f"intensity {view['intensity_factor']}" if "intensity_factor" in view else None,
        f"weighted power {view['normalized_power']} W"
        if "normalized_power" in view
        else None,
        f"pace {view['pace_min_km']}/km" if "pace_min_km" in view else None,
        f"pace {view['pace_min_100m']}/100m" if "pace_min_100m" in view else None,
        f"{view['speed_kmh']} km/h" if "speed_kmh" in view else None,
        f"load {view['training_load']}" if "training_load" in view else None,
    ]
    sport = str(latest.type or "activity")
    name = f" '{_one_line(latest.name)}'" if latest.name else ""
    clock = activity_start_clock(latest)
    stamp = f" {clock}" if clock else ""
    prefix = f"{day.isoformat()} ({day.strftime('%a')}){stamp} {sport}{name}: "
    line = prefix + ", ".join(part for part in headline if part)
    if view.get("interval_summary"):
        line += "; recap: " + ", ".join(view["interval_summary"])
    line += f"; {planned}."
    if latest.id:
        line += f" Splits: get_activity_detail(activity_id='{latest.id}')."
    return line


def build_scheduled_workouts_line(
    client: IntervalsClient | None,
    days: int = SCHEDULED_WORKOUTS_WINDOW_DAYS,
    today: date | None = None,
) -> str | None:
    """Build the "workouts booked by you" context line(s).

    Lets the coach recall its own coach-scheduled ad-hoc bookings
    (``schedule_workout``) without a tool round trip, since it has no other
    way to recall a same-day booking it just made. Filters on the same
    ``AD_HOC_EXTERNAL_ID_PREFIX``
    :func:`endury.planning.unschedule_workout.ad_hoc_workouts` uses, so a
    plan session or a race event never shows up here.

    Args:
        client: intervals.icu client, or None when not configured.
        days: Look-ahead window, inclusive of today.
        today: Reference date (defaults to the app clock).

    Returns:
        One line per booked workout, or None when nothing is booked (or
        the API is unreachable — never fails a turn).
    """
    if client is None:
        return None
    today = today or clock.today()
    try:
        events = client.list_events(
            today, today + timedelta(days=days - 1), category="WORKOUT"
        )
    except Exception as exc:
        logger.warning("Could not load scheduled workouts: %s", exc)
        return None
    workouts = [
        event
        for event in events
        if (getattr(event, "external_id", None) or "").startswith(
            AD_HOC_EXTERNAL_ID_PREFIX
        )
    ]
    if not workouts:
        return None
    return "\n".join(_scheduled_workout_line(event) for event in workouts)


def _scheduled_workout_line(event: Any) -> str:
    """Render one booked workout as its date, name, minutes and TSS."""
    day_str = (event.start_date_local or "")[:10]
    try:
        day = date.fromisoformat(day_str)
        stamp = f"{day.isoformat()} ({day.strftime('%a')})"
    except ValueError:
        stamp = day_str
    line = f"{stamp} {_one_line(event.name or 'workout')}"
    if event.moving_time:
        line += f" — {round(event.moving_time / 60)} min"
    if event.icu_training_load is not None:
        line += f" — load {round(event.icu_training_load)}"
    return line


def _race_countdown(race_date: str, today: date) -> str:
    """Human "today"/"in N days"/"in N weeks" countdown to an upcoming race."""
    delta = (date.fromisoformat(race_date) - today).days
    if delta <= 0:
        return "today"
    if delta < 14:
        return f"in {delta} days"
    return f"in {delta // 7} weeks"


def _one_line(text: str) -> str:
    """Athlete-written text for the context: one line, capped, so it stays data."""
    return " ".join(text.split())[:80]


def _upcoming_race_line(race: Any, today: date) -> str:
    """Render one upcoming race: date, countdown, name, priority, goal."""
    goal = f", {race.goal_type.replace('_', ' ')}" if race.goal_type else ""
    countdown = _race_countdown(race.race_date, today)
    name = _one_line(race.name)
    return f"- {race.race_date} ({countdown}) {name} [{race.priority}]{goal}"


def build_races_section(repos: Any) -> str | None:
    """Render the athlete's upcoming and recently past races.

    Reads the athlete_goals table (``AthleteGoalsRepository.list``, its own
    30-day past window) — the single home for race data; races the athlete
    or coach dropped (``status="cancelled"``) never appear here.

    Args:
        repos: Repository container with ``races`` access.

    Returns:
        The section body, or None when there are no races (or the read
        fails — never fails a turn).
    """
    try:
        today = clock.today()
        today_str = today.isoformat()
        races = [race for race in repos.races.list() if race.status != "cancelled"]
        lines = [
            _upcoming_race_line(race, today)
            for race in races
            if race.race_date >= today_str
        ]
        lines += [
            f"- {race.race_date} {_one_line(race.name)} — {race.status}"
            for race in races
            if race.race_date < today_str
        ]
    except Exception:
        logger.debug("Could not read races", exc_info=True)
        return None
    return "\n".join(lines) if lines else None


def build_due_check_in_section(memory: str, today: date) -> str:
    """Render the due-reminder block for the memory document's check-in lines.

    A "- Next check-in YYYY-MM-DD: <why>" bullet is the only reminder the app
    has; nothing fires on its own, so the turn that first sees the date pass
    has to raise it.

    Args:
        memory: The athlete-memory document.
        today: Today's date.

    Returns:
        The markdown section, or an empty string when nothing is due.
    """
    due = due_check_ins(memory, today)
    if not due:
        return ""
    lines = ["\n## Due check-in"]
    lines += [
        f"- The athlete asked to revisit this on {when}: {why}" for when, why in due
    ]
    lines.append("Raise it now, then rewrite the memory document without that line.\n")
    return "\n".join(lines)


def build_due_review_section(memory: str, today: date, written: date | None) -> str:
    """Render the block asking the coach to re-confirm constraints under review.

    Args:
        memory: The athlete-memory document.
        today: Today's date.
        written: The day the document last changed, if known.

    Returns:
        The markdown section, or an empty string when nothing is due.
    """
    due = due_reviews(memory, today, written)
    if not due:
        return ""
    lines = ["\n## Constraints to re-confirm", *due]
    lines.append(
        "Their review date has come, so they may be out of date: do not advise "
        "from them. Ask the athlete once whether each still applies, then "
        "remove it or give it a new review date with update_memory.\n"
    )
    return "\n".join(lines)


def build_system_context(
    config: CoachConfig,
    athlete_profile: AthleteProfile | None,
    memory: str,
    recent_health: str | None = None,
    form: str | None = None,
    memory_written: date | None = None,
    active_plan_summary: str | None = None,
    macro_plan_summary: str | None = None,
    plan_anchor: str | None = None,
    latest_session: str | None = None,
    scheduled_workouts: str | None = None,
    races: str | None = None,
    sport_doctrine: str | None = None,
    nudge_profile: bool = True,
    experience_fallback: str = DEFAULT_EXPERIENCE_PITCH,
) -> str:
    """Build the system prompt from athlete context, wellness, and plan state.

    Args:
        config: Coach configuration (persona, system prompt template).
        athlete_profile: Stored profile, or None when there is none yet.
        memory: The athlete-memory document, injected verbatim when non-empty.
        memory_written: The day the document last changed, if known.
        recent_health: Wellness snapshot line, if available.
        form: Today's fitness, fatigue and form line, if readable.
        active_plan_summary: Active plan brief, if a plan is committed.
        macro_plan_summary: Macro plan overview, if a plan is committed.
        plan_anchor: Today's position in the plan.
        latest_session: The "latest completed session" line.
        scheduled_workouts: The "booked by you" line(s), if any are booked.
        races: The "Races" section body, if any race is on file.
        sport_doctrine: The active goal family's design rules, if any.
        nudge_profile: Whether this turn may carry a profile-intake question.
        experience_fallback: The jargon level to pitch at while the athlete's
            experience level is unknown.
    """
    # Language instruction goes first so it is never overridden.
    language_code = (athlete_profile.language if athlete_profile else None) or "en"
    lang_instruction = _LANGUAGE_INSTRUCTIONS.get(
        language_code, f"Respond in the language with code '{language_code}'."
    )
    if language_code != "en":
        context = (
            f"## ⚠️ MANDATORY LANGUAGE RULE — HIGHEST PRIORITY\n"
            f"{lang_instruction} "
            f"This rule overrides everything else. "
            f"Every single response MUST be in this language, no exceptions, "
            f"regardless of what language the athlete writes in "
            f"or what the conversation history shows.\n\n"
        )
    else:
        context = ""

    context += build_system_prompt(config) + "\n\n"
    now = clock.now()
    # The weekday, daypart, timezone and week boundaries are spelled out
    # explicitly rather than left for the model to derive from a bare
    # timestamp.
    sunday = now.date() + timedelta(days=6 - now.weekday())
    context += (
        f"Current date and time: {now.strftime('%A %Y-%m-%d %H:%M')} "
        f"({clock.daypart(now.hour)}, {now.astimezone().tzname()}). "
        f"This week means today through Sunday {sunday.isoformat()}; "
        f"next week starts Monday {(sunday + timedelta(days=1)).isoformat()}.\n"
    )
    # The window spans days: without this, "tomorrow" said last night — by
    # the athlete or by the coach — reads as still tomorrow.
    context += (
        "Earlier messages, yours and the athlete's, open with a "
        "[weekday date time] stamp the app added — nobody typed it and you "
        'never write one; read "today"/"tomorrow" in an older message against '
        'its own stamp, not against now. Their "last week" or "this week" names '
        "the sessions they mean: read it against the stamps and answer, never "
        "correct their calendar words.\n"
    )

    context += "\n## App facts\n"
    context += f"{build_app_knowledge()}\n"

    if sport_doctrine:
        context += "\n## Sport doctrine\n"
        context += f"{sport_doctrine}\n"

    if athlete_profile:
        has_name = bool(athlete_profile.name)
        has_sport = bool(athlete_profile.primary_sport)
        has_level = bool(athlete_profile.experience_level)

        context += "\n## Current Athlete\n"
        if has_name:
            context += f"Name: {athlete_profile.name}\n"
        if has_level:
            context += f"Experience Level: {athlete_profile.experience_level}\n"
        else:
            context += (
                "Experience Level: not stated — app default, pitch at "
                f"{experience_fallback}; never save this as their level\n"
            )
        if has_sport:
            context += f"Primary Sport: {athlete_profile.primary_sport}\n"
        if athlete_profile.communication_style:
            context += f"Communication Style: {athlete_profile.communication_style}\n"
        if athlete_profile.preferred_run_surface:
            surface = athlete_profile.preferred_run_surface
            context += f"Preferred Run Surface: {surface}\n"
        if athlete_profile.open_water_access is not None:
            context += f"Open Water Access: {athlete_profile.open_water_access}\n"
        if athlete_profile.race_location_access is not None:
            context += f"Race Location Access: {athlete_profile.race_location_access}\n"

        if nudge_profile and not has_name and (has_sport or has_level):
            context += "\n## ⚠️ Profile Note\n"
            context += (
                "You already know this athlete's sport and/or "
                "experience level (shown above). "
                "You do NOT know their name yet. "
                "Your ONLY missing piece is their name — "
                "ask for it naturally and conversationally. "
                "Do NOT ask for sport, level, or preferences "
                "— you already have those. "
                "Do NOT present a numbered list of questions.\n"
            )
        elif nudge_profile and has_name and has_sport and not has_level:
            context += "\n## ⚠️ Profile Note\n"
            context += (
                f"You know this athlete's name ({athlete_profile.name}) and "
                "primary sport, but not their experience level, and the "
                "experience level is what tells you how much jargon they can "
                "follow. Ask for their experience level when it fits the "
                "conversation and save the answer with "
                "update_athlete_profile. Do NOT ask for anything else at the "
                "same time and do NOT present a numbered list of "
                "questions.\n"
            )
        elif nudge_profile and has_name and not has_sport and has_level:
            context += "\n## ⚠️ Profile Note\n"
            context += (
                f"You know this athlete's name ({athlete_profile.name}) and "
                "experience level, but not their primary sport. Ask for "
                "their primary sport when it fits the conversation and save "
                "the answer with update_athlete_profile. Do NOT ask for "
                "anything else at the same time and do NOT present a "
                "numbered list of questions.\n"
            )
        elif nudge_profile and has_name and not has_sport and not has_level:
            context += "\n## ⚠️ Profile Note\n"
            context += (
                f"You know this athlete's name ({athlete_profile.name}) but "
                "neither their primary sport nor their experience level, and "
                "the experience level is what tells you how much jargon they "
                "can follow. Ask for ONE of the two when it fits the "
                "conversation — sport first — and save the answer with "
                "update_athlete_profile. Do NOT ask for both at once and do "
                "NOT present a numbered list of questions.\n"
            )

    if memory.strip():
        context += (
            "\n## Athlete memory (durable — maintained by you via update_memory)\n"
        )
        context += f"{memory}\n"
    else:
        context += "\n## Athlete memory (empty)\n"
        context += (
            "You have no durable memory about this athlete yet. Early "
            "conversations should gather the background that matters for "
            "coaching — anything limiting their training right now, life "
            "constraints, training history, motivation — one or two questions at a "
            "time, woven naturally into the conversation. Save what you "
            "learn with update_memory as soon as you learn it; don't wait "
            "for a complete picture.\n"
        )

    context += build_due_check_in_section(memory, now.date())
    context += build_due_review_section(memory, now.date(), memory_written)

    if recent_health:
        context += "\n## Recent Wellness Snapshot (last few days)\n"
        context += f"{recent_health}\n"

    if form:
        context += "\n## Form today\n"
        context += f"{form}\n"

    if plan_anchor:
        context += "\n## Today\n"
        context += f"{plan_anchor}\n"

    if latest_session:
        context += "\n## Latest completed session\n"
        context += f"{latest_session}\n"
        context += (
            "One line per activity that day — a debrief covers every one of "
            "them. You may open with it when it is worth a word. Pull the "
            "splits before judging it.\n"
        )

    if scheduled_workouts:
        context += "\n## Scheduled workouts (booked by you)\n"
        context += f"{scheduled_workouts}\n"
        context += (
            "These are yours — schedule_workout put them there. "
            "unschedule_workout takes one back off; pause_training clears "
            "them for a window.\n"
        )

    if races:
        context += "\n## Races\n"
        context += f"{races}\n"
        context += (
            "This table is the source of truth for races — upsert_race is "
            "how it changes; the athlete-memory document should not also "
            "list them.\n"
        )

    if active_plan_summary:
        context += "\n## Active Training Plan\n"
        context += f"{active_plan_summary}\n"

    if macro_plan_summary:
        context += "\n## Macro Training Plan Overview\n"
        context += f"{macro_plan_summary}\n"

    # Last, where a small model reads it best: the days named, so it never
    # counts them from the date line.
    context += "\n" + build_day_table(now.date()) + "\n"
    return context
