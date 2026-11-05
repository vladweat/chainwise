import logging

from celery import Celery
from sqlalchemy.orm import Session

from app.core.proxy import apply_internal_no_proxy

apply_internal_no_proxy()

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.sentry import capture_pipeline_error, init_sentry
from app.models.contract import Contract
from app.models.contract_status import ContractStatus, set_contract_status
from app.services.indexing_worker import run_indexing
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
def load_contract_task(self, contract_id: int) -> dict:
    db: Session = SessionLocal()
    try:
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if contract is None:
            logger.error("Contract %s not found", contract_id)
            return {"status": "error", "detail": "contract not found"}

        return run_indexing(db, contract, celery_task_id=self.request.id)
    except Exception as exc:
        db.rollback()
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if contract is not None:
            set_contract_status(contract, ContractStatus.FAILED, actor="worker")
            contract.last_error = str(exc)[:2000]
            db.commit()
            release_indexing_lock(contract.user_id, contract.chain_id, contract.address)
        logger.exception("Failed to load contract %s: %s", contract_id, exc)
        capture_pipeline_error("indexing", exc)
        raise
    finally:
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if contract is not None and contract.status == ContractStatus.READY.value:
            release_indexing_lock(contract.user_id, contract.chain_id, contract.address)
        db.close()
