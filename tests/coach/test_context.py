"""Tests for endury.coach.context module."""

import os
import re
import time
from dataclasses import replace
from datetime import date

import pytest

from endury.coach.context import (
    _LANGUAGE_INSTRUCTIONS,
    build_app_knowledge,
    build_system_context,
)
from endury.domain.models.athlete import AthleteProfile
from endury.planner.foundations.types import GoalType, PlanMaturity
from endury.planner.phase.phase_config import (
    BC_EARLIEST_WEEK,
    BC_PROXIMITY_TO_A_RACE_WEEKS,
    BC_WINDOW_FRACTION,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planning.generate_plan import min_join_weeks
from endury.wiring.config import CoachConfig

# _LANGUAGE_INSTRUCTIONS


def test_language_instructions_has_common_languages():
    assert "de" in _LANGUAGE_INSTRUCTIONS
    assert "fr" in _LANGUAGE_INSTRUCTIONS
    assert "es" in _LANGUAGE_INSTRUCTIONS
    assert "en" in _LANGUAGE_INSTRUCTIONS


def test_language_instructions_values_are_strings():
    for lang, instruction in _LANGUAGE_INSTRUCTIONS.items():
        assert isinstance(instruction, str), (
            f"Language {lang} instruction is not a string"
        )


# build_app_knowledge


def test_app_knowledge_lists_horizons_from_goal_profiles():
    text = build_app_knowledge()
    assert "5k 4-16" in text
    assert "ironman 22-36" in text
    assert "half ironman 16-28" in text
    assert "maintenance block" in text


def test_app_knowledge_names_the_plans_on_offer(offered_as_shipped):
    text = build_app_knowledge()
    assert (
        "A new plan can be built only for these: half marathon (moderate), "
        "Trail 20K (basic), half-distance triathlon (moderate), full-distance "
        "triathlon (moderate), trail maintenance (basic), triathlon maintenance "
        "(moderate). create_plan refuses any other"
    ) in text


def test_app_knowledge_skips_maintenance_goals_in_table():
    assert "maintenance 4-None" not in build_app_knowledge()


def test_app_knowledge_states_create_plan_replaces_wizard_only_authority():
    """The App facts must state the coach can build a plan itself via
    create_plan, not claim only the wizard can."""
    text = build_app_knowledge()
    assert "You build a plan yourself with create_plan" in text
    assert "You cannot delete a plan." in text
    assert "you cannot create or delete plans" not in text


@pytest.mark.usefixtures("alpha_goals")
def test_app_knowledge_marks_only_the_experimental_goals(monkeypatch):
    """The coach can only caveat honestly if it knows which goals to caveat."""
    goal = GoalType.TRAIL_MAINTENANCE
    monkeypatch.setitem(
        GOAL_PROFILES,
        goal,
        replace(GOAL_PROFILES[goal], maturity=PlanMaturity.EXPERIMENTAL),
    )
    text = build_app_knowledge()
    assert "5k 4-16 (experimental)" in text
    assert "trail 100k 14-28 (experimental)" in text
    assert "trail (experimental)" in text
    # Alpha goals carry no marker at all.
    assert "running, trail (experimental), triathlon" in text
    assert "trail 50k 12-24," in text
    assert "half marathon 8-24," in text
    assert "half ironman 16-28," in text
    assert "ironman 22-36." in text
    assert "ironman 22-36 (" not in text
    assert "marathon 12-28 (" not in text
    assert "(alpha)" not in text
    assert "its sessions have not yet been reviewed one by one" in text
    assert "needs the athlete's confirmation" in text
    assert "experimental_plan" in text


def test_context_includes_app_facts_section():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "## App facts" in ctx
    assert "ironman 22-36" in ctx


def test_app_knowledge_states_short_notice_join_floors():
    """Below the minimum the wizard joins a prep mid-flight; the coach must
    not present the minimum as a refusal, and must know the real floor."""
    text = build_app_knowledge()
    assert "enforced minimum" not in text
    assert "short-notice plan" in text
    assert f"trail 50k {min_join_weeks(GoalType.TRAIL_50K)}" in text
    assert f"ironman {min_join_weeks(GoalType.IRONMAN)}." in text


def test_app_knowledge_states_tune_up_race_rules():
    """The coach labels tune-ups B or C; the wizard's window rules decide
    which one fits, so the coach must be told them from the same config."""
    text = build_app_knowledge()
    assert "exactly one A race" in text
    assert f"not in its first {BC_EARLIEST_WEEK} weeks" in text
    assert f"within {BC_PROXIMITY_TO_A_RACE_WEEKS} weeks of it" in text
    assert "takes only its own race week out of the A-race build" in text
    assert f"at most {BC_WINDOW_FRACTION:.0%} of the pre-taper weeks" in text


def test_app_knowledge_lets_a_maintenance_block_take_a_tune_up():
    """Seen live: an A race inside a maintenance block got a hand-made
    taper in chat because the planner refused tune-ups there."""
    text = build_app_knowledge()
    assert "A maintenance block takes a tune-up race in any of its weeks" in text
    assert "never a hand-made taper" in text
    assert "Raceless" not in text


def test_app_knowledge_states_the_race_day_freshness_floors():
    """Seen live: asked for a race-day TSB target, the coach said the
    app sets none; the planner's floors are what a taper arrives at."""
    text = build_app_knowledge()
    assert "arrive at its A race with form at least 27% of fitness" in text
    assert "at a B race at least 13.5%" in text


def test_app_knowledge_names_the_controls_the_athlete_actually_sees():
    """The App facts must name only controls that exist in the UI: the plan
    pill, the wizard's own fields, and the panel's real labels."""
    text = build_app_knowledge()
    for label in (
        '"No active plan"',
        '"Create a plan"',
        '"Choose a plan"',
        '"Edit plan"',
        "Triathlon / Running / Trail Running",
        "Basic / Moderate / Competitive",
        '"+ Add"',
        '"Race profile"',
        '"distance km", "climb m"',
        '"Preview <goal> plan"',
        '"Preview N-week maintenance block"',
        '"Discard"',
        '"Apply plan"',
        '"Replan from next Monday"',
        '"Delete plan"',
        '"Start a new plan"',
        '"Archive plan"',
    ):
        assert label in text, label
    # The km-effort bands the trail race profile suggests from.
    assert "<50 Trail 20K, 50-99 Trail 50K, 100-169 Trail 100K, 170+" in text


def test_app_knowledge_says_there_is_no_pause_button():
    """There is no pause control in the UI; a pause request must route to
    the coach. What each plan tool does is that tool's own description."""
    text = build_app_knowledge()
    assert "There is no pause button — pausing goes through you." in text
    assert "Your levers" not in text


def test_app_knowledge_splits_the_plan_opener_by_shell():
    """Only the phone has a tappable plan pill — the desktop's top-left text
    is a plain label, and its panel opens from the plan card's Edit button."""
    text = build_app_knowledge()
    assert "on desktop that top-left text is a read-only label" in text
    assert 'the panel opens from the plan card\'s "Edit" (or "Create")' in text
    assert '"Apply" on the phone' in text
    assert 'a "Replan" button on the desktop plan card' in text


def test_app_knowledge_states_the_mid_week_first_week():
    """Up to Tuesday the first week is partial; from Wednesday it is next week."""
    text = build_app_knowledge()
    assert "THIS week's Monday with the elapsed days dropped" in text
    assert "from Wednesday on it starts next Monday" in text


def test_app_knowledge_states_maintenance_tier_volumes():
    """Rendered off MAINTENANCE_WEEKLY_HOURS, so the tier volumes the coach
    quotes cannot drift from the ones the block is actually built to."""
    from endury.planner.foundations.maintenance_tables import (
        MAINTENANCE_WEEKLY_HOURS,
    )
    from endury.planner.foundations.types import AmbitionLevel

    text = build_app_knowledge()
    assert "running/trail 3/4.5/6 h, triathlon 5/8.5/10 h" in text
    run = MAINTENANCE_WEEKLY_HOURS[GoalType.RUNNING_MAINTENANCE]
    tri = MAINTENANCE_WEEKLY_HOURS[GoalType.TRIATHLON_MAINTENANCE]
    assert (run[AmbitionLevel.BASIC], run[AmbitionLevel.COMPETITIVE]) == (3.0, 6.0)
    assert (tri[AmbitionLevel.BASIC], tri[AmbitionLevel.COMPETITIVE]) == (5.0, 10.0)
    assert "converge on a fixed weekly volume" in text
    assert "ramped up to it and then held" in text
    assert "pick the tier against measured hours" in text


def test_app_knowledge_explains_where_session_targets_come_from():
    # Pace targets need a threshold pace set on intervals.icu; without one a
    # session shows only zone number and RPE.
    text = build_app_knowledge()
    assert "pace targets appear only once a threshold pace is set" in text
    assert "zone number and RPE only" in text
    # A race time or the watch's number is enough; the test only checks it.
    assert "set_athlete_thresholds (an Apply card)" in text
    assert "the booked test is a check, not a prerequisite" in text


def test_app_knowledge_denies_any_reminder_mechanism():
    """There is none, so a promised reminder is a promise the app breaks."""
    text = build_app_knowledge()
    assert "no reminder or notification mechanism" in text
    assert "put the date in the athlete-memory document" in text


# build_system_context


def test_context_contains_system_prompt():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "coach" in ctx.lower()


def test_context_includes_current_date():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "2026" in ctx  # currentDate from CLAUDE.md


@pytest.fixture
def berlin_tz():
    """Pin the process clock to Europe/Berlin, the app's deployment zone.

    The date line names the zone it resolved, so without this the exact-match
    assertions below would read "UTC" on a CI runner and "CEST" on the Mac.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def test_context_date_line_names_the_weekday_and_week_boundaries(
    monkeypatch, berlin_tz
):
    """A Saturday must read as a Saturday, or "this week" spills backwards."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-05")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "22:37:00")
    ctx = build_system_context(CoachConfig(), None, "")
    assert (
        "Current date and time: Saturday 2026-09-05 22:37 (night, CEST). "
        "This week means today through Sunday 2026-09-06; "
        "next week starts Monday 2026-09-07." in ctx
    )


