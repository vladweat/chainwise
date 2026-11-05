import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.sentry import capture_pipeline_error
from app.utils.pragma_parser import parse_pragma_solidity

logger = logging.getLogger(__name__)

DEFAULT_SOLC_VERSION = "0.8.24"
FILE_HEADER = re.compile(r"^// File: (.+)$", re.MULTILINE)


class SolcCompileError(Exception):
    """Raised when solc cannot compile the given sources."""


@dataclass(frozen=True)
class CompileResult:
    solc_version: str
    sources: dict[str, dict]

    def to_dict(self) -> dict:
        return {"solc_version": self.solc_version, "sources": self.sources}


def solc_binary_dir() -> Path:
    env_path = os.environ.get("SOLCX_BINARY_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / ".solc"


def _apply_binary_path() -> str:
    path = str(solc_binary_dir())
    os.environ["SOLCX_BINARY_PATH"] = path
    return path


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
    matches = list(FILE_HEADER.finditer(source_code))
    if not matches:
        content = source_code.strip()
        if not content:
            raise SolcCompileError("Source code is empty")
        return {_safe_source_path(default_filename): content}

    files: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source_code)
        path = _safe_source_path(match.group(1).strip())
        content = source_code[start:end].strip()
        if content:
            files[path] = content

    if not files:
        raise SolcCompileError("Source code is empty")
    return files


def is_solc_available(version: str = DEFAULT_SOLC_VERSION) -> bool:
    try:
        from solcx import get_installed_solc_versions
    except ImportError:
        return False

    target = normalize_solc_version(version) or DEFAULT_SOLC_VERSION
    try:
        installed = get_installed_solc_versions(solcx_binary_path=_apply_binary_path())
    except Exception:
        return False
    return any(str(installed_version) == target for installed_version in installed)


def compile_to_ast(
    source_code: str,
    compiler_version: str | None = None,
    filename: str | None = None,
) -> CompileResult:
    """Compile Solidity sources with solc and return per-file ASTs."""
    from solcx import compile_standard
    from solcx.exceptions import SolcError, UnexpectedVersionError

    default_filename = filename if filename else "Contract.sol"
    sources = split_source_files(source_code, default_filename=default_filename)
    version = _resolve_version(compiler_version, source_code)
    version = _ensure_solc(version)

    standard_input = {
        "language": "Solidity",
        "sources": {path: {"content": content} for path, content in sources.items()},
        "settings": {
            "outputSelection": {
                "*": {
                    "": ["ast"],
                }
            }
        },
    }

    try:
        output = compile_standard(standard_input, solc_version=version)
    except (SolcError, UnexpectedVersionError, FileNotFoundError, OSError) as exc:
        error = SolcCompileError(f"solc {version} failed: {exc}")
        capture_pipeline_error("solc", error)
        raise error from exc

    errors = [
        item
        for item in output.get("errors", [])
        if item.get("severity") == "error"
    ]
    if errors:
        messages = "; ".join(
            item.get("formattedMessage") or item.get("message") or "compile error"
            for item in errors
        )
        raise SolcCompileError(f"solc {version} failed: {messages}")

    ast_sources = output.get("sources") or {}
    if not ast_sources:
        raise SolcCompileError(f"solc {version} produced no AST")

    logger.info(
        "Compiled Solidity to AST",
        extra={"solc_version": version, "file_count": len(ast_sources)},
    )
    return CompileResult(solc_version=version, sources=ast_sources)


def _safe_source_path(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("/")
    parts = Path(normalized).parts
    if not normalized or ".." in parts:
        raise SolcCompileError(f"Unsafe source path: {path}")
    return normalized


def _resolve_version(compiler_version: str | None, source_code: str) -> str:
    normalized = normalize_solc_version(compiler_version)
    if normalized:
        return normalized
    parsed = parse_pragma_solidity(source_code)
    return parsed or DEFAULT_SOLC_VERSION


def _ensure_solc(version: str) -> str:
    """Select a locally installed solc. Never download at runtime (offline Docker)."""
    from solcx import get_installed_solc_versions, set_solc_version

    binary_dir = solc_binary_dir()
    binary_dir.mkdir(parents=True, exist_ok=True)
    binary_path = _apply_binary_path()

    installed = {str(item) for item in get_installed_solc_versions(solcx_binary_path=binary_path)}
    selected = _select_installed_version(version, installed)

    set_solc_version(selected, silent=True, solcx_binary_path=binary_path)
    return selected


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
