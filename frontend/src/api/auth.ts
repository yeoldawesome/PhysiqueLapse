import { useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post } from "./client";

export interface AccountUser {
  id: number;
  email: string;
  name: string;
  is_admin: boolean;
}

export interface AuthStatus {
  mode: "desktop" | "server";
  auth_required: boolean;
  logged_in: boolean;
  user: AccountUser | null;
  path_import: boolean;
  email_reset: boolean;
  setup_needed?: boolean;
}

export function useAuthStatus() {
  return useQuery({ queryKey: ["auth"], queryFn: () => get<AuthStatus>("/auth/status"), staleTime: 60_000 });
}

/** True when running as the home-server app (as opposed to the local desktop app). */
export function useServerMode(): boolean {
  return useAuthStatus().data?.mode === "server";
}

export const login = (email: string, password: string) => post<{ ok: boolean }>("/auth/login", { email, password });
export const signup = (invite: string, email: string, name: string, password: string) =>
  post<{ ok: boolean }>("/auth/signup", { invite, email, name, password });
export const forgot = (email: string) => post<{ ok: boolean; message: string }>("/auth/forgot", { email });
export const resetPassword = (token: string, password: string) => post<{ ok: boolean }>("/auth/reset", { token, password });
export const changePassword = (current: string, next: string) => post<{ ok: boolean }>("/auth/password", { current, new: next });
export const deleteAccount = (password: string) => post<{ ok: boolean }>("/auth/delete-account", { password });

export function useUsage(enabled: boolean) {
  return useQuery({
    queryKey: ["auth", "usage"],
    queryFn: () => get<{ used_mb: number | null; quota_mb: number | null }>("/auth/usage"),
    enabled,
  });
}

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

// ---- admin

export interface Invite {
  id: number;
  note: string;
  uses_left: number;
  expires_at: string | null;
  revoked: boolean;
  usable: boolean;
  created_at: string;
}

export interface AdminUser extends AccountUser {
  disabled: boolean;
  created_at: string;
  quota_mb: number;
  used_mb: number;
}

export const admin = {
  invites: () => get<Invite[]>("/admin/invites"),
  createInvite: (note: string, uses: number, days: number | null) =>
    post<{ id: number; code: string; link: string }>("/admin/invites", { note, uses, days }),
  revokeInvite: (id: number) => del(`/admin/invites/${id}`),
  users: () => get<AdminUser[]>("/admin/users"),
  patchUser: (id: number, body: { disabled?: boolean; quota_mb?: number | null }) => patch(`/admin/users/${id}`, body),
  resetLink: (id: number) => post<{ link: string; emailed: boolean }>(`/admin/users/${id}/reset-link`),
  deleteUser: (id: number) => del(`/admin/users/${id}`),
};
