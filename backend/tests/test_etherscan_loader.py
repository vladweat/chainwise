import json

import pytest

from app.services.etherscan_loader import (
    EtherscanContractNotFoundError,
    EtherscanNotConfiguredError,
    _normalize_compiler_version,
    _unwrap_source_code,
    fetch_contract_source,
)


def test_normalize_compiler_version():
    assert _normalize_compiler_version("v0.8.24+commit.abc") == "0.8.24"
    assert _normalize_compiler_version("") is None


def test_unwrap_plain_source():
    source = "pragma solidity ^0.8.0;\ncontract A {}"
    assert _unwrap_source_code(source) == source


def test_unwrap_json_metadata():
    metadata = {
        "language": "Solidity",
        "sources": {
            "Token.sol": {"content": "pragma solidity ^0.8.24;\ncontract Token {}"},
            "Lib.sol": {"content": "library L {}"},
        },
    }
    wrapped = "{{" + json.dumps(metadata) + "}}"
    result = _unwrap_source_code(wrapped)
    assert "// File: Lib.sol" in result
    assert "// File: Token.sol" in result
    assert "contract Token" in result


def test_fetch_not_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ETHERSCAN_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(EtherscanNotConfiguredError):
        fetch_contract_source("0x1234567890123456789012345678901234567890", 1)

    get_settings.cache_clear()


def test_fetch_contract_success(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    def fake_get(url, params=None, timeout=None):
        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "status": "1",
                    "message": "OK",
                    "result": [
                        {
                            "SourceCode": "pragma solidity ^0.8.24;\ncontract Demo {}",
                            "ContractName": "Demo",
                            "CompilerVersion": "v0.8.24+commit.abc",
                            "ABI": "[{}]",
                        }
                    ],
                }

        return Response()

    monkeypatch.setattr("app.services.etherscan_loader.httpx.get", fake_get)

    result = fetch_contract_source("0x1234567890123456789012345678901234567890", 56)
    assert result.chain_id == 56
    assert result.name == "Demo"
    assert result.compiler_version == "0.8.24"
    assert result.is_verified is True
    assert "contract Demo" in result.source_code

    get_settings.cache_clear()


def test_fetch_contract_not_found(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    get_settings.cache_clear()

    def fake_get(url, params=None, timeout=None):
        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "0", "message": "NOTOK", "result": []}

        return Response()

    monkeypatch.setattr("app.services.etherscan_loader.httpx.get", fake_get)

    with pytest.raises(EtherscanContractNotFoundError):
        fetch_contract_source("0x1234567890123456789012345678901234567890", 11124)

    get_settings.cache_clear()