def test_context_date_line_on_a_monday_spans_the_whole_week(monkeypatch, berlin_tz):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-07")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "06:00:00")
    ctx = build_system_context(CoachConfig(), None, "")
    assert (
        "Current date and time: Monday 2026-09-07 06:00 (early morning, CEST). "
        "This week means today through Sunday 2026-09-13; "
        "next week starts Monday 2026-09-14." in ctx
    )


def test_context_date_line_on_a_sunday_ends_today(monkeypatch, berlin_tz):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-06")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "09:15:00")
    ctx = build_system_context(CoachConfig(), None, "")
    assert (
        "Current date and time: Sunday 2026-09-06 09:15 (morning, CEST). "
        "This week means today through Sunday 2026-09-06; "
        "next week starts Monday 2026-09-07." in ctx
    )


def test_context_explains_the_send_time_stamp_on_every_message(monkeypatch):
    """Each replayed message, the coach's own included, carries its send-time
    stamp, so a relative term like "tomorrow" is judged against that stamp,
    not against now."""
    ctx = build_system_context(CoachConfig(), None, "")
    assert "Earlier messages, yours and the athlete's, open with a" in ctx
    assert "[weekday date time] stamp the app added" in ctx
    assert "nobody typed it and you never write one" in ctx
    assert "against its own stamp, not against now" in ctx


def test_context_ends_with_the_day_table(monkeypatch):
    """The table sits last, where a small model reads it best, and names the
    days so the coach never computes one."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-26")
    ctx = build_system_context(
        CoachConfig(), None, "", macro_plan_summary="Week 1 (base): 300 TSS"
    )
    assert ctx.rstrip().endswith("- Saturday 2026-10-03 — in 7 days, next week")
    assert "- Sunday 2026-09-27 — tomorrow" in ctx


def test_context_date_line_names_the_winter_zone(monkeypatch, berlin_tz):
    """The zone is resolved per turn, so a January turn must read CET."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2027-01-13")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "13:05:00")
    ctx = build_system_context(CoachConfig(), None, "")
    assert "Current date and time: Wednesday 2027-01-13 13:05 (midday, CET)." in ctx


def test_context_no_profile():
    ctx = build_system_context(CoachConfig(), None, "")
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_context_includes_athlete_name():
    profile = AthleteProfile(name="Sam", primary_sport="cycling")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Sam" in ctx


