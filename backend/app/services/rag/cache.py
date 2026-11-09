"""LLM answer cache: Redis + analysis_reports, plus chat turn persistence."""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.analysis_report import AnalysisReport
from app.schemas.chat import Citation
from app.services.chat_history import save_chat_turn
from app.services.rag.retrieval import citations_are_interface_stubs
from app.services.rag.types import ChatResult
from app.utils.redis_lock import get_redis_client

logger = logging.getLogger(__name__)

CACHE_VERSION = "v7"
LLM_CACHE_TTL_SECONDS = 86400


def question_hash(question: str, generation_id: str | None = None) -> str:
    if generation_id is None:
        value = f"{CACHE_VERSION}\n{question}"
    else:
        value = f"{CACHE_VERSION}\n{generation_id}\n{question}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cache_key(contract_id: int, qhash: str) -> str:
    return f"llm_cache:{CACHE_VERSION}:{contract_id}:{qhash}"


def load_cached_answer(
    db: Session,
    contract_id: int,
    question: str,
    *,
    generation_id: str | None = None,
) -> ChatResult | None:
    qhash = question_hash(question, generation_id)
    key = cache_key(contract_id, qhash)
    redis_value = _redis_get(key)
    if redis_value is not None:
        result = _result_from_cache_payload(redis_value, cached=True)
        if not citations_are_interface_stubs(result.citations):
            return result
        _redis_delete(key)

    row = (
        db.query(AnalysisReport)
        .filter(
            AnalysisReport.contract_id == contract_id,
            AnalysisReport.question_hash == qhash,
        )
        .first()
    )
    if row is None or not row.llm_answer:
        return None
    result = ChatResult(
        answer=row.llm_answer,
        citations=_citations_from_json(row.citations),
        cached=True,
        tokens_used=row.tokens_used,
        cost_usd=row.cost_usd,
    )
    if citations_are_interface_stubs(result.citations):
        return None
    _redis_set(key, _cache_payload(result))
    return result


def persist_chat_turn(
    db: Session,
    *,
    contract_id: int,
    user_id: int,
    question: str,
    result: ChatResult,
) -> None:
    try:
        save_chat_turn(
            db,
            contract_id=contract_id,
            user_id=user_id,
            question=question,
            answer=result.answer,
            citations=result.citations,
            cached=result.cached,
        )
    except Exception:
        logger.exception("Failed to persist chat history for contract %s", contract_id)


def save_cached_answer(
    db: Session,
    *,
    contract_id: int,
    user_id: int | None,
    question: str,
    result: ChatResult,
    generation_id: str | None = None,
) -> None:
    qhash = question_hash(question, generation_id)
    if citations_are_interface_stubs(result.citations):
        _redis_delete(cache_key(contract_id, qhash))
        return
    payload = _cache_payload(result)
    _redis_set(cache_key(contract_id, qhash), payload)

    existing = (
        db.query(AnalysisReport)
        .filter(
            AnalysisReport.contract_id == contract_id,
            AnalysisReport.question_hash == qhash,
        )
        .first()
    )
    citations = [citation.model_dump() for citation in result.citations]
    if existing is None:
        db.add(
            AnalysisReport(
                contract_id=contract_id,
                user_id=user_id,
                question_hash=qhash,
                llm_answer=result.answer,
                citations=citations,
                tokens_used=result.tokens_used,
                cost_usd=result.cost_usd,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    else:
        existing.llm_answer = result.answer
        existing.citations = citations
        existing.tokens_used = result.tokens_used
        existing.cost_usd = result.cost_usd
        db.commit()


def _cache_payload(result: ChatResult) -> dict:
    return {
        "answer": result.answer,
        "citations": [citation.model_dump() for citation in result.citations],
        "tokens_used": result.tokens_used,
        "cost_usd": str(result.cost_usd) if result.cost_usd is not None else None,
    }


def _result_from_cache_payload(payload: dict, *, cached: bool) -> ChatResult:
    cost_raw = payload.get("cost_usd")
    cost = Decimal(str(cost_raw)) if cost_raw is not None else None
    return ChatResult(
        answer=str(payload.get("answer") or ""),
        citations=_citations_from_json(payload.get("citations")),
        cached=cached,
        tokens_used=payload.get("tokens_used"),
        cost_usd=cost,
    )


def _citations_from_json(raw: Any) -> list[Citation]:
    if not raw:
        return []
    citations: list[Citation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        citations.append(
            Citation(
                file_path=str(item.get("file_path") or "Contract.sol"),
                function_name=str(item.get("function_name") or ""),
                start_line=int(item.get("start_line") or 1),
                end_line=int(item.get("end_line") or 1),
                content=str(item.get("content") or ""),
            )
        )
    return citations


def _redis_get(key: str) -> dict | None:
    try:
        value = get_redis_client().get(key)
    except Exception as exc:
        logger.warning("Redis cache get failed: %s", exc)
        return None
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _redis_set(key: str, payload: dict) -> None:
    try:
        get_redis_client().set(key, json.dumps(payload, ensure_ascii=False), ex=LLM_CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Redis cache set failed: %s", exc)


def _redis_delete(key: str) -> None:
    try:
        get_redis_client().delete(key)
    except Exception as exc:
        logger.warning("Redis cache delete failed: %s", exc)
