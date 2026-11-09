"""Opt-in live pipeline check; external LLM generation is mocked by default."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("CHAINWISE_LIVE_E2E") != "1",
    reason="set CHAINWISE_LIVE_E2E=1 to run against real local integrations",
)

DEMO_SOL = b"""// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract LiveDemo {
    uint256 private storedValue;
    address private owner;

    constructor() {
        owner = msg.sender;
    }

    function setValue(uint256 value) external {
        storedValue = value;
    }

    function getValue() external view returns (uint256) {
        return storedValue;
    }
}
"""
INVALID_ZIP = b"not a zip archive"
POLL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 1
LIVE_ANALYZER_TIMEOUT_SECONDS = 20
LIVE_QDRANT_TIMEOUT_SECONDS = 10


def _register(client: TestClient) -> str:
    email = f"live-e2e-{uuid.uuid4().hex}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "live-e2e-only-password-45"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _wait_ready(client: TestClient, token: str, contract_id: int) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/contracts/{contract_id}/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        status = response.json()
        if status["status"] == "ready":
            return status
        if status["status"] == "failed":
            pytest.fail(f"Live indexing failed: {status.get('last_error')}")
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail("Live indexing exceeded the 120-second smoke limit")


def test_live_solidity_to_rag_and_pdf(monkeypatch, record_property):
    from app.celery_config import celery_app
    from app.services.rag.types import LLMUsage
    from app.services.slither_runner import is_slither_available
    from app.services.solc_compiler import is_solc_available
    from app.services.qdrant_indexer import is_embedder_available, is_qdrant_available
    from app.services import analyzer_retry, qdrant_indexer, slither_runner, solc_compiler
    from qdrant_client import QdrantClient

    assert is_solc_available(), "allowlisted solc 0.8.24 is unavailable in analyzer"
    assert is_slither_available(), "analyzer sidecar is unavailable"
    assert is_embedder_available(), "prepare the local FastEmbed MiniLM model first"
    assert is_qdrant_available(), "Qdrant is unavailable"

    from app.services.rag import engine as rag_engine

    settings = get_settings()
    live_qdrant = QdrantClient(
        url=settings.qdrant_url,
        timeout=LIVE_QDRANT_TIMEOUT_SECONDS,
        trust_env=False,
    )
    monkeypatch.setattr(qdrant_indexer, "_get_client", lambda: live_qdrant)
    monkeypatch.setattr(
        solc_compiler, "ANALYZER_TIMEOUT_SECONDS", LIVE_ANALYZER_TIMEOUT_SECONDS
    )
    original_http_post = slither_runner.httpx.post

    def bounded_analyzer_post(url, *args, **kwargs):
        if str(url).endswith("/analyze"):
            kwargs["timeout"] = LIVE_ANALYZER_TIMEOUT_SECONDS
        return original_http_post(url, *args, **kwargs)

    monkeypatch.setattr(slither_runner.httpx, "post", bounded_analyzer_post)
    monkeypatch.setattr(analyzer_retry, "MAX_ANALYZER_BUSY_RETRIES", 1)
    monkeypatch.setattr(analyzer_retry, "MAX_ANALYZER_BUSY_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(analyzer_retry, "MAX_ANALYZER_RETRY_AFTER_SECONDS", 2.0)

    def bounded_retry_wait(_delay):
        time.sleep(0.1)

    monkeypatch.setattr(solc_compiler, "wait_for_analyzer_retry", bounded_retry_wait)
    monkeypatch.setattr(slither_runner, "wait_for_analyzer_retry", bounded_retry_wait)

    use_real_llm = bool(settings.openai_api_key) and os.environ.get(
        "CHAINWISE_LIVE_E2E_MOCK_LLM"
    ) != "1"
    if not use_real_llm:
        monkeypatch.setattr(
            rag_engine,
            "call_openai",
            lambda _messages: (
                "getValue returns the value stored by setValue.",
                LLMUsage(tokens_used=8),
            ),
        )
    record_property("llm_mode", "live" if use_real_llm else "mocked")

    original_eager = celery_app.conf.task_always_eager
    original_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    owner_token = None
    other_token = None
    contract_id = None
    try:
        with TestClient(app) as client:
            try:
                owner_token = _register(client)
                other_token = _register(client)
                auth = {"Authorization": f"Bearer {owner_token}"}

                invalid = client.post(
                    "/api/v1/contracts/analyze/upload",
                    headers=auth,
                    files={"file": ("invalid.zip", INVALID_ZIP, "application/zip")},
                )
                assert invalid.status_code == 400

                uploaded = client.post(
                    "/api/v1/contracts/analyze/upload",
                    headers=auth,
                    files={"file": ("LiveDemo.sol", DEMO_SOL, "text/plain")},
                )
                assert uploaded.status_code == 202, uploaded.text
                contract_id = uploaded.json()["contract_id"]
                ready = _wait_ready(client, owner_token, contract_id)
                assert ready["chunk_count"] > 0
                assert ready["risk_score"] in {"A", "B", "C", "D"}
                assert ready["risk_model"] in {"xgboost", "heuristic"}
                record_property("risk_model", ready["risk_model"])

                findings = client.get(
                    f"/api/v1/contracts/{contract_id}/findings",
                    headers=auth,
                )
                assert findings.status_code == 200, findings.text
                finding_rows = findings.json()["findings"]
                assert any(item["check"] == "immutable-states" for item in finding_rows)

                denied = client.get(
                    f"/api/v1/contracts/{contract_id}/status",
                    headers={"Authorization": f"Bearer {other_token}"},
                )
                assert denied.status_code == 404

                chat = client.post(
                    f"/api/v1/contracts/{contract_id}/chat",
                    headers=auth,
                    json={"question": "What does getValue return?", "stream": False},
                )
                assert chat.status_code == 200, chat.text
                body = chat.json()
                assert body["answer"].strip()
                assert body["citations"]
                source = client.get(
                    f"/api/v1/contracts/{contract_id}/source",
                    headers=auth,
                )
                assert source.status_code == 200, source.text
                source_files = {
                    item["path"]: item["content"].splitlines()
                    for item in source.json()["files"]
                }
                for citation in body["citations"]:
                    lines = source_files[citation["file_path"]]
                    assert 1 <= citation["start_line"] <= citation["end_line"] <= len(lines)

                pdf = client.get(
                    f"/api/v1/contracts/{contract_id}/report",
                    headers=auth,
                )
                assert pdf.status_code == 200
                assert pdf.headers["content-type"].startswith("application/pdf")
                assert pdf.content.startswith(b"%PDF-")
                assert pdf.content.rstrip().endswith(b"%%EOF")
            finally:
                if contract_id is not None and owner_token is not None:
                    deleted = client.delete(
                        f"/api/v1/contracts/{contract_id}",
                        headers={"Authorization": f"Bearer {owner_token}"},
                    )
                    assert deleted.status_code in {204, 404}, deleted.text
                    missing = client.get(
                        f"/api/v1/contracts/{contract_id}/status",
                        headers={"Authorization": f"Bearer {owner_token}"},
                    )
                    assert missing.status_code == 404, missing.text
                    contract_id = None
                for token in (owner_token, other_token):
                    if token is not None:
                        deleted_account = client.delete(
                            "/api/v1/auth/me",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        assert deleted_account.status_code in {204, 401}, deleted_account.text
                        missing_account = client.get(
                            "/api/v1/auth/me",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        assert missing_account.status_code == 401, missing_account.text
    finally:
        live_qdrant.close()
        celery_app.conf.task_always_eager = original_eager
        celery_app.conf.task_eager_propagates = original_propagate
