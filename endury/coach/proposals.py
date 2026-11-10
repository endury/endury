"""Apply cards: how a coach write reaches the athlete, and how they apply it.

The model never confirms anything. Every tool whose signature has a
``confirm`` parameter runs as a preview inside a turn — :func:`run_tool`
forces ``confirm=False`` and drops ``expected_input_hash`` — and a preview
that offers a write becomes a :class:`Proposal`: a card the athlete sees next
to the reply, with Apply and Dismiss. :func:`apply_proposal` is the one place
``confirm=True`` is ever passed, and only with the arguments the athlete saw.

A proposal is stamped with the day it was made and the active plan's id and
last write, so applying it later re-previews instead of writing when the
world moved underneath it: a replan changes session ids, ``pause_training``
starts "today", and ``create_plan`` defaults to this Monday.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from endury.coach.card_summary import preview_summary
from endury.coach.tools.registry import HIDDEN_PARAMETERS, CoachTool, execute_tool
from endury.common import clock

logger = logging.getLogger(__name__)

#: The tools whose success changes the plan or the intervals.icu calendar —
#: what tells the UI its calendar is stale (a threshold change moves the
#: targets the calendar shows for booked workouts). ``update_memory`` and
#: ``update_athlete_profile`` write the database but never the plan;
#: ``build_workout`` never persists anything.
PLAN_WRITE_TOOLS = frozenset(
    {
        "create_plan",
        "set_plan_directives",
        "add_race_to_plan",
        "set_session_status",
        "skip_planned_session",
        "move_planned_session",
        "replan_active_plan",
        "adjust_current_week",
        "swap_planned_session",
        "pause_training",
        "schedule_workout",
        "schedule_workout_override",
        "unschedule_workout",
        "upsert_race",
        "cancel_or_move_race",
        "set_athlete_thresholds",
    }
)

#: The argument naming what one card changes, for the tools whose cards each
#: change one thing: a turn keeps one card per tool *and* target, so two
#: sessions swapped in one reply are two cards (Seen live: "run 45
#: minutes and ride shorter" kept only the ride's). The other card tools
#: change the plan as a whole and keep one card each.
_CARD_TARGETS: dict[str, str] = {
    "swap_planned_session": "session_id",
    "skip_planned_session": "session_id",
    "schedule_workout_override": "date",
    "unschedule_workout": "date",
    "add_race_to_plan": "race_id",
    "cancel_or_move_race": "race_id",
    "set_athlete_thresholds": "sport",
}


@dataclass(frozen=True)
class Proposal:
    """One write the athlete can apply, exactly as it was previewed.

    Attributes:
        id: An opaque handle for the card.
        tool: The tool that previewed it.
        args: The arguments the preview ran with, minus ``confirm`` — plus
            the preview's own ``input_hash`` as ``expected_input_hash`` for
            a tool that takes one, so the apply can prove it is the plan the
            athlete saw.
        preview_summary: One line, in the athlete's language, saying what
            applying does (``card_summary.preview_summary``).
        date: The day the preview was made (ISO).
        plan_id: The active plan's id at preview time, or None.
        plan_revision: The active plan's last write at preview time, or None.
    """

    id: str
    tool: str
    args: dict[str, Any]
    preview_summary: str
    date: str
    plan_id: int | None
    plan_revision: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the wire shape the UI holds and hands back."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Proposal:
        """Rebuild a proposal the UI handed back.

        Args:
            data: A :meth:`to_dict` payload.
        """
        return cls(
            id=str(data["id"]),
            tool=str(data["tool"]),
            args=dict(data.get("args") or {}),
            preview_summary=str(data.get("preview_summary") or ""),
            date=str(data["date"]),
            plan_id=data.get("plan_id"),
            plan_revision=data.get("plan_revision"),
        )


@dataclass
class ApplyResult:
    """What applying a proposal did.

    Attributes:
        status: ``"applied"`` when the write landed; ``"preview"`` when the
            athlete gets a replacement card instead (``proposal`` set);
            otherwise the tool's own status (``"error"``, ``"refused"``,
            ``"not_found"``, …) and nothing changed.
        message: The tool's own message, for the UI and the history note.
        result: The tool's parsed payload, or None when it was not JSON.
        proposal: The replacement card, when ``status`` is ``"preview"``.
        plan_changed: Whether the plan or the calendar changed.
        reason: ``"stale"`` when the stamp no longer matched and the tool
            was re-previewed instead of applied, else ``""``.
    """

    status: str
    message: str = ""
    result: dict[str, Any] | None = None
    proposal: Proposal | None = None
    plan_changed: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the wire shape."""
        data = asdict(self)
        data["proposal"] = self.proposal.to_dict() if self.proposal else None
        return data


