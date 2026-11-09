import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, Response

SOLC_DIR = Path(os.environ.get("SOLC_DIR", "/opt/solc"))
SOLC_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_SOLC_VERSIONS = frozenset({"0.8.24"})
DEFAULT_SOLC_VERSION = "0.8.24"

MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_SOURCE_FILES = 100
MAX_SOURCE_FILE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 25 * 1024 * 1024
MAX_SOLC_STDOUT_BYTES = 32 * 1024 * 1024
MAX_SOLC_STDERR_BYTES = 64 * 1024
MAX_SLITHER_STDOUT_BYTES = 1024 * 1024
MAX_SLITHER_STDERR_BYTES = 64 * 1024
MAX_REPORT_JSON_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_JSON_BYTES = 32 * 1024 * 1024
MAX_CONCURRENT_ANALYZER_REQUESTS = 2
SOLC_TIMEOUT_SECONDS = 45
SLITHER_TIMEOUT_SECONDS = 90
_ANALYZER_REQUEST_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYZER_REQUESTS)


class RequestSizeLimitMiddleware:
    """Reject oversized analyzer JSON before FastAPI buffers it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {"/compile", "/analyze"}:
            await self.app(scope, receive, send)
            return

        if not _ANALYZER_REQUEST_SLOTS.acquire(blocking=False):
            response = JSONResponse(
                {"detail": "Analyzer is at capacity; retry later"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
            await response(scope, receive, send)
            return

        try:
            content_length = next(
                (
                    value.decode("ascii", errors="ignore")
                    for name, value in scope.get("headers", [])
                    if name.lower() == b"content-length"
                ),
                None,
            )
            if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
                await _request_too_large(scope, receive, send)
                return

            total_bytes = 0

            async def limited_receive():
                nonlocal total_bytes
                message = await receive()
                if message["type"] == "http.request":
                    total_bytes += len(message.get("body", b""))
                    if total_bytes > MAX_REQUEST_BYTES:
                        raise _RequestBodyTooLarge
                return message

            try:
                await self.app(scope, limited_receive, send)
            except _RequestBodyTooLarge:
                await _request_too_large(scope, receive, send)
        finally:
            _ANALYZER_REQUEST_SLOTS.release()


class _RequestBodyTooLarge(Exception):
    pass


async def _request_too_large(scope, receive, send) -> None:
    response = JSONResponse(
        {"detail": "Analyzer request exceeds 64 MiB limit"},
        status_code=413,
    )
    await response(scope, receive, send)


app = FastAPI(
    title="ChainWise Analyzer",
    version="0.2.0",
    middleware=[Middleware(RequestSizeLimitMiddleware)],
)


class AnalyzeRequest(BaseModel):
    files: dict[str, str] = Field(..., min_length=1)
    solc_version: str = DEFAULT_SOLC_VERSION


class CompileRequest(AnalyzeRequest):
    pass


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessTimedOut(Exception):
    pass


class ProcessOutputLimitExceeded(Exception):
    pass


def _version_key(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))


def available_solc_versions() -> list[str]:
    if not SOLC_DIR.is_dir():
        return []
    versions = []
    for version in ALLOWED_SOLC_VERSIONS:
        path = SOLC_DIR / f"solc-v{version}"
        if SOLC_VERSION_RE.fullmatch(version) and path.is_file() and os.access(path, os.X_OK):
            versions.append(version)
    return sorted(versions, key=_version_key)


def _select_solc_version(requested: str) -> str:
    if not SOLC_VERSION_RE.fullmatch(requested):
        raise HTTPException(status_code=400, detail="Invalid solc version")

    installed = available_solc_versions()
    if requested in installed:
        return requested
    if DEFAULT_SOLC_VERSION in installed:
        return DEFAULT_SOLC_VERSION
    if installed:
        return installed[-1]
    raise HTTPException(
        status_code=503,
        detail="No preinstalled allowlisted solc compiler is available",
    )


def _normalize_source_path(relative: str) -> str:
    if not relative or any(ord(char) < 0x20 or ord(char) == 0x7F for char in relative):
        raise HTTPException(status_code=400, detail="Unsafe source path")
    try:
        path_bytes = relative.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise HTTPException(status_code=400, detail="Source path must be valid UTF-8") from exc
    if len(path_bytes) > 4096:
        raise HTTPException(status_code=400, detail="Source path exceeds 4096 bytes")

    windows_path = PureWindowsPath(relative)
    if windows_path.drive or windows_path.is_absolute():
        raise HTTPException(status_code=400, detail="Unsafe source path")

    normalized = unicodedata.normalize("NFC", relative).replace("\\", "/")
    if normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Unsafe source path")
    components = normalized.split("/")
    if ".." in components:
        raise HTTPException(status_code=400, detail="Unsafe source path")
    normalized = "/".join(part for part in components if part not in {"", "."})
    if not normalized:
        raise HTTPException(status_code=400, detail="Unsafe source path")
    return normalized


def _validate_files(files: dict[str, str]) -> dict[str, str]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one Solidity source file is required")
    if len(files) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=413, detail="Analyzer accepts at most 100 source files")

    validated: dict[str, str] = {}
    seen_names: set[str] = set()
    total_bytes = 0
    for relative, content in files.items():
        path = _normalize_source_path(relative)
        duplicate_key = path.casefold()
        if duplicate_key in seen_names:
            raise HTTPException(status_code=400, detail="Duplicate source path")
        seen_names.add(duplicate_key)

        if any(ord(char) < 0x20 and char not in "\t\r\n" for char in content):
            raise HTTPException(status_code=400, detail="Solidity source contains invalid control characters")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise HTTPException(status_code=400, detail="Solidity source must be valid UTF-8") from exc
        if size > MAX_SOURCE_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Solidity source file exceeds 10 MiB limit")
        total_bytes += size
        if total_bytes > MAX_SOURCE_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Total Solidity source exceeds 25 MiB limit")
        validated[path] = content
    return validated


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()


def _run_bounded_process(
    command: list[str],
    *,
    input_bytes: bytes | None,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    env: dict[str, str] | None = None,
) -> BoundedProcessResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
        start_new_session=True,
    )
    stdout_data = bytearray()
    stderr_data = bytearray()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()

    def drain(pipe, target: bytearray, limit: int, overflow: threading.Event) -> None:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            remaining = limit - len(target)
            if len(chunk) > remaining:
                if remaining > 0:
                    target.extend(chunk[:remaining])
                overflow.set()
                _kill_process_group(process)
            elif not overflow.is_set():
                target.extend(chunk)

    def write_input() -> None:
        try:
            view = memoryview(input_bytes or b"")
            while view:
                written = process.stdin.write(view[:64 * 1024])
                if not written:
                    break
                view = view[written:]
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    readers = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout_data, stdout_limit, stdout_overflow),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr_data, stderr_limit, stderr_overflow),
            daemon=True,
        ),
    ]
    writer = threading.Thread(target=write_input, daemon=True)
    for thread in readers:
        thread.start()
    writer.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    if not timed_out:
        for thread in (writer, *readers):
            thread.join(timeout=max(0, deadline - time.monotonic()))
            if thread.is_alive():
                timed_out = True
                break

    if timed_out:
        _kill_process_group(process)
        if process.poll() is None:
            process.wait()
        writer.join(timeout=1)
        for thread in readers:
            thread.join(timeout=1)

    if timed_out:
        raise ProcessTimedOut
    if stdout_overflow.is_set() or stderr_overflow.is_set():
        raise ProcessOutputLimitExceeded
    return BoundedProcessResult(
        returncode=process.returncode,
        stdout=bytes(stdout_data),
        stderr=bytes(stderr_data),
    )


def _bounded_json_response(payload: dict, *, error_status: int = 502) -> Response:
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=error_status, detail="Analyzer produced invalid JSON") from exc
    if len(body) > MAX_RESPONSE_JSON_BYTES:
        raise HTTPException(status_code=502, detail="Analyzer JSON response exceeds 32 MiB limit")
    return Response(content=body, media_type="application/json")


def _compiler_error_detail(errors: list[dict], files: dict[str, str]) -> str:
    first_error = next((error for error in errors if error.get("severity") == "error"), {})
    error_type = first_error.get("type")
    code = first_error.get("errorCode")
    if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", error_type):
        error_type = "CompilerError"
    if not isinstance(code, str) or not code.isdigit():
        code = "unknown"

    location = first_error.get("sourceLocation")
    if isinstance(location, dict):
        filename = location.get("file")
        offset = location.get("start")
        source = files.get(filename) if isinstance(filename, str) else None
        if isinstance(source, str) and isinstance(offset, int) and offset >= 0:
            source_bytes = source.encode("utf-8")
            if offset > len(source_bytes):
                return f"Solidity compilation failed ({error_type}, {code})"
            line = source_bytes[:offset].count(b"\n") + 1
            return f"Solidity compilation failed ({error_type}, {code}) at {filename}:{line}"
    return f"Solidity compilation failed ({error_type}, {code})"


def _subprocess_environment(path_prefix: str = "") -> dict[str, str]:
    path = os.pathsep.join(part for part in (path_prefix, "/usr/local/bin", "/usr/bin", "/bin") if part)
    return {
        "PATH": path,
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/solc/versions")
def solc_versions() -> dict[str, list[str]]:
    return {"versions": available_solc_versions()}


@app.post("/compile")
def compile_solidity(body: CompileRequest) -> Response:
    files = _validate_files(body.files)
    version = _select_solc_version(body.solc_version)
    solc_bin = SOLC_DIR / f"solc-v{version}"

    standard_input = {
        "language": "Solidity",
        "sources": {path: {"content": content} for path, content in files.items()},
        "settings": {"outputSelection": {"*": {"": ["ast"]}}},
    }
    request_bytes = json.dumps(
        standard_input,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(request_bytes) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="Solidity compiler request exceeds 64 MiB limit")

    try:
        completed = _run_bounded_process(
            [str(solc_bin), "--standard-json"],
            input_bytes=request_bytes,
            timeout=SOLC_TIMEOUT_SECONDS,
            stdout_limit=MAX_SOLC_STDOUT_BYTES,
            stderr_limit=MAX_SOLC_STDERR_BYTES,
            env=_subprocess_environment(),
        )
    except ProcessTimedOut as exc:
        raise HTTPException(status_code=504, detail="solc compilation timed out after 45 seconds") from exc
    except ProcessOutputLimitExceeded as exc:
        raise HTTPException(status_code=502, detail="solc output limit exceeded") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Preinstalled solc compiler could not be started") from exc

    try:
        output = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="solc returned invalid standard JSON") from exc
    if not isinstance(output, dict):
        raise HTTPException(status_code=502, detail="solc returned invalid standard JSON")

    errors = output.get("errors")
    if isinstance(errors, list) and any(
        isinstance(error, dict) and error.get("severity") == "error" for error in errors
    ):
        raise HTTPException(status_code=422, detail=_compiler_error_detail(errors, files))
    if completed.returncode != 0:
        raise HTTPException(status_code=502, detail=f"solc exited with status {completed.returncode}")

    sources = output.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise HTTPException(status_code=502, detail="solc returned no AST sources")
    return _bounded_json_response({"solc_version": version, "sources": sources})


def _safe_path(root: Path, relative: str) -> Path:
    normalized = _normalize_source_path(relative)
    root_path = root.resolve()
    dest = (root_path / normalized).resolve()
    try:
        dest.relative_to(root_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsafe source path") from exc
    return dest


@app.post("/analyze")
def analyze(body: AnalyzeRequest) -> Response:
    files = _validate_files(body.files)
    version = _select_solc_version(body.solc_version)
    solc_bin = SOLC_DIR / f"solc-v{version}"

    with tempfile.TemporaryDirectory(prefix="slither-") as tmp:
        root = Path(tmp)
        workdir = root / "src"
        workdir.mkdir()
        for relative, content in files.items():
            dest = _safe_path(workdir, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        solc_dir = root / "solc-bin"
        solc_dir.mkdir()
        solc_link = solc_dir / "solc"
        solc_link.symlink_to(solc_bin)

        report_path = root / "slither-report.json"
        try:
            completed = _run_bounded_process(
                [
                    sys.executable,
                    "-m",
                    "slither",
                    str(workdir),
                    "--solc",
                    str(solc_bin),
                    "--json",
                    str(report_path),
                    "--fail-none",
                    "--disable-color",
                ],
                input_bytes=None,
                timeout=SLITHER_TIMEOUT_SECONDS,
                stdout_limit=MAX_SLITHER_STDOUT_BYTES,
                stderr_limit=MAX_SLITHER_STDERR_BYTES,
                env=_subprocess_environment(str(solc_dir)),
            )
        except ProcessTimedOut as exc:
            raise HTTPException(status_code=504, detail="Slither analysis timed out") from exc
        except ProcessOutputLimitExceeded as exc:
            raise HTTPException(status_code=502, detail="Slither output limit exceeded") from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Slither process could not be started") from exc

        if not report_path.is_file():
            raise HTTPException(
                status_code=502,
                detail=f"Slither exited with status {completed.returncode} without JSON output",
            )
        if report_path.stat().st_size > MAX_REPORT_JSON_BYTES:
            raise HTTPException(status_code=502, detail="Slither JSON report exceeds 32 MiB limit")

        report_bytes = report_path.read_bytes()
        try:
            report = json.loads(report_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail="Slither returned invalid JSON") from exc

    if not isinstance(report, dict):
        raise HTTPException(status_code=502, detail="Slither JSON report must be an object")

    results = report.setdefault("results", {})
    if not isinstance(results, dict):
        results = {}
        report["results"] = results
    results.setdefault("detectors", [])
    return _bounded_json_response(report)
