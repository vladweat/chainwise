from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    stream: bool = False

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be empty")
        return stripped


class Citation(BaseModel):
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    content: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    cached: bool = False
    tokens_used: int | None = None
    cost_usd: float | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    cached: bool = False
    created_at: datetime | None = None
