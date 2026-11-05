"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { ContractFindingsList } from "@/components/contract-findings-list";
import { ContractSourceViewer, type SourceHighlight } from "@/components/contract-source-viewer";
import { Button } from "@/components/ui/button";
import { ApiRequestError, fetchContractSource } from "@/lib/api";
import { resolveSourcePath, type SourceTarget } from "@/lib/findings-filter";
import { cn } from "@/lib/utils";
import type { Finding, SourceFile } from "@/types/contract";

export function ContractFindingsPanel({
  contractId,
  selectedFinding,
  sourceTarget,
  onSelectFinding,
  onClose,
}: {
  contractId: number;
  selectedFinding: Finding | null;
  sourceTarget: SourceTarget | null;
  onSelectFinding: (finding: Finding) => void;
  onClose?: () => void;
}) {
  const [files, setFiles] = useState<SourceFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<SourceHighlight | null>(null);
  const [mobileView, setMobileView] = useState<"list" | "code">("list");

  useEffect(() => {
    let cancelled = false;
    setFiles(null);
    setError(null);
    setActivePath(null);
    setHighlight(null);
    setMobileView("list");
    void fetchContractSource(contractId)
      .then((data) => {
        if (cancelled) return;
        setFiles(data.files);
        setActivePath(data.files[0]?.path ?? null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiRequestError ? err.message : "Не удалось загрузить исходник");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [contractId]);

  useEffect(() => {
    if (!sourceTarget || !files) return;
    const path = resolveSourcePath(files.map((item) => item.path), sourceTarget.file_path);
    if (path) setActivePath(path);
    setHighlight({
      path,
      startLine: sourceTarget.start_line,
      endLine: sourceTarget.end_line ?? sourceTarget.start_line,
    });
    setMobileView("code");
  }, [sourceTarget, files]);

  function handleSelect(finding: Finding) {
    setMobileView("code");
    onSelectFinding(finding);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-slate-800 px-2 py-1.5 lg:hidden">
        <Button
          type="button"
          size="sm"
          variant={mobileView === "list" ? "default" : "outline"}
          onClick={() => setMobileView("list")}
        >
          Findings
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mobileView === "code" ? "default" : "outline"}
          onClick={() => setMobileView("code")}
        >
          Код
        </Button>
        {onClose ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={onClose}
            aria-label="Закрыть findings"
          >
            <X />
          </Button>
        ) : null}
      </div>
      <div
        className={cn(
          "min-h-0 overflow-y-auto px-3 py-3 sm:px-4",
          mobileView === "list" ? "flex-1" : "hidden",
          "lg:block lg:max-h-[42%] lg:shrink-0 lg:flex-none",
        )}
      >
        <ContractFindingsList
          contractId={contractId}
          selected={selectedFinding}
          onSelectFinding={handleSelect}
        />
      </div>
      <div
        className={cn(
          "min-h-0 flex-1 flex-col",
          mobileView === "code" ? "flex" : "hidden",
          "lg:flex",
        )}
      >
        {error ? <p className="px-3 py-3 text-xs text-red-400">{error}</p> : null}
        {files === null && !error ? (
          <p className="px-3 py-3 text-xs text-slate-500">Исходник…</p>
        ) : null}
        {files && files.length > 0 ? (
          <ContractSourceViewer
            files={files}
            activePath={activePath}
            onPathChange={setActivePath}
            highlight={highlight}
          />
        ) : null}
      </div>
    </div>
  );
}
