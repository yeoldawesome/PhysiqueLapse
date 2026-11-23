export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

let redirecting = false;

/** Server mode: the session ended (logged out elsewhere, expired). Go to the login page once. */
export function redirectToLogin() {
  if (redirecting || window.location.pathname === "/login") return;
  redirecting = true;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

type Params =Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown; params?: Params } = {},
): Promise<T> {
  const { json, params, ...rest } = init;
  const headers = new Headers(rest.headers);
  let body = rest.body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  const res = await fetch(`/api${path}${qs(params)}`, { ...rest, headers, body });
  if (res.status === 401 && !path.startsWith("/auth/")) redirectToLogin();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string, params?: Params) => api<T>(path, { params });
export const post = <T>(path: string, json?: unknown, params?: Params) => api<T>(path, { method: "POST", json, params });
export const put = <T>(path: string, json?: unknown) => api<T>(path, { method: "PUT", json });
export const patch = <T>(path: string, json?: unknown) => api<T>(path, { method: "PATCH", json });
export const del = <T>(path: string, params?: Params) => api<T>(path, { method: "DELETE", params });
