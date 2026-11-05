import io
import zipfile

import pytest

from app.services.upload_handler import UploadError, parse_upload, synthetic_address
from app.utils.address import is_valid_address, is_supported_chain, normalize_address


SAMPLE_SOL = b'pragma solidity ^0.8.24;\ncontract Token { function mint() public {} }'


def test_parse_sol_upload():
    result = parse_upload("Token.sol", SAMPLE_SOL, chain_id=0)
    assert result.compiler_version == "0.8.24"
    assert result.name == "Token"
    assert result.is_verified is False
    assert is_valid_address(result.address)
    assert result.chain_id == 0


def test_parse_zip_upload():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("contracts/Token.sol", SAMPLE_SOL)
    result = parse_upload("bundle.zip", buffer.getvalue(), chain_id=1)
    assert "Token.sol" in result.source_code
    assert result.compiler_version == "0.8.24"
    assert result.chain_id == 1


def test_synthetic_address_is_deterministic():
    a = synthetic_address("source", 0)
    b = synthetic_address("source", 0)
    c = synthetic_address("source", 1)
    assert a == b
    assert a != c


def test_unsupported_extension():
    with pytest.raises(UploadError, match="Unsupported file type"):
        parse_upload("readme.txt", b"hello", chain_id=0)


def test_empty_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    with pytest.raises(UploadError, match="no .sol files"):
        parse_upload("empty.zip", buffer.getvalue())


def test_address_utils():
    addr = "0xAbCdEf1234567890123456789012345678901234"
    assert normalize_address(addr) == addr.lower()
    assert is_valid_address(addr)
    assert is_supported_chain(1)
    assert not is_supported_chain(999)
