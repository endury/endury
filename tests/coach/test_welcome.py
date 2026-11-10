"""Tests for endury.coach.welcome module."""

from unittest.mock import patch

import pytest

from endury.coach.welcome import generate_welcome_message, select_opener
from endury.domain.models.athlete import AthleteProfile
from endury.wiring.config import CoachConfig

pytestmark = pytest.mark.coach


# select_opener


def test_select_opener_returns_string():
    result = select_opener(CoachConfig(), "Sam")
    assert isinstance(result, str)
    assert len(result) > 0


def test_select_opener_includes_name_in_some_form():
    config = CoachConfig()
    results = [select_opener(config, "Sam") for _ in range(10)]
    assert any("Sam" in r for r in results)


@pytest.mark.parametrize(
    ("hour", "attr"),
    [
        (2, "night_openers"),
        (7, "early_morning_openers"),
        (10, "morning_openers"),
        (13, "midday_openers"),
        (17, "afternoon_openers"),
        (20, "evening_openers"),
        (23, "night_openers"),
    ],
)
def test_select_opener_picks_the_daypart_bucket(hour, attr):
    config = CoachConfig()
    with (
        patch("endury.coach.welcome.clock.now") as mock_now,
        patch(
            "endury.coach.welcome.secrets.choice",
            return_value=getattr(config, attr)[0],
        ) as choice,
    ):
        mock_now.return_value.hour = hour
        select_opener(config, "Sam")
    assert choice.call_args.args[0] == getattr(config, attr)


# generate_welcome_message


def test_generate_welcome_no_profile_returns_string():
    result = generate_welcome_message(None, CoachConfig(), None)
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_welcome_with_name_lists_starters():
    profile = AthleteProfile(name="Sam", primary_sport="triathlon")
    result = generate_welcome_message(profile, CoachConfig(), None)
    assert "Where do you want to start?" in result
    assert "**Recent training**" in result
    assert "**Charts**" in result
    assert "**Recovery**" not in result


def test_generate_welcome_with_name_includes_name():
    config = CoachConfig()
    profile = AthleteProfile(name="Sam")
    results = [generate_welcome_message(profile, config, None) for _ in range(10)]
    assert any("Sam" in r for r in results)


def test_generate_welcome_offers_recovery_when_wellness_is_known():
    profile = AthleteProfile(name="Sam")
    result = generate_welcome_message(
        profile, CoachConfig(), "Last 7 days — sleep 7.5h/night avg over 7 nights"
    )
    assert "**Recovery**" in result


def test_generate_welcome_sport_only_mentions_sport():
    profile = AthleteProfile(primary_sport="cycling")
    result = generate_welcome_message(profile, CoachConfig(), None)
    assert "cycling" in result


def test_generate_welcome_level_only_mentions_level():
    profile = AthleteProfile(experience_level="advanced")
    result = generate_welcome_message(profile, CoachConfig(), None)
    assert "advanced" in result


def test_generate_welcome_level_and_sport_no_name():
    profile = AthleteProfile(primary_sport="cycling", experience_level="advanced")
    result = generate_welcome_message(profile, CoachConfig(), None)
    assert "advanced" in result
    assert "cycling" in result


def test_generate_welcome_asks_for_name_when_sport_but_no_name():
    profile = AthleteProfile(primary_sport="running")
    result = generate_welcome_message(profile, CoachConfig(), None)
    assert "name" in result.lower() or "call you" in result.lower()


def test_generate_welcome_offers_about_you_when_memory_empty():
    profile = AthleteProfile(name="Sam")
    result = generate_welcome_message(profile, CoachConfig(), None, memory_empty=True)
    assert "**About you**" in result


def test_generate_welcome_omits_about_you_when_memory_present():
    profile = AthleteProfile(name="Sam")
    result = generate_welcome_message(profile, CoachConfig(), None, memory_empty=False)
    assert "**About you**" not in result
