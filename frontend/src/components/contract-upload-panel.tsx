"use client";

import { FileCode2, Globe } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { analyzeContract, ApiRequestError, uploadContract } from "@/lib/api";
import { setLastContractId } from "@/lib/contract-storage";
import { isCompletedStatus } from "@/lib/contract-status";
import { cn } from "@/lib/utils";
import { SUPPORTED_CHAINS } from "@/types/contract";

type UploadMode = "address" | "file";

export function ContractUploadPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<UploadMode>("address");
  const [address, setAddress] = useState("");
  const [chainId, setChainId] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function pickFile(next: File | null) {
    if (!next) {
      setFile(null);
      return;
    }
    const lower = next.name.toLowerCase();
    if (!lower.endsWith(".sol") && !lower.endsWith(".zip")) {
      setError("Нужен файл .sol или архив .zip с исходниками");
      return;
    }
    setError(null);
    setFile(next);
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files[0] ?? null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (mode === "file" && !file) {
      setError("Выберите файл .sol или .zip");
      return;
    }

    setSubmitting(true);
    try {
      const result =
        mode === "address"
          ? await analyzeContract(address.trim(), chainId)
          : await uploadContract(file as File, 0);
      setLastContractId(result.contract_id);
      const existing = result.reused || isCompletedStatus(result.status);
      router.push(
        existing
          ? `/contracts/${result.contract_id}?existing=1`
          : `/contracts/${result.contract_id}`,
      );
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Не удалось начать анализ";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 space-y-2">
        <h2 className="text-xl font-semibold tracking-tight sm:text-2xl">Новый анализ</h2>
        <p className="text-sm text-slate-400">
          Контракты привязаны только к вашему аккаунту. Укажите адрес в блокчейне или загрузите
          исходники — ChainWise скомпилирует код, прогонит Slither и откроет чат. Действуют
          дневные лимиты analyze/chat (см. сообщение при превышении).
        </p>
      </div>

      <div className="mb-6 grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => {
            setMode("address");
            setError(null);
          }}
          className={cn(
            "flex items-start gap-3 rounded-2xl border p-4 text-left transition-colors",
            mode === "address"
              ? "border-blue-500 bg-blue-950/30"
              : "border-slate-800 bg-slate-900/40 hover:border-slate-600",
          )}
        >
          <Globe className="mt-0.5 size-5 shrink-0 text-blue-400" />
          <span>
            <span className="block font-medium text-slate-100">По адресу</span>
            <span className="mt-1 block text-xs text-slate-400">
              Ethereum, BSC или Abstract — код подтянется с эксплорера
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={() => {
            setMode("file");
            setError(null);
          }}
          className={cn(
            "flex items-start gap-3 rounded-2xl border p-4 text-left transition-colors",
            mode === "file"
              ? "border-blue-500 bg-blue-950/30"
              : "border-slate-800 bg-slate-900/40 hover:border-slate-600",
          )}
        >
          <FileCode2 className="mt-0.5 size-5 shrink-0 text-blue-400" />
          <span>
            <span className="block font-medium text-slate-100">Файл</span>
            <span className="mt-1 block text-xs text-slate-400">
              Локальный .sol или zip с несколькими файлами
            </span>
          </span>
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        {mode === "address" ? (
          <>
            <div>
              <Label htmlFor="address">Адрес</Label>
              <Input
                id="address"
                type="text"
                required
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="0x…"
                className="h-11 font-mono"
                autoComplete="off"
              />
            </div>
            <div>
              <Label>Сеть</Label>
              <div className="flex flex-wrap gap-2">
                {SUPPORTED_CHAINS.map((chain) => (
                  <Button
                    key={chain.id}
                    type="button"
                    size="sm"
                    variant={chainId === chain.id ? "default" : "secondary"}
                    onClick={() => setChainId(chain.id)}
                  >
                    {chain.name}
                  </Button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div
            role="button"
            tabIndex={0}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") fileInputRef.current?.click();
            }}
            className={cn(
              "flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-8 text-center transition-colors sm:min-h-48 sm:px-6 sm:py-10",
              dragOver
                ? "border-blue-500 bg-blue-950/20"
                : "border-slate-700 bg-slate-900/40 hover:border-slate-500",
            )}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".sol,.zip"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            <FileCode2 className="mb-3 size-8 text-slate-500" />
            {file ? (
                <>
                  <p className="break-all text-sm font-medium text-slate-100">{file.name}</p>
                  <p className="mt-1 text-xs text-slate-500">Нажмите, чтобы выбрать другой файл</p>
                </>
              ) : (
                <>
                  <p className="text-sm text-slate-200">Нажмите, чтобы выбрать .sol или .zip</p>
                  <p className="mt-1 hidden text-xs text-slate-500 sm:block">
                    или перетащите файл сюда
                  </p>
                </>
              )}
          </div>
        )}

        {error ? (
          <p className="rounded-lg bg-red-950/40 px-3 py-2 text-sm text-red-400">{error}</p>
        ) : null}

        <Button type="submit" size="lg" disabled={submitting} className="w-full sm:w-auto">
          {submitting ? "Запускаем анализ…" : "Начать анализ"}
        </Button>
      </form>
    </div>
  );
}
