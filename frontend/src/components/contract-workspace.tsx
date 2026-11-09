"use client";

import { ChevronLeft, FileDown, PanelLeft, PanelLeftClose, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useLayoutEffect, useState } from "react";

import { ContractChatPanel } from "@/components/contract-chat-panel";
import { ContractFindingsList } from "@/components/contract-findings-list";
import { ContractFindingsPanel } from "@/components/contract-findings-panel";
import { ContractIndexingStatus } from "@/components/contract-indexing-status";
import { ContractStatusBadge } from "@/components/contract-status-badge";
import { RiskScoreBadge } from "@/components/risk-score-badge";
import { RiskScoreNotice } from "@/components/risk-score-notice";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { useContractActions } from "@/hooks/use-contract-actions";
import { useContractStatus } from "@/hooks/use-contract-status";
import { isReadyStatus } from "@/lib/contract-status";
import { isCodeCitationPath, type SourceTarget } from "@/lib/findings-filter";
import { chainName, truncateAddress } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Citation, Finding } from "@/types/contract";

export function ContractWorkspace({ contractId }: { contractId: number }) {
  const searchParams = useSearchParams();
  const alreadyIndexed = searchParams.get("existing") === "1";
  const { status, error, setError, seenLiveIndexing } = useContractStatus(contractId);
  const {
    pdfBusy,
    pdfError,
    deleting,
    actionError,
    handleDownloadPdf,
    handleDeleteContract,
  } = useContractActions(contractId);

  const [reportOpen, setReportOpen] = useState(false);
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);
  const [sourceTarget, setSourceTarget] = useState<SourceTarget | null>(null);
  const [leftOpen, setLeftOpen] = useState(false);

  useLayoutEffect(() => {
    setReportOpen(false);
    setSelectedFinding(null);
    setSourceTarget(null);
    setLeftOpen(window.matchMedia("(min-width: 1024px)").matches);
  }, [contractId]);

  useEffect(() => {
    if (!status) return;
    if (isReadyStatus(status.status) && seenLiveIndexing.current) {
      setReportOpen(true);
    }
  }, [status, seenLiveIndexing]);

  useEffect(() => {
    if (actionError) setError(actionError);
  }, [actionError, setError]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (reportOpen) {
        setReportOpen(false);
        return;
      }
      setLeftOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reportOpen]);

  if (error && !status) {
    return (
      <div className="w-full flex-1 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
        <div className="mx-auto max-w-5xl">
          <Panel title="Контракт">
            <p className="mb-4 text-sm text-red-400">{error}</p>
            <Button asChild variant="secondary">
              <Link href="/history">К истории</Link>
            </Button>
          </Panel>
        </div>
      </div>
    );
  }

  if (!status) {
    return <p className="px-4 py-8 text-sm text-slate-400">Загрузка статуса…</p>;
  }

  const ready = isReadyStatus(status.status);

  if (!ready) {
    return (
      <div className="w-full flex-1 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
        <div className="mx-auto max-w-5xl space-y-4">
          <ContractIndexingStatus status={status} />
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-red-300 hover:text-red-200"
              onClick={() => void handleDeleteContract()}
              disabled={deleting}
              aria-label="Удалить контракт"
            >
              <Trash2 />
              {deleting ? "Удаление…" : "Удалить контракт"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex w-full shrink-0 flex-col gap-2 border-b border-slate-800 px-3 py-2 lg:flex-row lg:items-center lg:px-6 lg:py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <Button asChild variant="ghost" size="sm" className="h-8 shrink-0 gap-0.5 px-1.5 lg:px-2">
            <Link href="/history">
              <ChevronLeft />
              <span className="hidden lg:inline">Назад</span>
            </Link>
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium leading-tight text-slate-100">
              {status.name || `Контракт #${status.contract_id}`}
            </p>
            <p
              className="mt-0.5 truncate font-mono text-[11px] leading-tight text-slate-500"
              title={status.address}
            >
              <span className="lg:hidden">
                {truncateAddress(status.address, true)} · {chainName(status.chain_id)}
              </span>
              <span className="hidden lg:inline">
                {truncateAddress(status.address)} · {chainName(status.chain_id)}
              </span>
            </p>
          </div>
          <RiskScoreBadge score={status.risk_score} model={status.risk_model} />
          <span className="hidden lg:inline">
            <ContractStatusBadge status={status.status} />
          </span>
        </div>
        <div className="flex items-center gap-2 lg:ml-auto">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1 lg:flex-none"
            onClick={() => setLeftOpen((open) => !open)}
            aria-pressed={leftOpen}
            aria-label={leftOpen ? "Скрыть findings" : "Показать findings"}
            title={leftOpen ? "Скрыть findings" : "Показать findings"}
          >
            {leftOpen ? <PanelLeftClose /> : <PanelLeft />}
            Findings
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1 lg:flex-none"
            onClick={() => setReportOpen(true)}
          >
            Отчёт
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1 lg:flex-none"
            onClick={() => void handleDownloadPdf()}
            disabled={pdfBusy}
            title={pdfError ?? "Скачать PDF"}
          >
            <FileDown />
            {pdfBusy ? "PDF…" : "PDF"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1 text-red-300 hover:text-red-200 lg:flex-none"
            onClick={() => void handleDeleteContract()}
            disabled={deleting}
            aria-label="Удалить контракт"
            title="Удалить контракт"
          >
            <Trash2 />
            {deleting ? "Удаление…" : "Удалить"}
          </Button>
        </div>
      </div>

      {alreadyIndexed ? (
        <p className="border-b border-slate-800 px-3 py-1.5 text-xs text-sky-400/90 sm:px-6">
          Этот адрес уже разобран. Открыта существующая карточка.
        </p>
      ) : null}
      {status.risk_score ? (
        <div className="border-b border-slate-800 px-3 py-1.5 sm:px-6">
          <RiskScoreNotice model={status.risk_model} />
        </div>
      ) : null}
      {pdfError ? (
        <p className="border-b border-slate-800 px-3 py-1.5 text-xs text-red-400 sm:px-6">{pdfError}</p>
      ) : null}

      <div className="relative flex min-h-0 flex-1">
        {leftOpen ? (
          <button
            type="button"
            className="absolute inset-0 z-10 bg-slate-950/70 lg:hidden"
            aria-label="Закрыть findings"
            onClick={() => setLeftOpen(false)}
          />
        ) : null}
        <div
          className={cn(
            "min-h-0 flex-col border-slate-800 bg-slate-950",
            leftOpen ? "flex" : "hidden",
            "absolute inset-0 z-20 min-w-0 lg:static lg:z-auto lg:w-[46%] lg:max-w-[46%] lg:shrink-0 lg:border-r",
          )}
        >
          <ContractFindingsPanel
            contractId={status.contract_id}
            selectedFinding={selectedFinding}
            sourceTarget={sourceTarget}
            onSelectFinding={(finding) => {
              setSelectedFinding(finding);
              setSourceTarget({
                file_path: finding.file_path,
                start_line: finding.start_line,
                end_line: finding.end_line ?? finding.start_line,
              });
            }}
            onClose={() => setLeftOpen(false)}
          />
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <ContractChatPanel
            contractId={status.contract_id}
            emptyIndex={status.chunk_count === 0}
            onSelectCitation={(citation: Citation) => {
              if (!isCodeCitationPath(citation.file_path)) return;
              setSelectedFinding(null);
              setSourceTarget({
                file_path: citation.file_path,
                start_line: citation.start_line,
                end_line: citation.end_line,
              });
              setLeftOpen(true);
            }}
          />
        </div>
      </div>

      {reportOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/80 sm:items-start sm:overflow-y-auto sm:p-4 sm:pt-16"
          onClick={() => setReportOpen(false)}
          role="presentation"
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="indexing-report-title"
            className="flex max-h-[88dvh] w-full max-w-3xl flex-col rounded-t-2xl border border-slate-800 bg-slate-900 p-4 shadow-2xl pb-[max(1rem,env(safe-area-inset-bottom))] sm:mb-8 sm:rounded-2xl sm:p-5"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex shrink-0 items-center justify-between gap-2">
              <h2 id="indexing-report-title" className="text-sm font-medium text-slate-200">
                Отчёт индексации
              </h2>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleDownloadPdf()}
                  disabled={pdfBusy}
                >
                  <FileDown />
                  {pdfBusy ? "PDF…" : "Скачать PDF"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setReportOpen(false)}
                >
                  <X />
                  Закрыть
                </Button>
              </div>
            </div>
            <div className="min-h-0 overflow-y-auto">
              <ContractIndexingStatus status={status} />
              <ContractFindingsList
                contractId={status.contract_id}
                selected={selectedFinding}
                onSelectFinding={(finding) => {
                  setSelectedFinding(finding);
                  setSourceTarget({
                    file_path: finding.file_path,
                    start_line: finding.start_line,
                    end_line: finding.end_line ?? finding.start_line,
                  });
                  setLeftOpen(true);
                  setReportOpen(false);
                }}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
