import redis

from app.core.config import get_settings

LOCK_PREFIX = "indexing_lock"
DEFAULT_LOCK_TTL_SECONDS = 3600


def lock_key(user_id: int, chain_id: int, address: str) -> str:
    return f"{LOCK_PREFIX}:{user_id}:{chain_id}:{address.lower()}"


def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def acquire_indexing_lock(
    user_id: int, chain_id: int, address: str, ttl: int = DEFAULT_LOCK_TTL_SECONDS
) -> bool:
    client = get_redis_client()
    return bool(client.set(lock_key(user_id, chain_id, address), "1", nx=True, ex=ttl))


def release_indexing_lock(user_id: int, chain_id: int, address: str) -> None:
    client = get_redis_client()
    client.delete(lock_key(user_id, chain_id, address))


def is_indexing_locked(user_id: int, chain_id: int, address: str) -> bool:
    client = get_redis_client()
    return client.exists(lock_key(user_id, chain_id, address)) > 0