def test_context_includes_sport():
    profile = AthleteProfile(name="Sam", primary_sport="cycling")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "cycling" in ctx


def test_context_includes_experience_level():
    profile = AthleteProfile(name="Sam", experience_level="advanced")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "advanced" in ctx


def test_context_includes_the_memory_document_verbatim():
    document = (
        "## Goals & races\n- 2026-06-14 Kraichgau 70.3\n\n## Notes\n- likes hills"
    )
    ctx = build_system_context(CoachConfig(), None, document)
    assert "## Athlete memory (durable — maintained by you via update_memory)" in ctx
    assert document in ctx


def test_context_flags_empty_memory_and_tells_the_coach_to_gather_it():
    ctx = build_system_context(CoachConfig(), None, "   \n  ")
    assert "## Athlete memory (empty)" in ctx
    assert "update_memory" in ctx
    assert "## Athlete memory (durable" not in ctx


def test_context_includes_health_data():
    ctx = build_system_context(
        CoachConfig(), None, "", recent_health="2024-01-01: sleep:85"
    )
    assert "sleep:85" in ctx


def test_context_tells_the_coach_not_to_correct_calendar_words():
    # Seen live: "It was this week, not last week." on a Sunday evening.
    ctx = build_system_context(CoachConfig(), None, "")
    assert "never correct their calendar words" in ctx


def test_context_carries_todays_form_line():
    line = "As of Tuesday 2026-10-20, from completed sessions: form +2.0"
    ctx = build_system_context(CoachConfig(), None, "", form=line)
    assert f"## Form today\n{line}\n" in ctx
    assert "## Form today" not in build_system_context(CoachConfig(), None, "")


def test_context_non_english_adds_language_rule():
    profile = AthleteProfile(name="Hans", language="de")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "MANDATORY LANGUAGE RULE" in ctx


def test_context_german_instruction_in_context():
    profile = AthleteProfile(name="Hans", language="de")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Deutsch" in ctx or "Antworte" in ctx


def test_context_english_no_language_block():
    profile = AthleteProfile(name="Sam", language="en")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "MANDATORY LANGUAGE RULE" not in ctx


def test_context_unknown_language_fallback():
    profile = AthleteProfile(name="X", language="xx")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "xx" in ctx


def test_context_includes_preferences_when_set():
    profile = AthleteProfile(
        name="Sam",
        preferred_run_surface="trail",
        open_water_access=True,
        race_location_access=False,
    )
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Preferred Run Surface: trail" in ctx
    assert "Open Water Access: True" in ctx
    assert "Race Location Access: False" in ctx


def test_context_omits_preferences_when_unset():
    profile = AthleteProfile(name="Sam")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Preferred Run Surface" not in ctx
    assert "Open Water Access" not in ctx
    assert "Race Location Access" not in ctx


def test_context_profile_note_when_sport_but_no_name():
    profile = AthleteProfile(primary_sport="running")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Profile Note" in ctx


# build_plan_anchor / plan_anchor rendering


def _anchor_repos(active, sessions=None, ambition=None):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    repos = MagicMock()
    repos.plan.get_active.return_value = active
    repos.plan.get_sessions_by_date_range.return_value = sessions or []
    repos.plan.get_macro_for_plan.return_value = (
        SimpleNamespace(ambition_level=ambition) if ambition else None
    )
    return repos


def _plan_row(**overrides):
    from types import SimpleNamespace

    base = {
        "date": "2026-08-06",
        "sport": "running",
        "activity_key": "run_aerobic_tempo",
        "phase_type": "build",
        "duration_minutes": 60.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _active_plan():
    from types import SimpleNamespace

    return SimpleNamespace(id=1, plan_start="2026-07-06", goal_date="2026-10-04")


# build_latest_session_line / latest_session rendering


def _latest_activity(day: str, **fields):
    from tests.coach.conftest import make_activity

    return make_activity(day, activity_id="i9", moving_time=3110, load=51.0).model_copy(
        update={
            "name": "Alpine Trail Running",
            "type": "TrailRun",
            "distance": 7895.0,
            "average_heartrate": 131,
            "icu_intensity": 76.6,
            "pace": 2.5387,
            "interval_summary": ["4x 7m26s 139bpm", "1x 5m32s 130bpm"],
            **fields,
        }
    )


def test_latest_session_line_none_without_client():
    from endury.coach.context import build_latest_session_line

    assert build_latest_session_line(None, _anchor_repos(None)) is None


def test_latest_session_line_none_when_nothing_completed(monkeypatch):
    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-17")
    client = StubIntervalsClient(activities=[_latest_activity("2026-08-01")])
    assert build_latest_session_line(client, _anchor_repos(None)) is None
    assert client.activity_windows == [(date(2026, 8, 11), date(2026, 8, 17))]


def test_latest_session_line_none_on_api_error(monkeypatch):
    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient

    client = StubIntervalsClient(activities_error=RuntimeError("down"))
    assert build_latest_session_line(client, _anchor_repos(None)) is None


def test_latest_session_line_picks_newest_and_names_the_planned_session(monkeypatch):
    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient, make_activity

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-17")
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-08-15", "Ride", activity_id="older"),
            _latest_activity("2026-08-17"),
        ]
    )
    repos = _anchor_repos(
        _active_plan(),
        [_plan_row(date="2026-08-17", activity_key="run_long", duration_minutes=75.0)],
    )
    line = build_latest_session_line(client, repos)
    assert line == (
        "2026-08-17 (Mon) 07:00 TrailRun 'Alpine Trail Running': 51.8 min, 7.89 km, "
        "avg HR 131, intensity 0.77, pace 6:34/km, load 51.0; "
        "recap: 4x 7m26s 139bpm, 1x 5m32s 130bpm; "
        "planned that day: Running — Run Long (75 min). "
        "Splits: get_activity_detail(activity_id='i9')."
    )
    repos.plan.get_sessions_by_date_range.assert_called_once_with(
        1, "2026-08-17", "2026-08-17"
    )


