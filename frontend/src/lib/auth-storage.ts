const LEGACY_TOKEN_KEY = "chainwise_token";

/** Drop JWT left in localStorage from pre-cookie auth. */
export function clearLegacyAuth(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LEGACY_TOKEN_KEY);
}
