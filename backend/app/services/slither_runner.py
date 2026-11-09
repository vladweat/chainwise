import json
import logging

import httpx

from app.core.config import get_settings
from app.core.sentry import capture_pipeline_error
from app.services.solc_compiler import (
    DEFAULT_SOLC_VERSION,
    MAX_ANALYZER_REQUEST_BYTES,
    MAX_ANALYZER_RESPONSE_BYTES,
    normalize_solc_version,
    split_source_files,
    validate_analyzer_sources,
)

logger = logging.getLogger(__name__)


class SlitherError(Exception):
    """Raised when the Slither Docker service cannot analyze the contract."""


def is_slither_available() -> bool:
    settings = get_settings()
    if not settings.slither_url:
        return False
    try:
        response = httpx.get(
            f"{settings.slither_url.rstrip('/')}/health",
            timeout=2.0,
            trust_env=False,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def run_slither(
    source_code: str,
    compiler_version: str | None = None,
    filename: str | None = None,
) -> dict:
    settings = get_settings()
    if not settings.slither_url:
        raise SlitherError("Slither service URL is not configured")

    files = split_source_files(source_code, default_filename=filename or "Contract.sol")
    validate_analyzer_sources(files)
    version = normalize_solc_version(compiler_version) or DEFAULT_SOLC_VERSION
    request_body = json.dumps(
        {"files": files, "solc_version": version},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(request_body) > MAX_ANALYZER_REQUEST_BYTES:
        raise SlitherError("Analyzer request exceeds the 64 MiB limit")

    try:
        response = httpx.post(
            f"{settings.slither_url.rstrip('/')}/analyze",
            content=request_body,
            headers={"content-type": "application/json"},
            timeout=120.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        logger.exception("Slither request failed")
        capture_pipeline_error("slither", exc)
        raise SlitherError("Cannot reach Slither service") from exc

    if response.status_code != 200:
        error = SlitherError(f"Slither failed (HTTP {response.status_code})")
        capture_pipeline_error("slither", error)
        raise error

    if len(response.content) > MAX_ANALYZER_RESPONSE_BYTES:
        raise SlitherError("Analyzer response exceeds the 32 MiB limit")

    try:
        report = response.json()
    except ValueError as exc:
        raise SlitherError("Slither returned invalid JSON") from exc

    if not isinstance(report, dict):
        raise SlitherError("Slither returned invalid JSON")

    if report.get("success") is False:
        raise SlitherError("Slither analysis failed")

    results = report.get("results")
    if not isinstance(results, dict):
        results = {}
        report["results"] = results
    results.setdefault("detectors", [])

    detectors = results["detectors"]
    logger.info(
        "Slither analysis complete",
        extra={"solc_version": version, "detector_count": len(detectors)},
    )
    return report
