from datetime import datetime

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    address: str = Field(..., examples=["0x1234567890123456789012345678901234567890"])
    chain_id: int = Field(..., examples=[1])


class AnalyzeResponse(BaseModel):
    task_id: str
    contract_id: int
    status: str
    reused: bool = False


class FindingOut(BaseModel):
    check: str
    impact: str
    confidence: str
    location: str
    description: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    function_name: str | None = None


class FindingsResponse(BaseModel):
    contract_id: int
    findings: list[FindingOut]


class SourceFileOut(BaseModel):
    path: str
    content: str


class ContractSourceResponse(BaseModel):
    contract_id: int
    files: list[SourceFileOut]


class ContractStatusResponse(BaseModel):
    contract_id: int
    address: str
    chain_id: int
    status: str
    indexing_step: str | None = None
    risk_score: str | None = None
    risk_model: str | None = None
    features_version: str | None = None
    compiler_version: str | None = None
    name: str | None = None
    chunk_count: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None
