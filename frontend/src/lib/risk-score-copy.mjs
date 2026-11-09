export const RISK_SCORE_ADVISORY =
  "Экспериментальная оценка A–D — эвристически сформированный advisory-индикатор для предварительной проверки. Это не вероятность эксплуатации, не калиброванная вероятность риска и не аудит безопасности.";

export function riskModelAttribution(model) {
  if (model === "xgboost") {
    return "XGBoost — модель обучена на синтетических эвристических метках";
  }
  if (model === "heuristic") {
    return "Эвристический fallback";
  }
  return "Источник скоринга не указан";
}
