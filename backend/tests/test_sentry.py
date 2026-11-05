from app.core.sentry import before_send, capture_pipeline_error, init_sentry


def test_before_send_strips_request_body_and_source():
    event = {
        "request": {
            "data": {"source_code": "pragma solidity ^0.8.24; contract X {}"},
            "headers": {"Authorization": "Bearer secret", "content-type": "application/json"},
        },
        "extra": {"note": "pragma solidity ^0.8.24; contract Leak {}"},
        "exception": {
            "values": [
                {"value": "pragma solidity ^0.8.24; contract Leak {}"},
            ]
        },
    }
    cleaned = before_send(event, None)
    assert "data" not in cleaned["request"]
    assert cleaned["request"]["headers"]["Authorization"] == "[redacted]"
    assert cleaned["extra"]["note"] == "[redacted]"
    assert cleaned["exception"]["values"][0]["value"] == "[redacted]"


def test_init_sentry_noop_without_dsn(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SENTRY_DSN", "")
    get_settings.cache_clear()
    init_sentry()
    capture_pipeline_error("llm", RuntimeError("boom"))
    get_settings.cache_clear()
