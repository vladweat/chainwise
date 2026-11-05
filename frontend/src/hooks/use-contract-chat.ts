"use client";

import { useEffect, useRef, useState } from "react";

import { ApiRequestError, fetchContractChatHistory, streamContractChat } from "@/lib/api";
import { NO_CONTEXT_ANSWER } from "@/lib/chat";
import type { ChatStreamEvent, Citation } from "@/types/contract";

export interface ChatUiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  cached?: boolean;
  error?: boolean;
}

export function useContractChat(contractId: number, emptyIndex = false) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatUiMessage[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  function resizeInput() {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  useEffect(() => {
    setMessages([]);
    setError(null);
    setQuestion("");
    abortRef.current?.abort();

    let cancelled = false;
    setLoadingHistory(true);
    void fetchContractChatHistory(contractId)
      .then((history) => {
        if (cancelled) return;
        setMessages(
          history.map((item) => ({
            id: String(item.id),
            role: item.role,
            content: item.content,
            citations: item.citations ?? [],
            cached: item.cached,
          })),
        );
      })
      .catch((err) => {
        if (cancelled) return;
        const message =
          err instanceof ApiRequestError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Не удалось загрузить историю чата";
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });

    return () => {
      cancelled = true;
    };
  }, [contractId]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending || loadingHistory) return;

    if (emptyIndex) {
      const userMessage: ChatUiMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
        citations: [],
      };
      const assistantMessage: ChatUiMessage = {
        id: `a-${Date.now()}`,
        role: "assistant",
        content: NO_CONTEXT_ANSWER,
        citations: [],
      };
      setQuestion("");
      setError(null);
      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      requestAnimationFrame(() => {
        if (inputRef.current) {
          inputRef.current.style.height = "auto";
        }
      });
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const userMessage: ChatUiMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      content: trimmed,
      citations: [],
    };
    const assistantId = `a-${Date.now()}`;
    const assistantMessage: ChatUiMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      citations: [],
    };

    setQuestion("");
    setError(null);
    setSending(true);
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    requestAnimationFrame(() => {
      if (inputRef.current) {
        inputRef.current.style.height = "auto";
      }
    });

    const applyEvent = (event: ChatStreamEvent) => {
      setMessages((prev) =>
        prev.map((item) => {
          if (item.id !== assistantId) return item;
          if (event.type === "token") {
            return { ...item, content: item.content + event.content };
          }
          if (event.type === "citations") {
            return { ...item, citations: event.citations };
          }
          if (event.type === "meta") {
            return { ...item, cached: event.cached };
          }
          if (event.type === "error") {
            return { ...item, content: event.content, error: true };
          }
          return item;
        }),
      );
    };

    try {
      await streamContractChat(contractId, trimmed, applyEvent, controller.signal);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Не удалось получить ответ";
      setError(message);
      setMessages((prev) =>
        prev.map((item) =>
          item.id === assistantId
            ? { ...item, content: item.content || message, error: true }
            : item,
        ),
      );
    } finally {
      setSending(false);
    }
  }

  return {
    question,
    setQuestion,
    messages,
    loadingHistory,
    sending,
    error,
    inputRef,
    resizeInput,
    send,
  };
}
