"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";

export function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (loading || isAuthenticated) return;
    const next = pathname && pathname !== "/login" ? `?next=${encodeURIComponent(pathname)}` : "";
    router.replace(`/login${next}`);
  }, [isAuthenticated, loading, pathname, router]);

  if (loading) {
    return <p className="px-6 py-10 text-center text-sm text-slate-400">Загрузка…</p>;
  }
  if (!isAuthenticated) {
    return <p className="px-6 py-10 text-center text-sm text-slate-400">Перенаправление на вход…</p>;
  }
  return <div className="flex min-h-0 flex-1 flex-col">{children}</div>;
}
