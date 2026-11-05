import uuid

from fastapi.testclient import TestClient

from app.core.auth_cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.main import app
from app.models.user import User

client = TestClient(app)


def _unique_email() -> str:
    return f"cookie-{uuid.uuid4().hex[:8]}@example.com"


CSRF_HEADERS = {"X-Requested-With": "ChainWise"}


def test_register_sets_httponly_cookies():
    email = _unique_email()
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert response.status_code == 201
    assert ACCESS_COOKIE in response.cookies
    assert REFRESH_COOKIE in response.cookies
    header = response.headers.get("set-cookie") or ""
    assert "httponly" in header.lower()

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_logout_clears_cookies():
    email = _unique_email()
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert client.get("/api/v1/auth/me").status_code == 200
    logout = client.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_refresh_rotates_cookies():
    email = _unique_email()
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    first_refresh = client.cookies.get(REFRESH_COOKIE)
    rotated = client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert rotated.status_code == 200
    assert rotated.json()["access_token"]
    assert client.cookies.get(REFRESH_COOKIE) != first_refresh
    assert client.get("/api/v1/auth/me").status_code == 200


def test_refresh_token_cannot_be_used_as_bearer():
    email = _unique_email()
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    refresh = response.cookies.get(REFRESH_COOKIE)
    client.cookies.clear()
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert me.status_code == 401


def test_access_token_without_typ_still_works():
    email = _unique_email()
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    role = user.role
    db.close()
    token = create_access_token(email, role=role)
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
