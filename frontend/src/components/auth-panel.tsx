"use client";

import { FormEvent, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { GoogleSignInButton } from "@/components/google-sign-in-button";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";

type AuthMode = "login" | "register";

export function AuthPanel() {
  const { login, register, error, clearError } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    clearError();
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
      setPassword("");
    } catch {
      // error handled in context
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next: AuthMode) {
    setMode(next);
    clearError();
  }

  return (
    <Panel title="Вход" subtitle="Email и пароль или Google">
      <div className="mb-4 grid w-full grid-cols-2 gap-2">
        {(["login", "register"] as const).map((tab) => (
          <Button
            key={tab}
            type="button"
            variant={mode === tab ? "default" : "secondary"}
            size="sm"
            className="w-full"
            onClick={() => switchMode(tab)}
          >
            {tab === "login" ? "Вход" : "Регистрация"}
          </Button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
          />
        </div>

        <div>
          <Label htmlFor="password">
            Пароль {mode === "register" ? "(мин. 8 символов)" : ""}
          </Label>
          <Input
            id="password"
            type="password"
            required
            minLength={mode === "register" ? 8 : 1}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </div>

        {error ? (
          <p className="rounded-lg bg-red-950/40 px-3 py-2 text-sm text-red-400">{error}</p>
        ) : null}

        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "…" : mode === "login" ? "Войти" : "Зарегистрироваться"}
        </Button>
      </form>

      <div className="my-4 flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-xs text-slate-500">или</span>
        <Separator className="flex-1" />
      </div>

      <GoogleSignInButton />
    </Panel>
  );
}
