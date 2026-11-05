"use client";

import { motion } from "framer-motion";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchHealth } from "@/lib/api";
import type { HealthResponse } from "@/types/health";

const CHECKS = ["postgres", "redis", "qdrant", "slither"] as const;

function StatusBadge({ ok }: { ok: boolean }) {
  return <Badge variant={ok ? "success" : "destructive"}>{ok ? "ok" : "error"}</Badge>;
}

export function HealthStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card>
        <CardContent className="text-sm text-slate-400">Проверка API…</CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-red-900/50 bg-red-950/30">
        <CardContent className="text-sm text-red-400">
          Не удалось подключиться к API: {error}
        </CardContent>
      </Card>
    );
  }

  if (!health) return null;

  const allOk = health.status === "ok";

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium text-slate-400">Backend status</CardTitle>
          <StatusBadge ok={allOk} />
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-lg font-semibold capitalize">{health.status}</p>
          <ul className="space-y-2">
            {CHECKS.map((key) => (
              <li key={key} className="flex items-center justify-between text-sm">
                <span className="text-slate-400">{key}</span>
                <StatusBadge ok={health[key] === "ok"} />
              </li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-slate-500">env: {health.environment}</p>
        </CardContent>
      </Card>
    </motion.div>
  );
}