def test_latest_session_line_lists_every_activity_on_the_latest_day(monkeypatch):
    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient, make_activity

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-04")
    morning_run = make_activity(
        "2026-09-04", "TrailRun", activity_id="run", moving_time=5460, load=80.0
    )
    evening_ride = make_activity(
        "2026-09-04", "Ride", activity_id="ride", moving_time=3750, load=37.0
    ).model_copy(update={"start_date_local": "2026-09-04T20:06:26"})
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-09-03", "Swim", activity_id="older"),
            evening_ride,  # listed first: order must come from start time
            morning_run,
        ]
    )
    repos = _anchor_repos(_active_plan(), [])
    line = build_latest_session_line(client, repos)
    assert line == (
        "2026-09-04 (Fri) 07:00 TrailRun: 91.0 min, 10.0 km, load 80.0; "
        "nothing planned that day. Splits: get_activity_detail(activity_id='run').\n"
        "2026-09-04 (Fri) 20:06 Ride: 62.5 min, 10.0 km, load 37.0; "
        "nothing planned that day. Splits: get_activity_detail(activity_id='ride')."
    )
    # The planned note is per day, so it is read once, not once per activity.
    repos.plan.get_sessions_by_date_range.assert_called_once_with(
        1, "2026-09-04", "2026-09-04"
    )


def test_latest_session_line_reports_rest_day_no_plan_and_plan_errors(monkeypatch):
    from unittest.mock import MagicMock

    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-17")
    client = StubIntervalsClient(activities=[_latest_activity("2026-08-16")])

    assert "nothing planned that day." in build_latest_session_line(
        client, _anchor_repos(_active_plan(), [])
    )
    assert "; no active plan." in build_latest_session_line(client, _anchor_repos(None))
    broken = MagicMock()
    broken.plan.get_active.side_effect = RuntimeError("db down")
    assert "; plan unavailable." in build_latest_session_line(client, broken)


def test_latest_session_line_names_the_session_the_watch_ran(monkeypatch):
    """Seen live: Sunday's long run, run on Monday, read as yesterday's."""
    import json
    from types import SimpleNamespace

    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient

    def row(session_id, day, title, minutes, matched=None):
        return SimpleNamespace(
            id=session_id,
            date=day,
            sport="running",
            activity_key="run_easy",
            status="completed" if matched else "pending",
            matched_activity_id=matched,
            duration_minutes=minutes,
            optional=False,
            workout_json=None,
            pushed_event_json=json.dumps({"name": title}),
        )

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-10-26")
    client = StubIntervalsClient(
        activities=[_latest_activity("2026-10-26", name="Hillside - Long Trail Run")]
    )
    repos = _anchor_repos(_active_plan(), [])
    repos.plan.get_sessions.return_value = [
        row(470, "2026-10-25", "Optional: Easy Run + Strides", 30.0),
        row(566, "2026-10-26", "Easy Run", 14.0, matched="i9"),
        row(477, "2026-11-01", "Long Trail Run", 42.0),
    ]

    line = build_latest_session_line(client, repos)

    assert (
        "; it completes Easy Run (session_id 566); its name says it was Long "
        "Trail Run planned Sunday 2026-11-01 (session_id 477)."
    ) in line


def test_latest_session_line_renders_ride_power_and_skips_missing_fields(monkeypatch):
    from endury.coach.context import build_latest_session_line
    from tests.coach.conftest import StubIntervalsClient, make_activity

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-17")
    ride = make_activity(
        "2026-08-16",
        "Ride",
        activity_id="r1",
        moving_time=5400,
        load=90.0,
        distance=None,
    ).model_copy(update={"icu_weighted_avg_watts": 231, "average_speed": 8.5})
    line = build_latest_session_line(
        StubIntervalsClient(activities=[ride]), _anchor_repos(None)
    )
    assert line.startswith(
        "2026-08-16 (Sun) 07:00 Ride: 90.0 min, weighted power 231 W, 30.6 km/h, load 90.0;"
    )
    assert "recap" not in line
    assert "km," not in line.split(":")[1].split("NP")[0]  # no distance rendered


def test_context_includes_latest_session_section():
    ctx = build_system_context(
        CoachConfig(), None, "", latest_session="2026-08-17 (Mon) Run: 40.0 min"
    )
    assert "## Latest completed session" in ctx
    assert "2026-08-17 (Mon) Run: 40.0 min" in ctx
    assert "Pull the splits before judging it." in ctx


def test_context_omits_latest_session_section_when_absent():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "Latest completed session" not in ctx


# build_scheduled_workouts_line / scheduled_workouts rendering


def _booked_event(
    day: str, *, name: str = "Easy Shakeout", moving_time=None, load=None
):
    from types import SimpleNamespace

    return SimpleNamespace(
        external_id=f"coachie:adhoc:{day}:aaa",
        start_date_local=f"{day}T00:00:00",
        name=name,
        moving_time=moving_time,
        icu_training_load=load,
    )


def test_scheduled_workouts_line_is_none_without_a_client():
    from endury.coach.context import build_scheduled_workouts_line

    assert build_scheduled_workouts_line(None) is None


def test_scheduled_workouts_line_is_none_when_the_read_fails():
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line

    client = MagicMock()
    client.list_events.side_effect = RuntimeError("down")
    assert build_scheduled_workouts_line(client) is None


def test_scheduled_workouts_line_is_none_when_nothing_is_booked():
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line

    client = MagicMock()
    client.list_events.return_value = []
    assert build_scheduled_workouts_line(client) is None


def test_scheduled_workouts_line_lists_only_ad_hoc_events():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line
    from endury.intervals.push import race_external_id, session_external_id
    from endury.planner.foundations.types import GoalType, RaceGoal

    client = MagicMock()
    client.list_events.return_value = [
        SimpleNamespace(
            external_id=session_external_id(7, "w05_run_0"),
            start_date_local="2026-09-19T00:00:00",
            name="Plan session",
            moving_time=None,
            icu_training_load=None,
        ),
        SimpleNamespace(
            external_id=race_external_id(
                7,
                RaceGoal(
                    goal_type=GoalType.RUNNING_10K,
                    race_date=date(2026, 10, 4),
                    priority="A",
                ),
            ),
            start_date_local="2026-10-04T00:00:00",
            name="Race",
            moving_time=None,
            icu_training_load=None,
        ),
        _booked_event("2026-09-19", moving_time=1800, load=45.0),
    ]

    line = build_scheduled_workouts_line(client, today=date(2026, 9, 18))

    assert line == "2026-09-19 (Sat) Easy Shakeout — 30 min — load 45"
    client.list_events.assert_called_once_with(
        date(2026, 9, 18), date(2026, 10, 1), category="WORKOUT"
    )


