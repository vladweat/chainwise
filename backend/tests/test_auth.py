import uuid

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


@pytest.fixture(autouse=True)
def clean_tables():
    db: Session = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


def test_register_and_me():
    email = _unique_email()
    password = "securepass123"

    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    assert token

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    data = me.json()
    assert data["email"] == email
    assert data["role"] == "user"
    assert data["is_active"] is True


def test_register_duplicate_email():
    email = _unique_email()
    payload = {"email": email, "password": "securepass123"}

    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_login_success():
    email = _unique_email()
    password = "securepass123"
    client.post("/api/v1/auth/register", json={"email": email, "password": password})

    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_wrong_password():
    email = _unique_email()
    client.post("/api/v1/auth/register", json={"email": email, "password": "securepass123"})

    response = client.post("/api/v1/auth/login", json={"email": email, "password": "wrongpassword"})
    assert response.status_code == 401


def test_me_unauthorized():
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_google_not_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    get_settings.cache_clear()

    response = client.post("/api/v1/auth/google", json={"id_token": "fake-token"})
    assert response.status_code == 503

    get_settings.cache_clear()


def test_google_auth_success(monkeypatch):
    email = _unique_email()
    google_sub = f"google-{uuid.uuid4().hex[:8]}"

    def fake_verify(token: str, client_id: str) -> dict:
        assert token == "valid-token"
        assert client_id == "test-client-id.apps.googleusercontent.com"
        return {"sub": google_sub, "email": email}

    from app.core.config import get_settings

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.auth.verify_google_id_token", fake_verify)

    response = client.post("/api/v1/auth/google", json={"id_token": "valid-token"})
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    get_settings.cache_clear()


def test_admin_role():
    email = _unique_email()
    db: Session = SessionLocal()
    user = User(email=email, hashed_password="hashed", role="admin")
    db.add(user)
    db.commit()
    db.close()

    from app.core.security import create_access_token

    token = create_access_token(email, role="admin")
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
