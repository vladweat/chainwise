import secrets

import redis

from app.core.config import get_settings

LOCK_PREFIX = "indexing_lock"
DEFAULT_LOCK_TTL_SECONDS = 3600
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def lock_key(user_id: int, chain_id: int, address: str) -> str:
    return f"{LOCK_PREFIX}:{user_id}:{chain_id}:{address.lower()}"


def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def acquire_indexing_lock(
    user_id: int, chain_id: int, address: str, ttl: int = DEFAULT_LOCK_TTL_SECONDS
) -> str | None:
    owner_token = secrets.token_urlsafe(32)
    client = get_redis_client()
    if client.set(lock_key(user_id, chain_id, address), owner_token, nx=True, ex=ttl):
        return owner_token
    return None


def release_indexing_lock(
    user_id: int, chain_id: int, address: str, owner_token: str | None
) -> bool:
    if not owner_token:
        return False
    client = get_redis_client()
    return bool(client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key(user_id, chain_id, address), owner_token))


def is_indexing_locked(user_id: int, chain_id: int, address: str) -> bool:
    client = get_redis_client()
    return client.exists(lock_key(user_id, chain_id, address)) > 0