def test_scheduled_workouts_line_omits_missing_minutes_and_tss():
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line

    client = MagicMock()
    client.list_events.return_value = [_booked_event("2026-09-19")]

    line = build_scheduled_workouts_line(client, today=date(2026, 9, 18))

    assert line == "2026-09-19 (Sat) Easy Shakeout"


def test_scheduled_workouts_line_lists_more_than_one():
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line

    client = MagicMock()
    client.list_events.return_value = [
        _booked_event("2026-09-19", moving_time=1800, load=45.0),
        _booked_event("2026-09-21", name="Strides", moving_time=2400),
    ]

    line = build_scheduled_workouts_line(client, today=date(2026, 9, 18))

    assert line == (
        "2026-09-19 (Sat) Easy Shakeout — 30 min — load 45\n"
        "2026-09-21 (Mon) Strides — 40 min"
    )


def test_scheduled_workouts_line_falls_back_to_the_raw_date_when_unparsable():
    from unittest.mock import MagicMock

    from endury.coach.context import build_scheduled_workouts_line

    event = _booked_event("2026-09-19")
    event.start_date_local = None
    client = MagicMock()
    client.list_events.return_value = [event]

    line = build_scheduled_workouts_line(client, today=date(2026, 9, 18))

    assert line == " Easy Shakeout"


def test_context_includes_scheduled_workouts_section():
    ctx = build_system_context(
        CoachConfig(),
        None,
        "",
        scheduled_workouts="2026-09-19 (Sat) Easy Shakeout — 30 min",
    )
    assert "## Scheduled workouts (booked by you)" in ctx
    assert "2026-09-19 (Sat) Easy Shakeout — 30 min" in ctx
    assert "unschedule_workout takes one back off" in ctx


def test_context_omits_scheduled_workouts_section_when_absent():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "Scheduled workouts" not in ctx


# build_races_section / races rendering


def _race(**overrides):
    from endury.domain.models.race import AthleteRace

    fields = {
        "id": 1,
        "name": "City 10K",
        "race_date": "2026-10-04",
        "goal_type": "running_10k",
        "priority": "B",
        "status": "planned",
        "created_at": "",
        "updated_at": "",
    }
    fields.update(overrides)
    return AthleteRace(**fields)


def test_races_section_lists_upcoming_and_recent(monkeypatch):
    from unittest.mock import MagicMock

    from endury.coach.context import build_races_section

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-18")
    repos = MagicMock()
    repos.races.list.return_value = [
        _race(name="City 10K", race_date="2026-10-04", priority="B"),
        _race(
            id=2,
            name="Spring 5K",
            race_date="2026-08-01",
            priority="C",
            goal_type="running_5k",
            status="completed",
        ),
    ]

    section = build_races_section(repos)

    assert "2026-10-04 (in 2 weeks) City 10K [B], running 10k" in section
    assert "2026-08-01 Spring 5K — completed" in section


def test_races_section_countdown_phrasing(monkeypatch):
    from unittest.mock import MagicMock

    from endury.coach.context import build_races_section

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-18")
    repos = MagicMock()
    repos.races.list.return_value = [
        _race(id=1, name="Race day", race_date="2026-09-18"),
        _race(id=2, name="Next weekend", race_date="2026-09-25"),
    ]

    section = build_races_section(repos)

    assert "(today) Race day" in section
    assert "(in 7 days) Next weekend" in section


def test_races_section_excludes_cancelled_races(monkeypatch):
    from unittest.mock import MagicMock

    from endury.coach.context import build_races_section

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-18")
    repos = MagicMock()
    repos.races.list.return_value = [_race(status="cancelled")]

    assert build_races_section(repos) is None


def test_races_section_is_omitted_when_empty(monkeypatch):
    from unittest.mock import MagicMock

    from endury.coach.context import build_races_section

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-18")
    repos = MagicMock()
    repos.races.list.return_value = []

    assert build_races_section(repos) is None


def test_races_section_never_fails_a_turn():
    from unittest.mock import MagicMock

    from endury.coach.context import build_races_section

    repos = MagicMock()
    repos.races.list.side_effect = RuntimeError("db down")

    assert build_races_section(repos) is None


def test_context_includes_races_section():
    ctx = build_system_context(
        CoachConfig(), None, "", races="- 2026-10-04 (in 2 weeks) City 10K [B]"
    )
    assert "## Races" in ctx
    assert "City 10K" in ctx
    assert "upsert_race is how it changes" in ctx


def test_context_omits_races_section_when_absent():
    ctx = build_system_context(CoachConfig(), None, "")
    assert "## Races" not in ctx


def test_plan_anchor_none_without_active_plan(monkeypatch):
    from endury.coach.context import build_plan_anchor

    assert build_plan_anchor(_anchor_repos(None)) is None


def test_plan_anchor_none_on_repo_error():
    from unittest.mock import MagicMock

    from endury.coach.context import build_plan_anchor

    repos = MagicMock()
    repos.plan.get_active.side_effect = RuntimeError("db down")
    assert build_plan_anchor(repos) is None


def test_no_plan_note_only_when_no_plan_is_active():
    from unittest.mock import MagicMock

    from endury.coach.context import NO_ACTIVE_PLAN, build_no_plan_note

    assert build_no_plan_note(_anchor_repos(None)) == NO_ACTIVE_PLAN
    assert build_no_plan_note(_anchor_repos(_active_plan())) is None
    repos = MagicMock()
    repos.plan.get_active.side_effect = RuntimeError("db down")
    assert build_no_plan_note(repos) is None


def test_plan_anchor_none_on_malformed_plan_dates():
    from types import SimpleNamespace

    from endury.coach.context import build_plan_anchor

    active = SimpleNamespace(id=1, plan_start="not-a-date", goal_date="also-not")
    assert build_plan_anchor(_anchor_repos(active)) is None


