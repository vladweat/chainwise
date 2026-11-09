import { Badge } from "@/components/ui/badge";
import { RISK_SCORE_ADVISORY, riskModelAttribution } from "@/lib/risk-score-copy.mjs";

const SCORE_VARIANT: Record<string, "success" | "default" | "warning" | "destructive"> = {
  A: "success",
  B: "default",
  C: "warning",
  D: "destructive",
};

export function RiskScoreBadge({
  score,
  model,
}: {
  score: string | null;
  model?: string | null;
}) {
  if (!score) {
    return (
      <Badge variant="secondary" className="size-7 shrink-0 justify-center px-0">
        —
      </Badge>
    );
  }
  return (
    <Badge
      variant={SCORE_VARIANT[score] ?? "secondary"}
      className="size-7 shrink-0 justify-center px-0 font-mono text-sm font-semibold"
      aria-label={`Risk Score ${score}. ${riskModelAttribution(model)}. ${RISK_SCORE_ADVISORY}`}
      title={`${riskModelAttribution(model)}. ${RISK_SCORE_ADVISORY}`}
    >
      {score}
    </Badge>
  );
}
