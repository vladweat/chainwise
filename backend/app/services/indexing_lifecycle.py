"""Database fencing and recovery helpers for contract indexing attempts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.contract_status import ContractStatus, set_contract_status

RECONCILE_ERROR = "Indexing attempt exceeded the recovery age; retry analysis."


class IndexingAttemptNotCurrent(RuntimeError):
    """Raised when a worker tries to write for a superseded attempt."""


def claim_indexing_attempt(
    db: Session,
    contract_id: int,
    attempt_id: str,
    *,
    celery_task_id: str | None,
    now: datetime | None = None,
) -> bool:
    """Atomically let one delivery claim an active attempt."""
    claimed_at = now or datetime.now(UTC)
    result = db.execute(
        update(Contract)
        .where(
            Contract.id == contract_id,
            Contract.status == ContractStatus.INDEXING.value,
            Contract.indexing_attempt_id == attempt_id,
            Contract.indexing_claimed_at.is_(None),
        )
        .values(
            indexing_claimed_at=claimed_at,
            indexing_heartbeat_at=claimed_at,
            celery_task_id=celery_task_id,
        )
    )
    db.commit()
    return result.rowcount == 1


def lock_current_attempt(db: Session, contract_id: int, attempt_id: str) -> Contract:
    """Lock the active row before a worker mutation, fencing stale workers."""
    contract = (
        db.query(Contract)
        .filter(Contract.id == contract_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if (
        contract is None
        or contract.status != ContractStatus.INDEXING.value
        or contract.indexing_attempt_id != attempt_id
    ):
        db.rollback()
        raise IndexingAttemptNotCurrent(
            f"Indexing attempt for contract {contract_id} is no longer current"
        )
    return contract


def mark_attempt_failed(
    db: Session,
    contract_id: int,
    attempt_id: str,
    error: str,
) -> bool:
    """Fail only the matching active attempt; stale workers become no-ops."""
    try:
        contract = lock_current_attempt(db, contract_id, attempt_id)
    except IndexingAttemptNotCurrent:
        return False

    set_contract_status(contract, ContractStatus.FAILED, actor="worker")
    contract.last_error = error[:2000]
    db.commit()
    return True


def reconcile_stale_indexing(
    db: Session,
    *,
    stale_before: datetime,
    dry_run: bool = False,
) -> list[int]:
    """Find stale indexing rows and optionally fence them as terminal failures."""
    stale_rows = (
        db.query(Contract)
        .filter(
            Contract.status == ContractStatus.INDEXING.value,
            or_(
                Contract.indexing_heartbeat_at <= stale_before,
                and_(
                    Contract.indexing_heartbeat_at.is_(None),
                    Contract.created_at <= stale_before,
                ),
            ),
        )
        .order_by(Contract.id)
        .with_for_update(skip_locked=True)
        .all()
    )
    contract_ids = [contract.id for contract in stale_rows]
    if dry_run:
        db.rollback()
        return contract_ids

    for contract in stale_rows:
        set_contract_status(contract, ContractStatus.FAILED, actor="worker")
        contract.last_error = RECONCILE_ERROR
    if stale_rows:
        db.commit()
    else:
        db.rollback()
    return contract_ids
