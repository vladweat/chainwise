import importlib.util
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _load_analyzer_module():
    app_path = Path(__file__).resolve().parents[2] / "slither" / "app.py"
    spec = importlib.util.spec_from_file_location("chainwise_analyzer_app", app_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def analyzer(monkeypatch):
    module = _load_analyzer_module()
    monkeypatch.setattr(module, "SOLC_DIR", Path("/nonexistent-solc-dir"))
    return module, TestClient(module.app)


def _install_fake_solc(directory: Path, version: str, behavior: str = "valid") -> Path:
    binary = directory / f"solc-v{version}"
    script = textwrap.dedent(
        """\
        #!__PYTHON__
        import json
        import os
        import sys
        import time

        request = json.load(sys.stdin)
        behavior = __BEHAVIOR__
        if behavior == "sleep":
            time.sleep(2)
        elif behavior == "orphan-child":
            child = os.fork()
            if child == 0:
                time.sleep(2)
                os._exit(0)
        elif behavior == "large-output":
            sys.stdout.write("x" * 100000)
        elif behavior == "large-stderr":
            sys.stderr.write("x" * 100000)
            json.dump(
                {"sources": {"Demo.sol": {"id": 0, "ast": {"nodeType": "SourceUnit", "nodes": []}}}, "errors": []},
                sys.stdout,
            )
        elif behavior == "large-json":
            json.dump(
                {"sources": {"Demo.sol": {"id": 0, "ast": {"description": "x" * 10000}}}, "errors": []},
                sys.stdout,
            )
        elif behavior == "invalid-json":
            sys.stdout.write("not-json")
        elif behavior == "syntax-error":
            json.dump({
                "sources": {},
                "errors": [{
                    "severity": "error",
                    "type": "ParserError",
                    "errorCode": "2314",
                    "formattedMessage": "ParserError: secret source text",
                }],
            }, sys.stdout)
        else:
            json.dump({
                "sources": {
                    name: {"id": index, "ast": {"nodeType": "SourceUnit", "nodes": []}}
                    for index, name in enumerate(request["sources"])
                },
                "errors": [],
            }, sys.stdout)
        """
    ).replace("__PYTHON__", sys.executable).replace("__BEHAVIOR__", repr(behavior))
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _compile(client, *, version="0.8.24", files=None):
    return client.post(
        "/compile",
        json={
            "files": files or {"Demo.sol": "pragma solidity ^0.8.24; contract Demo {}"},
            "solc_version": version,
        },
    )


def test_compile_returns_standard_compile_result(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)

    response = _compile(client)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "solc_version": "0.8.24",
        "sources": {
            "Demo.sol": {
                "id": 0,
                "ast": {"nodeType": "SourceUnit", "nodes": []},
            }
        },
    }


def test_compile_reports_syntax_error_without_echoing_source(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="syntax-error")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)

    response = _compile(client)

    assert response.status_code == 422, response.text
    assert "ParserError" in response.json()["detail"]
    assert "2314" in response.json()["detail"]
    assert "secret source text" not in response.text


def test_compile_reports_missing_preinstalled_compiler(analyzer):
    _module, client = analyzer

    response = _compile(client)

    assert response.status_code == 503
    assert "preinstalled" in response.json()["detail"]


def test_compile_enforces_subprocess_deadline(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="sleep")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)
    monkeypatch.setattr(module, "SOLC_TIMEOUT_SECONDS", 0.05, raising=False)

    response = _compile(client)

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


def test_compile_deadline_includes_orphaned_child_process(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="orphan-child")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)
    monkeypatch.setattr(module, "SOLC_TIMEOUT_SECONDS", 0.05, raising=False)

    started = time.monotonic()
    response = _compile(client)

    assert response.status_code == 504
    assert time.monotonic() - started < 1


def test_compile_bounds_subprocess_output(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="large-output")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)
    monkeypatch.setattr(module, "MAX_SOLC_STDOUT_BYTES", 1024, raising=False)

    response = _compile(client)

    assert response.status_code == 502
    assert "output limit" in response.json()["detail"]


def test_compile_bounds_subprocess_stderr(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="large-stderr")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)
    monkeypatch.setattr(module, "MAX_SOLC_STDERR_BYTES", 1024, raising=False)

    response = _compile(client)

    assert response.status_code == 502
    assert "output limit" in response.json()["detail"]


def test_compile_bounds_json_response(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="large-json")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)
    monkeypatch.setattr(module, "MAX_RESPONSE_JSON_BYTES", 128, raising=False)

    response = _compile(client)

    assert response.status_code == 502
    assert "JSON response exceeds" in response.json()["detail"]


def test_compile_rejects_malformed_standard_json(analyzer, tmp_path, monkeypatch):
    module, client = analyzer
    _install_fake_solc(tmp_path, "0.8.24", behavior="invalid-json")
    monkeypatch.setattr(module, "SOLC_DIR", tmp_path)

    response = _compile(client)

    assert response.status_code == 502
    assert "invalid standard JSON" in response.json()["detail"]


def test_compile_rejects_malformed_request(analyzer):
    _module, client = analyzer

    response = client.post(
        "/compile",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    ["../outside.sol", "/tmp/outside.sol", "C:\\outside.sol", "\\\\server\\share\\x.sol"],
)
def test_compile_rejects_unsafe_source_paths(analyzer, path):
    _module, client = analyzer

    response = _compile(client, files={path: "contract Demo {}"})

    assert response.status_code == 400
    assert "unsafe source path" in response.json()["detail"].lower()


def test_compile_rejects_oversized_request_body(analyzer, monkeypatch):
    module, client = analyzer
    monkeypatch.setattr(module, "MAX_REQUEST_BYTES", 128, raising=False)

    response = client.post("/compile", content=b" " * 129)

    assert response.status_code == 413


def test_compile_rejects_too_many_files(analyzer):
    _module, client = analyzer
    files = {f"File{index}.sol": "contract Demo {}" for index in range(101)}

    response = _compile(client, files=files)

    assert response.status_code == 413


def test_analyzer_rejects_requests_over_concurrency_limit(analyzer):
    module, client = analyzer
    acquired = 0
    try:
        for _ in range(module.MAX_CONCURRENT_ANALYZER_REQUESTS):
            assert module._ANALYZER_REQUEST_SLOTS.acquire(blocking=False)
            acquired += 1
        response = _compile(client)
    finally:
        for _ in range(acquired):
            module._ANALYZER_REQUEST_SLOTS.release()

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
