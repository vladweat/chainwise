import logging

import redis
from fastapi import HTTPException, status

from app.utils.redis_lock import get_redis_client

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


def enforce_rate_limit(scope: str, user_id: int, limit: int) -> None:
    """Fixed-window Redis limiter. limit<=0 disables. Redis errors fail open."""
    if limit <= 0:
        return

    key = f"rl:{scope}:{user_id}"
    try:
        client = get_redis_client()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, WINDOW_SECONDS)
        ttl = int(client.ttl(key) or WINDOW_SECONDS)
    except redis.RedisError as exc:
        logger.warning("Rate limit Redis failed, allowing request: %s", exc)
        return

    if count > limit:
        retry_after = str(max(ttl, 1))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": retry_after},
        )
