"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiRequestError, fetchContractFindings } from "@/lib/api";
import {
  CHECK_FAMILIES,
  IMPACT_FILTERS,
  findingMatchesFilters,
  type CheckFamily,
  type ImpactFilter,
} from "@/lib/findings-filter";
import { cn } from "@/lib/utils";
import type { Finding } from "@/types/contract";

function impactVariant(impact: string): "destructive" | "warning" | "secondary" {
  const value = impact.toLowerCase();
  if (value === "high") return "destructive";
  if (value === "medium") return "warning";
  return "secondary";
}

function toggleValue<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export function ContractFindingsList({
  contractId,
  selected,
  onSelectFinding,
}: {
  contractId: number;
  selected?: Finding | null;
  onSelectFinding?: (finding: Finding) => void;
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [impacts, setImpacts] = useState<Set<ImpactFilter>>(new Set());
  const [families, setFamilies] = useState<Set<CheckFamily>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setFindings(null);
    setError(null);
    void fetchContractFindings(contractId)
      .then((data) => {
        if (!cancelled) setFindings(data.findings);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiRequestError ? err.message : "Не удалось загрузить findings");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [contractId]);

  const visible = useMemo(() => {
    if (!findings) return [];
    return findings.filter((item) => findingMatchesFilters(item, impacts, families));
  }, [findings, impacts, families]);

  if (error) {
    return <p className="text-xs text-red-400">{error}</p>;
  }
  if (findings === null) {
    return <p className="text-xs text-slate-500">Findings…</p>;
  }
  if (findings.length === 0) {
    return <p className="text-xs text-slate-500">Slither не нашёл детекторов.</p>;
  }

  return (
    <div className="flex min-h-0 flex-col">
      <h3 className="mb-2 hidden text-sm font-medium text-slate-200 lg:block">Slither findings</h3>
      <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1 lg:flex-wrap">
        {IMPACT_FILTERS.map((item) => {
          const active = impacts.has(item);
          return (
            <Button
              key={item}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              aria-pressed={active}
              className="h-7 shrink-0 px-2 text-[11px]"
              onClick={() => setImpacts((prev) => toggleValue(prev, item))}
            >
              {item}
            </Button>
          );
        })}
        <span className="mx-1 h-7 w-px self-center bg-slate-800" />
        {CHECK_FAMILIES.map((item) => {
          const active = families.has(item.id);
          return (
            <Button
              key={item.id}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              aria-pressed={active}
              className="h-7 shrink-0 px-2 font-mono text-[11px]"
              onClick={() => setFamilies((prev) => toggleValue(prev, item.id))}
            >
              {item.label}
            </Button>
          );
        })}
      </div>
      {visible.length === 0 ? (
        <p className="text-xs text-slate-500">Нет findings по выбранным фильтрам.</p>
      ) : (
        <ul className="space-y-2">
          {visible.map((item, index) => {
            const active =
              selected != null &&
              selected.check === item.check &&
              selected.location === item.location &&
              selected.start_line === item.start_line;
            const clickable = Boolean(onSelectFinding);
            return (
              <li key={`${item.check}-${item.location}-${index}`}>
                <button
                  type="button"
                  disabled={!clickable}
                  onClick={() => onSelectFinding?.(item)}
                  className={cn(
                    "w-full rounded-lg border px-3 py-2 text-left text-xs",
                    active
                      ? "border-amber-500/50 bg-amber-950/30"
                      : "border-slate-800 bg-slate-950/60",
                    clickable ? "hover:border-blue-500/40" : "cursor-default",
                  )}
                >
                  <p className="flex flex-wrap items-center gap-2 font-mono text-slate-200">
                    {item.check}
                    {item.impact ? <Badge variant={impactVariant(item.impact)}>{item.impact}</Badge> : null}
                  </p>
                  {item.location ? <p className="mt-0.5 font-mono text-slate-500">{item.location}</p> : null}
                  {item.description ? <p className="mt-1 text-slate-400">{item.description}</p> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
