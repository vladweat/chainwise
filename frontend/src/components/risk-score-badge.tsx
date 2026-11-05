import { Badge } from "@/components/ui/badge";

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
      title={model ? `Risk model: ${model}` : undefined}
    >
      {score}
    </Badge>
  );
}
