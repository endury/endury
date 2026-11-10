"""Pydantic models for the athlete-memory document and chat history."""

from pydantic import BaseModel


class AthleteMemory(BaseModel):
    """The athlete-memory document: one markdown text and its last write."""

    content: str
    updated_at: str | None = None


class AthleteMemoryVersion(BaseModel):
    """A superseded revision of the athlete-memory document."""

    id: int
    content: str
    created_at: str


class ConversationHistoryEntry(BaseModel):
    """A single conversation history record."""

    id: int
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: str
