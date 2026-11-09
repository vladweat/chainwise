"""Contract analyze / status / chat orchestration."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.celery_config import load_contract_task
from app.models.contract import Contract
from app.models.contract_status import (
    ContractStatus,
    is_completed,
    is_indexing,
    require_chat_ready,
    require_report_ready,
    set_contract_status,
)
from app.models.user import User
from app.schemas.chat import ChatMessageOut
from app.schemas.contract import (
    AnalyzeResponse,
    ContractSourceResponse,
    ContractStatusResponse,
    FindingOut,
    FindingsResponse,
    SourceFileOut,
)
from app.services.chat_history import list_chat_messages
from app.services.pdf_report import build_contract_report_pdf, extract_slither_findings
from app.services.qdrant_indexer import delete_contract_chunks
from app.services.risk_scorer import FEATURES_VERSION, model_path
from app.services.rag_engine import ChatResult, answer_question, iter_sse_events
from app.services.etherscan_loader import (
    EtherscanContractNotFoundError,
    EtherscanError,
    EtherscanNetworkError,
    EtherscanNotConfiguredError,
    fetch_contract_source,
)
from app.services.solc_compiler import SolcCompileError, split_source_files
from app.services.upload_handler import UploadError, UploadTooLargeError, parse_upload
from app.services.indexing_lifecycle import mark_attempt_failed
from app.utils.address import is_supported_chain, is_valid_address, normalize_address
from app.utils.quota import (
    enforce_analyze_quota,
    enforce_chat_quota,
    enforce_max_contracts,
)
from app.utils.redis_lock import acquire_indexing_lock, is_indexing_locked, release_indexing_lock

logger = logging.getLogger(__name__)


def _analyze_response(
    contract: Contract, task_id: str | None = None, *, reused: bool = False
) -> AnalyzeResponse:
    return AnalyzeResponse(
        task_id=task_id or contract.celery_task_id or "",
        contract_id=contract.id,
        status=contract.status,
        reused=reused,
    )


def _status_response(contract: Contract) -> ContractStatusResponse:
    risk_model = contract.risk_model
    features_version = contract.features_version
    if contract.risk_score and not risk_model:
        risk_model = "xgboost" if model_path() else "heuristic"
        features_version = features_version or FEATURES_VERSION
    return ContractStatusResponse(
        contract_id=contract.id,
        address=contract.address,
        chain_id=contract.chain_id,
        status=contract.status,
        indexing_step=contract.indexing_step,
        risk_score=contract.risk_score,
        risk_model=risk_model,
        features_version=features_version,
        compiler_version=contract.compiler_version,
        name=contract.name,
        chunk_count=contract.chunk_count,
        last_error=contract.last_error,
        created_at=contract.created_at,
    )


def get_contract_for_user(db: Session, contract_id: int, user: User) -> Contract:
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    if user.role == "admin":
        return contract
    if contract.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    return contract


def _find_own_contract(
    db: Session, user: User, address: str, chain_id: int
) -> Contract | None:
    return (
        db.query(Contract)
        .filter(
            Contract.user_id == user.id,
            Contract.address == address,
            Contract.chain_id == chain_id,
        )
        .first()
    )


def _reset_for_reindex(contract: Contract) -> None:
    set_contract_status(contract, ContractStatus.INDEXING, actor="analyze")
    contract.indexing_step = None
    contract.risk_score = None
    contract.risk_model = None
    contract.features_version = None
    contract.slither_report = None
    contract.ast = None
    contract.chunk_count = None
    contract.last_error = None
    contract.indexing_claimed_at = None


def _start_indexing_attempt(contract: Contract) -> tuple[str, str]:
    attempt_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    contract.indexing_attempt_id = attempt_id
    contract.indexing_heartbeat_at = datetime.now(UTC)
    contract.indexing_claimed_at = None
    contract.celery_task_id = task_id
    return attempt_id, task_id


def _enqueue_indexing(
    db: Session,
    user: User,
    *,
    address: str,
    chain_id: int,
    source_code: str,
    name: str | None,
    compiler_version: str | None,
    is_verified: bool | None,
    existing: Contract | None,
) -> AnalyzeResponse:
    if existing is not None:
        if is_indexing(existing.status) or is_indexing_locked(user.id, chain_id, address):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contract is already being indexed",
            )
        if is_completed(existing.status):
            return _analyze_response(existing, reused=True)

    enforce_analyze_quota(user.id)
    if existing is None:
        enforce_max_contracts(db, user.id)

    lock_owner_token = acquire_indexing_lock(user.id, chain_id, address)
    if lock_owner_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contract is already being indexed",
        )

    attempt_id = None
    task_id = None
    try:
        if existing is not None:
            contract = existing
            contract.source_code = source_code
            contract.name = name
            contract.compiler_version = compiler_version
            contract.is_verified = is_verified
            _reset_for_reindex(contract)
        else:
            contract = Contract(
                user_id=user.id,
                address=address,
                chain_id=chain_id,
                name=name,
                source_code=source_code,
                compiler_version=compiler_version,
                is_verified=is_verified,
                status=ContractStatus.INDEXING.value,
            )
            db.add(contract)

        attempt_id, task_id = _start_indexing_attempt(contract)

        db.commit()
        db.refresh(contract)

        try:
            load_contract_task.apply_async(
                task_id=task_id,
                args=(contract.id, lock_owner_token, user.id, chain_id, address, attempt_id),
                argsrepr=(
                    f"({contract.id}, '<redacted-owner-token>', {user.id}, {chain_id}, "
                    "'<redacted-address>', '<attempt-id>')"
                ),
            )
        except Exception:
            try:
                mark_attempt_failed(
                    db,
                    contract.id,
                    attempt_id,
                    "Task dispatch failed; retry analysis after broker recovery.",
                )
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.exception(
                    "Could not persist enqueue failure for contract %s", contract.id
                )
            raise

        return AnalyzeResponse(
            task_id=task_id,
            contract_id=contract.id,
            status=ContractStatus.INDEXING.value,
        )
    except Exception:
        try:
            release_indexing_lock(user.id, chain_id, address, lock_owner_token)
        except Exception:
            logger.exception("Failed to release owned indexing lock after enqueue failure")
        raise


def analyze_by_address(
    db: Session,
    user: User,
    address: str,
    chain_id: int,
) -> AnalyzeResponse:
    if not is_valid_address(address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid contract address")

    if not is_supported_chain(chain_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported chain_id: {chain_id}")

    normalized = normalize_address(address)
    existing = _find_own_contract(db, user, normalized, chain_id)

    # Pre-check reuse / conflict before fetching remote source (saves Etherscan quota).
    if existing is not None:
        if is_indexing(existing.status) or is_indexing_locked(user.id, chain_id, normalized):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contract is already being indexed",
            )
        if is_completed(existing.status):
            return _analyze_response(existing, reused=True)

    try:
        source = fetch_contract_source(normalized, chain_id)
    except EtherscanNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except EtherscanContractNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EtherscanNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except EtherscanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _enqueue_indexing(
        db,
        user,
        address=source.address,
        chain_id=source.chain_id,
        source_code=source.source_code,
        name=source.name,
        compiler_version=source.compiler_version,
        is_verified=source.is_verified,
        existing=existing,
    )


def analyze_by_upload(
    db: Session,
    user: User,
    filename: str,
    content: bytes,
    chain_id: int = 0,
) -> AnalyzeResponse:
    try:
        uploaded = parse_upload(filename, content, chain_id=chain_id)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except UploadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    existing = _find_own_contract(db, user, uploaded.address, uploaded.chain_id)
    return _enqueue_indexing(
        db,
        user,
        address=uploaded.address,
        chain_id=uploaded.chain_id,
        source_code=uploaded.source_code,
        name=uploaded.name,
        compiler_version=uploaded.compiler_version,
        is_verified=uploaded.is_verified,
        existing=existing,
    )


def get_contract_status(db: Session, contract_id: int, user: User) -> ContractStatusResponse:
    contract = get_contract_for_user(db, contract_id, user)
    return _status_response(contract)


def delete_contract(db: Session, contract_id: int, user: User) -> None:
    contract = get_contract_for_user(db, contract_id, user)
    try:
        delete_contract_chunks(contract.id)
    except Exception as exc:
        logger.warning("Qdrant cleanup failed for contract %s: %s", contract.id, exc)
    db.delete(contract)
    db.commit()
    logger.info("Deleted contract %s for user %s", contract_id, user.id)


def list_contract_findings(db: Session, contract_id: int, user: User) -> FindingsResponse:
    contract = get_contract_for_user(db, contract_id, user)
    raw = extract_slither_findings(contract.slither_report)
    findings = [FindingOut(**item) for item in raw]
    return FindingsResponse(contract_id=contract.id, findings=findings)


def _source_filename(contract: Contract) -> str:
    name = (contract.name or "").strip()
    if not name:
        return "Contract.sol"
    if name.lower().endswith(".sol"):
        return name
    return f"{name}.sol"


def get_contract_source(db: Session, contract_id: int, user: User) -> ContractSourceResponse:
    contract = get_contract_for_user(db, contract_id, user)
    filename = _source_filename(contract)
    try:
        mapping = split_source_files(contract.source_code or "", default_filename=filename)
    except SolcCompileError:
        mapping = {filename: contract.source_code or ""}
    files = [SourceFileOut(path=path, content=content) for path, content in mapping.items()]
    return ContractSourceResponse(contract_id=contract.id, files=files)


def get_contract_report_pdf(db: Session, contract_id: int, user: User) -> tuple[bytes, str]:
    contract = get_contract_for_user(db, contract_id, user)
    require_report_ready(contract)
    pdf_bytes = build_contract_report_pdf(contract)
    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in (contract.name or "contract")
    ).strip("-") or "contract"
    filename = f"chainwise-{safe_name}-{contract.id}.pdf"
    return pdf_bytes, filename


def list_contracts_for_user(db: Session, user: User) -> list[ContractStatusResponse]:
    query = db.query(Contract)
    if user.role != "admin":
        query = query.filter(Contract.user_id == user.id)
    contracts = query.order_by(Contract.id.desc()).limit(50).all()
    return [_status_response(contract) for contract in contracts]


def require_ready_contract(db: Session, contract_id: int, user: User) -> Contract:
    contract = get_contract_for_user(db, contract_id, user)
    require_chat_ready(contract)
    return contract


def list_chat_history(db: Session, user: User, contract_id: int) -> list[ChatMessageOut]:
    contract = get_contract_for_user(db, contract_id, user)
    return list_chat_messages(db, contract_id=contract.id, user_id=user.id)


def chat_contract(
    db: Session,
    user: User,
    contract_id: int,
    question: str,
) -> ChatResult:
    enforce_chat_quota(user.id)
    contract = require_ready_contract(db, contract_id, user)
    return answer_question(db, contract, user, question)


def chat_contract_sse(db: Session, user: User, contract_id: int, question: str):
    enforce_chat_quota(user.id)
    contract = require_ready_contract(db, contract_id, user)
    return iter_sse_events(contract, user, question)
