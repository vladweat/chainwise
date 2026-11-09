"""Produce an experimental advisory A-D pre-screening indicator from Slither features."""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURES_VERSION = "v1"
FEATURE_NAMES: tuple[str, ...] = (
    "detector_count",
    "high_impact_count",
    "medium_impact_count",
    "low_impact_count",
    "informational_count",
    "high_confidence_count",
    "reentrancy_count",
    "delegatecall_count",
    "selfdestruct_count",
    "arbitrary_send_count",
    "timestamp_count",
    "unchecked_lowlevel_count",
    "unique_check_count",
    "severity_weighted_score",
)

IMPACT_WEIGHTS = {
    "high": 4.0,
    "medium": 2.0,
    "low": 1.0,
    "informational": 0.2,
}

HIGH_RISK_HEURISTIC_THRESHOLD = 6.0

_model_bundle: dict | None = None
_model_loaded = False


def extract_features(report: dict | None) -> dict[str, float]:
    """Turn Slither JSON into a stable numeric feature vector."""
    detectors = _detectors(report)
    high = _count_impact(detectors, "high")
    medium = _count_impact(detectors, "medium")
    low = _count_impact(detectors, "low")
    informational = _count_impact(detectors, "informational")
    checks = [_check_name(item) for item in detectors]

    features = {
        "detector_count": float(len(detectors)),
        "high_impact_count": float(high),
        "medium_impact_count": float(medium),
        "low_impact_count": float(low),
        "informational_count": float(informational),
        "high_confidence_count": float(_count_confidence(detectors, "high")),
        "reentrancy_count": float(_count_check(checks, ("reentrancy",))),
        "delegatecall_count": float(_count_check(checks, ("delegatecall",))),
        "selfdestruct_count": float(_count_check(checks, ("suicidal", "selfdestruct"))),
        "arbitrary_send_count": float(_count_check(checks, ("arbitrary-send", "unchecked-send"))),
        "timestamp_count": float(_count_check(checks, ("timestamp", "block-timestamp", "weak-prng"))),
        "unchecked_lowlevel_count": float(
            _count_check(checks, ("low-level", "unchecked-lowlevel", "unchecked-transfer"))
        ),
        "unique_check_count": float(len({name for name in checks if name})),
        "severity_weighted_score": 0.0,
    }
    features["severity_weighted_score"] = heuristic_numeric(features)
    return features


def feature_vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


def heuristic_numeric(features: dict[str, float]) -> float:
    return (
        IMPACT_WEIGHTS["high"] * features.get("high_impact_count", 0.0)
        + IMPACT_WEIGHTS["medium"] * features.get("medium_impact_count", 0.0)
        + IMPACT_WEIGHTS["low"] * features.get("low_impact_count", 0.0)
        + IMPACT_WEIGHTS["informational"] * features.get("informational_count", 0.0)
        + 3.0 * features.get("reentrancy_count", 0.0)
        + 3.0 * features.get("delegatecall_count", 0.0)
        + 3.0 * features.get("selfdestruct_count", 0.0)
        + 2.0 * features.get("arbitrary_send_count", 0.0)
        + 1.0 * features.get("timestamp_count", 0.0)
        + 1.0 * features.get("unchecked_lowlevel_count", 0.0)
    )


def heuristic_high_risk(features: dict[str, float]) -> bool:
    return heuristic_numeric(features) >= HIGH_RISK_HEURISTIC_THRESHOLD


def heuristic_letter(features: dict[str, float]) -> str:
    score = heuristic_numeric(features)
    if score <= 0:
        return "A"
    if score < 4:
        return "B"
    if score < 8:
        return "C"
    return "D"


def probability_to_letter(probability: float) -> str:
    if probability < 0.25:
        return "A"
    if probability < 0.50:
        return "B"
    if probability < 0.75:
        return "C"
    return "D"


def score_slither_report(report: dict | None) -> str:
    """Return A/B/C/D. Uses XGBoost when the pickle is available, else the heuristic."""
    return score_slither_report_detailed(report)[0]


def score_slither_report_detailed(report: dict | None) -> tuple[str, str]:
    """Return (letter, source) where source is `xgboost` or `heuristic`."""
    features = extract_features(report)
    try:
        bundle = _get_model()
        if bundle is not None:
            probability = _predict_proba(bundle, features)
            letter = probability_to_letter(probability)
            logger.info(
                "Risk score predicted",
                extra={
                    "risk_score": letter,
                    "probability": round(probability, 4),
                    "detector_count": int(features["detector_count"]),
                    "source": "xgboost",
                    "features_version": FEATURES_VERSION,
                },
            )
            return letter, "xgboost"
    except Exception:
        logger.exception("XGBoost scoring failed, using heuristic")

    letter = heuristic_letter(features)
    logger.info(
        "Risk score predicted",
        extra={
            "risk_score": letter,
            "detector_count": int(features["detector_count"]),
            "source": "heuristic",
            "features_version": FEATURES_VERSION,
        },
    )
    return letter, "heuristic"


