import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.utils import redis_lock


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class MemoryRedis:
    def __init__(self, clock=None):
        self.clock = clock or FakeClock()
        self.values = {}
        self.mutex = threading.Lock()

    def _purge_expired(self, key):
        entry = self.values.get(key)
        if entry is not None and entry[1] <= self.clock.now:
            del self.values[key]

    def set(self, key, value, *, nx=False, ex=None):
        with self.mutex:
            self._purge_expired(key)
            if nx and key in self.values:
                return None
            self.values[key] = (value, self.clock.now + ex)
            return "OK"

    def eval(self, _script, numkeys, key, owner_token):
        assert numkeys == 1
        with self.mutex:
            self._purge_expired(key)
            entry = self.values.get(key)
            if entry is None or entry[0] != owner_token:
                return 0
            del self.values[key]
            return 1

    def exists(self, key):
        with self.mutex:
            self._purge_expired(key)
            return int(key in self.values)

    def value(self, key):
        with self.mutex:
            self._purge_expired(key)
            entry = self.values.get(key)
            return entry[0] if entry else None


@pytest.fixture
def memory_redis(monkeypatch):
    fake = MemoryRedis()
    monkeypatch.setattr(redis_lock, "get_redis_client", lambda: fake)
    return fake


def test_same_owner_can_release_indexing_lock(memory_redis):
    owner = redis_lock.acquire_indexing_lock(7, 1, "0xabc", ttl=10)

    assert isinstance(owner, str) and owner
    assert redis_lock.release_indexing_lock(7, 1, "0xabc", owner) is True
    assert redis_lock.is_indexing_locked(7, 1, "0xabc") is False


def test_wrong_owner_cannot_release_indexing_lock(memory_redis):
    owner = redis_lock.acquire_indexing_lock(7, 1, "0xabc", ttl=10)

    assert owner
    assert redis_lock.release_indexing_lock(7, 1, "0xabc", "different-owner") is False
    assert redis_lock.is_indexing_locked(7, 1, "0xabc") is True
    assert memory_redis.value("indexing_lock:7:1:0xabc") == owner


def test_expired_owner_cannot_release_successor_lock(memory_redis):
    old_owner = redis_lock.acquire_indexing_lock(7, 1, "0xabc", ttl=10)
    memory_redis.clock.advance(11)
    successor = redis_lock.acquire_indexing_lock(7, 1, "0xabc", ttl=10)

    assert old_owner and successor and old_owner != successor
    assert redis_lock.release_indexing_lock(7, 1, "0xabc", old_owner) is False
    assert memory_redis.value("indexing_lock:7:1:0xabc") == successor


def test_concurrent_lock_acquisition_has_single_winner(memory_redis):
    workers = 16
    barrier = threading.Barrier(workers)

    def acquire():
        barrier.wait()
        return redis_lock.acquire_indexing_lock(7, 1, "0xabc", ttl=30)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        owners = list(pool.map(lambda _index: acquire(), range(workers)))

    winners = [owner for owner in owners if owner is not None]
    assert len(winners) == 1


def test_database_failure_releases_only_the_request_owner(monkeypatch):
    from app.services import contract_service

    owner = "request-owner-token"
    released = []

    class FailedCommitSession:
        def add(self, _contract):
            pass

        def commit(self):
            raise RuntimeError("database commit failed")

    monkeypatch.setattr(contract_service, "enforce_analyze_quota", lambda _user_id: None)
    monkeypatch.setattr(contract_service, "enforce_max_contracts", lambda _db, _user_id: None)
    monkeypatch.setattr(contract_service, "acquire_indexing_lock", lambda *_args: owner)
    monkeypatch.setattr(
        contract_service,
        "release_indexing_lock",
        lambda *args: released.append(args),
    )

    with pytest.raises(RuntimeError, match="database commit failed"):
        contract_service._enqueue_indexing(
            FailedCommitSession(),
            SimpleNamespace(id=7),
            address="0xabc",
            chain_id=1,
            source_code="contract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
            existing=None,
        )

    assert released == [(7, 1, "0xabc", owner)]


