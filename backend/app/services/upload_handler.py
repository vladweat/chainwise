import hashlib
import io
import zipfile
from dataclasses import dataclass

from app.utils.pragma_parser import parse_pragma_solidity

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UploadError(Exception):
    pass


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


def _read_sol_file(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("Invalid .sol file encoding (expected UTF-8)") from exc


def _extract_sol_from_zip(content: bytes) -> tuple[str, str | None]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sol_files = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".sol") and not name.startswith("__MACOSX/")
            ]
            if not sol_files:
                raise UploadError("ZIP archive contains no .sol files")

            parts: list[str] = []
            primary_name: str | None = None
            for file_name in sorted(sol_files):
                file_content = archive.read(file_name)
                source = _read_sol_file(file_content)
                parts.append(f"// File: {file_name}\n{source}")
                if primary_name is None:
                    primary_name = file_name.rsplit("/", 1)[-1]

            return "\n\n".join(parts), primary_name
    except zipfile.BadZipFile as exc:
        raise UploadError("Invalid ZIP archive") from exc


def parse_upload(filename: str, content: bytes, chain_id: int = 0) -> UploadedContract:
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadError("File exceeds 10 MB limit")

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
