import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PureWindowsPath

import httpx

from app.core.config import get_settings
from app.core.sentry import capture_pipeline_error
from app.utils.pragma_parser import parse_pragma_solidity

logger = logging.getLogger(__name__)

DEFAULT_SOLC_VERSION = "0.8.24"
FILE_HEADER = re.compile(r"^// File: (.+)$", re.MULTILINE)
MAX_ANALYZER_SOURCE_BYTES = 25 * 1024 * 1024
MAX_ANALYZER_FILE_BYTES = 10 * 1024 * 1024
MAX_ANALYZER_FILES = 100
MAX_ANALYZER_REQUEST_BYTES = 64 * 1024 * 1024
MAX_ANALYZER_RESPONSE_BYTES = 32 * 1024 * 1024
ANALYZER_TIMEOUT_SECONDS = 55


class SolcCompileError(Exception):
    """Raised when solc cannot compile the given sources."""


@dataclass(frozen=True)
class CompileResult:
    solc_version: str
    sources: dict[str, dict]

    def to_dict(self) -> dict:
        return {"solc_version": self.solc_version, "sources": self.sources}


def normalize_solc_version(raw: str | None) -> str | None:
    if not raw:
        return None
    version = raw.strip()
    if version.startswith("v"):
        version = version[1:]
    if "+" in version:
        version = version.split("+", 1)[0]
    return version or None


def split_source_files(source_code: str, default_filename: str = "Contract.sol") -> dict[str, str]:
    """Split concatenated sources (`// File: path`) into a solc Standard JSON map."""
    if len(source_code) > MAX_ANALYZER_SOURCE_BYTES:
        raise SolcCompileError("Total Solidity source exceeds 25 MiB limit")
    matches = []
    for match in FILE_HEADER.finditer(source_code):
        matches.append(match)
        if len(matches) > MAX_ANALYZER_FILES:
            raise SolcCompileError("Analyzer accepts at most 100 source files")
    if not matches:
        content = source_code.strip()
        if not content:
            raise SolcCompileError("Source code is empty")
        return {_safe_source_path(default_filename): content}

    files: dict[str, str] = {}
    seen_paths: set[str] = set()
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source_code)
        path = _safe_source_path(match.group(1).strip())
        duplicate_key = path.casefold()
        if duplicate_key in seen_paths:
            raise SolcCompileError("Duplicate source path")
        seen_paths.add(duplicate_key)
        content = source_code[start:end].strip()
        if content:
            files[path] = content

    if not files:
        raise SolcCompileError("Source code is empty")
    return files


def is_solc_available(version: str = DEFAULT_SOLC_VERSION) -> bool:
    settings = get_settings()
    if not settings.slither_url:
        return False
    target = normalize_solc_version(version) or DEFAULT_SOLC_VERSION
    try:
        response = httpx.get(
            f"{settings.slither_url.rstrip('/')}/solc/versions",
            timeout=2.0,
            trust_env=False,
        )
        if response.status_code != 200:
            return False
        installed = response.json().get("versions")
        if not isinstance(installed, list):
            return False
        _select_installed_version(target, {item for item in installed if isinstance(item, str)})
        return True
    except (httpx.HTTPError, ValueError, SolcCompileError):
        return False


