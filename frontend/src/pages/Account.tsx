import { useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { changePassword, deleteAccount, useAuthStatus, useLogout, useUsage } from "../api/auth";
import { PageHeader, Spinner } from "../components/ui";
import { toast } from "../lib/toast";

const err = (e: unknown) => (e instanceof ApiError ? e.detail : "Could not reach the server.");

export default function Account() {
  const status = useAuthStatus();
  const user = status.data?.user;
  const usage = useUsage(!!user);
  const logout = useLogout();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [delPw, setDelPw] = useState("");
  const [confirm, setConfirm] = useState("");

  if (!user) {
    return (
      <div>
        <PageHeader title="Account" />
        <p className="p-3 text-sm text-muted md:p-6">The desktop app has no accounts.</p>
      </div>
    );
  }
  const u = usage.data;
  const pct = u?.used_mb != null && u.quota_mb ? Math.min(100, (100 * u.used_mb) / u.quota_mb) : 0;

  return (
    <div>
      <PageHeader title="Account" subtitle={user.email} />
      <div className="flex max-w-xl flex-col gap-4 p-3 md:p-6">
        <section className="card space-y-2 p-4">
          <h2 className="font-medium">{user.name || user.email}</h2>
          <div className="text-sm text-muted">
            {user.email}
            {user.is_admin && " · admin"}
          </div>
          <div className="pt-2 text-sm">
            Storage: {u ? `${u.used_mb} of ${u.quota_mb} MB` : <Spinner size={12} />}
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-border">
            <div className={`h-full ${pct > 90 ? "bg-danger" : "bg-accent-2"}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="flex gap-2 pt-2">
            <button className="btn" onClick={logout}>
              Sign out
            </button>
            <Link to="/privacy" className="btn btn-ghost">
              Privacy
            </Link>
          </div>
        </section>

        <form
          className="card space-y-3 p-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await changePassword(cur, next);
              toast.success("Password changed. Other devices were signed out.");
              setCur("");
              setNext("");
            } catch (ex) {
              toast.error(err(ex));
            } finally {
              setBusy(false);
            }
          }}
        >
          <h2 className="font-medium">Change password</h2>
          <input className="input w-full" type="password" autoComplete="current-password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} />
          <input className="input w-full" type="password" autoComplete="new-password" placeholder="New password (10+ characters)" value={next} onChange={(e) => setNext(e.target.value)} />
          <button className="btn btn-primary" disabled={busy || !cur || next.length < 10}>
            Change password
          </button>
        </form>

        <form
          className="card space-y-3 border-danger/40 p-4"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await deleteAccount(delPw);
              window.location.assign("/login");
            } catch (ex) {
              toast.error(err(ex));
            }
          }}
        >
          <h2 className="font-medium text-danger">Delete account</h2>
          <p className="text-sm text-muted">
            Deletes your account and everything in it from this server: every photo, alignment, measurement and export. This can't be undone; save anything you want to keep
            first.
          </p>
          <input className="input w-full" type="password" autoComplete="current-password" placeholder="Your password" value={delPw} onChange={(e) => setDelPw(e.target.value)} />
          <input className="input w-full" placeholder='Type "delete" to confirm' value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          <button className="btn btn-danger" disabled={!delPw || confirm.trim().toLowerCase() !== "delete"}>
            Delete my account and photos
          </button>
        </form>
      </div>
    </div>
  );
}

export function Privacy() {
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4 pt-[max(1rem,env(safe-area-inset-top))] text-sm leading-relaxed md:p-8">
      <h1 className="text-xl font-semibold">Privacy</h1>
      <p>
        PhysiqueLapse runs on a private home server. Your photos, their analysis (body landmarks, poses, alignments, measurements) and anything you enter are stored on that
        server, in a library that only your account can open.
      </p>
      <ul className="list-disc space-y-1 pl-5">
        <li>No one else using this server can see your photos or data. The person who runs the server has access to the machine itself.</li>
        <li>Nothing is sent to other companies: no analytics, no ads, no AI services. All processing (pose detection, alignment, videos) happens on the server.</li>
        <li>The only emails are password-reset links you ask for.</li>
        <li>Photos you take on the Capture page are saved to your own phone only if you choose to.</li>
        <li>
          You can delete your account at any time on the <Link to="/account" className="underline">Account</Link> page; that removes every photo and all data in it from the
          server.
        </li>
      </ul>
      <p className="text-muted">
        <Link to="/" className="underline">
          Back to the app
        </Link>
      </p>
    </div>
  );
}
