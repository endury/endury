"""Tests for endury.coach.memory_cleanup — the "Tidy up" proposal builder."""

from __future__ import annotations

from datetime import date

import pytest

from endury.coach.memory_cleanup import (
    CLEANUP_INSTRUCTIONS,
    MemoryCleanupError,
    build_cleanup_messages,
    propose_memory_cleanup,
    validate_memory_document,
)
from endury.common.memory_notes import (
    MEMORY_CHAR_CAP,
    MEMORY_DOCUMENT_RULES,
    MEMORY_HEADINGS,
)
from tests.doubles import FakeLLM

VALID_DOCUMENT = (
    "## Goals & races\n"
    "- 2026-10-04 Berlin Marathon\n\n"
    "## Constraints (injury, availability)\n"
    "- None\n\n"
    "## Preferences\n"
    "- Morning workouts\n\n"
    "## Notes\n"
    "- 2026-09-01: felt strong"
)


class TestBuildCleanupMessages:
    """build_cleanup_messages — the system/user pair sent to the model."""

    def test_carries_the_rules_instructions_date_and_document(self):
        today = date(2026, 9, 17)
        messages = build_cleanup_messages(VALID_DOCUMENT, today)

        assert messages[0]["role"] == "system"
        assert MEMORY_DOCUMENT_RULES in messages[0]["content"]
        assert CLEANUP_INSTRUCTIONS in messages[0]["content"]
        assert "2026-09-17" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": VALID_DOCUMENT}


class TestValidateMemoryDocument:
    """validate_memory_document — the shape check on the model's reply."""

    def test_strips_a_fenced_reply_with_a_language_tag(self):
        fenced = f"```markdown\n{VALID_DOCUMENT}\n```"
        assert validate_memory_document(fenced) == VALID_DOCUMENT

    def test_strips_a_fenced_reply_without_a_language_tag(self):
        fenced = f"```\n{VALID_DOCUMENT}\n```"
        assert validate_memory_document(fenced) == VALID_DOCUMENT

    def test_rejects_an_empty_reply(self):
        with pytest.raises(MemoryCleanupError):
            validate_memory_document("   ")

    def test_rejects_a_reply_missing_a_heading(self):
        broken = VALID_DOCUMENT.replace("## Preferences\n- Morning workouts\n\n", "")
        with pytest.raises(MemoryCleanupError, match="Preferences"):
            validate_memory_document(broken)

    def test_rejects_reordered_headings(self):
        reordered = (
            "## Notes\n- 2026-09-01: felt strong\n\n"
            "## Goals & races\n- 2026-10-04 Berlin Marathon\n\n"
            "## Constraints (injury, availability)\n- None\n\n"
            "## Preferences\n- Morning workouts"
        )
        with pytest.raises(MemoryCleanupError, match="reordered"):
            validate_memory_document(reordered)

    def test_rejects_a_reply_over_the_cap(self):
        bulky = VALID_DOCUMENT + ("\n- filler" * MEMORY_CHAR_CAP)
        with pytest.raises(MemoryCleanupError, match="cap"):
            validate_memory_document(bulky)

    def test_rejects_a_constraint_without_a_review_date(self):
        undated = VALID_DOCUMENT.replace("- None", "- Sore knee")
        with pytest.raises(MemoryCleanupError, match="review date: - Sore knee"):
            validate_memory_document(undated)

    def test_rejects_a_proposal_that_drops_a_goal(self):
        # Tidy-up cannot write the race table, so a race it drops is gone.
        original = VALID_DOCUMENT.replace(
            "- 2026-10-04 Berlin Marathon", "- 2026-10-04 Berlin Marathon\n- Ridge 37k"
        )
        with pytest.raises(MemoryCleanupError, match="changed a goal: - Ridge 37k"):
            validate_memory_document(VALID_DOCUMENT, original)

    def test_keeps_a_proposal_that_keeps_every_goal(self):
        assert validate_memory_document(VALID_DOCUMENT, VALID_DOCUMENT)

    def test_instructions_keep_goals_and_date_constraints(self):
        assert "Copy every bullet under" in CLEANUP_INSTRUCTIONS
        assert "Drop Notes about races already run" not in CLEANUP_INSTRUCTIONS
        assert "(review <today + 14 days>)" in CLEANUP_INSTRUCTIONS

    def test_instructions_keep_only_the_latest_plan_note(self):
        # The app notes a plan as "committed", "replanned" or "deleted", never
        # "replaced", so a later plan note is what supersedes an earlier one.
        assert (
            'keep only the latest "committed", "replanned" or "deleted" plan note'
            in " ".join(CLEANUP_INSTRUCTIONS.split())
        )

    def test_returns_stripped_text_on_the_happy_path(self):
        assert validate_memory_document(f"  {VALID_DOCUMENT}  \n") == VALID_DOCUMENT

    def test_rejects_commentary_before_the_document(self):
        chatty = f"Sure! Here is the tidied document:\n\n{VALID_DOCUMENT}"
        with pytest.raises(MemoryCleanupError, match="before the document"):
            validate_memory_document(chatty)

    def test_a_heading_mentioned_inside_a_bullet_does_not_count(self):
        bullets = "\n".join(f"- see {heading}" for heading in MEMORY_HEADINGS)
        with pytest.raises(MemoryCleanupError, match="dropped"):
            validate_memory_document(bullets)

    def test_a_deeper_heading_level_does_not_count(self):
        demoted = VALID_DOCUMENT.replace("## Notes", "### Notes")
        with pytest.raises(MemoryCleanupError, match="Notes"):
            validate_memory_document(demoted)


class TestProposeMemoryCleanup:
    """propose_memory_cleanup — the model round-trip plus validation."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_the_validated_proposal(self):
        llm = FakeLLM([VALID_DOCUMENT])
        result = await propose_memory_cleanup(llm, VALID_DOCUMENT, date(2026, 9, 17))
        assert result == VALID_DOCUMENT

    @pytest.mark.asyncio
    async def test_a_failing_model_call_is_chained_into_memory_cleanup_error(self):
        class BoomLLM:
            async def complete(self, messages, tools=None, model=None):
                raise RuntimeError("openrouter is down")

        with pytest.raises(MemoryCleanupError, match="openrouter is down") as excinfo:
            await propose_memory_cleanup(BoomLLM(), VALID_DOCUMENT, date(2026, 9, 17))
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    @pytest.mark.asyncio
    async def test_a_missing_content_field_raises_memory_cleanup_error(self):
        llm = FakeLLM([{"role": "assistant"}])
        with pytest.raises(MemoryCleanupError):
            await propose_memory_cleanup(llm, VALID_DOCUMENT, date(2026, 9, 17))

    @pytest.mark.asyncio
    async def test_an_empty_content_field_raises_memory_cleanup_error(self):
        llm = FakeLLM([""])
        with pytest.raises(MemoryCleanupError):
            await propose_memory_cleanup(llm, VALID_DOCUMENT, date(2026, 9, 17))
