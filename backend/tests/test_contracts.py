import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.main import app
from app.models.analysis_report import AnalysisReport
from app.models.chat_message import ChatMessage
from app.models.contract import Contract
from app.models.user import User

client = TestClient(app)

SAMPLE_SOL = b'pragma solidity ^0.8.24;\ncontract UploadTest {}'
TEST_ADDRESS = "0x1234567890123456789012345678901234567890"


@pytest.fixture(autouse=True)
def stub_solc_compile(monkeypatch):
    from app.services.solc_compiler import CompileResult, SolcCompileError

    def fake_compile(source_code: str, compiler_version=None, filename=None):
        if "contract Broken" in source_code or "this is not solidity" in source_code:
            raise SolcCompileError("solc 0.8.24 failed: parse error")
        name = filename or "Contract.sol"
        return CompileResult(
            solc_version=compiler_version or "0.8.24",
            sources={name: {"id": 0, "ast": {"nodeType": "SourceUnit", "nodes": []}}},
        )

    monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", fake_compile)
    monkeypatch.setattr(
        "app.services.indexing_worker.run_slither",
        lambda *_args, **_kwargs: {
            "success": True,
            "error": None,
            "results": {"detectors": []},
        },
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.upsert_code_chunks",
        lambda *_args, **_kwargs: 1,
    )


@pytest.fixture(autouse=True)
def clean_state():
    db: Session = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()

    from app.celery_config import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    yield

    from app.utils.redis_lock import get_redis_client

    try:
        client = get_redis_client()
        for key in client.scan_iter("indexing_lock:*"):
            client.delete(key)
        for key in client.scan_iter("quota:*"):
            client.delete(key)
        for key in client.scan_iter("rl:*"):
            client.delete(key)
    except Exception:
        pass

    db = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()


def _register_user() -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_analyze_requires_auth():
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
    )
    assert response.status_code == 401


def test_analyze_invalid_address():
    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": "not-an-address", "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


def test_analyze_unsupported_chain():
    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 999},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


def test_analyze_etherscan_not_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ETHERSCAN_API_KEY", "")
    get_settings.cache_clear()

    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 503

    get_settings.cache_clear()


def test_analyze_by_address_success(monkeypatch):
    from app.core.config import get_settings
    from app.services.etherscan_loader import ContractSource

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    def fake_fetch(address: str, chain_id: int) -> ContractSource:
        return ContractSource(
            address=address.lower(),
            chain_id=chain_id,
            source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
        )

    monkeypatch.setattr("app.services.contract_service.fetch_contract_source", fake_fetch)

    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "indexing"
    assert data["contract_id"]
    assert data["task_id"]

    status_response = client.get(
        f"/api/v1/contracts/{data['contract_id']}/status",
        headers=_auth_headers(token),
    )
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["status"] == "ready"
    assert status_data["compiler_version"] == "0.8.24"
    assert status_data["name"] == "Demo"
    assert status_data["risk_score"] == "A"
    assert status_data["risk_model"] in {"xgboost", "heuristic"}
    assert status_data["features_version"] == "v1"
    assert status_data["chunk_count"] == 1

    db = SessionLocal()
    stored = db.query(Contract).filter(Contract.id == data["contract_id"]).first()
    assert stored is not None
    assert stored.ast is not None
    assert stored.ast["solc_version"] == "0.8.24"
    assert stored.slither_report is not None
    assert stored.slither_report["success"] is True
    assert stored.chunk_count == 1
    db.close()

    get_settings.cache_clear()


def test_analyze_idempotent_when_loaded(monkeypatch):
    from app.core.config import get_settings
    from app.services.etherscan_loader import ContractSource

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    calls = {"count": 0}

    def fake_fetch(address: str, chain_id: int) -> ContractSource:
        calls["count"] += 1
        return ContractSource(
            address=address.lower(),
            chain_id=chain_id,
            source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
            name="Demo",
            compiler_version="0.8.24",
            is_verified=True,
        )

    monkeypatch.setattr("app.services.contract_service.fetch_contract_source", fake_fetch)

    token = _register_user()
    headers = _auth_headers(token)
    payload = {"address": TEST_ADDRESS, "chain_id": 1}

    first = client.post("/api/v1/contracts/analyze", json=payload, headers=headers)
    assert first.status_code == 202
    contract_id = first.json()["contract_id"]

    second = client.post("/api/v1/contracts/analyze", json=payload, headers=headers)
    assert second.status_code == 202
    assert second.json()["contract_id"] == contract_id
    assert second.json()["reused"] is True
    assert first.json().get("reused") is False
    assert calls["count"] == 1

    get_settings.cache_clear()


