import type { ChatMessage, ChatStreamEvent } from "@/types/contract";

import { API_URL, ApiRequestError, apiFetch, parseError } from "./client";

export async function fetchContractChatHistory(contractId: number): Promise<ChatMessage[]> {
  return apiFetch<ChatMessage[]>(`/api/v1/contracts/${contractId}/chat`, {}, true);
}

export async function streamContractChat(
  contractId: number,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({
    "Content-Type": "application/json",
    "X-Requested-With": "ChainWise",
  });
  const response = await fetch(`${API_URL}/api/v1/contracts/${contractId}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, stream: true }),
    cache: "no-store",
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    const message = await parseError(response);
    if (response.status === 429) {
      const retry = response.headers.get("Retry-After");
      const suffix = retry ? `. Retry in ${retry}s` : "";
      throw new ApiRequestError(`${message}${suffix}`, response.status);
    }
    throw new ApiRequestError(message, response.status);
  }
  if (!response.body) {
    throw new ApiRequestError("Empty chat stream", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleBlock = (block: string) => {
    const line = block.split("\n").find((part) => part.startsWith("data: "));
    if (!line) return;
    const data = line.slice(6);
    if (data === "[DONE]") {
      onEvent({ type: "done" });
      return;
    }
    try {
      const parsed = JSON.parse(data) as ChatStreamEvent;
      onEvent(parsed);
    } catch {
      // ignore malformed SSE chunks
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) handleBlock(part);
  }
  if (buffer.trim()) handleBlock(buffer);
}
