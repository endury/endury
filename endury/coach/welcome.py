"""Welcome message generation for the coach agent."""

from __future__ import annotations

import logging
import secrets

from endury.common import clock
from endury.domain.models.athlete import AthleteProfile
from endury.wiring.config import CoachConfig

logger = logging.getLogger(__name__)


def select_opener(config: CoachConfig, name: str) -> str:
    """Select a short, natural opener based on time of day.

    Args:
        config: Coach config carrying the per-daypart opener templates.
        name: The athlete's name, interpolated into the opener.

    Returns:
        A greeting line that sets a human tone — no score judgment.
    """
    pools = {
        "night": config.night_openers,
        "early morning": config.early_morning_openers,
        "morning": config.morning_openers,
        "midday": config.midday_openers,
        "afternoon": config.afternoon_openers,
        "evening": config.evening_openers,
    }
    return str(secrets.choice(pools[clock.daypart(clock.now().hour)]).format(name=name))


def _format_conversation_options(has_wellness: bool, memory_empty: bool) -> str:
    """Return a short markdown list of conversation starters.

    Args:
        has_wellness: Whether recent wellness data is available.
        memory_empty: Whether the athlete-memory document is empty — offers
            an "About you" starter so background gets gathered early.

    Returns:
        A markdown prompt listing where the athlete can start.
    """
    options: list[str] = []
    if memory_empty:
        options.append(
            "**About you** — fill me in on your background so I can coach you properly"
        )
    if has_wellness:
        options.append("**Recovery** — how the body's doing")
    options.append("**Recent training** — review what's been logged")
    options.append(
        "**Charts** — ask me to plot a session or a trend, "
        "like your pace per km in your last race"
    )
    options.append("**Plan** — what's coming up")
    options.append("**Goals** — training targets or race strategy")

    lines = "\n".join(f"- {o}" for o in options)
    return f"Where do you want to start?\n\n{lines}"


def generate_welcome_message(
    profile: AthleteProfile | None,
    config: CoachConfig,
    health_summary: str | None = None,
    memory_empty: bool = False,
) -> str:
    """Generate a context-aware welcome message.

    Args:
        profile: The athlete's profile, or None if not set up yet.
        config: Coach config carrying the opener templates.
        health_summary: Recent wellness snapshot, when available.
        memory_empty: Whether the athlete-memory document is empty — offers
            an "About you" starter so background gets gathered early.

    Returns:
        The welcome message to show the athlete.
    """
    name = profile.name if profile else None
    sport = profile.primary_sport if profile else None
    level = profile.experience_level if profile else None

    if name:
        opener = select_opener(config, name)
        options = _format_conversation_options(
            has_wellness=health_summary is not None, memory_empty=memory_empty
        )
        return f"{opener}\n\n{options}"

    if sport or level:
        if level and sport:
            known_str = f"an {level} {sport} athlete"
        elif level:
            known_str = f"an {level} athlete"
        else:
            known_str = f"a {sport} athlete"
        return (
            f"Hey! I'm an AI model, and I can see you're {known_str}. "
            "I just don't have your name yet "
            "— what should I call you?"
        )

    return config.welcome_message