def compile_to_ast(
    source_code: str,
    compiler_version: str | None = None,
    filename: str | None = None,
) -> CompileResult:
    """Compile Solidity through the isolated analyzer and return per-file ASTs."""
    default_filename = filename if filename else "Contract.sol"
    sources = split_source_files(source_code, default_filename=default_filename)
    version = _resolve_version(compiler_version, source_code)
    validate_analyzer_sources(sources)
    settings = get_settings()
    if not settings.slither_url:
        raise SolcCompileError("Analyzer service URL is not configured")
    request_body = json.dumps(
        {"files": sources, "solc_version": version},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(request_body) > MAX_ANALYZER_REQUEST_BYTES:
        raise SolcCompileError("Analyzer request exceeds the 64 MiB limit")

    response_body = bytearray()
    try:
        with httpx.stream(
            "POST",
            f"{settings.slither_url.rstrip('/')}/compile",
            content=request_body,
            headers={"content-type": "application/json"},
            timeout=ANALYZER_TIMEOUT_SECONDS,
            trust_env=False,
        ) as response:
            status_code = response.status_code
            for chunk in response.iter_bytes():
                if len(response_body) + len(chunk) > MAX_ANALYZER_RESPONSE_BYTES:
                    raise SolcCompileError("Analyzer response exceeds the 32 MiB limit")
                response_body.extend(chunk)
    except SolcCompileError:
        raise
    except httpx.TimeoutException as exc:
        error = SolcCompileError("Analyzer compilation request timed out")
        capture_pipeline_error("solc", error)
        raise error from exc
    except httpx.HTTPError as exc:
        error = SolcCompileError("Cannot reach isolated analyzer service")
        capture_pipeline_error("solc", error)
        raise error from exc

    try:
        result = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SolcCompileError("Analyzer returned invalid JSON") from exc

    if status_code != 200:
        raise SolcCompileError(_sanitized_analyzer_error(status_code, result))
    if not isinstance(result, dict):
        raise SolcCompileError("Analyzer returned invalid JSON")
    selected_version = result.get("solc_version")
    ast_sources = result.get("sources")
    if not isinstance(selected_version, str) or not isinstance(ast_sources, dict) or not ast_sources:
        raise SolcCompileError("Analyzer returned no AST sources")

    logger.info(
        "Compiled Solidity to AST",
        extra={"solc_version": selected_version, "file_count": len(ast_sources)},
    )
    return CompileResult(solc_version=selected_version, sources=ast_sources)


def _safe_source_path(path: str) -> str:
    windows_path = PureWindowsPath(path)
    normalized = unicodedata.normalize("NFC", path).replace("\\", "/")
    try:
        path_size = len(path.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SolcCompileError("Source path must be valid UTF-8") from exc
    if (
        not path
        or windows_path.drive
        or windows_path.is_absolute()
        or normalized.startswith("/")
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in path)
        or path_size > 4096
    ):
        raise SolcCompileError("Unsafe source path")
    parts = normalized.split("/")
    if ".." in parts:
        raise SolcCompileError("Unsafe source path")
    normalized = "/".join(part for part in parts if part not in {"", "."})
    if not normalized:
        raise SolcCompileError("Unsafe source path")
    return normalized


def _resolve_version(compiler_version: str | None, source_code: str) -> str:
    normalized = normalize_solc_version(compiler_version)
    if normalized:
        return normalized
    parsed = parse_pragma_solidity(source_code)
    return parsed or DEFAULT_SOLC_VERSION


def _select_installed_version(requested: str, installed: set[str]) -> str:
    if requested in installed:
        return requested
    if DEFAULT_SOLC_VERSION in installed:
        logger.warning(
            "solc %s is not installed; using %s (offline, no download)",
            requested,
            DEFAULT_SOLC_VERSION,
        )
        return DEFAULT_SOLC_VERSION
    if installed:
        newest = max(installed, key=_version_key)
        logger.warning(
            "solc %s is not installed; using %s (offline, no download)",
            requested,
            newest,
        )
        return newest
    raise SolcCompileError(
        f"solc {requested} is not installed. Run: make solc"
    )


def _version_key(version: str) -> tuple[int, int, int]:
    parts = (normalize_solc_version(version) or "0.0.0").split(".")
    nums = [int(part) if part.isdigit() else 0 for part in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def validate_analyzer_sources(sources: dict[str, str]) -> None:
    if len(sources) > MAX_ANALYZER_FILES:
        raise SolcCompileError("Analyzer accepts at most 100 source files")
    total_bytes = 0
    for source in sources.values():
        if any(ord(char) < 0x20 and char not in "\t\r\n" for char in source):
            raise SolcCompileError("Solidity source contains invalid control characters")
        try:
            size = len(source.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SolcCompileError("Solidity source must be valid UTF-8") from exc
        if size > MAX_ANALYZER_FILE_BYTES:
            raise SolcCompileError("Solidity source file exceeds 10 MiB limit")
        total_bytes += size
        if total_bytes > MAX_ANALYZER_SOURCE_BYTES:
            raise SolcCompileError("Total Solidity source exceeds 25 MiB limit")


def _sanitized_analyzer_error(status_code: int, body: object) -> str:
    if status_code == 422 and isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            match = re.match(
                r"^(Solidity compilation failed \([A-Za-z][A-Za-z0-9]*, (?:\d+|unknown)\))",
                detail,
            )
            if match:
                return match.group(1)
    if status_code == 413:
        return "Analyzer request exceeds configured size limits"
    if status_code == 503:
        return "No preinstalled allowlisted solc compiler is available"
    if status_code == 504:
        return "Solidity compilation timed out"
    return f"Analyzer failed to compile Solidity (HTTP {status_code})"