def test_plan_anchor_before_plan_start(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-07-01")
    anchor = build_plan_anchor(_anchor_repos(_active_plan()))
    assert anchor == "Today is Wednesday 2026-07-01 — the plan starts 2026-07-06."


def test_plan_anchor_after_goal_date(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-10-10")
    anchor = build_plan_anchor(_anchor_repos(_active_plan()))
    assert anchor == (
        "Today is Saturday 2026-10-10 — the plan's goal date 2026-10-04 has passed."
    )


def test_plan_anchor_session_day_uses_calendar_week_number(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")  # week 5, Thursday
    rows = [
        _plan_row(),
        _plan_row(date="2026-08-08", activity_key="run_long"),
    ]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert anchor == (
        "Today is Thursday 2026-08-06 — plan week 5, build phase; "
        "today's session(s): Running — Run Aerobic Tempo (60 min); "
        "tomorrow (Friday 2026-08-07) is a rest day."
    )


def test_plan_anchor_marks_completed_sessions(monkeypatch):
    """A delivered session must read as done, or the coach keeps
    prescribing adjustments to a workout the athlete already logged."""
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [
        _plan_row(status="completed", matched_activity_id="act-1"),
        _plan_row(activity_key="swim_drill", sport="swimming", duration_minutes=30.0),
    ]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert "Running — Run Aerobic Tempo (60 min) — ALREADY COMPLETED" in anchor
    assert "Swimming — Swim Drill (30 min);" in anchor


def test_plan_anchor_marks_skipped_sessions(monkeypatch):
    """A session removed by a pause or a skip must not read as still
    scheduled; its intervals.icu event is already gone."""
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [
        _plan_row(status="skipped"),
        _plan_row(activity_key="swim_drill", sport="swimming", duration_minutes=30.0),
    ]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert "Running — Run Aerobic Tempo (60 min) — SKIPPED" in anchor
    assert "Swimming — Swim Drill (30 min);" in anchor


def test_plan_anchor_rest_day_and_missing_duration(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [_plan_row(date="2026-08-08", duration_minutes=None)]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert anchor == (
        "Today is Thursday 2026-08-06 — plan week 5, build phase; "
        "today is a rest day; tomorrow (Friday 2026-08-07) is a rest day."
    )


def test_plan_anchor_no_sessions_this_week_omits_phase(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), []))
    assert anchor == (
        "Today is Thursday 2026-08-06 — plan week 5; today is a rest day; "
        "tomorrow (Friday 2026-08-07) is a rest day."
    )


def test_plan_anchor_duration_omitted_when_none(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [_plan_row(duration_minutes=None)]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert "Run Aerobic Tempo;" in anchor


def test_plan_anchor_names_tomorrows_session(monkeypatch):
    """What's on tomorrow is the question the coach most often got wrong:
    the anchor answers it without a tool call."""
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [
        _plan_row(),
        _plan_row(date="2026-08-07", activity_key="run_long", duration_minutes=90.0),
    ]
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), rows))
    assert anchor.endswith(
        "; tomorrow (Friday 2026-08-07): Running — Run Long (90 min)."
    )


def test_plan_anchor_reads_tomorrow_across_the_week_boundary(monkeypatch):
    """On a Sunday, tomorrow is next week's Monday: the read reaches it, and
    the phase still comes from this week."""
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-09")  # Sunday
    rows = [
        _plan_row(date="2026-08-09", activity_key="run_long"),
        _plan_row(date="2026-08-10", activity_key="run_easy", phase_type="peak"),
    ]
    repos = _anchor_repos(_active_plan(), rows)
    anchor = build_plan_anchor(repos)
    assert repos.plan.get_sessions_by_date_range.call_args.args[1:] == (
        "2026-08-03",
        "2026-08-17",
    )
    assert "plan week 5, build phase" in anchor
    assert anchor.endswith(
        "; tomorrow (Monday 2026-08-10): Running — Run Easy (60 min)."
    )


def test_plan_anchor_names_the_next_key_session(monkeypatch):
    """Seen live: every debrief ends on the next key session, and the
    coach twice invented a "vert hike" for it."""
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-08-06")
    rows = [
        _plan_row(family="tempo", status="completed", matched_activity_id="a1"),
        _plan_row(date="2026-08-07", activity_key="run_easy", family="easy"),
        _plan_row(
            date="2026-08-08", activity_key="run_vo2", family="interval", optional=True
        ),
        _plan_row(
            date="2026-08-09", activity_key="run_long", family="long", status="skipped"
        ),
        _plan_row(
            date="2026-08-11",
            activity_key="run_long",
            family="long",
            duration_minutes=90.0,
        ),
    ]
    repos = _anchor_repos(_active_plan(), rows)
    anchor = build_plan_anchor(repos)
    assert anchor.endswith(
        "; next key session: Tuesday 2026-08-11, Running — Run Long (90 min)."
    )
    assert repos.plan.get_sessions_by_date_range.call_args.args[1:] == (
        "2026-08-03",
        "2026-08-17",
    )


def test_plan_anchor_omits_tomorrow_on_goal_day(monkeypatch):
    from endury.coach.context import build_plan_anchor

    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-10-04")
    anchor = build_plan_anchor(_anchor_repos(_active_plan(), []))
    assert "tomorrow" not in anchor


def test_context_renders_plan_anchor_section():
    ctx = build_system_context(
        CoachConfig(),
        AthleteProfile(name="Sam"),
        "",
        plan_anchor="Today is 2026-08-06 — plan week 5, build phase; today is a rest day.",
    )
    assert "## Today" in ctx
    assert "plan week 5" in ctx


# build_sport_doctrine


def _plan_with_goal(goal_type, ambition=None):
    from types import SimpleNamespace

    return _anchor_repos(SimpleNamespace(id=1, goal_type=goal_type), ambition=ambition)


def test_sport_doctrine_covers_every_trail_goal():
    from endury.coach.context import build_sport_doctrine

    for goal, label in (
        ("trail_20k", "Trail 20K"),
        ("trail_50k", "Trail 50K"),
        ("trail_100k", "Trail 100K"),
    ):
        doctrine = build_sport_doctrine(_plan_with_goal(goal))
        assert doctrine.startswith(label), goal
        # The trail core the four categories share.
        assert "day 2 is 25-50% shorter" in doctrine
        assert "At least 2 weeks between descent bouts" in doctrine
        assert "The taper cuts volume, not terrain" in doctrine
        assert "4 h or more is the fuelling-rehearsal floor" in doctrine
        assert "Pace on grade is noise" in doctrine
        assert "Weekly vert at peak by tier:" in doctrine
        # The measured walk/run crossover, so the coach never invents one.
        assert "near 28% grade (about 16 degrees)" in doctrine
        # Citations live in code comments, never in the athlete-visible text.
        assert "tr §" not in doctrine
        # The block stays compact enough to ride in every system prompt.
        assert len(doctrine.splitlines()) <= 25, goal