def test_upload_sol_success():
    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze/upload",
        headers=_auth_headers(token),
        files={"file": ("MyToken.sol", SAMPLE_SOL, "application/octet-stream")},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "indexing"

    status_response = client.get(
        f"/api/v1/contracts/{data['contract_id']}/status",
        headers=_auth_headers(token),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "ready"
    assert status_response.json()["compiler_version"] == "0.8.24"
    assert status_response.json()["chunk_count"] == 1


def test_upload_zip_success():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Token.sol", SAMPLE_SOL)

    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze/upload",
        headers=_auth_headers(token),
        files={"file": ("bundle.zip", buffer.getvalue(), "application/zip")},
        data={"chain_id": "0"},
    )
    assert response.status_code == 202
    assert response.json()["contract_id"]


def test_status_not_found():
    token = _register_user()
    response = client.get("/api/v1/contracts/99999/status", headers=_auth_headers(token))
    assert response.status_code == 404


def test_status_requires_auth():
    response = client.get("/api/v1/contracts/1/status")
    assert response.status_code == 401


def test_analyze_compile_failure(monkeypatch):
    from app.celery_config import celery_app
    from app.core.config import get_settings
    from app.services.etherscan_loader import ContractSource
    from app.services.solc_compiler import SolcCompileError

    celery_app.conf.task_eager_propagates = False
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    def fake_fetch(address: str, chain_id: int) -> ContractSource:
        return ContractSource(
            address=address.lower(),
            chain_id=chain_id,
            source_code="pragma solidity ^0.8.24;\ncontract Broken {",
            name="Broken",
            compiler_version="0.8.24",
            is_verified=True,
        )

    def fail_compile(*_args, **_kwargs):
        raise SolcCompileError("solc 0.8.24 failed: parse error")

    monkeypatch.setattr("app.services.contract_service.fetch_contract_source", fake_fetch)
    monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", fail_compile)

    token = _register_user()
    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 202
    contract_id = response.json()["contract_id"]

    status_response = client.get(
        f"/api/v1/contracts/{contract_id}/status",
        headers=_auth_headers(token),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "failed"

    get_settings.cache_clear()


def test_list_contracts_requires_auth():
    response = client.get("/api/v1/contracts")
    assert response.status_code == 401
    history = client.get("/api/v1/history")
    assert history.status_code == 401


def test_list_contracts_returns_own():
    token = _register_user()
    empty = client.get("/api/v1/contracts", headers=_auth_headers(token))
    assert empty.status_code == 200
    assert empty.json() == []

    uploaded = client.post(
        "/api/v1/contracts/analyze/upload",
        headers=_auth_headers(token),
        files={"file": ("MyToken.sol", SAMPLE_SOL, "application/octet-stream")},
    )
    assert uploaded.status_code == 202
    contract_id = uploaded.json()["contract_id"]

    listing = client.get("/api/v1/contracts", headers=_auth_headers(token))
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["contract_id"] == contract_id
    assert rows[0]["status"] == "ready"
    assert rows[0]["created_at"] is not None

    history = client.get("/api/v1/history", headers=_auth_headers(token))
    assert history.status_code == 200
    assert history.json()[0]["contract_id"] == contract_id


def test_analyze_409_while_indexing():
    token = _register_user()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
    user_id = me.json()["id"]

    db = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address=TEST_ADDRESS.lower(),
        chain_id=1,
        name="Indexing",
        source_code="pragma solidity ^0.8.24;\ncontract Indexing {}",
        compiler_version="0.8.24",
        is_verified=True,
        status="indexing",
    )
    db.add(contract)
    db.commit()
    db.close()

    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 409
    assert "already being indexed" in response.json()["detail"]


