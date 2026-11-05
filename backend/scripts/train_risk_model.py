"""Train a synthetic XGBoost risk model and save ml_models/risk_xgb_model.pkl."""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.risk_scorer import (  # noqa: E402
    FEATURE_NAMES,
    extract_features,
    feature_vector,
    heuristic_high_risk,
    heuristic_numeric,
    score_slither_report,
)

REPO_ROOT = BACKEND_DIR.parent
OUTPUT_PATHS = (
    REPO_ROOT / "ml_models" / "risk_xgb_model.pkl",
    BACKEND_DIR / "ml_models" / "risk_xgb_model.pkl",
)
RNG_SEED = 42
N_SAMPLES = 4000


def _sample_features(rng: np.random.Generator) -> dict[str, float]:
    roll = rng.random()
    if roll < 0.28:
        high = 0
        medium = int(rng.integers(0, 2))
        low = int(rng.integers(0, 3))
        info = int(rng.integers(0, 4))
        reentrancy = 0
        delegatecall = 0
        selfdestruct = 0
        arbitrary = 0
        timestamp = int(rng.integers(0, 2))
        unchecked = int(rng.integers(0, 2))
    elif roll < 0.65:
        high = int(rng.integers(0, 3))
        medium = int(rng.integers(0, 5))
        low = int(rng.integers(0, 6))
        info = int(rng.integers(0, 6))
        reentrancy = int(rng.integers(0, 2))
        delegatecall = int(rng.integers(0, 2))
        selfdestruct = int(rng.integers(0, 2))
        arbitrary = int(rng.integers(0, 2))
        timestamp = int(rng.integers(0, 3))
        unchecked = int(rng.integers(0, 3))
    else:
        high = int(rng.integers(1, 8))
        medium = int(rng.integers(0, 8))
        low = int(rng.integers(0, 6))
        info = int(rng.integers(0, 5))
        reentrancy = int(rng.integers(0, 4))
        delegatecall = int(rng.integers(0, 3))
        selfdestruct = int(rng.integers(0, 3))
        arbitrary = int(rng.integers(0, 3))
        timestamp = int(rng.integers(0, 4))
        unchecked = int(rng.integers(0, 4))

    detector_count = high + medium + low + info
    unique = min(detector_count, high + medium + reentrancy + delegatecall + 1)
    features = {
        "detector_count": float(detector_count),
        "high_impact_count": float(high),
        "medium_impact_count": float(medium),
        "low_impact_count": float(low),
        "informational_count": float(info),
        "high_confidence_count": float(min(high + medium, detector_count)),
        "reentrancy_count": float(reentrancy),
        "delegatecall_count": float(delegatecall),
        "selfdestruct_count": float(selfdestruct),
        "arbitrary_send_count": float(arbitrary),
        "timestamp_count": float(timestamp),
        "unchecked_lowlevel_count": float(unchecked),
        "unique_check_count": float(unique),
        "severity_weighted_score": 0.0,
    }
    features["severity_weighted_score"] = heuristic_numeric(features)
    return features


def _build_dataset(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    labels = []
    rows.append(feature_vector(extract_features({"success": True, "results": {"detectors": []}})))
    labels.append(0)
    for _ in range(N_SAMPLES - 1):
        features = _sample_features(rng)
        rows.append(feature_vector(features))
        labels.append(1 if heuristic_high_risk(features) else 0)
    return np.asarray(rows, dtype=np.float32), np.asarray(labels, dtype=np.float32)


def main() -> int:
    try:
        import xgboost as xgb
    except ImportError:
        print("ERROR: xgboost is not installed. Run: pip install -r backend/requirements.txt", file=sys.stderr)
        return 1

    rng = np.random.default_rng(RNG_SEED)
    features, labels = _build_dataset(rng)
    split = int(0.8 * len(features))
    dtrain = xgb.DMatrix(features[:split], label=labels[:split], feature_names=list(FEATURE_NAMES))
    dtest = xgb.DMatrix(features[split:], label=labels[split:], feature_names=list(FEATURE_NAMES))
    booster = xgb.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 4,
            "eta": 0.2,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "min_child_weight": 2,
            "seed": RNG_SEED,
            "verbosity": 0,
        },
        dtrain,
        num_boost_round=80,
        evals=[(dtest, "test")],
        verbose_eval=False,
    )

    preds = booster.predict(dtest)
    accuracy = float(np.mean((preds >= 0.5) == labels[split:]))
    print(f"Holdout accuracy vs heuristic labels: {accuracy:.3f}")

    bundle = {"booster": booster, "feature_names": list(FEATURE_NAMES), "version": 1}
    for path in OUTPUT_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"Saved {path}")

    from app.services.risk_scorer import reset_model_cache

    reset_model_cache()
    empty_report = {"success": True, "results": {"detectors": []}}
    empty_letter = score_slither_report(empty_report)
    dangerous = {
        "success": True,
        "results": {
            "detectors": [
                {"check": "reentrancy-eth", "impact": "High", "confidence": "High"},
                {"check": "suicidal", "impact": "High", "confidence": "High"},
                {"check": "controlled-delegatecall", "impact": "High", "confidence": "Medium"},
            ]
        },
    }
    dangerous_letter = score_slither_report(dangerous)
    print(f"Empty report → {empty_letter} (proba-mapped)")
    print(f"Dangerous report → {dangerous_letter}")
    if empty_letter != "A":
        print("ERROR: empty report should score A", file=sys.stderr)
        return 1
    if dangerous_letter not in {"C", "D"}:
        print("ERROR: dangerous report should score C or D", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
