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
from app.services.qdrant_reconciler import reconcile_qdrant_generations


class _FakeQdrant:
    def __init__(self, points):
        self._order = [point.id for point in points]
        self._points = {point.id: point for point in points}
        self.deleted = []

    def collection_exists(self, _collection_name):
        return True

    def scroll(self, *, limit, offset=None, **_kwargs):
        start = 0 if offset is None else self._order.index(offset) + 1
        available = [
            self._points[point_id]
            for point_id in self._order[start:]
            if point_id in self._points
        ]
        page = available[:limit]
        next_offset = page[-1].id if len(available) > limit else None
        return page, next_offset

    def delete(self, *, points_selector, **_kwargs):
        self.deleted.extend(points_selector)
        for point_id in points_selector:
            self._points.pop(point_id, None)


def _qdrant_point(point_id, contract_id, generation):
    return SimpleNamespace(
        id=point_id,
        payload={"contract_id": contract_id, "indexing_attempt_id": generation},
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


def test_postgres_reconciles_only_stale_indexing_rows(postgres_contract):
    db = SessionLocal()
    stale = db.query(Contract).filter(Contract.id == postgres_contract).one()
    stale.indexing_heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    active = Contract(
        user_id=stale.user_id,
        address=f"0x{uuid.uuid4().hex[:40]}",
        chain_id=1,
        status=ContractStatus.INDEXING.value,
        indexing_attempt_id="attempt-active",
        indexing_heartbeat_at=datetime.now(UTC),
    )
    ready = Contract(
        user_id=stale.user_id,
        address=f"0x{uuid.uuid4().hex[:40]}",
        chain_id=1,
        status=ContractStatus.READY.value,
        indexing_attempt_id="attempt-finished",
        indexing_heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add_all([active, ready])
    db.commit()
    active_id, ready_id = active.id, ready.id

    failed_ids = reconcile_stale_indexing(
        db,
        stale_before=datetime.now(UTC) - timedelta(hours=1),
    )

    assert failed_ids == [postgres_contract]
    db.expire_all()
    assert db.query(Contract).filter(Contract.id == postgres_contract).one().status == "failed"
    assert db.query(Contract).filter(Contract.id == active_id).one().status == "indexing"
    assert db.query(Contract).filter(Contract.id == ready_id).one().status == "ready"
    db.close()


def test_qdrant_reconciliation_keeps_current_generations_and_removes_orphans(
    postgres_contract,
):
    db = SessionLocal()
    client = _FakeQdrant(
        [
            _qdrant_point("previous", postgres_contract, "generation-previous"),
            _qdrant_point("active", postgres_contract, "attempt-old"),
            _qdrant_point("stale", postgres_contract, "generation-stale"),
            _qdrant_point("orphan", postgres_contract + 100000, "attempt-orphan"),
            _qdrant_point("legacy", postgres_contract, None),
        ]
    )

    preview = reconcile_qdrant_generations(
        db, client=client, dry_run=True, batch_size=2
    )
    assert preview == {
        "orphan_points": 1,
        "stale_generation_points": 2,
        "unclassified_points": 0,
        "deleted_points": 0,
    }
    assert len(client._points) == 5

    applied = reconcile_qdrant_generations(
        db, client=client, dry_run=False, batch_size=2
    )
    assert applied["deleted_points"] == 3
    assert set(client._points) == {"previous", "active"}
    assert reconcile_qdrant_generations(db, client=client, dry_run=False)[
        "deleted_points"
    ] == 0
    db.close()


def test_qdrant_reconciliation_with_live_qdrant(postgres_contract, monkeypatch):
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from app.core.config import get_settings
    from app.services import qdrant_reconciler

    settings = get_settings()
    try:
        client = qdrant_client.QdrantClient(
            url=settings.qdrant_url,
            timeout=5,
            trust_env=False,
        )
        client.get_collections()
    except Exception as exc:
        pytest.skip(f"Live Qdrant is unavailable: {type(exc).__name__}")

    collection = f"reconcile-test-{uuid.uuid4().hex[:12]}"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    monkeypatch.setattr(qdrant_reconciler, "COLLECTION_NAME", collection)
    client.upsert(
        collection_name=collection,
        wait=True,
        points=[
            PointStruct(
                id=1,
                vector=[0.1, 0.2, 0.3],
                payload={
                    "contract_id": postgres_contract,
                    "indexing_attempt_id": "generation-previous",
                },
            ),
            PointStruct(
                id=2,
                vector=[0.2, 0.3, 0.4],
                payload={"contract_id": postgres_contract, "indexing_attempt_id": "old"},
            ),
            PointStruct(
                id=3,
                vector=[0.3, 0.4, 0.5],
                payload={"contract_id": postgres_contract, "indexing_attempt_id": "attempt-old"},
            ),
            PointStruct(
                id=4,
                vector=[0.4, 0.5, 0.6],
                payload={"contract_id": postgres_contract + 100000, "indexing_attempt_id": "orphan"},
            ),
        ],
    )
    db = SessionLocal()
    try:
        result = reconcile_qdrant_generations(
            db,
            client=client,
            dry_run=False,
            batch_size=1,
        )
        assert result["stale_generation_points"] == 1
        assert result["orphan_points"] == 1
        assert result["deleted_points"] == 2
        records, _ = client.scroll(
            collection_name=collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        assert {record.id for record in records} == {1, 3}
    finally:
        db.close()
        client.delete_collection(collection)
        client.close()


def test_contract_delete_qdrant_failure_is_recovered_by_reconciliation(
    postgres_contract, monkeypatch
):
    from app.services import contract_service

    db = SessionLocal()
    contract = db.query(Contract).filter(Contract.id == postgres_contract).one()
    owner = db.query(User).filter(User.id == contract.user_id).one()
    client = _FakeQdrant(
        [_qdrant_point("orphan-after-delete", postgres_contract, "attempt-old")]
    )
    monkeypatch.setattr(
        contract_service,
        "delete_contract_chunks",
        lambda _contract_id: (_ for _ in ()).throw(RuntimeError("Qdrant unavailable")),
    )

    contract_service.delete_contract(db, postgres_contract, owner)

    assert db.query(Contract).filter(Contract.id == postgres_contract).first() is None
    result = reconcile_qdrant_generations(db, client=client, dry_run=False)
    assert result["orphan_points"] == 1
    assert result["deleted_points"] == 1
    assert client._points == {}
    db.close()


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
