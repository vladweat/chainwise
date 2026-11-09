import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.contract_status import ContractStatus, set_contract_status
from app.models.indexing_step import IndexingStep
from app.services.indexing_lifecycle import lock_current_attempt
from app.services.qdrant_indexer import upsert_code_chunks
from app.services.risk_scorer import FEATURES_VERSION, score_slither_report_detailed
from app.services.semantic_splitter import split_code_chunks
from app.services.slither_runner import run_slither
from app.services.solc_compiler import compile_to_ast
from app.utils.pragma_parser import parse_pragma_solidity

logger = logging.getLogger(__name__)
_UNSET = object()


def _source_filename(contract: Contract) -> str:
    name = (contract.name or "").strip()
    if not name:
        return "Contract.sol"
    if name.lower().endswith(".sol"):
        return name
    return f"{name}.sol"


def _commit_attempt_update(
    db: Session,
    contract: Contract,
    attempt_id: str | None,
    *,
    step: IndexingStep | None | object = _UNSET,
    values: dict | None = None,
) -> Contract:
    if attempt_id is None:
        if values:
            for key, value in values.items():
                setattr(contract, key, value)
        if step is not _UNSET:
            contract.indexing_step = step.value if step is not None else None
        db.commit()
        return contract

    current = lock_current_attempt(db, contract.id, attempt_id)
    if values:
        for key, value in values.items():
            setattr(current, key, value)
    if step is not _UNSET:
        current.indexing_step = step.value if step is not None else None
    current.indexing_heartbeat_at = datetime.now(UTC)
    db.commit()
    return current


def run_indexing(
    db: Session,
    contract: Contract,
    celery_task_id: str | None = None,
    attempt_id: str | None = None,
) -> dict:
    """Index a contract: AST, Slither, semantic split, Qdrant embeddings, risk score."""
    initial_values = {"celery_task_id": celery_task_id} if celery_task_id else {}
    if not contract.compiler_version:
        parsed = parse_pragma_solidity(contract.source_code)
        if parsed:
            initial_values["compiler_version"] = parsed

    filename = _source_filename(contract)

    contract = _commit_attempt_update(
        db,
        contract,
        attempt_id,
        step=IndexingStep.COMPILE,
        values=initial_values,
    )
    compiled = compile_to_ast(
        contract.source_code,
        compiler_version=contract.compiler_version,
        filename=filename,
    )
    contract = _commit_attempt_update(
        db,
        contract,
        attempt_id,
        values={"ast": compiled.to_dict(), "compiler_version": compiled.solc_version},
    )

    contract = _commit_attempt_update(db, contract, attempt_id, step=IndexingStep.SLITHER)
    report = run_slither(
        contract.source_code,
        compiler_version=contract.compiler_version,
        filename=filename,
    )
    contract = _commit_attempt_update(
        db, contract, attempt_id, values={"slither_report": report}
    )

    contract = _commit_attempt_update(db, contract, attempt_id, step=IndexingStep.EMBED)
    chunks = split_code_chunks(contract.source_code, compiled, filename=filename)
    chunk_count = upsert_code_chunks(
        contract.id, chunks, generation=attempt_id
    )
    contract = _commit_attempt_update(
        db, contract, attempt_id, values={"chunk_count": chunk_count}
    )

    contract = _commit_attempt_update(db, contract, attempt_id, step=IndexingStep.SCORE)
    letter, source = score_slither_report_detailed(report)
    contract = lock_current_attempt(db, contract.id, attempt_id) if attempt_id else contract
    contract.risk_score = letter
    contract.risk_model = source
    contract.features_version = FEATURES_VERSION
    contract.last_error = None
    if attempt_id is not None:
        # Publish this generation only with the READY commit. Until then chat
        # remains gated and retrieval continues to identify the last success.
        contract.analysis_generation_id = attempt_id
    set_contract_status(contract, ContractStatus.READY, actor="worker")
    contract.indexing_step = IndexingStep.DONE.value
    if attempt_id is not None:
        contract.indexing_heartbeat_at = datetime.now(UTC)
    db.commit()

    detectors = (report.get("results") or {}).get("detectors") or []
    logger.info(
        "Contract compiled and analyzed",
        extra={
            "contract_id": contract.id,
            "address": contract.address,
            "chain_id": contract.chain_id,
            "compiler_version": contract.compiler_version,
            "file_count": len(compiled.sources),
            "detector_count": len(detectors),
            "chunk_count": chunk_count,
            "risk_score": contract.risk_score,
            "risk_model": contract.risk_model,
        },
    )
    return {
        "status": ContractStatus.READY.value,
        "contract_id": contract.id,
        "compiler_version": contract.compiler_version,
        "chunk_count": chunk_count,
        "risk_score": contract.risk_score,
    }
