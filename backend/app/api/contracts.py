import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.user import User
from app.schemas.chat import ChatMessageOut, ChatRequest, ChatResponse
from app.schemas.contract import (
    AnalyzeRequest,
    AnalyzeResponse,
    ContractSourceResponse,
    ContractStatusResponse,
    FindingsResponse,
)
from app.services.contract_service import (
    analyze_by_address,
    analyze_by_upload,
    chat_contract,
    chat_contract_sse,
    delete_contract,
    get_contract_report_pdf,
    get_contract_source,
    get_contract_status,
    list_chat_history,
    list_contract_findings,
    list_contracts_for_user,
)
from app.services.rag_engine import OpenAINotConfiguredError, OpenAIRequestError, RagError
from app.services.upload_handler import UploadTooLargeError, read_upload_file
from app.utils.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/contracts", tags=["contracts"])


def require_analyze_limit(current_user: User = Depends(get_current_user)) -> User:
    enforce_rate_limit("analyze", current_user.id, get_settings().rate_limit_analyze_per_minute)
    return current_user


def require_chat_limit(current_user: User = Depends(get_current_user)) -> User:
    enforce_rate_limit("chat", current_user.id, get_settings().rate_limit_chat_per_minute)
    return current_user


@router.get("", response_model=list[ContractStatusResponse])
def list_contracts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContractStatusResponse]:
    return list_contracts_for_user(db, current_user)


@router.post("/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_202_ACCEPTED)
def analyze_contract(
    body: AnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyze_limit),
) -> AnalyzeResponse:
    return analyze_by_address(db, current_user, body.address, body.chain_id)


@router.post("/analyze/upload", response_model=AnalyzeResponse, status_code=status.HTTP_202_ACCEPTED)
async def analyze_upload(
    file: UploadFile = File(...),
    chain_id: int = Form(default=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyze_limit),
) -> AnalyzeResponse:
    try:
        content = await read_upload_file(file)
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    filename = file.filename or "contract.sol"
    return analyze_by_upload(db, current_user, filename, content, chain_id=chain_id)


@router.get("/{contract_id}/status", response_model=ContractStatusResponse)
def contract_status(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContractStatusResponse:
    return get_contract_status(db, contract_id, current_user)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    delete_contract(db, contract_id, current_user)


@router.get("/{contract_id}/findings", response_model=FindingsResponse)
def contract_findings(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FindingsResponse:
    return list_contract_findings(db, contract_id, current_user)


@router.get("/{contract_id}/source", response_model=ContractSourceResponse)
def contract_source(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContractSourceResponse:
    return get_contract_source(db, contract_id, current_user)


@router.get("/{contract_id}/report")
def contract_report(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    pdf_bytes, filename = get_contract_report_pdf(db, contract_id, current_user)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{contract_id}/chat", response_model=list[ChatMessageOut])
def contract_chat_history(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageOut]:
    return list_chat_history(db, current_user, contract_id)


@router.post("/{contract_id}/chat", response_model=None)
def contract_chat(
    contract_id: int,
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_chat_limit),
) -> ChatResponse | StreamingResponse:
    if body.stream:
        try:
            events = chat_contract_sse(db, current_user, contract_id, body.question)
            first = next(events)
        except StopIteration:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Empty chat stream",
            ) from None
        except OpenAINotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except (OpenAIRequestError, RagError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        def _chain():
            yield first
            try:
                yield from events
            except (OpenAIRequestError, RagError) as exc:
                payload = json.dumps({"type": "error", "content": str(exc)}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            _chain(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = chat_contract(db, current_user, contract_id, body.question)
    except OpenAINotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (OpenAIRequestError, RagError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return result.to_response()
