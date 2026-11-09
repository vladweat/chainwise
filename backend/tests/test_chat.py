import json
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.analysis_report import AnalysisReport
from app.models.chat_message import ChatMessage
from app.models.contract import Contract
from app.models.user import User
from app.services.rag_engine import (
    LLMUsage,
    NO_CONTEXT_ANSWER,
    RetrievedChunk,
    question_hash,
)

client = TestClient(app)

SAMPLE_CHUNK = RetrievedChunk(
    file_path="Demo.sol",
    function_name="Demo.ping",
    start_line=3,
    end_line=5,
    content="function ping() external {}",
    score=1.0,
)


@pytest.fixture(autouse=True)
def clean_tables():
    db: Session = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(ChatMessage).delete()
    db.query(AnalysisReport).delete()
    db.query(Contract).delete()
    db.query(User).delete()
    db.commit()
    db.close()


def _register_user() -> tuple[str, int]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "securepass123"},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    return token, me.json()["id"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_contract(
    user_id: int,
    *,
    status: str = "ready",
    analysis_generation_id: str | None = None,
) -> int:
    db: Session = SessionLocal()
    contract = Contract(
        user_id=user_id,
        address="0x" + uuid.uuid4().hex[:40],
        chain_id=1,
        name="Demo",
        source_code="pragma solidity ^0.8.24;\ncontract Demo { function ping() external {} }",
        compiler_version="0.8.24",
        is_verified=True,
        status=status,
        analysis_generation_id=analysis_generation_id,
        chunk_count=1 if status == "ready" else None,
        risk_score="A" if status == "ready" else None,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    contract_id = contract.id
    db.close()
    return contract_id


def _fake_search(*_args, **_kwargs):
    return [SAMPLE_CHUNK]


def _fake_complete(_messages, **_kwargs):
    return "ping — пустая view-функция без сайд-эффектов.", LLMUsage(
        tokens_used=20,
        cost_usd=Decimal("0.000001"),
    )


def _fake_stream(_messages, **_kwargs):
    yield "ping "
    yield "без "
    yield "эффектов."
    yield LLMUsage(tokens_used=12, cost_usd=Decimal("0.000001"))


def test_chat_requires_auth():
    response = client.post("/api/v1/contracts/1/chat", json={"question": "ping?"})
    assert response.status_code == 401


def test_chat_not_found():
    token, _user_id = _register_user()
    response = client.post(
        "/api/v1/contracts/99999/chat",
        json={"question": "ping?"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 404


def test_chat_not_ready():
    token, user_id = _register_user()
    contract_id = _seed_contract(user_id, status="indexing")
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": "ping?"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 409


def test_chat_forbidden_other_user():
    token_a, user_a = _register_user()
    token_b, _user_b = _register_user()
    contract_id = _seed_contract(user_a, status="ready")
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": "ping?"},
        headers=_auth_headers(token_b),
    )
    assert response.status_code == 404


def test_chat_openai_not_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", _fake_search)

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": f"ping-{uuid.uuid4().hex}"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 503
    get_settings.cache_clear()


def test_chat_json_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", _fake_search)
    monkeypatch.setattr("app.services.rag.engine.call_openai", _fake_complete)

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    question = f"What does ping do {uuid.uuid4().hex}?"
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": question},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cached"] is False
    assert "ping" in data["answer"].lower()
    assert data["citations"][0]["file_path"] == "Demo.sol"
    assert data["citations"][0]["start_line"] == 3
    assert data["tokens_used"] == 20

    db = SessionLocal()
    stored = (
        db.query(AnalysisReport)
        .filter(
            AnalysisReport.contract_id == contract_id,
            AnalysisReport.question_hash == question_hash(question),
        )
        .first()
    )
    assert stored is not None
    assert stored.llm_answer == data["answer"]
    assert stored.citations[0]["function_name"] == "Demo.ping"
    db.close()
    get_settings.cache_clear()

    history = client.get(
        f"/api/v1/contracts/{contract_id}/chat",
        headers=_auth_headers(token),
    )
    assert history.status_code == 200
    messages = history.json()
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == question
    assert messages[1]["content"] == data["answer"]
    assert messages[1]["citations"][0]["file_path"] == "Demo.sol"


def test_chat_cache_skips_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    calls = {"complete": 0, "search": 0}

    def counting_search(*args, **kwargs):
        calls["search"] += 1
        return _fake_search(*args, **kwargs)

    def counting_complete(*args, **kwargs):
        calls["complete"] += 1
        return _fake_complete(*args, **kwargs)

    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", counting_search)
    monkeypatch.setattr("app.services.rag.engine.call_openai", counting_complete)

    token, user_id = _register_user()
    contract_id = _seed_contract(
        user_id, analysis_generation_id=str(uuid.uuid4())
    )
    question = f"cached ping {uuid.uuid4().hex}"
    headers = _auth_headers(token)
    first = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": question},
        headers=headers,
    )
    second = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": question},
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert second.json()["answer"] == first.json()["answer"]
    assert calls["complete"] == 1
    assert calls["search"] == 1

    history = client.get(
        f"/api/v1/contracts/{contract_id}/chat",
        headers=headers,
    )
    assert history.status_code == 200
    messages = history.json()
    assert len(messages) == 4
    assert messages[0]["content"] == question
    assert messages[2]["content"] == question
    assert messages[3]["cached"] is True
    get_settings.cache_clear()


@pytest.mark.parametrize("stream", [False, True])
def test_chat_after_reindex_ignores_previous_generation_stores(monkeypatch, stream):
    from app.services.rag import cache

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    old_generation = str(uuid.uuid4())
    current_generation = str(uuid.uuid4())
    token, user_id = _register_user()
    contract_id = _seed_contract(user_id, analysis_generation_id=current_generation)
    question = f"reindexed question {uuid.uuid4().hex}"

    old_citation = {
        "file_path": "Old.sol",
        "function_name": "Old.withdraw",
        "start_line": 3,
        "end_line": 5,
        "content": "function withdraw() external {}",
    }
    old_hash = question_hash(question, old_generation)
    db = SessionLocal()
    db.add(
        AnalysisReport(
            contract_id=contract_id,
            user_id=user_id,
            question_hash=old_hash,
            llm_answer="stale answer",
            citations=[old_citation],
        )
    )
    db.commit()
    db.close()

    old_key = cache.cache_key(contract_id, old_hash)
    redis_entries = {
        old_key: {
            "answer": "stale answer",
            "citations": [old_citation],
            "tokens_used": 5,
            "cost_usd": None,
        }
    }
    monkeypatch.setattr(cache, "_redis_get", lambda key: redis_entries.get(key))
    monkeypatch.setattr(
        cache,
        "_redis_set",
        lambda key, payload: redis_entries.__setitem__(key, payload),
    )
    monkeypatch.setattr(
        cache,
        "_redis_delete",
        lambda _key: (_ for _ in ()).throw(RuntimeError("Redis delete unavailable")),
    )

    current_chunk = RetrievedChunk(
        file_path="Current.sol",
        function_name="Demo.update",
        start_line=40,
        end_line=48,
        content="function update() external { currentGeneration = true; }",
        kind="function",
        score=1.0,
    )
    seen_generations = []

    def current_search(_contract_id, _question, *, generation, **_kwargs):
        seen_generations.append(generation)
        return [current_chunk]

    complete_calls = []

    def complete(_messages, **_kwargs):
        complete_calls.append("json")
        return "current generation answer", LLMUsage(tokens_used=12, cost_usd=None)

    def stream_answer(_messages, **_kwargs):
        complete_calls.append("sse")
        yield "current generation answer"
        yield LLMUsage(tokens_used=12, cost_usd=None)

    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", current_search)
    monkeypatch.setattr("app.services.rag.engine.call_openai", complete)
    monkeypatch.setattr("app.services.rag.engine.stream_openai", stream_answer)

    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": question, "stream": stream},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    if stream:
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        assert next(event for event in events if event["type"] == "meta")["cached"] is False
        assert events[0]["citations"][0]["file_path"] == "Current.sol"
    else:
        body = response.json()
        assert body["cached"] is False
        assert body["answer"] == "current generation answer"
        assert body["citations"][0]["file_path"] == "Current.sol"

    assert seen_generations == [current_generation]
    assert complete_calls == ["sse" if stream else "json"]
    assert redis_entries[old_key]["answer"] == "stale answer"
    get_settings.cache_clear()


@pytest.mark.parametrize("stream", [False, True])
def test_failed_reindex_cannot_serve_last_successful_cache(monkeypatch, stream):
    from app.services.rag import cache

    token, user_id = _register_user()
    old_generation = str(uuid.uuid4())
    contract_id = _seed_contract(
        user_id,
        status="failed",
        analysis_generation_id=old_generation,
    )
    question = f"failed generation {uuid.uuid4().hex}"
    old_hash = question_hash(question, old_generation)
    old_citation = {
        "file_path": "Old.sol",
        "function_name": "Old.withdraw",
        "start_line": 3,
        "end_line": 5,
        "content": "function withdraw() external {}",
    }
    db = SessionLocal()
    db.add(
        AnalysisReport(
            contract_id=contract_id,
            user_id=user_id,
            question_hash=old_hash,
            llm_answer="stale answer",
            citations=[old_citation],
        )
    )
    db.commit()
    db.close()

    reads = []
    old_key = cache.cache_key(contract_id, old_hash)
    redis_entries = {old_key: {"answer": "stale answer"}}
    monkeypatch.setattr(
        cache,
        "_redis_get",
        lambda key: reads.append(key) or redis_entries.get(key),
    )
    monkeypatch.setattr(
        "app.services.rag.retrieval.hybrid_search",
        lambda *_args, **_kwargs: pytest.fail("failed contract must not search chunks"),
    )
    monkeypatch.setattr(
        "app.services.rag.engine.call_openai",
        lambda *_args, **_kwargs: pytest.fail("failed contract must not call LLM"),
    )
    monkeypatch.setattr(
        "app.services.rag.engine.stream_openai",
        lambda *_args, **_kwargs: pytest.fail("failed contract must not stream LLM"),
    )

    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": question, "stream": stream},
        headers=_auth_headers(token),
    )

    assert response.status_code == 409
    assert reads == []
    assert old_key in redis_entries
    get_settings.cache_clear()