def card_key(name: str, args: dict[str, Any]) -> tuple[str, Any]:
    """Name what a card changes: its tool, and the target when it has one.

    A later preview with the same key replaces the earlier card (the model
    refining its arguments); a different target is a card of its own.

    Args:
        name: The previewing tool.
        args: The arguments it ran with.
    """
    target = _CARD_TARGETS.get(name)
    return name, args.get(target) if target else None


def find_tool(tools: list[CoachTool], name: str) -> CoachTool | None:
    """Return the registered tool named *name*, or None."""
    return next((candidate for candidate in tools if candidate.name == name), None)


def is_gated(tool: CoachTool) -> bool:
    """Return whether *tool* previews first — its signature has ``confirm``."""
    return "confirm" in inspect.signature(tool.fn).parameters


def run_tool(tools: list[CoachTool], name: str, args: dict[str, Any]) -> str:
    """Dispatch one tool call from the model, never as a confirmed write.

    A gated tool runs with ``confirm`` forced False and the app-only
    ``expected_input_hash`` dropped, whatever the model sent; every other
    tool runs as called.

    Args:
        tools: The available tools.
        name: The tool name requested by the model.
        args: Keyword arguments parsed from the model's tool call.

    Returns:
        The tool's JSON string result.
    """
    tool = find_tool(tools, name)
    if tool is not None and is_gated(tool):
        args = {
            key: value for key, value in args.items() if key not in HIDDEN_PARAMETERS
        }
        args["confirm"] = False
    return execute_tool(tools, name, args)


def _parse_object(result: str) -> dict[str, Any] | None:
    """Return *result* parsed as a JSON object, else None."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_preview(parsed: dict[str, Any]) -> bool:
    """Whether a payload previews a write the athlete can apply."""
    return (
        parsed.get("status") == "preview"
        and parsed.get("confirm_required") is not False
    )


def _offers_write(name: str, parsed: dict[str, Any]) -> bool:
    """Whether a preview-time payload should become a card.

    A ``pause_training`` window with nothing pending and nothing booked
    comes back ``"empty"``, yet applying it still records the window as
    unavailable on the plan — so with an active plan it is a card too.
    """
    if _is_preview(parsed):
        return True
    return (
        name == "pause_training"
        and parsed.get("status") == "empty"
        and not parsed.get("no_plan")
    )


def plan_stamp(repos: Any) -> tuple[int | None, str | None]:
    """Return the active plan's ``(id, revision)``, or ``(None, None)``.

    The revision digests the plan row's last write *and* every session's
    id, date, status, activity and load: skips, moves and swaps never touch
    ``plans.updated_at``, and a card previewed before one of them must not
    apply against the changed plan.

    Args:
        repos: The repository container.
    """
    try:
        active = repos.plan.get_active()
        if active is None:
            return None, None
        sessions = repos.plan.get_sessions(active.id)
    except Exception:
        logger.warning("Could not read the active plan for a proposal", exc_info=True)
        return None, None
    rows = sorted(
        (s.id, str(s.date), s.status, s.activity_key, s.target_tss) for s in sessions
    )
    digest = hashlib.sha1(
        json.dumps([str(active.updated_at), rows], default=str).encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return int(active.id), digest


def proposal_from_result(
    tools: list[CoachTool],
    repos: Any,
    name: str,
    args: dict[str, Any],
    result: str,
) -> Proposal | None:
    """Turn a preview-time tool result into a card, when it offers a write.

    Args:
        tools: The available tools.
        repos: The repository container, for the plan stamp.
        name: The tool that ran.
        args: The arguments it ran with.
        result: Its raw JSON string result.

    Returns:
        The proposal, or None when the tool is not gated, the result is not
        a JSON object, or it offers nothing to apply (a refusal, an error, a
        no-fit race, an adjustment with nothing to adjust).
    """
    tool = find_tool(tools, name)
    if tool is None or not is_gated(tool):
        return None
    parsed = _parse_object(result)
    if parsed is None or not _offers_write(name, parsed):
        return None
    plan_id, plan_revision = plan_stamp(repos)
    kept = {key: value for key, value in args.items() if key not in HIDDEN_PARAMETERS}
    if "expected_input_hash" in inspect.signature(tool.fn).parameters and isinstance(
        parsed.get("input_hash"), str
    ):
        kept["expected_input_hash"] = parsed["input_hash"]
    return Proposal(
        id=uuid.uuid4().hex,
        tool=name,
        args=kept,
        preview_summary=preview_summary(name, parsed),
        date=clock.today().isoformat(),
        plan_id=plan_id,
        plan_revision=plan_revision,
    )


def is_stale(proposal: Proposal, repos: Any) -> bool:
    """Whether the world moved since *proposal* was previewed.

    Args:
        proposal: The card the athlete is applying.
        repos: The repository container, for today's plan stamp.
    """
    if proposal.date != clock.today().isoformat():
        return True
    return (proposal.plan_id, proposal.plan_revision) != plan_stamp(repos)


def _refreshed(
    tools: list[CoachTool], repos: Any, proposal: Proposal, result: str
) -> ApplyResult:
    """Build the outcome of a call that did not write: a new card or a refusal."""
    parsed = _parse_object(result)
    message = str(parsed.get("message") or "") if parsed else result
    replacement = (
        proposal_from_result(tools, repos, proposal.tool, proposal.args, result)
        if parsed is not None
        else None
    )
    if replacement is not None:
        return ApplyResult("preview", message, parsed, replacement)
    status = str(parsed.get("status") or "error") if parsed else "error"
    if status == "preview":
        # A preview that offers nothing to apply (a race that no longer
        # fits): "preview" promises a replacement card, so it is a refusal.
        status = "refused"
    return ApplyResult(status, message, parsed)


def _re_preview(tools: list[CoachTool], repos: Any, proposal: Proposal) -> ApplyResult:
    """Re-run a stale card's preview: a replacement card or a refusal, no write."""
    logger.info(
        "Proposal %s for %s went stale; re-previewing", proposal.id, proposal.tool
    )
    refreshed = _refreshed(
        tools, repos, proposal, run_tool(tools, proposal.tool, proposal.args)
    )
    refreshed.reason = "stale"
    return refreshed


