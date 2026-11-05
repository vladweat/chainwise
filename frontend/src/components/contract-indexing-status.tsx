"use client";

import { motion } from "framer-motion";

import { ContractStatusBadge } from "@/components/contract-status-badge";
import { RiskScoreBadge } from "@/components/risk-score-badge";
import {
  CONTRACT_STATUS,
  isCompletedStatus,
  isReadyStatus,
} from "@/lib/contract-status";
import { chainName, truncateAddress } from "@/lib/format";
import type { ContractStatusResponse } from "@/types/contract";

type StepState = "pending" | "active" | "done" | "failed";

const PROGRESS_STEPS = ["compile", "slither", "embed", "score"] as const;
type ProgressStepId = (typeof PROGRESS_STEPS)[number];

interface PipelineStep {
  id: ProgressStepId;
  label: string;
  detail: string;
}

const STEPS: PipelineStep[] = [
  { id: "compile", label: "solc → AST", detail: "Компиляция исходника" },
  { id: "slither", label: "Slither", detail: "Статический анализ" },
  { id: "embed", label: "Qdrant", detail: "Semantic split + эмбеддинги" },
  { id: "score", label: "Risk Score", detail: "XGBoost A/B/C/D" },
];

function asProgressStep(step: string | null | undefined): ProgressStepId | null {
  if (!step) return null;
  return (PROGRESS_STEPS as readonly string[]).includes(step) ? (step as ProgressStepId) : null;
}

function stepState(
  status: string,
  indexingStep: string | null | undefined,
  error: string | null,
  index: number,
): StepState {
  if (status === CONTRACT_STATUS.FAILED) {
    const failedIndex = failedStepIndex(indexingStep, error);
    if (index < failedIndex) return "done";
    if (index === failedIndex) return "failed";
    return "pending";
  }
  if (isCompletedStatus(status) || indexingStep === "done") return "done";

  const active = asProgressStep(indexingStep) ?? "compile";
  const activeIndex = PROGRESS_STEPS.indexOf(active);
  if (index < activeIndex) return "done";
  if (index === activeIndex) return "active";
  return "pending";
}

function failedStepIndex(indexingStep: string | null | undefined, error: string | null): number {
  const known = asProgressStep(indexingStep);
  if (known) return PROGRESS_STEPS.indexOf(known);
  const text = (error ?? "").toLowerCase();
  if (text.includes("qdrant") || text.includes("embed")) return 2;
  if (text.includes("slither")) return 1;
  if (text.includes("risk") || text.includes("score") || text.includes("xgboost")) return 3;
  return 0;
}

function StepDot({ state }: { state: StepState }) {
  if (state === "done") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/20 text-xs text-emerald-400">
        ✓
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-red-500/20 text-xs text-red-400">
        !
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative flex h-6 w-6 items-center justify-center">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400/30" />
        <span className="relative h-2.5 w-2.5 rounded-full bg-amber-400" />
      </span>
    );
  }
  return <span className="h-6 w-6 rounded-full border border-slate-700" />;
}

export function ContractIndexingStatus({ status }: { status: ContractStatusResponse }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-slate-800 bg-slate-950 p-4"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <h3 className="text-sm font-medium text-slate-300">Контракт #{status.contract_id}</h3>
        <div className="flex items-center gap-2">
          <RiskScoreBadge score={status.risk_score} model={status.risk_model} />
          <ContractStatusBadge status={status.status} />
        </div>
      </div>

      <dl className="mb-4 grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Адрес</dt>
          <dd className="break-all font-mono text-slate-200" title={status.address}>
            {truncateAddress(status.address)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Сеть</dt>
          <dd className="text-slate-200">{chainName(status.chain_id)}</dd>
        </div>
        {status.name ? (
          <div>
            <dt className="text-slate-500">Имя</dt>
            <dd className="break-all text-slate-200">{status.name}</dd>
          </div>
        ) : null}
        {status.compiler_version ? (
          <div>
            <dt className="text-slate-500">Компилятор</dt>
            <dd className="font-mono text-slate-200">solc {status.compiler_version}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-slate-500">Чанки в Qdrant</dt>
          <dd className="font-mono text-slate-200">
            {status.chunk_count != null ? status.chunk_count : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Risk Score</dt>
          <dd className="text-slate-200">{status.risk_score ?? "—"}</dd>
        </div>
      </dl>

      <ol className="space-y-2">
        {STEPS.map((step, index) => {
          const state = stepState(status.status, status.indexing_step, status.last_error, index);
          const extra =
            step.id === "embed" && isReadyStatus(status.status) && status.chunk_count != null
              ? `${status.chunk_count} чанков`
              : step.id === "score" && isReadyStatus(status.status) && status.risk_score
                ? `оценка ${status.risk_score}`
                : step.detail;
          return (
            <li
              key={step.id}
              className="flex items-center gap-3 rounded-lg border border-slate-800/80 bg-slate-900/40 px-3 py-2"
            >
              <StepDot state={state} />
              <div className="min-w-0">
                <p className="text-sm text-slate-200">{step.label}</p>
                <p className="text-xs text-slate-500">{extra}</p>
              </div>
            </li>
          );
        })}
      </ol>

      {status.risk_model === "heuristic" ? (
        <p className="mt-3 text-xs text-amber-400/80">
          Оценка эвристическая (нет pickle XGBoost). Буква A–D — не аудит.
        </p>
      ) : null}
      {status.status === CONTRACT_STATUS.INDEXING ? (
        <p className="mt-3 text-xs text-amber-400/80">Идёт индексация, обычно это занимает до пары минут.</p>
      ) : null}
      {isReadyStatus(status.status) ? (
        <p className="mt-3 text-xs text-emerald-400/80">Индексация завершена.</p>
      ) : null}
      {status.status === CONTRACT_STATUS.FAILED ? (
        <p className="mt-3 whitespace-pre-wrap break-words text-xs text-red-400/80">
          {status.last_error || "Индексация не удалась. Проверьте исходник."}
        </p>
      ) : null}
    </motion.div>
  );
}
