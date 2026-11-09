"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiRequestError, fetchContractStatus } from "@/lib/api";
import { setLastContractId } from "@/lib/contract-storage";
import { isTerminalStatus } from "@/lib/contract-status";
import {
  getStatusRetryDelay,
  shouldRetryStatusFailure,
  STATUS_POLL_INTERVAL_MS,
} from "@/lib/status-polling.mjs";
import type { ContractStatusResponse } from "@/types/contract";

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
      return { status: data.status, retry: false };
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Не удалось получить статус";
      setError(message);
      const statusCode = err instanceof ApiRequestError ? err.status : undefined;
      const retry = shouldRetryStatusFailure(statusCode);
      if (!retry) setStatus(null);
      return { status: null, retry };
    }
  }, []);

  const currentStatus = status?.contract_id === contractId ? status : null;

  useEffect(() => {
    seenLiveIndexing.current = false;
  }, [contractId]);

  useEffect(() => {
    if (!currentStatus) return;
    if (currentStatus.status === "indexing" || currentStatus.status === "pending") {
      seenLiveIndexing.current = true;
    }
  }, [currentStatus]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async (consecutiveFailures = 0) => {
      const result = await pollStatus(contractId);
      if (cancelled) return;
      if (result.status === null) {
        if (result.retry) {
          const nextFailureCount = consecutiveFailures + 1;
          timer = setTimeout(
            () => void tick(nextFailureCount),
            getStatusRetryDelay(nextFailureCount),
          );
        }
        return;
      }
      if (!isTerminalStatus(result.status)) {
        timer = setTimeout(() => void tick(), STATUS_POLL_INTERVAL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [contractId, pollStatus]);

  return { status: currentStatus, error, setError, seenLiveIndexing };
}
