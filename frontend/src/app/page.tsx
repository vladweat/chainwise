"use client";

import Link from "next/link";

import { useAuth } from "@/components/auth-provider";
import { HealthStatus } from "@/components/health-status";
import { Button } from "@/components/ui/button";

export default function HomePage() {
  const { isAuthenticated, loading } = useAuth();

  return (
    <main className="w-full flex-1 overflow-y-auto px-4 py-8 sm:px-6 sm:py-10">
      <div className="mx-auto max-w-5xl space-y-8">
        <section className="space-y-3">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            AI-ассистент для аудита Solidity
          </h2>
          <p className="max-w-2xl text-sm text-slate-400">
            Загрузите контракт по адресу или файлом, дождитесь индексации (solc, Slither, Qdrant)
            и спросите про безопасность в чате с цитатами из кода.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
            {loading ? null : isAuthenticated ? (
              <>
                <Button asChild className="w-full sm:w-auto">
                  <Link href="/analyze">Новый анализ</Link>
                </Button>
                <Button asChild variant="outline" className="w-full sm:w-auto">
                  <Link href="/history">История</Link>
                </Button>
              </>
            ) : (
              <Button asChild className="w-full sm:w-auto">
                <Link href="/login">Войти</Link>
              </Button>
            )}
          </div>
        </section>
        <HealthStatus />
      </div>
    </main>
  );
}
