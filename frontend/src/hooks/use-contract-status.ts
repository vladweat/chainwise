"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiRequestError, fetchContractStatus } from "@/lib/api";
import { setLastContractId } from "@/lib/contract-storage";
import { isTerminalStatus } from "@/lib/contract-status";
import type { ContractStatusResponse } from "@/types/contract";

const POLL_INTERVAL_MS = 2000;

export function useContractStatus(contractId: number) {
  const [status, setStatus] = useState<ContractStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seenLiveIndexing = useRef(false);

  const pollStatus = useCallback(async (id: number) => {
    try {
      const data = await fetchContractStatus(id);
      setStatus(data);
      setLastContractId(id);
      setError(null);
      return data.status;
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Не удалось получить статус";
      setError(message);
      return "failed";
    }
  }, []);

  useEffect(() => {
    seenLiveIndexing.current = false;
  }, [contractId]);

  useEffect(() => {
    if (!status) return;
    if (status.status === "indexing" || status.status === "pending") {
      seenLiveIndexing.current = true;
    }
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      const current = await pollStatus(contractId);
      if (cancelled) return;
      if (!isTerminalStatus(current)) {
        timer = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [contractId, pollStatus]);

  return { status, error, setError, seenLiveIndexing };
}
