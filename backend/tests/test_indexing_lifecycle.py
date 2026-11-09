from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.contract import Contract
from app.models.user import User  # noqa: F401 - register relationship mapper
from app.services.indexing_lifecycle import (
    IndexingAttemptNotCurrent,
    claim_indexing_attempt,
    lock_current_attempt,
    mark_attempt_failed,
    reconcile_stale_indexing,
)


@pytest.fixture
def lifecycle_db():
    engine = create_engine("sqlite:///:memory:")
    Contract.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    session.add(
        Contract(
            id=71,
            user_id=7,
            address="0x" + "a" * 40,
            chain_id=1,
            status="indexing",
            indexing_attempt_id="attempt-current",
            indexing_heartbeat_at=datetime.now(UTC),
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_duplicate_delivery_has_one_database_claim(lifecycle_db):
    assert claim_indexing_attempt(
        lifecycle_db, 71, "attempt-current", celery_task_id="task-71"
    )
    assert not claim_indexing_attempt(
        lifecycle_db, 71, "attempt-current", celery_task_id="task-71"
    )


def test_concurrent_deliveries_have_one_database_claim(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'indexing.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Contract.__table__.create(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add(
            Contract(
                id=71,
                user_id=7,
                address="0x" + "a" * 40,
                chain_id=1,
                status="indexing",
                indexing_attempt_id="attempt-current",
                indexing_heartbeat_at=datetime.now(UTC),
            )
        )
        db.commit()

    barrier = threading.Barrier(2)

    def claim():
        with sessions() as db:
            barrier.wait()
            return claim_indexing_attempt(
                db, 71, "attempt-current", celery_task_id="task-71"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(lambda _index: claim(), range(2)))

    assert winners.count(True) == 1
    assert winners.count(False) == 1
    engine.dispose()


def test_stale_attempt_cannot_update_contract_after_successor_starts(lifecycle_db):
    contract = lifecycle_db.query(Contract).filter(Contract.id == 71).first()
    contract.indexing_attempt_id = "attempt-successor"
    contract.indexing_claimed_at = None
    lifecycle_db.commit()

    with pytest.raises(IndexingAttemptNotCurrent):
        lock_current_attempt(lifecycle_db, 71, "attempt-current")


def test_old_failure_cannot_fail_successor_attempt(lifecycle_db):
    contract = lifecycle_db.query(Contract).filter(Contract.id == 71).first()
    contract.indexing_attempt_id = "attempt-successor"
    lifecycle_db.commit()

    assert not mark_attempt_failed(
        lifecycle_db, 71, "attempt-current", "analyzer failed"
    )
    lifecycle_db.refresh(contract)
    assert contract.status == "indexing"
    assert contract.indexing_attempt_id == "attempt-successor"
    assert contract.last_error is None


def test_reconciler_marks_old_unclaimed_attempt_failed(lifecycle_db):
    contract = lifecycle_db.query(Contract).filter(Contract.id == 71).first()
    contract.indexing_heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    lifecycle_db.commit()

    failed_ids = reconcile_stale_indexing(
        lifecycle_db,
        stale_before=datetime.now(UTC) - timedelta(hours=1),
        dry_run=False,
    )

    lifecycle_db.refresh(contract)
    assert failed_ids == [71]
    assert contract.status == "failed"
    assert "retry analysis" in contract.last_error.lower()
    assert not claim_indexing_attempt(
        lifecycle_db, 71, "attempt-current", celery_task_id="late-task"
    )


def test_reconciler_dry_run_does_not_change_status(lifecycle_db):
    contract = lifecycle_db.query(Contract).filter(Contract.id == 71).first()
    contract.indexing_heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    lifecycle_db.commit()

    found = reconcile_stale_indexing(
        lifecycle_db,
        stale_before=datetime.now(UTC) - timedelta(hours=1),
        dry_run=True,
    )

    lifecycle_db.refresh(contract)
    assert found == [71]
    assert contract.status == "indexing"


def test_broker_publish_failure_persists_terminal_failure(monkeypatch):
    from app.services import contract_service

    owner = "request-lock-owner"
    session = _EnqueueSession()
    released = []

    monkeypatch.setattr(contract_service, "enforce_analyze_quota", lambda _user_id: None)
    monkeypatch.setattr(contract_service, "enforce_max_contracts", lambda _db, _user_id: None)
    monkeypatch.setattr(contract_service, "acquire_indexing_lock", lambda *_args: owner)
    monkeypatch.setattr(
        contract_service.load_contract_task,
        "apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )
    monkeypatch.setattr(
        contract_service,
        "release_indexing_lock",
        lambda *args: released.append(args),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        contract_service._enqueue_indexing(
            session,
            SimpleNamespace(id=7),
            address="0x" + "a" * 40,
            chain_id=1,
            source_code="contract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
            existing=None,
        )

    assert session.contract.status == "failed"
    assert session.contract.celery_task_id
    assert session.contract.indexing_attempt_id
    assert session.contract.last_error == "Task dispatch failed; retry analysis after broker recovery."
    assert released == [(7, 1, "0x" + "a" * 40, owner)]


def test_qdrant_write_crash_fences_stale_delivery_and_allows_new_generation(monkeypatch):
    from app import celery_config

    contract = SimpleNamespace(
        id=71,
        user_id=7,
        chain_id=1,
        address="0x" + "a" * 40,
        status="indexing",
        indexing_attempt_id="attempt-old",
        indexing_claimed_at=None,
        last_error=None,
    )
    qdrant_generations = []
    released = []
    calls = []

    class TaskQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def populate_existing(self):
            return self

        def first(self):
            return contract

    class TaskSession:
        def query(self, *_args):
            return TaskQuery()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    session = TaskSession()

    def claim(_db, _contract_id, attempt_id, **_kwargs):
        if (
            contract.status == "indexing"
            and contract.indexing_attempt_id == attempt_id
            and contract.indexing_claimed_at is None
        ):
            contract.indexing_claimed_at = datetime.now(UTC)
            return True
        return False

    def run(_db, current, *, attempt_id, **_kwargs):
        calls.append(attempt_id)
        qdrant_generations.append(attempt_id)
        if attempt_id == "attempt-old":
            raise RuntimeError("injected crash after Qdrant write")
        current.status = "ready"
        return {"status": "ready", "contract_id": current.id}

    monkeypatch.setattr(celery_config, "SessionLocal", lambda: session)
    monkeypatch.setattr(celery_config, "claim_indexing_attempt", claim)
    monkeypatch.setattr(celery_config, "run_indexing", run)
    monkeypatch.setattr(celery_config, "capture_pipeline_error", lambda *_args: None)
    monkeypatch.setattr(
        celery_config, "release_indexing_lock", lambda *args: released.append(args)
    )

    with pytest.raises(RuntimeError, match="injected crash"):
        celery_config.load_contract_task.run(
            71, "owner-old", 7, 1, contract.address, "attempt-old"
        )
    assert contract.status == "failed"
    assert contract.last_error == "injected crash after Qdrant write"

    # Simulate the user's explicit re-analysis after the terminal failure.
    contract.status = "indexing"
    contract.indexing_attempt_id = "attempt-new"
    contract.indexing_claimed_at = None
    contract.last_error = None

    duplicate = celery_config.load_contract_task.run(
        71, "owner-new", 7, 1, contract.address, "attempt-old"
    )
    assert duplicate["status"] == "skipped"
    assert contract.status == "indexing"

    result = celery_config.load_contract_task.run(
        71, "owner-new", 7, 1, contract.address, "attempt-new"
    )

    assert result["status"] == "ready"
    assert contract.status == "ready"
    assert calls == ["attempt-old", "attempt-new"]
    assert qdrant_generations == ["attempt-old", "attempt-new"]
    assert [call[-1] for call in released] == ["owner-old", "owner-new"]


class _EnqueueQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self, **_kwargs):
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self.session.contract


class _EnqueueSession:
    def __init__(self):
        self.contract = None

    def add(self, contract):
        self.contract = contract

    def commit(self):
        pass

    def refresh(self, contract):
        contract.id = 71

    def query(self, *_args):
        return _EnqueueQuery(self)
