"""Wire models for the chat endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

__all__ = ["ChatRequest", "MAX_HISTORY", "MAX_QUESTION"]

MAX_QUESTION = 600
MAX_HISTORY = 8


class ChatRequest(BaseModel):
    """One question, with the run it is about and the turns before it.

    The run is sent by the client rather than looked up, because a run is not
    persisted: it is recomputed on demand and lives in the browser's store. The
    client already has it, and re-deriving it server-side would mean storing
    run state this app deliberately does not keep (ADR-024: slider values are
    per-run inputs, never persisted onto the scene).
    """

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    run: dict | None = Field(None, description="The run result this question is about.")
    history: list[dict[str, str]] = Field(
        default_factory=list,
        description="Prior turns as {role, text}, oldest first. Last 8 are used.",
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()

    @field_validator("history")
    @classmethod
    def _cap(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        return value[-MAX_HISTORY:]
