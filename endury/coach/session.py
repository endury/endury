"""Coach conversation session: lifecycle, chat, Apply cards, and shutdown.

Conversation history lives in the ``conversation_history`` table. On start a
session resumes the recent turns (back to the last ``reset`` marker); "New
Chat" writes a fresh marker so the next load starts clean.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from endury.coach.messages import SENT_AT_KEY, Message
from endury.coach.proposals import ApplyResult, Proposal
from endury.coach.proposals import apply_proposal as run_apply_proposal
from endury.coach.proposals import refresh_proposal as run_refresh_proposal
from endury.coach.reply.finalizer import SessionFinalizer
from endury.coach.tools.analysis_tools import charts_for_model, with_chart_lines
from endury.coach.turn import TurnResult, run_turn
from endury.coach.welcome import generate_welcome_message
from endury.common import clock

logger = logging.getLogger(__name__)

# Persisted in place of the coach's reply when a turn dies with no reply
# produced. Write tools have already committed to SQLite and intervals.icu by
# then, so the turn must leave a trace either way.
_INTERRUPTED_NOTE = "(This reply was interrupted by an error before it finished.)"


class CoachSession:
    """Conversation-scoped coach runtime session."""

    def __init__(self, runtime: CoachRuntime) -> None:
        self.runtime = runtime
        self._initialized = False
        self.history: list[Message] = []

        self.finalizer = SessionFinalizer(runtime)
        self._llm: Any = None

    # Lifecycle

    def _load_history(self) -> list[Message]:
        """Load recent conversation turns, back to the last reset marker.

        Every message carries its stored ``created_at`` as ``sent_at``,
        which :func:`endury.coach.messages.stamp_user_messages` renders
        into the LLM input so a window spanning days cannot read last
        night's "tomorrow" — the athlete's or the coach's own — as still
        tomorrow. The content itself stays raw.
        """
        limit = self.runtime.config.max_message_history_messages
        try:
            entries = self.runtime.repos.history.get_recent(limit)
        except Exception as exc:
            logger.warning("Could not load conversation history: %s", exc)
            return []

        messages: list[Message] = []
        for entry in entries:
            if entry.role == "reset":
                messages = []
                continue
            if entry.role in {"user", "assistant"} and entry.content:
                messages.append(
                    {
                        "role": entry.role,
                        "content": charts_for_model(entry.content),
                        SENT_AT_KEY: entry.created_at,
                    }
                )
        return messages

    async def initialize(self, *, reset: bool = False) -> str:
        """Load context and history, and return a fresh welcome message.

        Args:
            reset: When True, start a new conversation — a reset marker is
                written so prior turns are dropped from context.
        """
        logger.info("Initializing CoachSession (reset=%s)...", reset)
        self.runtime.run_initializer()
        self.runtime.enrich_context()
        self._llm = self.runtime.coach_llm()

        if reset:
            try:
                self.runtime.repos.history.add("reset", "")
            except Exception as exc:
                logger.warning("Could not write reset marker: %s", exc)

        self.history = self._load_history()

        memory_empty = not self.runtime.memory_repo.get().content.strip()
        welcome = generate_welcome_message(
            self.runtime.athlete_profile,
            self.runtime.config,
            self.runtime.recent_health_summary,
            memory_empty=memory_empty,
        )
        self._initialized = True
        return welcome

    async def aclose(self) -> None:
        """Release session resources. No-op — history is persisted per turn."""
        return

    # Chat

    async def chat(self, user_message: str, *, isolated: bool = False) -> TurnResult:
        """Run one coaching turn and return its result.

        The turn is recorded even when it dies part-way: by then the coach's
        write tools have already changed the plan in SQLite and on
        intervals.icu, and dropping the turn would orphan those changes. The
        exception still propagates to the caller.

        An ``isolated`` turn (a take or a brief asked from a sheet) sees no
        conversation and leaves none: the model answers from the athlete's
        data and the request alone, and the reply lives with its activity or
        session, so the chat neither colours it nor carries it.
        """
        if not self._initialized:
            await self.initialize()

        self.runtime.enrich_context()

        if isolated:
            turn_log: list[Message] = []
            try:
                return await run_turn(
                    self.runtime, [], user_message, llm=self._llm, turn_log=turn_log
                )
            finally:
                self.finalizer.finalize(turn_log)

        self._persist("user", user_message)
        # Captured at persist time so this turn's stamp matches the one the
        # next load reads off `created_at`.
        sent_at = clock.now().isoformat()

        context_history = list(self.history)
        turn_log = []
        result: TurnResult | None = None

        try:
            result = await run_turn(
                self.runtime,
                context_history,
                user_message,
                llm=self._llm,
                turn_log=turn_log,
            )
        finally:
            final_text = result.reply if result is not None else _INTERRUPTED_NOTE
            stored = with_chart_lines(final_text, result.charts if result else [])
            self.history.append(
                {"role": "user", "content": user_message, SENT_AT_KEY: sent_at}
            )
            self.history.append(
                {
                    "role": "assistant",
                    "content": charts_for_model(stored),
                    SENT_AT_KEY: clock.now().isoformat(),
                }
            )
            self._persist("assistant", stored)
            self.finalizer.finalize(context_history + turn_log)
        return result

    # Apply cards

    async def apply_proposal(self, proposal: Proposal | dict[str, Any]) -> ApplyResult:
        """Apply a card the athlete tapped, and tell the model what happened.

        A landed write is appended to the conversation as ``[Applied: …]`` in
        the athlete's voice — they did it — so the next turn sees the
        outcome. A replacement card or a refusal writes nothing and leaves
        no note: the athlete still has the card in front of them.

        Args:
            proposal: The card, as a :class:`Proposal` or its wire dict.
        """
        card = (
            proposal if isinstance(proposal, Proposal) else Proposal.from_dict(proposal)
        )
        outcome = await asyncio.to_thread(
            run_apply_proposal, self.runtime.tools, self.runtime.repos, card
        )
        if outcome.status == "applied":
            self._note(f"[Applied: {outcome.message or card.preview_summary}]")
        return outcome

    async def refresh_proposal(
        self, proposal: Proposal | dict[str, Any]
    ) -> ApplyResult:
        """Bring an open card up to date after another card wrote the plan.

        Writes nothing and leaves no note: the athlete has not answered it.

        Args:
            proposal: The card, as a :class:`Proposal` or its wire dict.
        """
        card = (
            proposal if isinstance(proposal, Proposal) else Proposal.from_dict(proposal)
        )
        return await asyncio.to_thread(
            run_refresh_proposal, self.runtime.tools, self.runtime.repos, card
        )

    async def dismiss_proposal(self, proposal: Proposal | dict[str, Any]) -> None:
        """Record that the athlete dismissed a card, so the model stops offering it.

        Args:
            proposal: The card, as a :class:`Proposal` or its wire dict.
        """
        card = (
            proposal if isinstance(proposal, Proposal) else Proposal.from_dict(proposal)
        )
        self._note(f"[Dismissed: {card.preview_summary}]")

    def _note(self, content: str) -> None:
        """Append an athlete-side note to the conversation, stamped and persisted."""
        self._persist("user", content)
        self.history.append(
            {"role": "user", "content": content, SENT_AT_KEY: clock.now().isoformat()}
        )

    def _persist(self, role: str, content: str) -> None:
        """Persist a conversation entry, tolerating repository failures."""
        try:
            self.runtime.repos.history.add(role, content)
        except Exception as exc:
            logger.warning("Could not persist conversation entry: %s", exc)


if TYPE_CHECKING:
    from endury.coach.runtime import CoachRuntime
