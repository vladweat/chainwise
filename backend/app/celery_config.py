import logging

from celery import Celery
from sqlalchemy.orm import Session

from app.core.proxy import apply_internal_no_proxy

apply_internal_no_proxy()

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.sentry import capture_pipeline_error, init_sentry
from app.models.contract import Contract
from app.services.indexing_worker import run_indexing
from app.services.indexing_lifecycle import (
    IndexingAttemptNotCurrent,
    claim_indexing_attempt,
    mark_attempt_failed,
)
from app.utils.redis_lock import release_indexing_lock

logger = logging.getLogger(__name__)

settings = get_settings()
init_sentry()

celery_app = Celery(
    "chainwise",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


@celery_app.task(name="indexing.load_contract", bind=True)
def load_contract_task(
    self,
    contract_id: int,
    lock_owner_token: str | None = None,
    lock_user_id: int | None = None,
    lock_chain_id: int | None = None,
    lock_address: str | None = None,
    attempt_id: str | None = None,
) -> dict:
    db: Session | None = None
    contract = None
    lock_scope = None
    owns_attempt = False
    if lock_user_id is not None and lock_chain_id is not None and lock_address is not None:
        lock_scope = (lock_user_id, lock_chain_id, lock_address)
    try:
        db = SessionLocal()
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if contract is None:
            logger.error("Contract %s not found", contract_id)
            return {"status": "error", "detail": "contract not found"}

        if attempt_id is None:
            logger.info("Ignoring legacy indexing task for contract %s", contract_id)
            return {"status": "skipped", "detail": "attempt identity is required"}

        owns_attempt = claim_indexing_attempt(
            db,
            contract_id,
            attempt_id,
            celery_task_id=self.request.id,
        )
        if not owns_attempt:
            logger.info("Ignoring duplicate or stale task for contract %s", contract_id)
            return {"status": "skipped", "detail": "attempt is claimed or no longer current"}

        contract = (
            db.query(Contract)
            .filter(Contract.id == contract_id)
            .populate_existing()
            .first()
        )
        if contract is None:
            return {"status": "skipped", "detail": "contract was deleted"}

        if lock_owner_token is not None and lock_scope is None:
            lock_scope = (contract.user_id, contract.chain_id, contract.address)

        return run_indexing(
            db,
            contract,
            celery_task_id=self.request.id,
            attempt_id=attempt_id,
        )
    except IndexingAttemptNotCurrent:
        if db is not None:
            db.rollback()
        logger.info("Indexing attempt %s for contract %s was superseded", attempt_id, contract_id)
        return {"status": "skipped", "detail": "attempt is no longer current"}
    except Exception as exc:
        if db is not None:
            db.rollback()
            contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if contract is not None and attempt_id is not None:
            try:
                mark_attempt_failed(db, contract_id, attempt_id, str(exc))
            except Exception:
                logger.exception(
                    "Could not persist failure for indexing attempt on contract %s",
                    contract_id,
                )
        logger.exception("Failed to load contract %s: %s", contract_id, exc)
        capture_pipeline_error("indexing", exc)
        raise
    finally:
        try:
            if owns_attempt and lock_owner_token is not None and lock_scope is not None:
                release_indexing_lock(*lock_scope, lock_owner_token)
        except Exception:
            logger.exception("Failed to release owned indexing lock for contract %s", contract_id)
        finally:
            if db is not None:
                db.close()
