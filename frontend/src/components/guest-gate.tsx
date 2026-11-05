"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";

function GuestRedirect({ children }: { children: ReactNode }) {
  const { isAuthenticated, loading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (loading || !isAuthenticated) return;
    const next = searchParams.get("next");
    router.replace(next && next.startsWith("/") ? next : "/analyze");
  }, [isAuthenticated, loading, router, searchParams]);

  if (loading) {
    return <p className="px-6 py-10 text-center text-sm text-slate-400">Загрузка…</p>;
  }
  if (isAuthenticated) {
    return <p className="px-6 py-10 text-center text-sm text-slate-400">Перенаправление…</p>;
  }
  return <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>;
}

export function GuestGate({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<p className="px-6 py-10 text-center text-sm text-slate-400">Загрузка…</p>}>
      <GuestRedirect>{children}</GuestRedirect>
    </Suspense>
  );
}
