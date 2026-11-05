import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.analysis_report import AnalysisReport
from app.models.chat_message import ChatMessage
from app.models.contract import Contract

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_settings():
    yield
    get_settings.cache_clear()


def _unique_email() -> str:
    return f"rl-{uuid.uuid4().hex[:8]}@example.com"


def _auth() -> dict[str, str]:
    email = _unique_email()
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_analyze_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ANALYZE_PER_MINUTE", "1")
    get_settings.cache_clear()
    headers = _auth()

    first = client.post(
        "/api/v1/contracts/analyze",
        json={"address": "not-an-address", "chain_id": 1},
        headers=headers,
    )
    assert first.status_code == 400

    second = client.post(
        "/api/v1/contracts/analyze",
        json={"address": "not-an-address", "chain_id": 1},
        headers=headers,
    )
    assert second.status_code == 429
    assert second.headers.get("Retry-After")
    get_settings.cache_clear()


def test_chat_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_CHAT_PER_MINUTE", "1")
    get_settings.cache_clear()
    headers = _auth()
    me = client.get("/api/v1/auth/me", headers=headers)
    user_id = me.json()["id"]

    db: Session = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    contract = Contract(
        user_id=user_id,
        address="0x" + "b" * 40,
        chain_id=1,
        name="Rate.sol",
        source_code="contract Rate {}",
        status="ready",
        chunk_count=0,
    )
    db.add(contract)
    db.commit()
    contract_id = contract.id
    db.close()

    first = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": "Who is owner?", "stream": False},
        headers=headers,
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": "Who is owner?", "stream": False},
        headers=headers,
    )
    assert second.status_code == 429
    get_settings.cache_clear()
