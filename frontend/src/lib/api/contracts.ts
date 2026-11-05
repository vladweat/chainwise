import type {
  AnalyzeResponse,
  ContractSourceResponse,
  ContractStatusResponse,
  FindingsResponse,
} from "@/types/contract";
import type { HealthResponse } from "@/types/health";

import { API_URL, ApiRequestError, apiFetch, parseError } from "./client";

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export async function deleteContract(contractId: number): Promise<void> {
  await apiFetch<undefined>(`/api/v1/contracts/${contractId}`, { method: "DELETE" }, true);
}

export async function analyzeContract(
  address: string,
  chainId: number,
): Promise<AnalyzeResponse> {
  return apiFetch<AnalyzeResponse>(
    "/api/v1/contracts/analyze",
    {
      method: "POST",
      body: JSON.stringify({ address, chain_id: chainId }),
    },
    true,
  );
}

export async function uploadContract(file: File, chainId = 0): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("chain_id", String(chainId));
  return apiFetch<AnalyzeResponse>(
    "/api/v1/contracts/analyze/upload",
    {
      method: "POST",
      body: form,
    },
    true,
  );
}

export async function fetchContractStatus(
  contractId: number,
): Promise<ContractStatusResponse> {
  return apiFetch<ContractStatusResponse>(
    `/api/v1/contracts/${contractId}/status`,
    {},
    true,
  );
}

export async function fetchContractFindings(contractId: number): Promise<FindingsResponse> {
  return apiFetch<FindingsResponse>(`/api/v1/contracts/${contractId}/findings`, {}, true);
}

export async function fetchContractSource(contractId: number): Promise<ContractSourceResponse> {
  return apiFetch<ContractSourceResponse>(`/api/v1/contracts/${contractId}/source`, {}, true);
}

export async function downloadContractReport(contractId: number): Promise<void> {
  const response = await fetch(`${API_URL}/api/v1/contracts/${contractId}/report`, {
    headers: { "X-Requested-With": "ChainWise" },
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiRequestError(await parseError(response), response.status);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `chainwise-report-${contractId}.pdf`;

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function fetchContracts(): Promise<ContractStatusResponse[]> {
  return apiFetch<ContractStatusResponse[]>("/api/v1/contracts", {}, true);
}
