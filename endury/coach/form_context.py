"""Fitness, fatigue and form for the coach: one reading, read every turn.

Endury's own Banister EMA (:mod:`endury.intervals.fitness`) over
intervals.icu's per-activity ``icu_training_load``, from completed sessions
only. The system context's "Form today" line and the training-load tool
read it through the same functions, so the coach never holds two figures
for one day. intervals.icu's own fitness chart counts the day's planned
workout too, so it reads a little lower until the session is done.

Every reader is fault tolerant: no client, no network or no measured load
degrades to "no form line" rather than failing a chat turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import DAILY_TO_WEEKLY_TSS, per_sport_ctl_atl
from endury.intervals.models import IntervalsActivity

logger = logging.getLogger(__name__)

#: Warmup buffer (days) fetched before the reporting window so the CTL/ATL
#: EMA does not seed from zero.
CTL_WARMUP_DAYS = 42

#: Minimum history (days) fed into the CTL/ATL EMA, so the reported form
#: does not drift with how many weeks the caller asks to summarize.
PMC_HISTORY_DAYS = 90


@dataclass(frozen=True)
class Form:
    """Combined fitness and fatigue as of one day, with the per-sport split.

    Attributes:
        ctl: Combined fitness (daily TSS), unrounded.
        atl: Combined fatigue (daily TSS), unrounded.
        by_sport: Sport name -> rounded ``ctl``, ``atl`` and ``tsb``; a sport
            whose activities carry no load is absent.
    """

    ctl: float
    atl: float
    by_sport: dict[str, dict[str, float]]

    @property
    def tsb(self) -> float:
        """Combined form, rounded to one decimal."""
        return round(self.ctl - self.atl, 1)


def form_as_of(activities: list[IntervalsActivity], as_of: date) -> Form:
    """Run the per-sport EMA over ``activities`` up to and including ``as_of``.

    Args:
        activities: Activities across all sports, fetched with a warmup window.
        as_of: The day the form holds for.
    """
    by_sport: dict[str, dict[str, float]] = {}
    total_ctl = 0.0
    total_atl = 0.0
    for sport, measured in per_sport_ctl_atl(activities, as_of).items():
        if measured is None:  # sport trained, but no load recorded
            continue
        ctl = measured[0] / DAILY_TO_WEEKLY_TSS
        atl = measured[1] / DAILY_TO_WEEKLY_TSS
        total_ctl += ctl
        total_atl += atl
        by_sport[sport.value] = {
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(ctl - atl, 1),
        }
    return Form(total_ctl, total_atl, by_sport)


def fetch_form(
    client: IntervalsClient, as_of: date, weeks_back: int = 4
) -> tuple[Form, list[IntervalsActivity]]:
    """Fetch a warmup-seeded activity window ending on ``as_of`` and read its form.

    Args:
        client: intervals.icu read client.
        as_of: Last day of the window.
        weeks_back: Trailing weeks the caller wants to report on; the fetch
            widens to at least the PMC history plus the EMA warmup so the
            reported CTL/ATL does not depend on it.

    Returns:
        The form as of ``as_of`` and the activities it was read from.
    """
    history_days = max(weeks_back * 7, PMC_HISTORY_DAYS) + CTL_WARMUP_DAYS
    activities = client.list_activities(as_of - timedelta(days=history_days), as_of)
    return form_as_of(activities, as_of), activities


def tsb_zone_read(tsb: float) -> str:
    """Classify a TSB value into the coaching zone read.

    Bands follow the TSB heuristic table in
    docs/research/endurance_methodology.md (Coggan's PMC convention) — these
    are coaching heuristics, not peer-reviewed thresholds.
    """
    if tsb > 25:
        return "very fresh — above the typical race-ready range"
    if tsb >= 15:
        return "race-ready freshness"
    if tsb >= 5:
        return "fresh — suitable for key sessions or a B-race"
    if tsb >= -10:
        return "maintenance — neither building nor recovering significantly"
    if tsb >= -30:
        return "productive overload / functional overreaching territory"
    return "deep fatigue: ease off"


def build_form_line(client: IntervalsClient | None) -> str | None:
    """Build the "Form today" line for the system prompt.

    Today's fitness, fatigue and form from completed sessions, dated, with
    yesterday's form beside it so a move reads as a move: the coach advises
    from this line, never from a figure it quoted on an earlier day.

    Args:
        client: intervals.icu client, or None when not configured.

    Returns:
        The line, or None without a client, without any measured load, or
        when the API is unreachable — never fails a turn.
    """
    if client is None:
        return None
    today = clock.today()
    try:
        form, activities = fetch_form(client, today)
    except Exception as exc:
        logger.warning("Could not load intervals.icu activities for form: %s", exc)
        return None
    if not form.by_sport:
        return None
    yesterday = form_as_of(activities, today - timedelta(days=1))
    return (
        f"As of {today.strftime('%A %Y-%m-%d')}, from completed sessions: "
        f"fitness {form.ctl:.1f}, fatigue {form.atl:.1f}, "
        f"form {form.tsb:+.1f} — {tsb_zone_read(form.tsb)}; "
        f"yesterday's form {yesterday.tsb:+.1f}."
    )