def risk_model_attribution(source: str | None) -> str:
    """Describe the scoring source without implying model validation."""
    if source == "xgboost":
        return "XGBoost model trained on synthetic heuristic labels"
    if source == "heuristic":
        return "Heuristic fallback"
    return "Unknown source"


def risk_reasons(report: dict | None, limit: int = 5) -> list[str]:
    """Short English bullets for PDF / status: counts that drove the letter."""
    features = extract_features(report)
    ordered: list[str] = []
    mapping = (
        ("detector_count", "Slither detectors"),
        ("high_impact_count", "high-impact findings"),
        ("reentrancy_count", "reentrancy detectors"),
        ("delegatecall_count", "delegatecall detectors"),
        ("selfdestruct_count", "selfdestruct detectors"),
        ("arbitrary_send_count", "arbitrary-send detectors"),
        ("medium_impact_count", "medium-impact findings"),
    )
    for key, label in mapping:
        count = int(features.get(key, 0.0))
        if count:
            ordered.append(f"{count} {label}")
        if len(ordered) >= limit:
            break
    if not ordered:
        ordered.append("No Slither detectors; score is informational only")
    return ordered[:limit]


def model_path() -> Path | None:
    for candidate in _model_candidates():
        if candidate.is_file():
            return candidate
    return None


def reset_model_cache() -> None:
    global _model_bundle, _model_loaded
    _model_bundle = None
    _model_loaded = False


def _detectors(report: dict | None) -> list[dict]:
    if not isinstance(report, dict):
        return []
    results = report.get("results")
    if not isinstance(results, dict):
        return []
    detectors = results.get("detectors")
    if not isinstance(detectors, list):
        return []
    return [item for item in detectors if isinstance(item, dict)]


def _count_impact(detectors: list[dict], impact: str) -> int:
    return sum(1 for item in detectors if str(item.get("impact") or "").strip().lower() == impact)


def _count_confidence(detectors: list[dict], confidence: str) -> int:
    return sum(
        1 for item in detectors if str(item.get("confidence") or "").strip().lower() == confidence
    )


def _check_name(item: dict) -> str:
    return str(item.get("check") or "").strip().lower()


def _count_check(checks: list[str], needles: tuple[str, ...]) -> int:
    return sum(1 for name in checks if any(needle in name for needle in needles))


def _model_candidates() -> list[Path]:
    env_path = os.environ.get("RISK_MODEL_PATH", "").strip()
    if not env_path:
        try:
            from app.core.config import get_settings

            env_path = (get_settings().risk_model_path or "").strip()
        except Exception:
            env_path = ""
    backend_dir = Path(__file__).resolve().parents[2]
    repo_root = backend_dir.parent
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path("/app/ml_models/risk_xgb_model.pkl"),
            backend_dir / "ml_models" / "risk_xgb_model.pkl",
            repo_root / "ml_models" / "risk_xgb_model.pkl",
        ]
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _get_model() -> dict | None:
    global _model_bundle, _model_loaded
    if _model_loaded:
        return _model_bundle
    _model_loaded = True
    path = model_path()
    if path is None:
        logger.warning("Risk model pickle not found, using heuristic")
        return None
    try:
        with path.open("rb") as handle:
            bundle = pickle.load(handle)
    except Exception:
        logger.exception("Failed to load risk model from %s", path)
        return None
    if not isinstance(bundle, dict) or "booster" not in bundle:
        logger.error("Invalid risk model bundle at %s", path)
        return None
    names = bundle.get("feature_names")
    if names and tuple(names) != FEATURE_NAMES:
        logger.error("Risk model feature names do not match extractor")
        return None
    _model_bundle = bundle
    logger.info("Loaded risk model", extra={"path": str(path)})
    return _model_bundle


def _predict_proba(bundle: dict, features: dict[str, float]) -> float:
    import numpy as np
    import xgboost as xgb

    vector = np.asarray([feature_vector(features)], dtype=np.float32)
    matrix = xgb.DMatrix(vector, feature_names=list(FEATURE_NAMES))
    raw = bundle["booster"].predict(matrix)
    value = float(raw[0])
    return min(1.0, max(0.0, value))
