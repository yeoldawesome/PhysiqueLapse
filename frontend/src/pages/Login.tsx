import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { forgot, login, resetPassword, signup, useAuthStatus } from "../api/auth";
import { Spinner } from "../components/ui";

/** Only same-site paths, so ?next= can't send you to another site after signing in. */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || /^\/(login|signup|forgot|reset)/.test(raw)) return "/";
  return raw;
}

function errText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Could not reach the server.";
}

function Shell({ title, children, onSubmit }: { title: string; children: ReactNode; onSubmit: (e: FormEvent) => void }) {
  return (
    <div className="flex min-h-full items-center justify-center p-4 pt-[max(1rem,env(safe-area-inset-top))]">
      <form onSubmit={onSubmit} className="card w-full max-w-sm space-y-4 p-6">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-md bg-gradient-to-br from-accent to-[#a78bfa]" />
          <div>
            <div className="font-semibold leading-tight">PhysiqueLapse</div>
            <div className="text-xs text-muted">{title}</div>
          </div>
        </div>
        {children}
      </form>
    </div>
  );
}

function Field({ label, ...rest }: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block space-y-1">
      <span className="text-sm text-muted">{label}</span>
      <input className="input w-full" {...rest} />
    </label>
  );
}

function Submit({ busy, children, disabled }: { busy: boolean; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="submit" className="btn btn-primary w-full justify-center py-2" disabled={busy || disabled}>
      {busy ? <Spinner size={14} /> : null} {children}
    </button>
  );
}

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export default function Login() {
  const status = useAuthStatus();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useSubmit();

  if (status.data && (!status.data.auth_required || status.data.logged_in)) return <Navigate to={next} replace />;

  return (
    <Shell
      title="Sign in"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await login(email, password);
          window.location.assign(next); // full load: every query starts fresh with the new cookie
        });
      }}
    >
      {status.data?.setup_needed && (
        <p className="text-sm text-warn">
          No accounts yet. On the server run{" "}
          <code className="text-xs">docker compose exec physiquelapse python -m app.accounts create-admin --email you@example.com</code>
        </p>
      )}
      <Field label="Email" type="email" autoComplete="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      {error && <p className="text-sm text-danger">{error}</p>}
      <Submit busy={busy} disabled={!email || !password}>
        Sign in
      </Submit>
      <div className="flex justify-between text-sm">
        <Link to="/signup" className="text-accent">
          Have an invite?
        </Link>
        <Link to="/forgot" className="text-muted hover:text-text">
          Forgot password?
        </Link>
      </div>
    </Shell>
  );
}

export function Signup() {
  const [params] = useSearchParams();
  const [invite, setInvite] = useState(params.get("invite") ?? "");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useSubmit();
  return (
    <Shell
      title="Create your account"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await signup(invite, email, name, password);
          window.location.assign("/guide");
        });
      }}
    >
      <Field label="Invite code" value={invite} onChange={(e) => setInvite(e.target.value)} placeholder="ABCD-EFGH-JKLM" autoCapitalize="characters" />
      <Field label="Name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Password (10+ characters)" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <p className="text-xs text-muted">
        Your photos are stored on this server only, in your own private library. See <Link to="/privacy" className="underline">privacy</Link>.
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}
      <Submit busy={busy} disabled={!invite || !email || password.length < 10}>
        Create account
      </Submit>
      <div className="text-sm">
        <Link to="/login" className="text-muted hover:text-text">
          Already have an account? Sign in
        </Link>
      </div>
    </Shell>
  );
}

export function Forgot() {
  const status = useAuthStatus();
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const { busy, error, run } = useSubmit();
  return (
    <Shell
      title="Reset your password"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => setDone((await forgot(email)).message));
      }}
    >
      {status.data && !status.data.email_reset ? (
        <p className="text-sm text-muted">Password reset by email isn't set up on this server. Ask the person who invited you to send you a reset link.</p>
      ) : done ? (
        <p className="text-sm">{done} Check your inbox (and spam folder); the link works for an hour.</p>
      ) : (
        <>
          <Field label="Email" type="email" autoComplete="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          {error && <p className="text-sm text-danger">{error}</p>}
          <Submit busy={busy} disabled={!email}>
            Email me a reset link
          </Submit>
        </>
      )}
      <div className="text-sm">
        <Link to="/login" className="text-muted hover:text-text">
          Back to sign in
        </Link>
      </div>
    </Shell>
  );
}

export function Reset() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const { busy, error, run } = useSubmit();
  return (
    <Shell
      title="Choose a new password"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await resetPassword(token, password);
          window.location.assign("/");
        });
      }}
    >
      <Field label="New password (10+ characters)" type="password" autoComplete="new-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
      <Field label="Again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
      {again && again !== password && <p className="text-sm text-warn">The passwords don't match.</p>}
      {error && <p className="text-sm text-danger">{error}</p>}
      <Submit busy={busy} disabled={!token || password.length < 10 || password !== again}>
        Set password and sign in
      </Submit>
    </Shell>
  );
}