def test_celery_publish_failure_releases_only_the_request_owner(monkeypatch):
    from app.services import contract_service

    owner = "request-owner-token"
    released = []

    class FakeSession:
        def add(self, contract):
            self.contract = contract

        def commit(self):
            pass

        def refresh(self, contract):
            contract.id = 71

    def fail_publish(**_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(contract_service, "enforce_analyze_quota", lambda _user_id: None)
    monkeypatch.setattr(contract_service, "enforce_max_contracts", lambda _db, _user_id: None)
    monkeypatch.setattr(contract_service, "acquire_indexing_lock", lambda *_args: owner)
    monkeypatch.setattr(contract_service.load_contract_task, "apply_async", fail_publish)
    monkeypatch.setattr(
        contract_service,
        "release_indexing_lock",
        lambda *args: released.append(args),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        contract_service._enqueue_indexing(
            FakeSession(),
            SimpleNamespace(id=7),
            address="0xabc",
            chain_id=1,
            source_code="contract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
            existing=None,
        )

    assert released == [(7, 1, "0xabc", owner)]


def test_request_passes_owner_to_celery_without_exposing_it(monkeypatch):
    from app.services import contract_service

    owner = "request-owner-token"
    task_call = {}

    class FakeSession:
        contract = None

        def add(self, contract):
            self.contract = contract

        def commit(self):
            pass

        def refresh(self, contract):
            contract.id = 71

    def fake_apply_async(*, task_id, args, argsrepr):
        task_call.update(method="apply_async", task_id=task_id, args=args, argsrepr=argsrepr)
        return SimpleNamespace(id=task_id)

    def fake_delay(*args, **_kwargs):
        task_call.update(method="delay", args=args)
        return SimpleNamespace(id="task-71")

    monkeypatch.setattr(contract_service, "enforce_analyze_quota", lambda _user_id: None)
    monkeypatch.setattr(contract_service, "enforce_max_contracts", lambda _db, _user_id: None)
    monkeypatch.setattr(contract_service, "acquire_indexing_lock", lambda *_args: owner)
    monkeypatch.setattr(contract_service.load_contract_task, "apply_async", fake_apply_async)
    monkeypatch.setattr(contract_service.load_contract_task, "delay", fake_delay)

    response = contract_service._enqueue_indexing(
        FakeSession(),
        SimpleNamespace(id=7),
        address="0xabc",
        chain_id=1,
        source_code="contract Demo {}",
        name="Demo",
        compiler_version="0.8.24",
        is_verified=True,
        existing=None,
    )

    assert task_call["method"] == "apply_async"
    assert task_call["args"][:5] == (71, owner, 7, 1, "0xabc")
    assert task_call["args"][5]
    assert task_call["task_id"]
    assert owner not in task_call["argsrepr"]
    assert task_call["args"][5] not in task_call["argsrepr"]
    assert response.model_dump() == {
        "task_id": task_call["task_id"],
        "contract_id": 71,
        "status": "indexing",
        "reused": False,
    }


@pytest.mark.parametrize(
    ("stored_owner", "task_owner", "expected_owner"),
    [
        ("task-owner", "task-owner", None),
        ("successor-owner", "expired-task-owner", "successor-owner"),
    ],
)
def test_celery_error_cleanup_releases_only_its_owner(
    monkeypatch, memory_redis, stored_owner, task_owner, expected_owner
):
    from app import celery_config

    address = "0xabc"
    key = "indexing_lock:7:1:0xabc"
    memory_redis.set(key, stored_owner, nx=True, ex=30)
    contract = SimpleNamespace(
        id=71,
        user_id=7,
        chain_id=1,
        address=address,
        status="indexing",
        indexing_attempt_id="attempt-71",
        last_error=None,
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def populate_existing(self):
            return self

        def first(self):
            return contract

    class FakeSession:
        def query(self, *_args):
            return FakeQuery()

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(celery_config, "SessionLocal", FakeSession)
    monkeypatch.setattr(celery_config, "claim_indexing_attempt", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        celery_config,
        "run_indexing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("indexing failed")),
    )
    monkeypatch.setattr(celery_config, "capture_pipeline_error", lambda *_args: None)

    with pytest.raises(RuntimeError, match="indexing failed"):
        celery_config.load_contract_task.run(71, task_owner, 7, 1, address, "attempt-71")

    assert contract.status == "failed"
    assert memory_redis.value(key) == expected_owner


def test_celery_session_creation_failure_keeps_unclaimed_lock(monkeypatch, memory_redis):
    from app import celery_config

    address = "0xabc"
    key = "indexing_lock:7:1:0xabc"
    owner = "task-owner"
    memory_redis.set(key, owner, nx=True, ex=30)

    def fail_session_creation():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(celery_config, "SessionLocal", fail_session_creation)
    monkeypatch.setattr(celery_config, "capture_pipeline_error", lambda *_args: None)

    with pytest.raises(RuntimeError, match="database unavailable"):
        celery_config.load_contract_task.run(71, owner, 7, 1, address, "attempt-71")

    assert memory_redis.value(key) == owner


def test_lock_is_exclusive_case_insensitive_and_scoped_by_user(memory_redis):
    address = "0xAbC"
    owner = redis_lock.acquire_indexing_lock(7, 1, address.upper(), ttl=60)
    other_owner = redis_lock.acquire_indexing_lock(8, 1, address.lower(), ttl=60)

    assert owner and other_owner
    assert redis_lock.is_indexing_locked(7, 1, address.lower())
    assert not redis_lock.acquire_indexing_lock(7, 1, address.lower(), ttl=60)
    assert redis_lock.is_indexing_locked(8, 1, address)

    assert redis_lock.release_indexing_lock(7, 1, address.lower(), owner)
    assert not redis_lock.is_indexing_locked(7, 1, address)
    assert redis_lock.is_indexing_locked(8, 1, address)
    assert redis_lock.release_indexing_lock(8, 1, address, other_owner)
