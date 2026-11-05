"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";
import type { SourceFile } from "@/types/contract";

export interface SourceHighlight {
  path: string | null;
  startLine: number | null;
  endLine: number | null;
}

export function ContractSourceViewer({
  files,
  activePath,
  onPathChange,
  highlight,
}: {
  files: SourceFile[];
  activePath: string | null;
  onPathChange: (path: string) => void;
  highlight: SourceHighlight | null;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const file = files.find((item) => item.path === activePath) ?? files[0];
  const lines = file ? file.content.split("\n") : [];
  const sameFile = Boolean(file && highlight?.path && highlight.path === file.path);
  const start = sameFile ? highlight?.startLine ?? null : null;
  const end = sameFile ? (highlight?.endLine ?? start) : null;

  useEffect(() => {
    if (start == null) return;
    const el = scrollerRef.current?.querySelector(`[data-line="${start}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activePath, start, end, file?.content]);

  if (!file) {
    return <p className="px-3 py-4 text-xs text-slate-500">Исходник пуст.</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {files.length > 1 ? (
        <div className="shrink-0 border-b border-slate-800 px-2 py-1.5">
          <label className="sr-only" htmlFor="source-file">
            Файл исходника
          </label>
          <select
            id="source-file"
            value={file.path}
            onChange={(event) => onPathChange(event.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200"
          >
            {files.map((item) => (
              <option key={item.path} value={item.path}>
                {item.path}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <p className="shrink-0 truncate border-b border-slate-800 px-3 py-1.5 font-mono text-[11px] text-slate-500">
          {file.path}
        </p>
      )}
      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-auto">
        <pre className="min-w-max py-2 font-mono text-[11px] leading-5 text-slate-300">
          {lines.map((line, index) => {
            const n = index + 1;
            const marked = start != null && end != null && n >= start && n <= end;
            return (
              <div
                key={n}
                id={`src-L${n}`}
                data-line={n}
                className={cn(
                  "flex px-2",
                  marked ? "bg-amber-500/15 text-amber-50" : "hover:bg-slate-900/80",
                )}
              >
                <span className="w-10 shrink-0 select-none pr-3 text-right text-slate-600">{n}</span>
                <span className="whitespace-pre">{line || " "}</span>
              </div>
            );
          })}
        </pre>
      </div>
    </div>
  );
}
