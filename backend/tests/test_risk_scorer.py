from app.services.risk_scorer import (
    FEATURE_NAMES,
    extract_features,
    feature_vector,
    heuristic_high_risk,
    heuristic_letter,
    heuristic_numeric,
    probability_to_letter,
    risk_reasons,
    risk_model_attribution,
    score_slither_report,
    score_slither_report_detailed,
)


EMPTY_REPORT = {"success": True, "error": None, "results": {"detectors": []}}

DANGEROUS_REPORT = {
    "success": True,
    "results": {
        "detectors": [
            {"check": "reentrancy-eth", "impact": "High", "confidence": "High"},
            {"check": "suicidal", "impact": "High", "confidence": "Medium"},
            {"check": "controlled-delegatecall", "impact": "High", "confidence": "High"},
        ]
    },
}


def test_extract_features_empty_report():
    features = extract_features(EMPTY_REPORT)
    assert len(features) == len(FEATURE_NAMES)
    assert features["detector_count"] == 0
    assert features["high_impact_count"] == 0
    assert heuristic_numeric(features) == 0
    assert heuristic_letter(features) == "A"


def test_extract_features_ignores_malformed_report():
    assert extract_features(None)["detector_count"] == 0
    assert extract_features({"results": "nope"})["detector_count"] == 0
    assert extract_features({"results": {"detectors": [{"check": 1}]}})["detector_count"] == 1


def test_extract_features_medium_and_low_impacts():
    report = {
        "success": True,
        "results": {
            "detectors": [
                {"check": "timestamp", "impact": "Low", "confidence": "Medium"},
                {"check": "arbitrary-send-eth", "impact": "Medium", "confidence": "High"},
                {"check": "naming-convention", "impact": "Informational", "confidence": "High"},
            ]
        },
    }
    features = extract_features(report)
    assert features["low_impact_count"] == 1
    assert features["medium_impact_count"] == 1
    assert features["informational_count"] == 1
    assert features["timestamp_count"] == 1
    assert features["arbitrary_send_count"] == 1
    assert heuristic_letter(features) in {"B", "C", "D"}


def test_extract_features_counts_impacts_and_checks():
    features = extract_features(DANGEROUS_REPORT)
    assert features["detector_count"] == 3
    assert features["high_impact_count"] == 3
    assert features["high_confidence_count"] == 2
    assert features["reentrancy_count"] == 1
    assert features["selfdestruct_count"] == 1
    assert features["delegatecall_count"] == 1
    assert features["unique_check_count"] == 3
    assert len(feature_vector(features)) == len(FEATURE_NAMES)
    assert heuristic_letter(features) == "D"


def test_probability_to_letter_thresholds():
    assert probability_to_letter(0.0) == "A"
    assert probability_to_letter(0.24) == "A"
    assert probability_to_letter(0.25) == "B"
    assert probability_to_letter(0.49) == "B"
    assert probability_to_letter(0.50) == "C"
    assert probability_to_letter(0.74) == "C"
    assert probability_to_letter(0.75) == "D"
    assert probability_to_letter(1.0) == "D"


def test_score_empty_report_is_a():
    assert score_slither_report(EMPTY_REPORT) == "A"


def test_score_dangerous_report_is_c_or_d():
    assert score_slither_report(DANGEROUS_REPORT) in {"C", "D"}


def test_heuristic_fallback_when_model_missing(monkeypatch):
    monkeypatch.setattr("app.services.risk_scorer._get_model", lambda: None)
    assert score_slither_report(EMPTY_REPORT) == "A"
    assert score_slither_report(DANGEROUS_REPORT) == "D"


def test_heuristic_fallback_when_model_raises(monkeypatch):
    monkeypatch.setattr(
        "app.services.risk_scorer._get_model",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert score_slither_report(EMPTY_REPORT) == "A"


def test_score_detailed_reports_source(monkeypatch):
    monkeypatch.setattr("app.services.risk_scorer._get_model", lambda: None)
    letter, source = score_slither_report_detailed(DANGEROUS_REPORT)
    assert letter == "D"
    assert source == "heuristic"


def test_model_attribution_distinguishes_xgboost_from_heuristic():
    assert risk_model_attribution("xgboost") == "XGBoost model trained on synthetic heuristic labels"
    assert risk_model_attribution("heuristic") == "Heuristic fallback"
    assert risk_model_attribution(None) == "Unknown source"


def test_heuristic_high_risk_threshold():
    assert heuristic_high_risk(extract_features(EMPTY_REPORT)) is False
    assert heuristic_high_risk(extract_features(DANGEROUS_REPORT)) is True


def test_score_xgboost_bundle(monkeypatch):
    class FakeBooster:
        def predict(self, _matrix):
            return [0.8]

    bundle = {"booster": FakeBooster(), "feature_names": FEATURE_NAMES}
    monkeypatch.setattr("app.services.risk_scorer._get_model", lambda: bundle)
    letter, source = score_slither_report_detailed(EMPTY_REPORT)
    assert source == "xgboost"
    assert letter == "D"


def test_invalid_pickle_falls_back_to_heuristic(tmp_path, monkeypatch):
    from app.services import risk_scorer

    junk = tmp_path / "risk_xgb_model.pkl"
    junk.write_bytes(b"not-a-pickle")
    monkeypatch.setenv("RISK_MODEL_PATH", str(junk))
    risk_scorer.reset_model_cache()
    letter, source = score_slither_report_detailed(EMPTY_REPORT)
    assert letter == "A"
    assert source == "heuristic"
    risk_scorer.reset_model_cache()


def test_model_path_uses_env_file(tmp_path, monkeypatch):
    from app.services import risk_scorer

    path = tmp_path / "risk_xgb_model.pkl"
    path.write_bytes(b"x")
    monkeypatch.setenv("RISK_MODEL_PATH", str(path))
    risk_scorer.reset_model_cache()
    assert risk_scorer.model_path() == path
    risk_scorer.reset_model_cache()


def test_feature_name_mismatch_skips_model(tmp_path, monkeypatch):
    import pickle

    from app.services import risk_scorer

    bundle = {"booster": "placeholder", "feature_names": ("nope",)}
    path = tmp_path / "risk_xgb_model.pkl"
    path.write_bytes(pickle.dumps(bundle))
    monkeypatch.setenv("RISK_MODEL_PATH", str(path))
    risk_scorer.reset_model_cache()
    letter, source = score_slither_report_detailed(EMPTY_REPORT)
    assert source == "heuristic"
    assert letter == "A"
    risk_scorer.reset_model_cache()


def test_bundle_without_booster_falls_back(tmp_path, monkeypatch):
    import pickle

    from app.services import risk_scorer

    path = tmp_path / "risk_xgb_model.pkl"
    path.write_bytes(pickle.dumps({"feature_names": FEATURE_NAMES}))
    monkeypatch.setenv("RISK_MODEL_PATH", str(path))
    risk_scorer.reset_model_cache()
    letter, source = score_slither_report_detailed(EMPTY_REPORT)
    assert source == "heuristic"
    risk_scorer.reset_model_cache()


def test_risk_reasons_lists_high_impact_and_reentrancy():
    reasons = risk_reasons(DANGEROUS_REPORT)
    assert any("detectors" in item for item in reasons)
    assert any("high-impact" in item for item in reasons)
    assert any("reentrancy" in item for item in reasons)
    assert len(reasons) <= 5
