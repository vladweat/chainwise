const LAST_CONTRACT_KEY = "chainwise_last_contract_id";

export function getLastContractId(): number | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(LAST_CONTRACT_KEY);
  if (!raw) return null;
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export function setLastContractId(id: number): void {
  localStorage.setItem(LAST_CONTRACT_KEY, String(id));
}

export function clearLastContractId(): void {
  localStorage.removeItem(LAST_CONTRACT_KEY);
}
