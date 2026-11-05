"""Optional Sentry for FastAPI and Celery. No-op without SENTRY_DSN or the SDK."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.logging import SENSITIVE_KEYS

logger = logging.getLogger(__name__)

_initialized = False

_REDACT_MARKERS = ("pragma solidity", "function ", "contract ", "llm", "completion")


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    if "pragma solidity" in lowered:
        return True
    if len(text) > 800 and any(marker in lowered for marker in _REDACT_MARKERS):
        return True
    return False


def _scrub(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        return "[redacted]" if _looks_sensitive(value) else value
    if isinstance(value, dict):
        return {str(item_key): _scrub(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def before_send(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Drop source code and LLM text from Sentry payloads."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("body", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in {"authorization", "cookie", "x-api-key"}:
                    headers[name] = "[redacted]"
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = _scrub(extra)
    return _scrub(event)


def init_sentry() -> None:
    global _initialized
    if _initialized:
        return

    from app.core.config import get_settings

    settings = get_settings()
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=before_send,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            CeleryIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    _initialized = True
    logger.info("Sentry initialized", extra={"environment": settings.environment})


def capture_pipeline_error(component: str, exc: BaseException) -> None:
    """Tag solc / Slither / LLM failures when Sentry is active."""
    try:
        import sentry_sdk
    except ImportError:
        return
    if not sentry_sdk.is_initialized():
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("pipeline_component", component)
        sentry_sdk.capture_exception(exc)