def refresh_proposal(
    tools: list[CoachTool], repos: Any, proposal: Proposal
) -> ApplyResult:
    """Bring an open card up to date after another card wrote the plan.

    Applying one card of a reply moves the week the others describe; the
    shell refreshes them at once, so the athlete sees the current numbers
    and one tap applies each. Never writes and leaves no note.

    Args:
        tools: The available tools.
        repos: The repository container.
        proposal: The open card.

    Returns:
        ``"current"`` (no card) when its stamp still matches; otherwise what
        a stale apply returns: a replacement card or a refusal.
    """
    if find_tool(tools, proposal.tool) is None:
        return ApplyResult("error", f"Unknown tool '{proposal.tool}'.")
    if not is_stale(proposal, repos):
        return ApplyResult("current")
    return _re_preview(tools, repos, proposal)


def apply_proposal(
    tools: list[CoachTool], repos: Any, proposal: Proposal
) -> ApplyResult:
    """Run the write the athlete just applied, or hand back a fresh card.

    A stale stamp (another day, another plan, a plan written since) means
    the athlete is agreeing to something the preview no longer describes:
    the tool re-previews and the outcome is a replacement card, never a
    write. Otherwise the tool runs with ``confirm=True`` and the proposal's
    own arguments; a result that is itself a preview (``create_plan``'s
    freshness check declining to commit) becomes a replacement card too.

    Args:
        tools: The available tools.
        repos: The repository container.
        proposal: The card the athlete applied.

    Returns:
        What happened, see :class:`ApplyResult`.
    """
    tool = find_tool(tools, proposal.tool)
    if tool is None:
        return ApplyResult("error", f"Unknown tool '{proposal.tool}'.")
    if is_stale(proposal, repos):
        return _re_preview(tools, repos, proposal)
    result = execute_tool(tools, tool.name, {**proposal.args, "confirm": True})
    parsed = _parse_object(result)
    status = parsed.get("status") if parsed else None
    applied = status == "ok" or (tool.name == "pause_training" and status == "empty")
    if not applied:
        return _refreshed(tools, repos, proposal, result)
    return ApplyResult(
        "applied",
        str(parsed.get("message") or "") if parsed else "",
        parsed,
        plan_changed=tool.name in PLAN_WRITE_TOOLS,
    )


__all__ = [
    "PLAN_WRITE_TOOLS",
    "ApplyResult",
    "Proposal",
    "apply_proposal",
    "card_key",
    "find_tool",
    "is_gated",
    "is_stale",
    "plan_stamp",
    "proposal_from_result",
    "refresh_proposal",
    "run_tool",
]
