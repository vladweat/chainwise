import { Badge } from "@/components/ui/badge";
import type { ContractLoadStatus } from "@/types/contract";

const STATUS_LABELS: Record<ContractLoadStatus, string> = {
  pending: "Ожидание",
  indexing: "Индексация…",
  loaded: "Загружен",
  ready: "Готов",
  failed: "Ошибка",
};

const STATUS_VARIANT: Record<
  ContractLoadStatus,
  "secondary" | "warning" | "default" | "success" | "destructive"
> = {
  pending: "secondary",
  indexing: "warning",
  loaded: "default",
  ready: "success",
  failed: "destructive",
};

export function ContractStatusBadge({ status }: { status: string }) {
  const key = (status in STATUS_LABELS ? status : "pending") as ContractLoadStatus;
  return <Badge variant={STATUS_VARIANT[key]}>{STATUS_LABELS[key]}</Badge>;
}
