import json as jsonlib

import httpx
import pytest

from app.services.slither_runner import SlitherError, is_slither_available, run_slither

SAMPLE_REPORT = {
    "success": True,
    "error": None,
    "results": {
        "detectors": [
            {
                "check": "reentrancy-eth",
                "impact": "High",
                "confidence": "Medium",
                "description": "Reentrancy in withdraw()",
            }
        ]
    },
}


def test_run_slither_posts_sources(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            return SAMPLE_REPORT

        @property
        def text(self):
            return ""

    def fake_post(url, content=None, timeout=None, **_kwargs):
        captured["url"] = url
        captured["json"] = jsonlib.loads(content)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.slither_runner.httpx.post", fake_post)

    report = run_slither(
        "pragma solidity ^0.8.24;\ncontract Demo {}",
        compiler_version="0.8.24",
        filename="Demo.sol",
    )
    assert report["success"] is True
    assert captured["url"].endswith("/analyze")
    assert captured["json"]["solc_version"] == "0.8.24"
    assert "Demo.sol" in captured["json"]["files"]
    assert report["results"]["detectors"][0]["check"] == "reentrancy-eth"


def test_run_slither_network_error(monkeypatch):
    def fake_post(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.slither_runner.httpx.post", fake_post)

    with pytest.raises(SlitherError, match="Cannot reach Slither"):
        run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")


def test_run_slither_http_error(monkeypatch):
    class FakeResponse:
        status_code = 500
        text = "solc binary not found"

        def json(self):
            return {}

    monkeypatch.setattr(
        "app.services.slither_runner.httpx.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    with pytest.raises(SlitherError, match="Slither failed"):
        run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")


def test_run_slither_retries_analyzer_capacity_with_retry_after(monkeypatch):
    responses = []
    sleeps = []

    class FakeResponse:
        content = b"{}"

        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

        def json(self):
            return SAMPLE_REPORT

    def fake_post(*_args, **_kwargs):
        response = FakeResponse(503, {"Retry-After": "5"}) if not responses else FakeResponse(200)
        responses.append(response)
        return response

    monkeypatch.setattr("app.services.slither_runner.httpx.post", fake_post)
    monkeypatch.setattr("app.services.analyzer_retry.time.sleep", sleeps.append)

    report = run_slither(
        "pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24"
    )

    assert report["success"] is True
    assert len(responses) == 2
    assert sleeps == [5.0]


def test_run_slither_does_not_retry_non_capacity_503(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 503
        content = b"{}"
        headers = {}

        def json(self):
            return {}

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse()

    monkeypatch.setattr("app.services.slither_runner.httpx.post", fake_post)
    monkeypatch.setattr("app.services.analyzer_retry.time.sleep", lambda *_args: None)

    with pytest.raises(SlitherError, match="HTTP 503"):
        run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")

    assert len(calls) == 1


def test_run_slither_stops_after_bounded_capacity_retries(monkeypatch):
    calls = []
    sleeps = []

    class FakeResponse:
        status_code = 503
        content = b"{}"
        headers = {"Retry-After": "5"}

        def json(self):
            return {}

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse()

    monkeypatch.setattr("app.services.slither_runner.httpx.post", fake_post)
    monkeypatch.setattr("app.services.analyzer_retry.time.sleep", sleeps.append)

    with pytest.raises(SlitherError, match="temporarily at capacity"):
        run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")

    from app.services.analyzer_retry import MAX_ANALYZER_BUSY_RETRIES

    assert len(calls) == MAX_ANALYZER_BUSY_RETRIES + 1
    assert len(sleeps) == MAX_ANALYZER_BUSY_RETRIES


def test_run_slither_analysis_failed(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = ""

        def json(self):
            return {"success": False, "error": "Compiler error"}

    monkeypatch.setattr(
        "app.services.slither_runner.httpx.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    with pytest.raises(SlitherError, match="Slither analysis failed"):
        run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")


def test_run_slither_normalizes_empty_results(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = ""

        def json(self):
            return {"success": True, "error": None, "results": {}}

    monkeypatch.setattr(
        "app.services.slither_runner.httpx.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    report = run_slither("pragma solidity ^0.8.24;\ncontract Demo {}", compiler_version="0.8.24")
    assert report["results"]["detectors"] == []


@pytest.mark.skipif(not is_slither_available(), reason="Slither Docker service is not running")
def test_slither_service_returns_detectors_json():
    source = """pragma solidity ^0.8.24;

contract Demo {
    function ping() external pure returns (uint256) {
        return 1;
    }
}
"""
    report = run_slither(source, compiler_version="0.8.24", filename="Demo.sol")
    assert isinstance(report, dict)
    assert "results" in report
    assert "detectors" in report["results"]
    assert isinstance(report["results"]["detectors"], list)
