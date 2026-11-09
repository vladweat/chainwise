"""Bounded mocked API smoke for the synthetic Solidity upload journey."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

DEMO_SOL = b"""pragma solidity ^0.8.24;
contract SmokeDemo {
    function ping() public pure returns (uint256) {
        return 7;
    }
}
"""
INVALID_ZIP = b"this is not a ZIP archive"
MAX_STATUS_POLLS = 20
STATUS_POLL_INTERVAL_SECONDS = 0.05
STATUS_TIMEOUT_SECONDS = 5


@pytest.fixture(autouse=True)
def mocked_dependencies(monkeypatch):
    """Keep the smoke local: no solc, Slither, Qdrant, or paid LLM calls."""
    from app.celery_config import celery_app
    from app.core.config import get_settings
    from app.services.rag.types import LLMUsage, RetrievedChunk
    from app.services.solc_compiler import CompileResult

    old_eager = celery_app.conf.task_always_eager
    old_eager_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    assert get_settings().openai_api_key == ""

    indexed_chunks: list[RetrievedChunk] = []
    retrieval_generations: list[str | None] = []

    def fake_compile(_source_code: str, compiler_version=None, filename=None):
        source_name = filename or "SmokeDemo.sol"
        return CompileResult(
            solc_version=compiler_version or "0.8.24",
            sources={
                source_name: {
                    "id": 0,
                    "ast": {
                        "nodeType": "SourceUnit",
                        "nodes": [
                            {
                                "nodeType": "ContractDefinition",
                                "name": "SmokeDemo",
                                "contractKind": "contract",
                                "nodes": [],
                            }
                        ],
                    },
                }
            },
        )

    def fake_upsert(_contract_id, chunks, *, generation=None):
        indexed_chunks[:] = [
            RetrievedChunk(
                file_path=chunk.file_path,
                function_name=chunk.function_name,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                kind=chunk.kind,
            )
            for chunk in chunks
        ]
        return len(chunks)

    def fake_search(_contract_id, _question, *, generation=None, **_kwargs):
        retrieval_generations.append(generation)
        return list(indexed_chunks)

    monkeypatch.setattr(
        "app.services.indexing_worker.compile_to_ast",
        fake_compile,
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.run_slither",
        lambda *_args, **_kwargs: {
            "success": True,
            "error": None,
            "results": {
                "detectors": [
                    {
                        "check": "smoke-demo-check",
                        "impact": "Low",
                        "confidence": "High",
                        "description": (
                            "Synthetic smoke finding for the ping function."
                        ),
                        "elements": [
                            {
                                "type": "function",
                                "name": "SmokeDemo.ping",
                                "source_mapping": {
                                    "filename_short": "SmokeDemo.sol",
                                    "lines": [3, 5],
                                },
                            }
                        ],
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.upsert_code_chunks",
        fake_upsert,
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.score_slither_report_detailed",
        lambda _report: ("B", "heuristic"),
    )
    monkeypatch.setattr(
        "app.services.rag.retrieval.hybrid_search",
        fake_search,
    )
    monkeypatch.setattr(
        "app.services.rag.engine.call_openai",
        lambda _messages: (
            "The ping function returns 7.",
            LLMUsage(tokens_used=8),
        ),
    )
    monkeypatch.setattr(
        "app.services.contract_service.delete_contract_chunks",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.account_service.delete_contract_chunks",
        lambda *_args, **_kwargs: None,
    )

    yield retrieval_generations

    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_eager_propagates
    get_settings.cache_clear()


def _register_and_login() -> str:
    email = f"smoke-{uuid.uuid4().hex}@example.com"
    password = "smoke-test-password-123"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert registered.status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wait_until_ready(token: str, contract_id: int) -> dict:
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    last_status = None
    for poll_number in range(MAX_STATUS_POLLS):
        response = client.get(
            f"/api/v1/contracts/{contract_id}/status",
            headers=_auth(token),
        )
        assert response.status_code == 200, response.text
        last_status = response.json()
        if last_status["status"] == "ready":
            return last_status
        if last_status["status"] == "failed":
            error = last_status.get("last_error") or "no error detail"
            pytest.fail(f"Indexing failed: {error}")
        if time.monotonic() >= deadline or poll_number + 1 == MAX_STATUS_POLLS:
            pytest.fail(
                "Indexing did not finish within the smoke limit: "
                f"{last_status}"
            )
        time.sleep(STATUS_POLL_INTERVAL_SECONDS)
    pytest.fail(
        "Indexing did not finish within the smoke limit: "
        f"{last_status}"
    )


def test_mocked_synthetic_contract_smoke(mocked_dependencies):
    """Exercise the synthetic upload, chat, PDF, and cleanup flow."""
    owner_token = None
    other_token = None
    contract_id = None
    try:
        owner_token = _register_and_login()
        other_token = _register_and_login()

        bad_upload = client.post(
            "/api/v1/contracts/analyze/upload",
            headers=_auth(owner_token),
            files={"file": ("broken.zip", INVALID_ZIP, "application/zip")},
        )
        assert bad_upload.status_code == 400

        upload = client.post(
            "/api/v1/contracts/analyze/upload",
            headers=_auth(owner_token),
            files={"file": ("SmokeDemo.sol", DEMO_SOL, "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        task = upload.json()
        assert task["task_id"]
        contract_id = task["contract_id"]

        status = _wait_until_ready(owner_token, contract_id)
        assert status["risk_score"] == "B"
        assert status["risk_model"] == "heuristic"
        assert status["chunk_count"] == 1

        findings_response = client.get(
            f"/api/v1/contracts/{contract_id}/findings",
            headers=_auth(owner_token),
        )
        assert findings_response.status_code == 200
        findings = findings_response.json()["findings"]
        assert len(findings) == 1
        assert findings[0]["check"] == "smoke-demo-check"
        assert findings[0]["file_path"] == "SmokeDemo.sol"
        assert findings[0]["start_line"] == 3
        assert findings[0]["end_line"] == 5

        denied = client.get(
            f"/api/v1/contracts/{contract_id}/status",
            headers=_auth(other_token),
        )
        assert denied.status_code == 404

        source_response = client.get(
            f"/api/v1/contracts/{contract_id}/source",
            headers=_auth(owner_token),
        )
        assert source_response.status_code == 200
        source_files = {
            item["path"]: item["content"]
            for item in source_response.json()["files"]
        }
        assert (
            source_files["SmokeDemo.sol"].rstrip("\n")
            == DEMO_SOL.decode().rstrip("\n")
        )

        question = (
            "What does ping return? Answer only from the uploaded source."
        )
        chat_response = client.post(
            f"/api/v1/contracts/{contract_id}/chat",
            headers=_auth(owner_token),
            json={"question": question, "stream": False},
        )
        assert chat_response.status_code == 200, chat_response.text
        chat = chat_response.json()
        assert chat["answer"]
        assert chat["citations"]
        for citation in chat["citations"]:
            path = citation["file_path"]
            assert path in source_files
            source_lines = source_files[path].splitlines()
            assert (
                1
                <= citation["start_line"]
                <= citation["end_line"]
                <= len(source_lines)
            )
            start_index = citation["start_line"] - 1
            end_index = citation["end_line"]
            cited_source = "\n".join(
                source_lines[start_index:end_index]
            )
            assert citation["content"].strip() in cited_source
        assert mocked_dependencies and all(mocked_dependencies)

        pdf_response = client.get(
            f"/api/v1/contracts/{contract_id}/report",
            headers=_auth(owner_token),
        )
        assert pdf_response.status_code == 200
        assert pdf_response.headers["content-type"].startswith(
            "application/pdf"
        )
        assert pdf_response.content.startswith(b"%PDF-")
        assert pdf_response.content.rstrip().endswith(b"%%EOF")
    finally:
        if contract_id is not None and owner_token:
            deleted = client.delete(
                f"/api/v1/contracts/{contract_id}",
                headers=_auth(owner_token),
            )
            assert deleted.status_code == 204
            missing = client.get(
                f"/api/v1/contracts/{contract_id}/status",
                headers=_auth(owner_token),
            )
            assert missing.status_code == 404
        for token in (owner_token, other_token):
            if token:
                deleted_account = client.delete(
                    "/api/v1/auth/me",
                    headers=_auth(token),
                )
                assert deleted_account.status_code == 204
                missing_user = client.get(
                    "/api/v1/auth/me",
                    headers=_auth(token),
                )
                assert missing_user.status_code == 401
