"""PostgreSQL integration coverage for row-lock and attempt-fencing semantics."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import threading
from types import SimpleNamespace
import uuid

import pytest

from app.core.database import SessionLocal
from app.models.contract import Contract
from app.models.contract_status import ContractStatus
from app.models.user import User  # noqa: F401 - register relationship mapper
from app.services import indexing_worker
from app.services.indexing_lifecycle import (
    IndexingAttemptNotCurrent,
    claim_indexing_attempt,
    reconcile_stale_indexing,
)


@pytest.fixture
def postgres_contract():
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("This test verifies PostgreSQL row-lock behavior")

    db = SessionLocal()
    user = User(email=f"indexing-{uuid.uuid4()}@example.test")
    db.add(user)
    db.flush()
    contract = Contract(
        user_id=user.id,
        address=f"0x{uuid.uuid4().hex[:40]}",
        chain_id=1,
        name="Demo",
        compiler_version="0.8.24",
        status=ContractStatus.INDEXING.value,
        indexing_attempt_id="attempt-old",
        analysis_generation_id="generation-previous",
        indexing_heartbeat_at=datetime.now(UTC),
        source_code="pragma solidity ^0.8.24; contract Demo {}",
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    user_id = user.id
    db.close()

    try:
        yield contract_id
    finally:
        cleanup = SessionLocal()
        cleanup.query(Contract).filter(Contract.id == contract_id).delete()
        cleanup.query(User).filter(User.id == user_id).delete()
        cleanup.commit()
        cleanup.close()


def test_postgres_concurrent_claims_have_one_owner(postgres_contract):
    barrier = threading.Barrier(2)

    def claim(task_id):
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            return claim_indexing_attempt(
                db,
                postgres_contract,
                "attempt-old",
                celery_task_id=task_id,
            )
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("task-one", "task-two")))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_postgres_reconciliation_skips_row_during_valid_update(postgres_contract):
    stale_at = datetime.now(UTC) - timedelta(hours=2)
    setup = SessionLocal()
    setup.query(Contract).filter(Contract.id == postgres_contract).update(
        {Contract.indexing_heartbeat_at: stale_at}
    )
    setup.commit()
    setup.close()

    updater = SessionLocal()
    row = (
        updater.query(Contract)
        .filter(Contract.id == postgres_contract)
        .with_for_update()
        .first()
    )
    reconciler = SessionLocal()
    try:
        failed = reconcile_stale_indexing(
            reconciler,
            stale_before=datetime.now(UTC) - timedelta(hours=1),
        )
        assert failed == []

        row.indexing_heartbeat_at = datetime.now(UTC)
        updater.commit()
    finally:
        reconciler.close()
        updater.close()

    verify = SessionLocal()
    current = verify.query(Contract).filter(Contract.id == postgres_contract).one()
    assert current.status == ContractStatus.INDEXING.value
    assert current.indexing_heartbeat_at > datetime.now(UTC) - timedelta(minutes=1)
    verify.close()


def test_postgres_old_worker_cannot_finish_after_reconciliation(
    postgres_contract, monkeypatch
):
    stale_worker = SessionLocal()
    contract = stale_worker.query(Contract).filter(Contract.id == postgres_contract).one()

    stale = SessionLocal()
    stale.query(Contract).filter(Contract.id == postgres_contract).update(
        {Contract.indexing_heartbeat_at: datetime.now(UTC) - timedelta(hours=2)}
    )
    stale.commit()
    failed_ids = reconcile_stale_indexing(
        stale,
        stale_before=datetime.now(UTC) - timedelta(hours=1),
    )
    stale.close()
    assert failed_ids == [postgres_contract]

    monkeypatch.setattr(
        indexing_worker,
        "compile_to_ast",
        lambda *_args, **_kwargs: pytest.fail("stale worker must be fenced before compile"),
    )
    with pytest.raises(IndexingAttemptNotCurrent):
        indexing_worker.run_indexing(
            stale_worker,
            contract,
            attempt_id="attempt-old",
        )
    stale_worker.close()

    verify = SessionLocal()
    current = verify.query(Contract).filter(Contract.id == postgres_contract).one()
    assert current.status == ContractStatus.FAILED.value
    assert current.analysis_generation_id == "generation-previous"
    verify.close()


def test_postgres_old_worker_cannot_publish_ready_over_new_generation(
    postgres_contract, monkeypatch
):
    worker_db = SessionLocal()
    worker_contract = worker_db.query(Contract).filter(
        Contract.id == postgres_contract
    ).one()
    report = {"success": True, "results": {"detectors": []}}
    ast = {"Demo.sol": {"ast": {"nodeType": "SourceUnit", "nodes": []}}}
    monkeypatch.setattr(
        indexing_worker,
        "compile_to_ast",
        lambda *_args, **_kwargs: SimpleNamespace(
            solc_version="0.8.24",
            sources=ast,
            to_dict=lambda: {"solc_version": "0.8.24", "sources": ast},
        ),
    )
    monkeypatch.setattr(indexing_worker, "run_slither", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(indexing_worker, "split_code_chunks", lambda *_args, **_kwargs: ["chunk"])
    monkeypatch.setattr(indexing_worker, "upsert_code_chunks", lambda *_args, **_kwargs: 1)

    def replace_generation(_report):
        successor = SessionLocal()
        current = successor.query(Contract).filter(
            Contract.id == postgres_contract
        ).one()
        current.indexing_attempt_id = "attempt-new"
        current.indexing_claimed_at = None
        current.analysis_generation_id = "generation-previous"
        current.risk_score = "C"
        successor.commit()
        successor.close()
        return "A", "heuristic"

    monkeypatch.setattr(indexing_worker, "score_slither_report_detailed", replace_generation)

    with pytest.raises(IndexingAttemptNotCurrent):
        indexing_worker.run_indexing(
            worker_db,
            worker_contract,
            attempt_id="attempt-old",
        )
    worker_db.close()

    verify = SessionLocal()
    current = verify.query(Contract).filter(Contract.id == postgres_contract).one()
    assert current.status == ContractStatus.INDEXING.value
    assert current.indexing_attempt_id == "attempt-new"
    assert current.analysis_generation_id == "generation-previous"
    assert current.risk_score == "C"
    verify.close()
