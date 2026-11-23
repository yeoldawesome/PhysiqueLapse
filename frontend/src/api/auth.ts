import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "./client";

export interface AuthStatus {
  mode: "desktop" | "server";
  auth_required: boolean;
  password_set: boolean;
  logged_in: boolean;
}

export function useAuthStatus() {
  return useQuery({ queryKey: ["auth"], queryFn: () => get<AuthStatus>("/auth/status"), staleTime: 60_000 });
}

/** True when running as the home-server app (as opposed to the local desktop app). */
export function useServerMode(): boolean {
  return useAuthStatus().data?.mode === "server";
}

export const login = (password: string) => post<{ ok: boolean }>("/auth/login", { password });

export function useLogout() {
  const qc = useQueryClient();
  return async () => {
    try {
      await post("/auth/logout");
    } finally {
      qc.clear();
      window.location.assign("/login");
    }
  };
}
