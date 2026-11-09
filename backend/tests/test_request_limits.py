"""Tests for request limits that run before FastAPI parses multipart data."""

import asyncio
import json

from app.core.upload_limits import MAX_UPLOAD_REQUEST_BYTES
from app.main import app


def test_chunked_oversized_upload_returns_413_with_cors() -> None:
    boundary = b"LIMITTESTBOUNDARY"
    preamble = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="large.sol"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
    )
    file_bytes_remaining = MAX_UPLOAD_REQUEST_BYTES + 1 - len(preamble)
    pending_preamble = True
    sent: list[dict] = []

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/contracts/analyze/upload",
        "raw_path": b"/api/v1/contracts/analyze/upload",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"origin", b"http://localhost:3001"),
            (b"content-type", b"multipart/form-data; boundary=LIMITTESTBOUNDARY"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8001),
    }

    async def receive() -> dict:
        nonlocal file_bytes_remaining, pending_preamble
        if pending_preamble:
            pending_preamble = False
            return {"type": "http.request", "body": preamble, "more_body": True}
        if file_bytes_remaining:
            chunk_size = min(64 * 1024, file_bytes_remaining)
            file_bytes_remaining -= chunk_size
            return {
                "type": "http.request",
                "body": b"A" * chunk_size,
                "more_body": file_bytes_remaining > 0,
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.lower(): value for key, value in response_start["headers"]
    }

    assert response_start["status"] == 413
    assert response_headers[b"access-control-allow-origin"] == b"http://localhost:3001"
    assert json.loads(response_body)["detail"].startswith("Upload request exceeds")