def test_sport_doctrine_vert_line_names_only_the_plans_own_tier():
    from endury.coach.context import build_sport_doctrine

    doctrine = build_sport_doctrine(_plan_with_goal("trail_100k", "moderate"))
    assert "Weekly vert target at your tier: 3,500-4,500 m at peak." in doctrine
    # Only the plan's own tier appears; other tiers' bands must not leak in.
    assert "2,500-3,500 m" not in doctrine
    assert "4,500-6,000 m" not in doctrine
    assert "recreational" not in doctrine


def test_sport_doctrine_vert_line_per_ambition_tier():
    from endury.coach.context import build_sport_doctrine

    for ambition, band in (
        ("basic", "2,500-3,500 m"),
        ("moderate", "3,500-4,500 m"),
        ("competitive", "4,500-6,000 m"),
    ):
        doctrine = build_sport_doctrine(_plan_with_goal("trail_100k", ambition))
        assert f"Weekly vert target at your tier: {band} at peak." in doctrine


def test_sport_doctrine_vert_line_labels_every_tier_without_a_macro_row():
    from endury.coach.context import build_sport_doctrine

    doctrine = build_sport_doctrine(_plan_with_goal("trail_100k"))
    assert (
        "- Weekly vert at peak by tier: recreational 2,500-3,500 m, moderate "
        "3,500-4,500 m, competitive 4,500-6,000 m — quote only the athlete's "
        "tier." in doctrine
    )


def test_sport_doctrine_vert_line_falls_back_on_unreadable_ambition():
    from endury.coach.context import build_sport_doctrine

    repos = _plan_with_goal("trail_100k", "heroic")
    assert "Weekly vert at peak by tier:" in build_sport_doctrine(repos)


def test_sport_doctrine_vert_line_falls_back_when_the_macro_read_fails():
    from endury.coach.context import build_sport_doctrine

    repos = _plan_with_goal("trail_100k", "moderate")
    repos.plan.get_macro_for_plan.side_effect = RuntimeError("db down")
    assert "Weekly vert at peak by tier:" in build_sport_doctrine(repos)


def test_sport_doctrine_carries_category_specific_rules():
    from endury.coach.context import build_sport_doctrine

    assert "No back-to-backs at this distance" in build_sport_doctrine(
        _plan_with_goal("trail_20k")
    )
    assert "Back-to-backs are optional here" in build_sport_doctrine(
        _plan_with_goal("trail_50k")
    )
    assert "Back-to-backs are the defining structure" in build_sport_doctrine(
        _plan_with_goal("trail_100k")
    )


def test_sport_doctrine_covers_every_maintenance_goal():
    """Every maintenance goal renders its own doctrine, so the coach never
    prescribes progression or extra work the block deliberately withholds."""
    from endury.coach.context import build_sport_doctrine

    for goal, label in (
        ("running_maintenance", "Running maintenance block"),
        ("trail_maintenance", "Trail running maintenance block"),
        ("triathlon_maintenance", "Triathlon maintenance block"),
    ):
        doctrine = build_sport_doctrine(_plan_with_goal(goal))
        assert doctrine.startswith(label), goal
        assert "do not add work the plan deliberately withholds" in doctrine
        assert "Nothing here builds toward a race" in doctrine
        assert "One quality touch a week" in doctrine or "one quality session" in (
            doctrine
        ), goal
        assert "The long session stays moderate" in doctrine, goal
        assert "fitness retest — one per sport" in doctrine, goal
        assert "a race plan through the wizard" in doctrine, goal
        assert len(doctrine.splitlines()) <= 10, goal


def test_sport_doctrine_maintenance_family_flavours():
    from endury.coach.context import build_sport_doctrine

    trail = build_sport_doctrine(_plan_with_goal("trail_maintenance"))
    assert "hills and sustained climbing leading the rotation" in trail
    # The same bands coach_brief prints for TRAIL_MAINTENANCE.
    assert (
        "Weekly climb by tier: Basic 450-750 m, Moderate 700-1,100 m, "
        "Competitive 900-1,500 m" in trail
    )
    tri = build_sport_doctrine(_plan_with_goal("triathlon_maintenance"))
    assert "Every sport keeps a weekly touch" in tri


def test_sport_doctrine_maintenance_hours_name_only_the_plans_own_tier():
    from endury.coach.context import build_sport_doctrine

    doctrine = build_sport_doctrine(
        _plan_with_goal("triathlon_maintenance", "moderate")
    )
    assert "- Weekly volume at your tier: 8.5 h." in doctrine
    # Neither the basic (5 h) nor the competitive (10 h) target; "8.5 h"
    # itself ends in "5 h", hence the boundary.
    assert re.search(r"(?<![\d.])(5|10) h", doctrine) is None


def test_sport_doctrine_maintenance_hours_label_every_tier_without_a_macro_row():
    from endury.coach.context import build_sport_doctrine

    doctrine = build_sport_doctrine(_plan_with_goal("running_maintenance"))
    assert (
        "- Weekly volume by tier: Basic 3 h, Moderate 4.5 h, Competitive 6 h "
        "— quote only the athlete's tier." in doctrine
    )


def test_sport_doctrine_none_for_non_trail_goal():
    from endury.coach.context import build_sport_doctrine

    assert build_sport_doctrine(_plan_with_goal("running_marathon")) is None


def test_sport_doctrine_none_without_active_plan():
    from endury.coach.context import build_sport_doctrine

    assert build_sport_doctrine(_anchor_repos(None)) is None


def test_sport_doctrine_none_on_unknown_goal_type():
    from endury.coach.context import build_sport_doctrine

    assert build_sport_doctrine(_plan_with_goal("skyrunning_vertical_km")) is None


def test_sport_doctrine_none_on_repo_error():
    from unittest.mock import MagicMock

    from endury.coach.context import build_sport_doctrine

    repos = MagicMock()
    repos.plan.get_active.side_effect = RuntimeError("db down")
    assert build_sport_doctrine(repos) is None


