from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Response
from jose import JWTError, jwt
from redis import RedisError

from app.core.config import get_settings
from app.utils.redis_lock import get_redis_client

ACCESS_COOKIE = "cw_access"
REFRESH_COOKIE = "cw_refresh"
REFRESH_PREFIX = "refresh:"


def cookie_params() -> dict:
    settings = get_settings()
    samesite = (settings.cookie_samesite or "lax").strip().lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    secure = bool(settings.cookie_secure)
    if samesite == "none":
        secure = True
    return {
        "httponly": True,
        "secure": secure,
        "samesite": samesite,
        "path": "/",
    }


def create_refresh_token(subject: str, *, role: str) -> str:
    settings = get_settings()
    jti = uuid4().hex
    expire = datetime.now(UTC) + timedelta(minutes=settings.refresh_token_expire_minutes)
    payload = {"sub": subject, "role": role, "exp": expire, "typ": "refresh", "jti": jti}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    ttl = settings.refresh_token_expire_minutes * 60
    try:
        get_redis_client().set(f"{REFRESH_PREFIX}{jti}", subject, ex=ttl)
    except RedisError:
        pass
    return token


def decode_refresh_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
    if payload.get("typ") != "refresh":
        raise ValueError("Invalid token")
    jti = payload.get("jti")
    email = payload.get("sub")
    if not jti or not email:
        raise ValueError("Invalid token")
    try:
        stored = get_redis_client().get(f"{REFRESH_PREFIX}{jti}")
    except RedisError as exc:
        raise ValueError("Invalid token") from exc
    if stored != email:
        raise ValueError("Invalid token")
    return payload


def revoke_refresh_token(token: str) -> None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        jti = payload.get("jti")
        if jti:
            get_redis_client().delete(f"{REFRESH_PREFIX}{jti}")
    except (JWTError, RedisError, ValueError):
        return


def set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    settings = get_settings()
    params = cookie_params()
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.access_token_expire_minutes * 60,
        **params,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_expire_minutes * 60,
        **params,
    )


def clear_auth_cookies(response: Response) -> None:
    params = cookie_params()
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(
            name,
            path=params["path"],
            samesite=params["samesite"],
            secure=params["secure"],
            httponly=params["httponly"],
        )
