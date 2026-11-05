"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRequestError, deleteContract, downloadContractReport } from "@/lib/api";
import { clearLastContractId } from "@/lib/contract-storage";

export function useContractActions(contractId: number) {
  const router = useRouter();
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleDownloadPdf = async () => {
    setPdfBusy(true);
    setPdfError(null);
    try {
      await downloadContractReport(contractId);
    } catch (err) {
      setPdfError(err instanceof ApiRequestError ? err.message : "Не удалось скачать PDF");
    } finally {
      setPdfBusy(false);
    }
  };

  const handleDeleteContract = async () => {
    if (!window.confirm("Удалить этот контракт безвозвратно?")) {
      return;
    }
    setDeleting(true);
    try {
      await deleteContract(contractId);
      clearLastContractId();
      router.push("/history");
    } catch (err) {
      setActionError(err instanceof ApiRequestError ? err.message : "Не удалось удалить контракт");
      setDeleting(false);
    }
  };

  return {
    pdfBusy,
    pdfError,
    deleting,
    actionError,
    handleDownloadPdf,
    handleDeleteContract,
  };
}