def test_context_renders_sport_doctrine_for_a_trail_plan():
    from endury.coach.context import build_sport_doctrine

    ctx = build_system_context(
        CoachConfig(),
        AthleteProfile(name="Sam"),
        "",
        sport_doctrine=build_sport_doctrine(_plan_with_goal("trail_100k")),
    )
    assert "## Sport doctrine" in ctx
    assert "Trail 100K — these are the plan's own design rules." in ctx
    assert "never add descent work ad hoc" in ctx


def test_context_omits_sport_doctrine_for_a_marathon_plan():
    from endury.coach.context import build_sport_doctrine

    ctx = build_system_context(
        CoachConfig(),
        AthleteProfile(name="Sam"),
        "",
        sport_doctrine=build_sport_doctrine(_plan_with_goal("running_marathon")),
    )
    assert "Sport doctrine" not in ctx
    assert "back-to-back" not in ctx.lower()


# Due check-in and profile completion


def test_context_surfaces_a_check_in_whose_date_has_arrived(monkeypatch):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2027-03-04")
    document = "## Notes\n- Next check-in 2027-03-01: pick a spring race"
    ctx = build_system_context(CoachConfig(), None, document)
    assert "## Due check-in" in ctx
    assert "revisit this on 2027-03-01: pick a spring race" in ctx
    assert "rewrite the memory document without that line" in ctx


def test_context_holds_a_future_check_in_back(monkeypatch):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-14")
    document = "## Notes\n- Next check-in 2027-03-01: pick a spring race"
    ctx = build_system_context(CoachConfig(), None, document)
    assert "## Due check-in" not in ctx


def test_context_asks_to_re_confirm_a_constraint_under_review(monkeypatch):
    # An illness note sat in every prompt for a week after the athlete raced.
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-26")
    document = (
        "## Constraints (injury, availability)\n"
        "- Viral illness, dizziness (review 2026-09-24)\n"
        "- Travel Oct 10-12 (review 2026-10-13)\n"
    )
    ctx = build_system_context(CoachConfig(), None, document)
    section = ctx[ctx.index("## Constraints to re-confirm") :]
    assert "- Viral illness, dizziness (review 2026-09-24)" in section
    assert "Travel" not in section.split("Their review date")[0]
    assert "do not advise from them" in section


def test_context_has_no_review_section_before_any_is_due(monkeypatch):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-20")
    document = "## Constraints (injury, availability)\n- Sick (review 2026-09-24)\n"
    ctx = build_system_context(
        CoachConfig(), None, document, memory_written=date(2026, 9, 18)
    )
    assert "## Constraints to re-confirm" not in ctx


def test_context_renders_every_due_check_in_in_one_section(monkeypatch):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2027-03-04")
    document = (
        "## Notes\n"
        "- Next check-in 2027-03-01: pick a spring race\n"
        "- Next check-in 2027-02-01: book the training camp"
    )
    ctx = build_system_context(CoachConfig(), None, document)
    assert ctx.count("## Due check-in") == 1
    assert "pick a spring race" in ctx
    assert "book the training camp" in ctx


def test_context_asks_a_name_only_athlete_for_sport_and_level():
    ctx = build_system_context(CoachConfig(), AthleteProfile(name="Sam"), "")
    assert "Profile Note" in ctx
    assert "experience level" in ctx
    assert "update_athlete_profile" in ctx


def test_context_asks_a_name_and_sport_athlete_for_level_only():
    profile = AthleteProfile(name="Sam", primary_sport="running")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Profile Complete" not in ctx
    assert "Profile Note" in ctx
    assert "experience level" in ctx
    assert "update_athlete_profile" in ctx


def test_context_complete_profile_gets_no_profile_section():
    profile = AthleteProfile(
        name="Sam", primary_sport="running", experience_level="advanced"
    )
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Profile Complete" not in ctx
    assert "Profile Note" not in ctx


# Profile nudge gate + experience fallback


@pytest.mark.parametrize(
    "profile",
    [
        AthleteProfile(primary_sport="running"),
        AthleteProfile(name="Sam", primary_sport="running"),
        AthleteProfile(name="Sam", experience_level="advanced"),
        AthleteProfile(name="Sam"),
    ],
)
def test_context_drops_every_profile_note_when_the_nudge_is_gated(profile):
    """An illness turn or a tool round is no place for an intake question."""
    ctx = build_system_context(CoachConfig(), profile, "", nudge_profile=False)
    assert "Profile Note" not in ctx


def test_context_keeps_the_profile_note_by_default():
    ctx = build_system_context(CoachConfig(), AthleteProfile(name="Sam"), "")
    assert "Profile Note" in ctx


def test_context_pitches_an_unknown_experience_level_instead_of_asking():
    profile = AthleteProfile(name="Sam", primary_sport="running")
    ctx = build_system_context(
        CoachConfig(),
        profile,
        "",
        nudge_profile=False,
        experience_fallback="beginner",
    )
    # The pitch must read as the app's own default guess, not a fact the
    # model should save back via update_athlete_profile as the athlete's
    # stated answer.
    assert "pitch at beginner" in ctx
    assert "app default" in ctx
    assert "never save" in ctx.lower()


def test_context_prints_a_known_experience_level_unchanged():
    profile = AthleteProfile(name="Sam", experience_level="advanced")
    ctx = build_system_context(CoachConfig(), profile, "")
    assert "Experience Level: advanced" in ctx
    assert "pitch as" not in ctx


@pytest.mark.parametrize(
    ("ambition", "pitch"),
    [
        ("basic", "beginner"),
        ("moderate", "intermediate"),
        ("competitive", "advanced"),
    ],
)
def test_experience_fallback_follows_the_plans_ambition_tier(ambition, pitch):
    from endury.coach.context import build_experience_fallback

    assert build_experience_fallback(_plan_with_goal("running_10k", ambition)) == pitch


def test_experience_fallback_is_intermediate_without_a_readable_tier():
    from endury.coach.context import build_experience_fallback

    assert build_experience_fallback(_anchor_repos(None)) == "intermediate"
    assert build_experience_fallback(_plan_with_goal("running_10k")) == "intermediate"
    assert (
        build_experience_fallback(_plan_with_goal("running_10k", "whatever"))
        == "intermediate"
    )


def test_experience_fallback_survives_a_repo_error():
    from unittest.mock import MagicMock

    from endury.coach.context import build_experience_fallback

    repos = MagicMock()
    repos.plan.get_active.side_effect = RuntimeError("db gone")
    assert build_experience_fallback(repos) == "intermediate"
