import assert from "node:assert/strict";
import test from "node:test";

import {
  RISK_SCORE_ADVISORY,
  riskModelAttribution,
} from "../src/lib/risk-score-copy.mjs";

test("UI advisory explains that A-D is not a probability or audit", () => {
  assert.match(RISK_SCORE_ADVISORY, /эвристически сформированный/iu);
  assert.match(RISK_SCORE_ADVISORY, /advisory-индикатор для предварительной проверки/iu);
  assert.match(RISK_SCORE_ADVISORY, /не вероятность эксплуатации/iu);
  assert.match(RISK_SCORE_ADVISORY, /не калиброванная вероятность риска/iu);
  assert.match(RISK_SCORE_ADVISORY, /не аудит безопасности/iu);
});

test("UI attributes synthetic-trained model and heuristic fallback correctly", () => {
  assert.equal(
    riskModelAttribution("xgboost"),
    "XGBoost — модель обучена на синтетических эвристических метках",
  );
  assert.equal(riskModelAttribution("heuristic"), "Эвристический fallback");
  assert.equal(riskModelAttribution(null), "Источник скоринга не указан");
});
