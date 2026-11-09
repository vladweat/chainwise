import asyncio
import io
import zipfile

import pytest

from app.services import upload_handler
from app.services.upload_handler import UploadError, parse_upload, synthetic_address
from app.utils.address import is_valid_address, is_supported_chain, normalize_address


SAMPLE_SOL = b'pragma solidity ^0.8.24;\ncontract Token { function mint() public {} }'
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 10 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 25 * 1024 * 1024


class AsyncUpload:
    def __init__(self, content: bytes):
        self.content = content
        self.offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        assert size > 0
        self.read_sizes.append(size)
        chunk = self.content[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


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


def test_parse_multiple_sol_files_preserves_normalized_file_markers():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("contracts/Token.sol", SAMPLE_SOL)
        archive.writestr("contracts/Ownable.sol", b"pragma solidity ^0.8.24;\ncontract Ownable {}")

    result = parse_upload("bundle.zip", buffer.getvalue())

    assert "// File: contracts/Ownable.sol\npragma solidity ^0.8.24;\ncontract Ownable {}" in result.source_code
    assert "// File: contracts/Token.sol\n" in result.source_code
    assert result.name == "Ownable.sol"


def test_parse_sol_at_compressed_request_limit():
    prefix = b"pragma solidity ^0.8.24;\n"
    source = prefix + b" " * (MAX_UPLOAD_BYTES - len(prefix))

    result = parse_upload("Large.sol", source)

    assert result.source_code == source.decode("utf-8")


def test_parse_sol_over_compressed_request_limit_has_too_large_error():
    prefix = b"pragma solidity ^0.8.24;\n"
    source = prefix + b" " * (MAX_UPLOAD_BYTES - len(prefix) + 1)

    with pytest.raises(upload_handler.UploadTooLargeError, match="10 MiB limit"):
        parse_upload("Large.sol", source)


def test_upload_reader_reads_only_through_first_byte_over_limit():
    source = b"x" * (MAX_UPLOAD_BYTES + 1024 * 1024)
    upload = AsyncUpload(source)

    with pytest.raises(upload_handler.UploadTooLargeError, match="10 MiB limit"):
        asyncio.run(upload_handler.read_upload_file(upload))

    assert upload.offset == MAX_UPLOAD_BYTES + 1
    assert max(upload.read_sizes) <= 64 * 1024


def test_upload_reader_accepts_exact_limit():
    source = b"x" * MAX_UPLOAD_BYTES
    upload = AsyncUpload(source)

    assert asyncio.run(upload_handler.read_upload_file(upload)) == source


def test_zip_entry_expansion_limit_rejects_small_compressed_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Huge.sol", b"A" * (MAX_ZIP_ENTRY_BYTES + 1))

    assert len(buffer.getvalue()) < 100_000
    with pytest.raises(UploadError, match="entry exceeds"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_aggregate_expansion_limit_rejects_small_compressed_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(3):
            archive.writestr(
                f"Part{index}.sol",
                b"A" * (MAX_ZIP_TOTAL_BYTES // 3 + 1),
            )

    assert len(buffer.getvalue()) < 100_000
    with pytest.raises(UploadError, match="aggregate source limit"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_entry_count_limit_counts_non_solidity_entries():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(101):
            archive.writestr(f"files/{index}.txt", b"data")
        archive.writestr("Token.sol", SAMPLE_SOL)

    with pytest.raises(UploadError, match="too many entries"):
        parse_upload("bundle.zip", buffer.getvalue())


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../Token.sol",
        "/tmp/Token.sol",
        "C:\\contracts\\Token.sol",
        "\\\\server\\share\\Token.sol",
        "contracts/\n// File: ../Injected.sol",
    ],
)
def test_zip_rejects_unsafe_source_paths(unsafe_name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(unsafe_name, SAMPLE_SOL)

    with pytest.raises(UploadError, match="unsafe ZIP path"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_rejects_duplicate_paths_after_normalization():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("contracts/Token.sol", SAMPLE_SOL)
        archive.writestr("contracts/./Token.sol", SAMPLE_SOL)

    with pytest.raises(UploadError, match="duplicate ZIP path"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_rejects_case_insensitive_duplicate_paths():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("contracts/Token.sol", SAMPLE_SOL)
        archive.writestr("contracts/token.sol", SAMPLE_SOL)

    with pytest.raises(UploadError, match="duplicate ZIP path"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_rejects_invalid_utf8_source():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Token.sol", b"contract \xff {}")

    with pytest.raises(UploadError, match="expected UTF-8"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_rejects_unsupported_compression_method():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("Token.sol")
        info.compress_type = zipfile.ZIP_BZIP2
        archive.writestr(info, SAMPLE_SOL)

    with pytest.raises(UploadError, match="unsupported ZIP compression"):
        parse_upload("bundle.zip", buffer.getvalue())


def test_zip_rejects_encrypted_member_flag():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Token.sol", SAMPLE_SOL)
    data = bytearray(buffer.getvalue())
    local_header = data.index(b"PK\x03\x04")
    central_header = data.index(b"PK\x01\x02")
    data[local_header + 6] |= 1
    data[central_header + 8] |= 1

    with pytest.raises(UploadError, match="encrypted ZIP entries"):
        parse_upload("bundle.zip", bytes(data))


def test_zip_rejects_corrupt_archive():
    with pytest.raises(UploadError, match="Invalid ZIP archive"):
        parse_upload("bundle.zip", b"PK\x03\x04truncated")


def test_zip_rejects_underreported_decompressed_size():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Token.sol", b"A" * (MAX_ZIP_ENTRY_BYTES + 1))
    data = bytearray(buffer.getvalue())
    central_header = data.index(b"PK\x01\x02")
    data[central_header + 24:central_header + 28] = (1).to_bytes(4, "little")

    with pytest.raises(UploadError, match="Invalid ZIP archive"):
        parse_upload("bundle.zip", bytes(data))


def test_zip_rejects_overreported_decompressed_size_before_reading_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Token.sol", SAMPLE_SOL)
    data = bytearray(buffer.getvalue())
    central_header = data.index(b"PK\x01\x02")
    data[central_header + 24:central_header + 28] = (0xFFFFFFFF).to_bytes(4, "little")

    with pytest.raises(UploadError, match="entry exceeds 10 MiB limit"):
        parse_upload("bundle.zip", bytes(data))


def test_zip_rejects_invalid_utf8_filename():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Token.sol", SAMPLE_SOL)
    data = bytearray(buffer.getvalue())
    local_header = data.index(b"PK\x03\x04")
    central_header = data.index(b"PK\x01\x02")
    local_name = local_header + 30
    central_name = central_header + 46
    data[local_name] = 0xFF
    data[central_name] = 0xFF
    data[local_header + 7] |= 0x08
    data[central_header + 9] |= 0x08

    with pytest.raises(UploadError, match="ZIP filenames must be valid UTF-8"):
        parse_upload("bundle.zip", bytes(data))


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
