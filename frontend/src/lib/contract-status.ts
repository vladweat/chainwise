/** Mirrors backend app.models.contract_status */

export const CONTRACT_STATUS = {
  PENDING: "pending",
  INDEXING: "indexing",
  READY: "ready",
  LOADED: "loaded",
  FAILED: "failed",
} as const;

export type ContractLoadStatus =
  (typeof CONTRACT_STATUS)[keyof typeof CONTRACT_STATUS];

export const COMPLETED_STATUSES = new Set<string>([
  CONTRACT_STATUS.READY,
  CONTRACT_STATUS.LOADED,
]);

export const TERMINAL_STATUSES = new Set<string>([
  CONTRACT_STATUS.READY,
  CONTRACT_STATUS.LOADED,
  CONTRACT_STATUS.FAILED,
]);

export function isCompletedStatus(status: string): boolean {
  return COMPLETED_STATUSES.has(status);
}

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function isIndexingStatus(status: string): boolean {
  return status === CONTRACT_STATUS.INDEXING || status === CONTRACT_STATUS.PENDING;
}

export function isReadyStatus(status: string): boolean {
  return status === CONTRACT_STATUS.READY;
}

/** Mirrors backend IndexingStep */
export const INDEXING_STEPS = ["compile", "slither", "embed", "score", "done"] as const;
export type IndexingStepId = (typeof INDEXING_STEPS)[number];
