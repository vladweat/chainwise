"use client";

import { Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ContractStatusBadge } from "@/components/contract-status-badge";
import { RiskScoreBadge } from "@/components/risk-score-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { ApiRequestError, deleteContract, fetchContracts } from "@/lib/api";
import { clearLastContractId, getLastContractId } from "@/lib/contract-storage";
import { chainName, formatDateTime, truncateAddress } from "@/lib/format";
import type { ContractStatusResponse } from "@/types/contract";

export function HistoryList() {
  const [rows, setRows] = useState<ContractStatusResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchContracts();
      setRows(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Не удалось загрузить историю");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDelete(contractId: number) {
    if (!window.confirm("Удалить этот контракт безвозвратно?")) {
      return;
    }
    setDeletingId(contractId);
    try {
      await deleteContract(contractId);
      if (getLastContractId() === contractId) {
        clearLastContractId();
      }
      setRows((prev) => (prev ? prev.filter((row) => row.contract_id !== contractId) : prev));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Не удалось удалить контракт");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <Panel title="История">
      {error ? (
        <p className="rounded-lg bg-red-950/40 px-3 py-2 text-sm text-red-400">{error}</p>
      ) : null}
      {rows === null && !error ? <p className="text-sm text-slate-400">Загрузка…</p> : null}
      {rows && rows.length === 0 ? (
        <div className="space-y-3 text-sm text-slate-400">
          <p>Пока нет загруженных контрактов.</p>
          <Button asChild>
            <Link href="/analyze">Загрузить контракт</Link>
          </Button>
        </div>
      ) : null}
      {rows && rows.length > 0 ? (
        <ul className="space-y-2">
          {rows.map((item) => (
            <li
              key={item.contract_id}
              className="flex items-stretch gap-2 rounded-xl border border-slate-800 bg-slate-950/50 transition-colors hover:border-blue-500/50"
            >
              <Link
                href={`/contracts/${item.contract_id}`}
                className="flex min-w-0 flex-1 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-100">
                    {item.name || `Контракт #${item.contract_id}`}
                  </p>
                  <p className="truncate font-mono text-xs text-slate-500" title={item.address}>
                    {truncateAddress(item.address)} · {chainName(item.chain_id)}
                  </p>
                  <p className="text-xs text-slate-500">{formatDateTime(item.created_at)}</p>
                </div>
                <div className="flex items-center gap-2">
                  <RiskScoreBadge score={item.risk_score} model={item.risk_model} />
                  <ContractStatusBadge status={item.status} />
                </div>
              </Link>
              <div className="flex shrink-0 items-center pr-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-slate-500 hover:text-red-300"
                  aria-label="Удалить контракт"
                  title="Удалить контракт"
                  disabled={deletingId === item.contract_id}
                  onClick={() => void handleDelete(item.contract_id)}
                >
                  <Trash2 />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
