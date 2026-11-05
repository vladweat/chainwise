import type {
  LoginRequest,
  RegisterRequest,
  TokenResponse,
  UserResponse,
} from "@/types/auth";

import { clearLegacyAuth } from "../auth-storage";
import { apiFetch } from "./client";

export async function register(data: RegisterRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function login(data: LoginRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function fetchMe(): Promise<UserResponse> {
  return apiFetch<UserResponse>("/api/v1/auth/me", {}, true);
}

export async function logout(): Promise<void> {
  clearLegacyAuth();
  try {
    await apiFetch<undefined>("/api/v1/auth/logout", { method: "POST" }, true);
  } catch {
    // cookie is cleared server-side when possible
  }
}

export async function deleteAccount(): Promise<void> {
  await apiFetch<undefined>("/api/v1/auth/me", { method: "DELETE" }, true);
  clearLegacyAuth();
}

export async function loginWithGoogle(idToken: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/api/v1/auth/google", {
    method: "POST",
    body: JSON.stringify({ id_token: idToken }),
  });
}
