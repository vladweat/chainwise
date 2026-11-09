"""RAG facade: answer_question and SSE streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.contract import Contract
from app.models.user import User
from app.models.contract_status import require_chat_ready
from app.services.rag.cache import load_cached_answer, persist_chat_turn, save_cached_answer
from app.services.rag.llm_client import call_openai, stream_openai
from app.services.rag.prompting import NO_CONTEXT_ANSWER, build_messages
from app.services.rag.retrieval import citations_from_chunks, retrieve_for_chat
from app.services.rag.types import ChatResult, LLMUsage, OpenAINotConfiguredError


def _refresh_ready_contract(db: Session, contract: Contract) -> Contract:
    current = (
        db.query(Contract)
        .filter(Contract.id == contract.id)
        .populate_existing()
        .first()
    )
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract not found",
        )
    # A delayed SSE generator may hold an ORM identity from before reindexing.
    # Refresh its artifact relation along with the status/generation columns.
    db.expire(current, ["artifact"])
    require_chat_ready(current)
    return current


def _cache_generation_kwargs(contract: Contract) -> dict[str, str]:
    generation_id = getattr(contract, "analysis_generation_id", None)
    return {"generation_id": generation_id} if generation_id is not None else {}


def answer_question(
    db: Session,
    contract: Contract,
    user: User,
    question: str,
    *,
    retriever=None,
    completer=None,
) -> ChatResult:
    contract = _refresh_ready_contract(db, contract)
    cache_generation = _cache_generation_kwargs(contract)
    cached = load_cached_answer(db, contract.id, question, **cache_generation)
    if cached is not None:
        persist_chat_turn(
            db,
            contract_id=contract.id,
            user_id=user.id,
            question=question,
            result=cached,
        )
        return cached

    chunks = retrieve_for_chat(contract, question, retriever=retriever)
    if not chunks:
        result = ChatResult(answer=NO_CONTEXT_ANSWER, citations=[], cached=False)
        save_cached_answer(
            db,
            contract_id=contract.id,
            user_id=user.id,
            question=question,
            result=result,
            **cache_generation,
        )
        persist_chat_turn(
            db,
            contract_id=contract.id,
            user_id=user.id,
            question=question,
            result=result,
        )
        return result

    messages = build_messages(question, chunks)
    generate = completer or call_openai
    answer, usage = generate(messages)
    result = ChatResult(
        answer=answer,
        citations=citations_from_chunks(chunks, answer),
        cached=False,
        tokens_used=usage.tokens_used,
        cost_usd=usage.cost_usd,
    )
    save_cached_answer(
        db,
        contract_id=contract.id,
        user_id=user.id,
        question=question,
        result=result,
        **cache_generation,
    )
    persist_chat_turn(
        db,
        contract_id=contract.id,
        user_id=user.id,
        question=question,
        result=result,
    )
    return result


def iter_sse_events(
    contract: Contract,
    user: User,
    question: str,
    *,
    retriever=None,
    streamer=None,
) -> Iterator[str]:
    db = SessionLocal()
    try:
        contract = _refresh_ready_contract(db, contract)
        cache_generation = _cache_generation_kwargs(contract)
        cached = load_cached_answer(db, contract.id, question, **cache_generation)
        if cached is not None:
            persist_chat_turn(
                db,
                contract_id=contract.id,
                user_id=user.id,
                question=question,
                result=cached,
            )
            yield from _sse_from_result(cached)
            return

        chunks = retrieve_for_chat(contract, question, retriever=retriever)
        if chunks and not get_settings().openai_api_key:
            raise OpenAINotConfiguredError("OPENAI_API_KEY is not configured")
        citations = citations_from_chunks(chunks)
        yield _sse({"type": "citations", "citations": [c.model_dump() for c in citations]})

        if not chunks:
            result = ChatResult(answer=NO_CONTEXT_ANSWER, citations=[], cached=False)
            yield _sse({"type": "token", "content": result.answer})
            yield _sse(_meta_payload(result))
            yield _sse_done()
            save_cached_answer(
                db,
                contract_id=contract.id,
                user_id=user.id,
                question=question,
                result=result,
                **cache_generation,
            )
            persist_chat_turn(
                db,
                contract_id=contract.id,
                user_id=user.id,
                question=question,
                result=result,
            )
            return

        messages = build_messages(question, chunks)
        generate = streamer or stream_openai
        parts: list[str] = []
        usage = LLMUsage()
        for item in generate(messages):
            if isinstance(item, LLMUsage):
                usage = item
                continue
            parts.append(item)
            yield _sse({"type": "token", "content": item})

        result = ChatResult(
            answer="".join(parts),
            citations=citations,
            cached=False,
            tokens_used=usage.tokens_used,
            cost_usd=usage.cost_usd,
        )
        yield _sse(_meta_payload(result))
        yield _sse_done()
        save_cached_answer(
            db,
            contract_id=contract.id,
            user_id=user.id,
            question=question,
            result=result,
            **cache_generation,
        )
        persist_chat_turn(
            db,
            contract_id=contract.id,
            user_id=user.id,
            question=question,
            result=result,
        )
    finally:
        db.close()


def _sse_from_result(result: ChatResult) -> Iterator[str]:
    yield _sse(
        {
            "type": "citations",
            "citations": [citation.model_dump() for citation in result.citations],
        }
    )
    if result.answer:
        yield _sse({"type": "token", "content": result.answer})
    yield _sse(_meta_payload(result))
    yield _sse_done()


def _meta_payload(result: ChatResult) -> dict[str, Any]:
    cost = float(result.cost_usd) if result.cost_usd is not None else None
    return {
        "type": "meta",
        "cached": result.cached,
        "tokens_used": result.tokens_used,
        "cost_usd": cost,
    }


def _sse(payload: dict | str) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"
