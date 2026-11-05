import logging

import httpx

from app.core.config import get_settings
from app.core.sentry import capture_pipeline_error
from app.services.solc_compiler import DEFAULT_SOLC_VERSION, normalize_solc_version, split_source_files

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
    version = normalize_solc_version(compiler_version) or DEFAULT_SOLC_VERSION

    try:
        response = httpx.post(
            f"{settings.slither_url.rstrip('/')}/analyze",
            json={"files": files, "solc_version": version},
            timeout=120.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        logger.exception("Slither request failed")
        capture_pipeline_error("slither", exc)
        raise SlitherError("Cannot reach Slither service") from exc

    if response.status_code != 200:
        detail = response.text.strip() or f"HTTP {response.status_code}"
        error = SlitherError(f"Slither failed: {detail[:2000]}")
        capture_pipeline_error("slither", error)
        raise error

    try:
        report = response.json()
    except ValueError as exc:
        raise SlitherError("Slither returned invalid JSON") from exc

    if not isinstance(report, dict):
        raise SlitherError("Slither returned invalid JSON")

    if report.get("success") is False:
        raise SlitherError(str(report.get("error") or "Slither analysis failed"))

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
