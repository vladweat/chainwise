import {
  RISK_SCORE_ADVISORY,
  riskModelAttribution,
} from "@/lib/risk-score-copy.mjs";

export function RiskScoreNotice({ model }: { model?: string | null }) {
  return (
    <aside
      role="note"
      aria-label="Ограничения Risk Score"
      className="mt-3 rounded-lg border border-amber-800/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200/90"
    >
      <p>{RISK_SCORE_ADVISORY}</p>
      <p className="mt-1 text-amber-100/70">Источник: {riskModelAttribution(model)}</p>
    </aside>
  );
}
