import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { login, useAuthStatus } from "../api/auth";
import { Spinner } from "../components/ui";

/** Only same-site paths, so ?next= can't send you to another site after logging in. */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/login")) return "/";
  return raw;
}

export default function Login() {
  const status = useAuthStatus();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status.data && (!status.data.auth_required || status.data.logged_in)) return <Navigate to={next} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      window.location.assign(next); // full load: every query starts fresh with the new cookie
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the server.");
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-md bg-gradient-to-br from-accent to-[#a78bfa]" />
          <div>
            <div className="font-semibold leading-tight">PhysiqueLapse</div>
            <div className="text-xs text-muted">home server</div>
          </div>
        </div>
        {status.data && !status.data.password_set ? (
          <p className="text-sm text-warn">
            No password set yet. On the server run{" "}
            <code className="text-xs">docker compose exec physiquelapse python -m app.auth set-password</code>
          </p>
        ) : null}
        <label className="block space-y-1">
          <span className="text-sm text-muted">Password</span>
          <input
            type="password"
            className="input w-full"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="text-sm text-danger">{error}</p>}
        <button type="submit" className="btn btn-primary w-full justify-center" disabled={busy || !password}>
          {busy ? <Spinner size={14} /> : null} Log in
        </button>
      </form>
    </div>
  );
}
