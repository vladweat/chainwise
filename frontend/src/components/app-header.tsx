"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { LogOut, Trash2 } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/analyze", label: "Анализ" },
  { href: "/history", label: "История" },
];

export function AppHeader() {
  const { isAuthenticated, user, logout, deleteAccount } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);

  async function handleDeleteAccount() {
    if (!window.confirm("Удалить аккаунт и все ваши контракты безвозвратно?")) {
      return;
    }
    setDeleting(true);
    try {
      await deleteAccount();
      router.push("/login");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <header className="shrink-0 border-b border-slate-800 bg-slate-950/80 pt-[env(safe-area-inset-top)] backdrop-blur">
      <div className="mx-auto flex min-h-12 max-w-6xl items-center justify-between gap-2 px-3 py-2 sm:h-14 sm:gap-4 sm:px-6 sm:py-0">
        <div className="flex min-w-0 items-center gap-2 sm:gap-6">
          <Link href="/" className="block shrink-0">
            <h1 className="text-base font-bold tracking-tight sm:text-lg">ChainWise</h1>
          </Link>
          {isAuthenticated ? (
            <nav className="flex items-center gap-0.5 sm:gap-1">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "rounded-lg px-2 py-1.5 text-xs transition-colors sm:px-3 sm:text-sm",
                    pathname === item.href || pathname.startsWith(`${item.href}/`)
                      ? "bg-slate-800 text-white"
                      : "text-slate-400 hover:text-slate-200",
                  )}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-2 text-sm">
          {isAuthenticated && user ? (
            <>
              <span className="hidden max-w-[180px] truncate text-slate-400 md:inline">
                {user.email}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleDeleteAccount}
                disabled={deleting}
                aria-label="Удалить аккаунт"
                title="Удалить аккаунт"
                className="text-slate-400 hover:text-red-300"
              >
                <Trash2 className="sm:hidden" />
                <span className="hidden sm:inline">
                  {deleting ? "Удаление…" : "Удалить аккаунт"}
                </span>
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={logout} aria-label="Выйти">
                <LogOut className="sm:hidden" />
                <span className="hidden sm:inline">Выйти</span>
              </Button>
            </>
          ) : pathname !== "/login" ? (
            <Button asChild variant="outline" size="sm">
              <Link href="/login">Войти</Link>
            </Button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
