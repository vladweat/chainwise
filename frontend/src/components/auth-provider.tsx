"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ApiRequestError,
  deleteAccount as apiDeleteAccount,
  fetchMe,
  login,
  loginWithGoogle,
  logout as apiLogout,
  register,
} from "@/lib/api";
import { clearLegacyAuth } from "@/lib/auth-storage";
import { clearLastContractId } from "@/lib/contract-storage";
import type { UserResponse } from "@/types/auth";

interface AuthContextValue {
  user: UserResponse | null;
  loading: boolean;
  error: string | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  loginGoogle: (idToken: string) => Promise<void>;
  logout: () => Promise<void>;
  deleteAccount: () => Promise<void>;
  refreshUser: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshUser = useCallback(async () => {
    try {
      const me = await fetchMe();
      setUser(me);
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 401) {
        setUser(null);
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    clearLegacyAuth();

    const boot = async () => {
      for (let attempt = 0; attempt < 8; attempt += 1) {
        if (cancelled) return;
        try {
          const me = await fetchMe();
          if (!cancelled) setUser(me);
          return;
        } catch (err) {
          if (err instanceof ApiRequestError && err.status === 401) {
            if (!cancelled) setUser(null);
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
    };

    boot().finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogin = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      await login({ email, password });
      await refreshUser();
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Login failed";
      setError(message);
      throw err;
    }
  }, [refreshUser]);

  const handleRegister = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      await register({ email, password });
      await refreshUser();
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Registration failed";
      setError(message);
      throw err;
    }
  }, [refreshUser]);

  const handleGoogleLogin = useCallback(async (idToken: string) => {
    setError(null);
    try {
      await loginWithGoogle(idToken);
      await refreshUser();
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Google login failed";
      setError(message);
      throw err;
    }
  }, [refreshUser]);

  const handleLogout = useCallback(async () => {
    await apiLogout();
    clearLastContractId();
    setUser(null);
    setError(null);
  }, []);

  const handleDeleteAccount = useCallback(async () => {
    setError(null);
    try {
      await apiDeleteAccount();
      clearLastContractId();
      setUser(null);
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "Delete failed";
      setError(message);
      throw err;
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      isAuthenticated: user !== null,
      login: handleLogin,
      register: handleRegister,
      loginGoogle: handleGoogleLogin,
      logout: handleLogout,
      deleteAccount: handleDeleteAccount,
      refreshUser,
      clearError: () => setError(null),
    }),
    [
      user,
      loading,
      error,
      handleLogin,
      handleRegister,
      handleGoogleLogin,
      handleLogout,
      handleDeleteAccount,
      refreshUser,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
