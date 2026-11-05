import os

from app.core.proxy import apply_internal_no_proxy


def test_apply_internal_no_proxy_keeps_qdrant(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,130.100.7.0/24")
    monkeypatch.setenv("NO_PROXY", "localhost")
    apply_internal_no_proxy()
    assert "qdrant" in os.environ["no_proxy"].split(",")
    assert "slither" in os.environ["NO_PROXY"].split(",")
    assert "130.100.7.0/24" in os.environ["no_proxy"]
