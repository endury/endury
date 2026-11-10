"""The coach's brief tool: the deterministic sheet for a coming session or race.

The sheet is built by :func:`endury.planning.brief.build_brief` with no AI
model, the same one the session and race sheets show, so the coach and the
page quote the same numbers. Nothing here writes anything.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from typing import Any

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.registry import coach_tool
from endury.intervals.client import IntervalsClient
from endury.planning.brief import build_brief
from endury.wiring.container import RepositoryContainer


def _longest_session(
    repos: RepositoryContainer, day: date_type
) -> tuple[Any, list[str]]:
    """The active plan's longest session on ``day`` and the other sessions' keys."""
    plan = repos.plan.get_active()
    if plan is None:
        return None, []
    rows = repos.plan.get_sessions_by_date_range(
        plan.id, day.isoformat(), day.isoformat()
    )
    if not rows:
        return None, []
    rows = sorted(rows, key=lambda row: row.duration_minutes or 0, reverse=True)
    return rows[0], [row.activity_key for row in rows[1:]]


def make_brief_tools(
    repos: RepositoryContainer, intervals_client: IntervalsClient
) -> list:
    """Create the brief tool with injected dependencies.

    Args:
        repos: Repository container (the plan and the race table).
        intervals_client: Read client for intervals.icu history and wellness.

    Returns:
        List holding the ``get_brief`` coach tool.
    """

    @coach_tool
    def get_brief(
        session_id: int | None = None,
        race_date: str | None = None,
        date: str | None = None,
    ) -> str:
        """Get the brief for an upcoming planned session or race.

        The sheet holds the athlete's state going in (form, wellness, recent
        sessions), what to hold (heart-rate and power anchors from their own
        proven efforts and the session's card), fuel and water, kit from
        recent conditions, routes that fit, a finish-time range for a race,
        and what success looks like. Use it whenever the athlete asks how to
        approach, pace, fuel or dress for a coming session or race, or asks
        for a brief. Give exactly one of ``session_id``, ``race_date`` or
        ``date``. Read-only; the sheet is deterministic.

        Reply contract — when relaying the result:
        * Every number comes from the sheet: the athlete's own efforts and
          the card. Never state a pace, heart rate, power or time the sheet
          does not hold.
        * Say plainly what ``thin`` names as ungrounded; do not fill the gap.
        * A quick brief is three sentences: the one thing to hold, the fuel
          line, the one risk.
        * A full brief is short paragraphs with bold lead-ins **Where you
          stand**, **What to hold**, **Fuel and water**, **Kit and
          conditions**, **What success looks like**. A race adds **The race**
          first and **Finish time** after the hold.
        * End on a statement, never a question.
        * A change you recommend to the planned session is a card in the same
          turn (``swap_planned_session``, ``adjust_current_week``,
          ``skip_planned_session``), never a sentence.
        * When ``other_sessions_that_day`` is present, name those sessions.

        Args:
            session_id: The session's id from the request ("(session 412)").
            race_date: ISO date of the race, from "(race on 2026-10-24)" or
                the races list.
            date: ISO date (YYYY-MM-DD) to brief that day's planned session;
                with several, the longest is briefed.

        Returns:
            JSON string with the brief sheet, or a message when nothing is
            planned.
        """
        try:
            if [session_id, race_date, date].count(None) != 2:
                return json.dumps(
                    {"message": "Give exactly one of session_id, race_date or date."}
                )
            others: list[str] = []
            if date is not None:
                row, others = _longest_session(repos, date_type.fromisoformat(date))
                if row is None:
                    return json.dumps({"message": f"No session is planned on {date}."})
                session_id = int(row.id)
            sheet = build_brief(
                repos=repos,
                client=intervals_client,
                session_id=session_id,
                race_date=race_date,
            )
            if others:
                sheet = {**sheet, "other_sessions_that_day": others}
            return json.dumps(sheet, ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"message": str(exc)})
        except Exception as exc:
            return tool_error_response("build the brief", exc)

    return [get_brief]
