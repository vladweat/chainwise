"""Shared RAG types and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.chat import ChatResponse, Citation


class RagError(Exception):
    """Raised when retrieval or generation fails."""


class OpenAINotConfiguredError(RagError):
    """OPENAI_API_KEY is missing."""


class OpenAIRequestError(RagError):
    """OpenAI HTTP call failed."""


@dataclass(frozen=True)
class RetrievedChunk:
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    content: str
    kind: str = ""
    score: float = 0.0

    def to_citation(self) -> Citation:
        return Citation(
            file_path=self.file_path,
            function_name=self.function_name,
            start_line=self.start_line,
            end_line=self.end_line,
            content=self.content,
        )

    def identity(self) -> tuple:
        return (self.file_path, self.function_name, self.start_line, self.end_line)


@dataclass
class LLMUsage:
    tokens_used: int | None = None
    cost_usd: Decimal | None = None


@dataclass
class ChatResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    cached: bool = False
    tokens_used: int | None = None
    cost_usd: Decimal | None = None

    def to_response(self) -> ChatResponse:
        cost = float(self.cost_usd) if self.cost_usd is not None else None
        return ChatResponse(
            answer=self.answer,
            citations=self.citations,
            cached=self.cached,
            tokens_used=self.tokens_used,
            cost_usd=cost,
        )
