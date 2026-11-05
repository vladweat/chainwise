"""Personal SaaS: isolation, quotas, CSRF, account delete."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth_cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.analysis_report import AnalysisReport
from app.models.chat_message import ChatMessage
from app.models.contract import Contract
from app.models.user import User
from app.utils.redis_lock import get_redis_client

client = TestClient(app)

TEST_ADDRESS = "0x1234567890123456789012345678901234567890"
CSRF = {"X-Requested-With": "ChainWise"}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    db: Session = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()

    try:
        redis = get_redis_client()
        for pattern in ("indexing_lock:*", "quota:*", "rl:*"):
            for key in redis.scan_iter(pattern):
                redis.delete(key)
    except Exception:
        pass

    from app.celery_config import celery_app
    from app.services.etherscan_loader import ContractSource
    from app.services.solc_compiler import CompileResult

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    def fake_fetch(address: str, chain_id: int) -> ContractSource:
        return ContractSource(
            address=address.lower(),
            chain_id=chain_id,
            source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
        )

    def fake_compile(source_code: str, compiler_version=None, filename=None):
        name = filename or "Contract.sol"
        return CompileResult(
            solc_version=compiler_version or "0.8.24",
            sources={name: {"id": 0, "ast": {"nodeType": "SourceUnit", "nodes": []}}},
        )

    monkeypatch.setattr("app.services.contract_service.fetch_contract_source", fake_fetch)
    monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", fake_compile)
    monkeypatch.setattr(
        "app.services.indexing_worker.run_slither",
        lambda *_a, **_k: {"success": True, "error": None, "results": {"detectors": []}},
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.upsert_code_chunks",
        lambda *_a, **_k: 1,
    )
    monkeypatch.setattr(
        "app.services.account_service.delete_contract_chunks",
        lambda *_a, **_k: None,
    )

    yield
    get_settings.cache_clear()


def _register() -> tuple[str, int]:
    email = f"saas-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_orphan_contract_is_404_for_non_owner():
    token_a, _user_a = _register()
    token_b, _user_b = _register()

    # Contract owned by A must 404 for B.
    created = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token_a),
    )
    assert created.status_code == 202
    contract_id = created.json()["contract_id"]

    for path in (
        f"/api/v1/contracts/{contract_id}/status",
        f"/api/v1/contracts/{contract_id}/findings",
        f"/api/v1/contracts/{contract_id}/source",
        f"/api/v1/contracts/{contract_id}/chat",
    ):
        response = client.get(path, headers=_auth(token_b))
        assert response.status_code == 404, path


def test_second_user_same_address_gets_own_copy():
    token_a, _ = _register()
    token_b, _ = _register()
    payload = {"address": TEST_ADDRESS, "chain_id": 1}

    first = client.post("/api/v1/contracts/analyze", json=payload, headers=_auth(token_a))
    assert first.status_code == 202
    id_a = first.json()["contract_id"]

    second = client.post("/api/v1/contracts/analyze", json=payload, headers=_auth(token_b))
    assert second.status_code == 202
    id_b = second.json()["contract_id"]
    assert id_b != id_a
    assert second.json().get("reused") is not True

    assert client.get(f"/api/v1/contracts/{id_a}/status", headers=_auth(token_b)).status_code == 404
    assert client.get(f"/api/v1/contracts/{id_b}/status", headers=_auth(token_a)).status_code == 404
    assert client.get(f"/api/v1/contracts/{id_b}/status", headers=_auth(token_b)).status_code == 200


def test_csrf_rejects_cookie_mutation_without_header():
    email = f"csrf-{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert ACCESS_COOKIE in client.cookies

    # Cookie session, no Bearer, no CSRF header
    denied = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
    )
    assert denied.status_code == 403
    assert "X-Requested-With" in denied.json()["detail"]

    ok = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=CSRF,
    )
    assert ok.status_code == 202


def test_quota_max_contracts(monkeypatch):
    monkeypatch.setenv("QUOTA_MAX_CONTRACTS", "1")
    get_settings.cache_clear()
    token, _ = _register()

    first = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token),
    )
    assert first.status_code == 202

    second = client.post(
        "/api/v1/contracts/analyze",
        json={"address": "0x" + "e" * 40, "chain_id": 1},
        headers=_auth(token),
    )
    assert second.status_code == 429
    assert "Contract limit" in second.json()["detail"]


def test_quota_analyze_per_day(monkeypatch):
    monkeypatch.setenv("QUOTA_ANALYZE_PER_DAY", "1")
    get_settings.cache_clear()
    token, _ = _register()

    first = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token),
    )
    assert first.status_code == 202

    second = client.post(
        "/api/v1/contracts/analyze",
        json={"address": "0x" + "f" * 40, "chain_id": 1},
        headers=_auth(token),
    )
    assert second.status_code == 429
    assert "Daily analyze" in second.json()["detail"]


def test_delete_account_cascades_contracts():
    token, user_id = _register()
    created = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token),
    )
    assert created.status_code == 202
    contract_id = created.json()["contract_id"]

    deleted = client.delete("/api/v1/auth/me", headers=_auth(token))
    assert deleted.status_code == 204

    db = SessionLocal()
    assert db.query(User).filter(User.id == user_id).first() is None
    assert db.query(Contract).filter(Contract.id == contract_id).first() is None
    db.close()

    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401


def test_delete_contract_keeps_account(monkeypatch):
    cleaned: list[int] = []
    monkeypatch.setattr(
        "app.services.contract_service.delete_contract_chunks",
        lambda cid: cleaned.append(cid),
    )
    token, user_id = _register()
    created = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token),
    )
    assert created.status_code == 202
    contract_id = created.json()["contract_id"]

    deleted = client.delete(f"/api/v1/contracts/{contract_id}", headers=_auth(token))
    assert deleted.status_code == 204

    db = SessionLocal()
    assert db.query(User).filter(User.id == user_id).first() is not None
    assert db.query(Contract).filter(Contract.id == contract_id).first() is None
    db.close()

    assert cleaned == [contract_id]
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 200
    assert (
        client.get(f"/api/v1/contracts/{contract_id}/status", headers=_auth(token)).status_code
        == 404
    )


def test_delete_contract_isolation():
    token_a, _ = _register()
    token_b, _ = _register()
    created = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth(token_a),
    )
    assert created.status_code == 202
    contract_id = created.json()["contract_id"]

    denied = client.delete(f"/api/v1/contracts/{contract_id}", headers=_auth(token_b))
    assert denied.status_code == 404

    assert (
        client.get(f"/api/v1/contracts/{contract_id}/status", headers=_auth(token_a)).status_code
        == 200
    )


def test_cookie_logout_requires_csrf_header():
    email = f"logout-{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert REFRESH_COOKIE in client.cookies

    denied = client.post("/api/v1/auth/logout")
    assert denied.status_code == 403

    ok = client.post("/api/v1/auth/logout", headers=CSRF)
    assert ok.status_code == 204
