export interface AnalyzeResponse {
  task_id: string;
  contract_id: number;
  status: string;
  reused?: boolean;
}

export interface ContractStatusResponse {
  contract_id: number;
  address: string;
  chain_id: number;
  status: string;
  indexing_step: string | null;
  risk_score: string | null;
  risk_model: "xgboost" | "heuristic" | string | null;
  features_version: string | null;
  compiler_version: string | null;
  name: string | null;
  chunk_count: number | null;
  last_error: string | null;
  created_at: string | null;
}

export interface Finding {
  check: string;
  impact: string;
  confidence: string;
  location: string;
  description: string;
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
  function_name?: string | null;
}

export interface FindingsResponse {
  contract_id: number;
  findings: Finding[];
}

export interface SourceFile {
  path: string;
  content: string;
}

export interface ContractSourceResponse {
  contract_id: number;
  files: SourceFile[];
}

export interface Citation {
  file_path: string;
  function_name: string;
  start_line: number;
  end_line: number;
  content: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  cached: boolean;
  created_at: string | null;
}

export type ChatStreamEvent =
  | { type: "token"; content: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "meta"; cached: boolean; tokens_used: number | null; cost_usd: number | null }
  | { type: "error"; content: string }
  | { type: "done" };

export type { ContractLoadStatus } from "@/lib/contract-status";
export { CONTRACT_STATUS } from "@/lib/contract-status";

export interface ChainOption {
  id: number;
  name: string;
}

export const SUPPORTED_CHAINS: ChainOption[] = [
  { id: 1, name: "Ethereum" },
  { id: 56, name: "BSC" },
  { id: 11124, name: "Abstract" },
];