def test_reanalyze_ready_does_not_spawn_celery(monkeypatch):
    delay_calls: list[int] = []

    def fake_delay(*_args, **_kwargs):
        delay_calls.append(1)

        class _Task:
            id = "should-not-run"

        return _Task()

    monkeypatch.setattr(
        "app.services.contract_service.load_contract_task.delay",
        fake_delay,
    )

    token = _register_user()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
    user_id = me.json()["id"]

    db = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address=TEST_ADDRESS.lower(),
        chain_id=1,
        name="Demo",
        source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
        compiler_version="0.8.24",
        is_verified=True,
        status="ready",
        risk_score="A",
        chunk_count=1,
        celery_task_id="existing-task",
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    db.close()

    response = client.post(
        "/api/v1/contracts/analyze",
        json={"address": TEST_ADDRESS, "chain_id": 1},
        headers=_auth_headers(token),
    )
    assert response.status_code == 202
    data = response.json()
    assert data["contract_id"] == contract_id
    assert data["status"] == "ready"
    assert data["task_id"] == "existing-task"
    assert data["reused"] is True
    assert delay_calls == []


def test_report_pdf_ready_contract():
    token = _register_user()
    uploaded = client.post(
        "/api/v1/contracts/analyze/upload",
        headers=_auth_headers(token),
        files={"file": ("MyToken.sol", SAMPLE_SOL, "application/octet-stream")},
    )
    assert uploaded.status_code == 202
    contract_id = uploaded.json()["contract_id"]

    response = client.get(
        f"/api/v1/contracts/{contract_id}/report",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert b"pragma solidity" not in response.content


def test_report_pdf_not_ready():
    token = _register_user()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
    user_id = me.json()["id"]

    db = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address="0x" + "b" * 40,
        chain_id=1,
        name="Pending",
        source_code="pragma solidity ^0.8.24;\ncontract Pending {}",
        status="indexing",
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    db.close()

    response = client.get(
        f"/api/v1/contracts/{contract_id}/report",
        headers=_auth_headers(token),
    )
    assert response.status_code == 409


def test_report_pdf_requires_auth():
    response = client.get("/api/v1/contracts/1/report")
    assert response.status_code == 401


def test_findings_endpoint_maps_slither_detectors():
    token = _register_user()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
    user_id = me.json()["id"]

    db = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address="0x" + "c" * 40,
        chain_id=1,
        name="Vault",
        source_code="pragma solidity ^0.8.24;\ncontract Vault {}",
        status="ready",
        risk_score="C",
        risk_model="heuristic",
        features_version="v1",
        slither_report={
            "success": True,
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "confidence": "Medium",
                        "description": "Reentrancy in withdraw()",
                        "elements": [
                            {
                                "source_mapping": {
                                    "filename_short": "Vault.sol",
                                    "lines": [10, 12],
                                }
                            }
                        ],
                    }
                ]
            },
        },
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    db.close()

    response = client.get(
        f"/api/v1/contracts/{contract_id}/findings",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contract_id"] == contract_id
    assert body["findings"][0]["check"] == "reentrancy-eth"
    assert body["findings"][0]["location"] == "Vault.sol:10-12"
    assert body["findings"][0]["file_path"] == "Vault.sol"


def test_findings_requires_auth():
    response = client.get("/api/v1/contracts/1/findings")
    assert response.status_code == 401


def test_source_endpoint_splits_files():
    token = _register_user()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
    user_id = me.json()["id"]

    db = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address="0x" + "d" * 40,
        chain_id=1,
        name="Vault",
        source_code=(
            "// File: Vault.sol\npragma solidity ^0.8.24;\ncontract Vault {}\n"
            "// File: lib/Math.sol\nlibrary Math {}"
        ),
        status="ready",
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    db.close()

    response = client.get(
        f"/api/v1/contracts/{contract_id}/source",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contract_id"] == contract_id
    paths = {item["path"] for item in body["files"]}
    assert paths == {"Vault.sol", "lib/Math.sol"}
    vault = next(item for item in body["files"] if item["path"] == "Vault.sol")
    assert "contract Vault" in vault["content"]


def test_source_requires_auth():
    response = client.get("/api/v1/contracts/1/source")
    assert response.status_code == 401
