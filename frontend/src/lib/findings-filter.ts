import type { Finding } from "@/types/contract";

export const IMPACT_FILTERS = ["High", "Medium", "Low"] as const;
export type ImpactFilter = (typeof IMPACT_FILTERS)[number];

export const CHECK_FAMILIES = [
  { id: "reentrancy", label: "reentrancy" },
  { id: "delegatecall", label: "delegatecall" },
  { id: "selfdestruct", label: "selfdestruct" },
] as const;

export type CheckFamily = (typeof CHECK_FAMILIES)[number]["id"];

export function checkMatchesFamily(check: string, family: CheckFamily): boolean {
  const value = check.toLowerCase();
  if (family === "reentrancy") return value.includes("reentrancy");
  if (family === "delegatecall") return value.includes("delegatecall");
  return value.includes("selfdestruct") || value.includes("suicidal");
}

export function findingMatchesFilters(
  finding: Finding,
  impacts: ReadonlySet<ImpactFilter>,
  families: ReadonlySet<CheckFamily>,
): boolean {
  if (impacts.size > 0) {
    const impact = finding.impact.trim().toLowerCase();
    const ok = [...impacts].some((item) => item.toLowerCase() === impact);
    if (!ok) return false;
  }
  if (families.size > 0) {
    const ok = [...families].some((family) => checkMatchesFamily(finding.check, family));
    if (!ok) return false;
  }
  return true;
}

export function resolveSourcePath(paths: string[], wanted: string | null | undefined): string | null {
  if (paths.length === 0) return null;
  if (!wanted) return paths[0];
  const normalized = wanted.replace(/\\/g, "/");
  if (paths.includes(normalized)) return normalized;
  const base = normalized.split("/").pop();
  const byBase = paths.find((path) => path.split("/").pop() === base);
  return byBase ?? paths[0];
}

export function isCodeCitationPath(filePath: string | null | undefined): boolean {
  if (!filePath) return false;
  return filePath.replace(/\\/g, "/").toLowerCase().endsWith(".sol");
}

export interface SourceTarget {
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
}
