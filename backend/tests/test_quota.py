import redis
import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.utils import quota


class FakeRedis:
    def __init__(self, *, count: int, ttl_seconds: int = 60) -> None:
        self.count = count
        self.ttl_seconds = ttl_seconds
        self.expired: tuple[str, int] | None = None

    def incr(self, key: str) -> int:
        self.key = key
        return self.count

    def expire(self, key: str, seconds: int) -> None:
        self.expired = (key, seconds)

    def ttl(self, _key: str) -> int:
        return self.ttl_seconds


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_chat_quota_returns_retry_after_when_limit_is_exceeded(monkeypatch):
    monkeypatch.setenv("QUOTA_CHAT_PER_DAY", "1")
    client = FakeRedis(count=2, ttl_seconds=37)
    monkeypatch.setattr(quota, "get_redis_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        quota.enforce_chat_quota(user_id=42)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Daily chat quota exceeded"
    assert exc_info.value.headers == {"Retry-After": "37"}
    assert client.key.startswith("quota:chat:42:")


def test_chat_quota_sets_daily_expiry_for_first_request(monkeypatch):
    monkeypatch.setenv("QUOTA_CHAT_PER_DAY", "1")
    client = FakeRedis(count=1)
    monkeypatch.setattr(quota, "get_redis_client", lambda: client)

    quota.enforce_chat_quota(user_id=42)

    assert client.expired == (client.key, 86_400)


def test_chat_quota_fails_open_when_redis_is_unavailable(monkeypatch):
    monkeypatch.setenv("QUOTA_CHAT_PER_DAY", "1")

    def unavailable_redis():
        raise redis.ConnectionError("Redis unavailable")

    monkeypatch.setattr(quota, "get_redis_client", unavailable_redis)

    quota.enforce_chat_quota(user_id=42)


def test_disabled_chat_quota_does_not_connect_to_redis(monkeypatch):
    monkeypatch.setenv("QUOTA_CHAT_PER_DAY", "0")

    def unexpected_redis_call():
        pytest.fail("disabled quota should not contact Redis")

    monkeypatch.setattr(quota, "get_redis_client", unexpected_redis_call)

    quota.enforce_chat_quota(user_id=42)
