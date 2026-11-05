import { SUPPORTED_CHAINS } from "@/types/contract";

export function truncateAddress(address: string, compact = false): string {
  if (address.length <= 14) return address;
  if (compact) return `${address.slice(0, 6)}…${address.slice(-4)}`;
  return `${address.slice(0, 8)}…${address.slice(-6)}`;
}

export function chainName(chainId: number): string {
  if (chainId === 0) return "Upload";
  return SUPPORTED_CHAINS.find((c) => c.id === chainId)?.name ?? `chain ${chainId}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU");
}
