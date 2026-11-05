import logging

from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.contract_status import ContractStatus, set_contract_status
from app.models.indexing_step import IndexingStep, set_indexing_step
from app.services.qdrant_indexer import upsert_code_chunks
from app.services.risk_scorer import FEATURES_VERSION, score_slither_report_detailed
from app.services.semantic_splitter import split_code_chunks
from app.services.slither_runner import run_slither
from app.services.solc_compiler import compile_to_ast
from app.utils.pragma_parser import parse_pragma_solidity

logger = logging.getLogger(__name__)


def _source_filename(contract: Contract) -> str:
    name = (contract.name or "").strip()
    if not name:
        return "Contract.sol"
    if name.lower().endswith(".sol"):
        return name
    return f"{name}.sol"


def run_indexing(db: Session, contract: Contract, celery_task_id: str | None = None) -> dict:
    """Index a contract: AST, Slither, semantic split, Qdrant embeddings, risk score."""
    if celery_task_id:
        contract.celery_task_id = celery_task_id

    if not contract.compiler_version:
        parsed = parse_pragma_solidity(contract.source_code)
        if parsed:
            contract.compiler_version = parsed

    filename = _source_filename(contract)

    set_indexing_step(db, contract, IndexingStep.COMPILE)
    compiled = compile_to_ast(
        contract.source_code,
        compiler_version=contract.compiler_version,
        filename=filename,
    )
    contract.ast = compiled.to_dict()
    contract.compiler_version = compiled.solc_version
    db.commit()

    set_indexing_step(db, contract, IndexingStep.SLITHER)
    report = run_slither(
        contract.source_code,
        compiler_version=contract.compiler_version,
        filename=filename,
    )
    contract.slither_report = report
    db.commit()

    set_indexing_step(db, contract, IndexingStep.EMBED)
    chunks = split_code_chunks(contract.source_code, compiled, filename=filename)
    chunk_count = upsert_code_chunks(contract.id, chunks)
    contract.chunk_count = chunk_count
    db.commit()

    set_indexing_step(db, contract, IndexingStep.SCORE)
    letter, source = score_slither_report_detailed(report)
    contract.risk_score = letter
    contract.risk_model = source
    contract.features_version = FEATURES_VERSION
    contract.last_error = None
    set_contract_status(contract, ContractStatus.READY, actor="worker")
    set_indexing_step(db, contract, IndexingStep.DONE)

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
