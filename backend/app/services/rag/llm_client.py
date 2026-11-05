"""OpenAI-compatible LLM client (sync + stream)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.sentry import capture_pipeline_error
from app.services.rag.types import LLMUsage, OpenAINotConfiguredError, OpenAIRequestError

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_KODIK_BASE_URL = "https://api.kodikrouter.ru/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_KODIK_MODEL = "openai/gpt-4o-mini"
KODIK_KEY_PREFIXES = ("sk-kr_", "kdk_", "sk-sr_")
OPENAI_TIMEOUT = 60.0
INPUT_COST_PER_MILLION = Decimal("0.150")
OUTPUT_COST_PER_MILLION = Decimal("0.600")


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    cost = (
        Decimal(prompt_tokens) * INPUT_COST_PER_MILLION / Decimal(1_000_000)
        + Decimal(completion_tokens) * OUTPUT_COST_PER_MILLION / Decimal(1_000_000)
    )
    return cost.quantize(Decimal("0.000001"))


def _uses_kodik(settings) -> bool:
    key = (settings.openai_api_key or "").strip()
    base = (settings.openai_base_url or "").strip().lower()
    if "kodikrouter.ru" in base:
        return True
    return key.startswith(KODIK_KEY_PREFIXES)


def openai_base_url() -> str:
    settings = get_settings()
    configured = (settings.openai_base_url or "").strip().rstrip("/")
    if configured:
        return configured
    if _uses_kodik(settings):
        return DEFAULT_KODIK_BASE_URL
    return DEFAULT_OPENAI_BASE_URL


def openai_model() -> str:
    settings = get_settings()
    configured = (settings.openai_model or "").strip()
    if configured:
        return configured
    if _uses_kodik(settings):
        return DEFAULT_KODIK_MODEL
    return DEFAULT_OPENAI_MODEL


def openai_completions_url() -> str:
    return f"{openai_base_url()}/chat/completions"


def _openai_headers() -> dict[str, str]:
    key = get_settings().openai_api_key
    if not key:
        raise OpenAINotConfiguredError("OPENAI_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def call_openai(
    messages: list[dict[str, str]],
    *,
    client: httpx.Client | None = None,
) -> tuple[str, LLMUsage]:
    payload = {
        "model": openai_model(),
        "messages": messages,
        "temperature": 0.2,
    }
    http = client or httpx.Client(timeout=OPENAI_TIMEOUT)
    owns_client = client is None
    try:
        response = http.post(openai_completions_url(), headers=_openai_headers(), json=payload)
        if response.status_code >= 400:
            error = OpenAIRequestError(
                f"LLM error {response.status_code}: {response.text[:300]}"
            )
            capture_pipeline_error("llm", error)
            raise error
        body = response.json()
    except OpenAIRequestError:
        raise
    except Exception as exc:
        capture_pipeline_error("llm", exc)
        raise OpenAIRequestError(f"LLM request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    choices = body.get("choices") or []
    if not choices:
        raise OpenAIRequestError("LLM returned no choices")
    answer = ((choices[0].get("message") or {}).get("content")) or ""
    return answer, _usage_from_payload(body.get("usage"))


def stream_openai(
    messages: list[dict[str, str]],
    *,
    client: httpx.Client | None = None,
) -> Iterator[str | LLMUsage]:
    payload: dict[str, Any] = {
        "model": openai_model(),
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    if openai_base_url().startswith(DEFAULT_OPENAI_BASE_URL):
        payload["stream_options"] = {"include_usage": True}
    http = client or httpx.Client(timeout=OPENAI_TIMEOUT)
    owns_client = client is None
    usage = LLMUsage()
    try:
        with http.stream(
            "POST",
            openai_completions_url(),
            headers=_openai_headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                error_body = response.read().decode("utf-8", errors="replace")[:300]
                error = OpenAIRequestError(
                    f"LLM error {response.status_code}: {error_body}"
                )
                capture_pipeline_error("llm", error)
                raise error
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = _usage_from_payload(chunk.get("usage"))
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
    except OpenAIRequestError:
        raise
    except Exception as exc:
        capture_pipeline_error("llm", exc)
        raise OpenAIRequestError(f"LLM stream failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()
    yield usage


def _usage_from_payload(usage: dict | None) -> LLMUsage:
    if not usage:
        return LLMUsage()
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    if total <= 0:
        return LLMUsage()
    return LLMUsage(tokens_used=total, cost_usd=estimate_cost(prompt, completion))