def test_chat_empty_chunks_without_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", lambda *_a, **_k: [])

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": f"empty-{uuid.uuid4().hex}"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == NO_CONTEXT_ANSWER
    assert data["citations"] == []
    get_settings.cache_clear()


def test_chat_sse_stream(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", _fake_search)
    monkeypatch.setattr("app.services.rag.engine.stream_openai", _fake_stream)

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": f"stream ping {uuid.uuid4().hex}", "stream": True},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    events = []
    for block in body.split("\n\n"):
        line = next((part for part in block.split("\n") if part.startswith("data: ")), None)
        if not line:
            continue
        payload = line[6:]
        if payload == "[DONE]":
            events.append("[DONE]")
            continue
        events.append(json.loads(payload))

    types = [item["type"] if isinstance(item, dict) else item for item in events]
    assert types[0] == "citations"
    assert events[0]["citations"][0]["file_path"] == "Demo.sol"
    assert "token" in types
    assert types[-1] == "[DONE]"
    tokens = "".join(item["content"] for item in events if isinstance(item, dict) and item.get("type") == "token")
    assert tokens == "ping без эффектов."
    meta = next(item for item in events if isinstance(item, dict) and item.get("type") == "meta")
    assert meta["cached"] is False
    get_settings.cache_clear()


def test_chat_history_empty_and_private():
    token_a, user_a = _register_user()
    token_b, _user_b = _register_user()
    contract_id = _seed_contract(user_a)
    empty = client.get(
        f"/api/v1/contracts/{contract_id}/chat",
        headers=_auth_headers(token_a),
    )
    assert empty.status_code == 200
    assert empty.json() == []

    forbidden = client.get(
        f"/api/v1/contracts/{contract_id}/chat",
        headers=_auth_headers(token_b),
    )
    assert forbidden.status_code == 404


def test_chat_history_not_saved_on_openai_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", _fake_search)

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": f"fail-{uuid.uuid4().hex}"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 503
    history = client.get(
        f"/api/v1/contracts/{contract_id}/chat",
        headers=_auth_headers(token),
    )
    assert history.json() == []
    get_settings.cache_clear()


def test_chat_empty_question_rejected():
    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": "   "},
        headers=_auth_headers(token),
    )
    assert response.status_code == 422


def test_chat_json_drops_invented_file_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()

    def complete(_messages, **_kwargs):
        return "See Evil.sol:1-12 for the hidden mint.", LLMUsage(
            tokens_used=12,
            cost_usd=Decimal("0.000001"),
        )

    monkeypatch.setattr("app.services.rag.retrieval.hybrid_search", _fake_search)
    monkeypatch.setattr("app.services.rag.engine.call_openai", complete)

    token, user_id = _register_user()
    contract_id = _seed_contract(user_id)
    response = client.post(
        f"/api/v1/contracts/{contract_id}/chat",
        json={"question": f"where is mint {uuid.uuid4().hex}?"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    paths = [item["file_path"] for item in response.json()["citations"]]
    assert paths == ["Demo.sol"]
    assert "Evil.sol" not in paths
    get_settings.cache_clear()
