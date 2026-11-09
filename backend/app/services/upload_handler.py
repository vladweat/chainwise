import hashlib
import io
import stat
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

from app.core.upload_limits import MAX_UPLOAD_BYTES
from app.utils.pragma_parser import parse_pragma_solidity

MAX_ZIP_ENTRY_BYTES = 10 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 100
UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class UploadError(Exception):
    pass


class UploadTooLargeError(UploadError):
    """Raised when an uploaded file exceeds the compressed request limit."""


class AsyncReadableUpload(Protocol):
    async def read(self, size: int = -1) -> bytes:
        ...


@dataclass
class UploadedContract:
    address: str
    chain_id: int
    source_code: str
    name: str | None
    compiler_version: str | None
    is_verified: bool


def synthetic_address(source_code: str, chain_id: int) -> str:
    digest = hashlib.sha256(f"{chain_id}:{source_code}".encode()).hexdigest()
    return f"0x{digest[:40]}"


async def read_upload_file(upload_file: AsyncReadableUpload) -> bytes:
    """Read an UploadFile in bounded chunks and stop at the first excess byte."""
    content = bytearray()
    while True:
        remaining = MAX_UPLOAD_BYTES - len(content)
        chunk = await upload_file.read(min(UPLOAD_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise UploadTooLargeError("File exceeds 10 MiB limit")


def _read_sol_file(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("Invalid .sol file encoding (expected UTF-8)") from exc


def _normalized_zip_path(info: zipfile.ZipInfo) -> str:
    raw_name = info.orig_filename
    if not raw_name or any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_name):
        raise UploadError("ZIP archive contains an unsafe ZIP path")

    name = unicodedata.normalize("NFC", raw_name)
    windows_path = PureWindowsPath(name)
    if windows_path.drive or windows_path.is_absolute():
        raise UploadError("ZIP archive contains an unsafe ZIP path")

    name = name.replace("\\", "/")
    if name.startswith("/"):
        raise UploadError("ZIP archive contains an unsafe ZIP path")

    components = name.split("/")
    if ".." in components:
        raise UploadError("ZIP archive contains an unsafe ZIP path")
    normalized = "/".join(part for part in components if part not in {"", "."})
    if not normalized:
        raise UploadError("ZIP archive contains an unsafe ZIP path")
    return normalized


def _validate_zip_info(info: zipfile.ZipInfo) -> str:
    normalized_name = _normalized_zip_path(info)
    if info.flag_bits & 0x2041:
        raise UploadError("ZIP archive contains encrypted ZIP entries")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise UploadError("ZIP archive contains unsupported ZIP compression")

    file_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(file_mode)
    is_directory = (
        info.is_dir()
        or info.orig_filename.endswith(("/", "\\"))
        or file_type == stat.S_IFDIR
    )
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise UploadError("ZIP archive contains an unsupported entry type")
    if is_directory and file_type not in {0, stat.S_IFDIR}:
        raise UploadError("ZIP archive contains an unsupported entry type")
    if not is_directory and file_type == stat.S_IFDIR:
        raise UploadError("ZIP archive contains an unsupported entry type")
    return normalized_name


def _zip_info_is_dir(info: zipfile.ZipInfo) -> bool:
    file_mode = (info.external_attr >> 16) & 0xFFFF
    return (
        info.is_dir()
        or info.orig_filename.endswith(("/", "\\"))
        or stat.S_IFMT(file_mode) == stat.S_IFDIR
    )


def _read_zip_source(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    total_read: int,
) -> bytes:
    entry_content = bytearray()
    with archive.open(info, "r") as source_file:
        while True:
            entry_remaining = MAX_ZIP_ENTRY_BYTES - len(entry_content)
            total_remaining = MAX_ZIP_TOTAL_BYTES - total_read - len(entry_content)
            read_size = min(
                UPLOAD_READ_CHUNK_BYTES,
                entry_remaining + 1,
                total_remaining + 1,
            )
            chunk = source_file.read(read_size)
            if not chunk:
                return bytes(entry_content)

            entry_content.extend(chunk)
            if len(entry_content) > MAX_ZIP_ENTRY_BYTES:
                raise UploadError("ZIP source entry exceeds 10 MiB limit")
            if total_read + len(entry_content) > MAX_ZIP_TOTAL_BYTES:
                raise UploadError("ZIP aggregate source limit of 25 MiB exceeded")


def _extract_sol_from_zip(content: bytes) -> tuple[str, str | None]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise UploadError("ZIP archive contains too many entries (maximum 100)")

            seen_names: set[str] = set()
            sol_files: list[tuple[str, zipfile.ZipInfo]] = []
            declared_total = 0
            for info in infos:
                normalized_name = _validate_zip_info(info)
                duplicate_key = normalized_name.casefold()
                if duplicate_key in seen_names:
                    raise UploadError("ZIP archive contains a duplicate ZIP path")
                seen_names.add(duplicate_key)

                if _zip_info_is_dir(info) or normalized_name.startswith("__MACOSX/"):
                    continue
                if normalized_name.lower().endswith(".sol"):
                    if info.file_size > MAX_ZIP_ENTRY_BYTES:
                        raise UploadError("ZIP source entry exceeds 10 MiB limit")
                    declared_total += info.file_size
                    if declared_total > MAX_ZIP_TOTAL_BYTES:
                        raise UploadError("ZIP aggregate source limit of 25 MiB exceeded")
                    sol_files.append((normalized_name, info))

            if not sol_files:
                raise UploadError("ZIP archive contains no .sol files")

            parts: list[str] = []
            primary_name: str | None = None
            total_read = 0
            for file_name, info in sorted(sol_files, key=lambda entry: entry[0]):
                file_content = _read_zip_source(archive, info, total_read=total_read)
                total_read += len(file_content)
                source = _read_sol_file(file_content)
                parts.append(f"// File: {file_name}\n{source}")
                if primary_name is None:
                    primary_name = file_name.rsplit("/", 1)[-1]

            return "\n\n".join(parts), primary_name
    except UploadError:
        raise
    except UnicodeDecodeError as exc:
        raise UploadError("ZIP filenames must be valid UTF-8") from exc
    except (
        zipfile.BadZipFile,
        EOFError,
        NotImplementedError,
        RuntimeError,
        OSError,
        struct.error,
        ValueError,
        zlib.error,
    ) as exc:
        raise UploadError("Invalid ZIP archive") from exc


def parse_upload(filename: str, content: bytes, chain_id: int = 0) -> UploadedContract:
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError("File exceeds 10 MiB limit")

    lower_name = filename.lower()
    if lower_name.endswith(".sol"):
        source_code = _read_sol_file(content)
        contract_name = filename.rsplit("/", 1)[-1].removesuffix(".sol")
    elif lower_name.endswith(".zip"):
        source_code, contract_name = _extract_sol_from_zip(content)
    else:
        raise UploadError("Unsupported file type. Use .sol or .zip")

    if not source_code.strip():
        raise UploadError("Source code is empty")

    compiler_version = parse_pragma_solidity(source_code)
    address = synthetic_address(source_code, chain_id)

    return UploadedContract(
        address=address,
        chain_id=chain_id,
        source_code=source_code,
        name=contract_name,
        compiler_version=compiler_version,
        is_verified=False,
    )
