"use client";

import { ArrowUp, LoaderCircle } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useContractChat, type ChatUiMessage } from "@/hooks/use-contract-chat";
import { NO_CONTEXT_ANSWER } from "@/lib/chat";
import { isCodeCitationPath } from "@/lib/findings-filter";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types/contract";

const SUGGESTIONS = [
  "Есть ли reentrancy в функциях вывода?",
  "Кто может вызывать mint и burn?",
  "Какие права есть у owner?",
];

export function ContractChatPanel({
  contractId,
  emptyIndex = false,
  onSelectCitation,
}: {
  contractId: number;
  emptyIndex?: boolean;
  onSelectCitation?: (citation: Citation) => void;
}) {
  const {
    question,
    setQuestion,
    messages,
    loadingHistory,
    sending,
    error,
    inputRef,
    resizeInput,
    send,
  } = useContractChat(contractId, emptyIndex);

  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return;
    const isCoarse = window.matchMedia("(pointer: coarse)").matches;
    if (isCoarse) return;
    e.preventDefault();
    void send(question);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-4 sm:py-6">
        <div className="mx-auto flex min-h-full max-w-3xl flex-col gap-4 sm:gap-6">
          {loadingHistory ? (
            <div className="flex flex-1 items-center justify-center py-10 text-sm text-slate-400">
              Загрузка истории…
            </div>
          ) : emptyIndex && messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-6 text-center sm:py-10">
              <h2 className="text-lg font-semibold sm:text-xl">Нет индекса для чата</h2>
              <p className="max-w-md text-sm text-slate-400">{NO_CONTEXT_ANSWER}</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-5 py-6 text-center sm:gap-6 sm:py-10">
              <div>
                <h2 className="text-lg font-semibold sm:text-xl">Спросите про этот контракт</h2>
                <p className="mt-2 max-w-md text-sm text-slate-400">
                  Ответ строится только по проиндексированному коду.
                </p>
              </div>
              <div className="flex w-full flex-col gap-2">
                {SUGGESTIONS.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => void send(item)}
                    className="rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3 text-left text-sm text-slate-200 transition-colors hover:border-blue-500/50 hover:bg-slate-900"
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                streaming={sending && message.role === "assistant" && !message.error}
                onSelectCitation={onSelectCitation}
              />
            ))
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-slate-800 bg-slate-950 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 sm:px-4 sm:py-4">
        <div className="mx-auto max-w-3xl">
          {error ? (
            <p className="mb-3 rounded-lg bg-red-950/40 px-3 py-2 text-sm text-red-400">{error}</p>
          ) : null}
          <div className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-slate-900 px-2 py-2">
            <Textarea
              ref={inputRef}
              value={question}
              onChange={(e) => {
                setQuestion(e.target.value);
                resizeInput();
              }}
              onKeyDown={handleKeyDown}
              placeholder={emptyIndex ? "Индекс пуст — вопрос задать нельзя" : "Вопрос по контракту…"}
              disabled={sending || loadingHistory || emptyIndex}
              rows={1}
              className="max-h-32 flex-1 overflow-y-auto sm:max-h-40"
            />
            <Button
              type="button"
              size="icon"
              className="mb-0.5 size-11 shrink-0 sm:size-9"
              disabled={sending || loadingHistory || emptyIndex || !question.trim()}
              onClick={() => void send(question)}
              aria-label="Отправить"
            >
              {sending ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}
            </Button>
          </div>
          <p className="mt-2 hidden text-center text-[11px] text-slate-500 sm:block">
            Enter — отправить, Shift+Enter — новая строка
          </p>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  streaming,
  onSelectCitation,
}: {
  message: ChatUiMessage;
  streaming: boolean;
  onSelectCitation?: (citation: Citation) => void;
}) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[92%] rounded-2xl px-3 py-2.5 text-sm leading-relaxed sm:max-w-[85%] sm:px-4 sm:py-3",
          isUser ? "bg-blue-600 text-white" : "bg-slate-800 text-slate-100",
          message.error ? "border border-red-500/40" : "",
        )}
      >
        {!isUser ? (
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
            ChainWise{message.cached ? " · из кеша" : ""}
          </p>
        ) : null}
        <p className="whitespace-pre-wrap break-words">
          {message.content || (streaming && !message.content ? "Печатает…" : "")}
          {streaming && message.content ? (
            <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-current align-middle" />
          ) : null}
        </p>
        {message.citations.length > 0 && !streaming ? (
          <CitationList citations={message.citations} onSelectCitation={onSelectCitation} />
        ) : null}
      </div>
    </div>
  );
}

function CitationList({
  citations,
  onSelectCitation,
}: {
  citations: Citation[];
  onSelectCitation?: (citation: Citation) => void;
}) {
  return (
    <details className="mt-3 border-t border-slate-700/80 pt-2">
      <summary className="cursor-pointer select-none text-xs text-slate-400 hover:text-slate-200">
        Источники · {citations.length}
      </summary>
      <ul className="mt-2 space-y-1">
        {citations.map((citation, index) => (
          <CitationItem
            key={`${citation.file_path}-${citation.start_line}-${index}`}
            citation={citation}
            onSelect={
              onSelectCitation && isCodeCitationPath(citation.file_path)
                ? () => onSelectCitation(citation)
                : undefined
            }
          />
        ))}
      </ul>
    </details>
  );
}

function CitationItem({
  citation,
  onSelect,
}: {
  citation: Citation;
  onSelect?: () => void;
}) {
  const label = `${citation.file_path}:${citation.start_line}–${citation.end_line}`;
  const detail = citation.function_name || label;
  return (
    <li className="rounded-md bg-slate-950/80 px-2 py-1.5">
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          className="block w-full truncate text-left font-mono text-[11px] text-blue-300 hover:text-blue-200 hover:underline"
        >
          {label}
        </button>
      ) : (
        <p className="truncate font-mono text-[11px] text-slate-400">{label}</p>
      )}
      <p className="mt-0.5 truncate text-[10px] text-slate-500">{detail}</p>
    </li>
  );
}
