"""Bounded retries for explicit analyzer admission-control responses."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import time

MAX_ANALYZER_BUSY_RETRIES = 18
MAX_ANALYZER_BUSY_WAIT_SECONDS = 90.0
MAX_ANALYZER_RETRY_AFTER_SECONDS = 15.0


def is_retryable_analyzer_busy(response) -> bool:
    """Retry only the analyzer's capacity response, which includes Retry-After."""
    if getattr(response, "status_code", None) != 503:
        return False
    headers = getattr(response, "headers", {}) or {}
    return _header_value(headers, "retry-after") is not None


def analyzer_busy_retry_delay(response, retry_count: int, total_wait: float) -> float | None:
    """Return a capped delay, or None once retry/time budgets are exhausted."""
    if retry_count >= MAX_ANALYZER_BUSY_RETRIES:
        return None

    headers = getattr(response, "headers", {}) or {}
    retry_after = _header_value(headers, "retry-after")
    delay = _parse_retry_after(retry_after) if retry_after is not None else None
    if delay is None or delay <= 0:
        delay = min(2.0**retry_count, MAX_ANALYZER_RETRY_AFTER_SECONDS)
    delay = min(delay, MAX_ANALYZER_RETRY_AFTER_SECONDS)
    if total_wait + delay > MAX_ANALYZER_BUSY_WAIT_SECONDS:
        return None
    return delay


def wait_for_analyzer_retry(delay: float) -> None:
    time.sleep(delay)


def _header_value(headers, expected_name: str) -> str | None:
    for name, value in headers.items():
        if str(name).lower() == expected_name:
            normalized = str(value).strip()
            return normalized or None
    return None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
