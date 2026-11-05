"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";

import { AuthGate } from "@/components/auth-gate";
import { ContractWorkspace } from "@/components/contract-workspace";

export default function ContractPage() {
  const params = useParams<{ id: string }>();
  const contractId = Number(params.id);

  return (
    <AuthGate>
      <main className="flex min-h-0 flex-1 flex-col">
        {!Number.isInteger(contractId) || contractId <= 0 ? (
          <p className="px-4 py-8 text-sm text-red-400">Некорректный идентификатор контракта</p>
        ) : (
          <Suspense fallback={<p className="px-4 py-8 text-sm text-slate-400">Загрузка…</p>}>
            <ContractWorkspace contractId={contractId} />
          </Suspense>
        )}
      </main>
    </AuthGate>
  );
}
