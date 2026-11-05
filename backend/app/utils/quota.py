"""Soft per-user quotas (no billing). Redis day counters + contract count in Postgres."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.contract import Contract
from app.utils.redis_lock import get_redis_client

logger = logging.getLogger(__name__)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _enforce_daily(scope: str, user_id: int, limit: int, detail: str) -> None:
    if limit <= 0:
        return

    key = f"quota:{scope}:{user_id}:{_utc_day()}"
    try:
        client = get_redis_client()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, 86_400)
        ttl = int(client.ttl(key) or 86_400)
    except redis.RedisError as exc:
        logger.warning("Quota Redis failed, allowing request: %s", exc)
        return

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(max(ttl, 1))},
        )


def enforce_analyze_quota(user_id: int) -> None:
    settings = get_settings()
    _enforce_daily(
        "analyze",
        user_id,
        settings.quota_analyze_per_day,
        "Daily analyze quota exceeded",
    )


def enforce_chat_quota(user_id: int) -> None:
    settings = get_settings()
    _enforce_daily(
        "chat",
        user_id,
        settings.quota_chat_per_day,
        "Daily chat quota exceeded",
    )


def enforce_max_contracts(db: Session, user_id: int) -> None:
    settings = get_settings()
    limit = settings.quota_max_contracts
    if limit <= 0:
        return
    count = db.query(Contract).filter(Contract.user_id == user_id).count()
    if count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Contract limit reached ({limit})",
        )
