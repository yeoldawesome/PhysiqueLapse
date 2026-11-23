import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { ApiError } from "../api/client";
import { admin, useAuthStatus } from "../api/auth";
import { ConfirmButton, PageHeader, Spinner, Toggle } from "../components/ui";
import { toast } from "../lib/toast";
import { fmtDate } from "../lib/format";

const err = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    window.prompt(`Copy the ${what.toLowerCase()}:`, text);
  }
}

export default function Admin() {
  const status = useAuthStatus();
  const qc = useQueryClient();
  const invites = useQuery({ queryKey: ["admin", "invites"], queryFn: admin.invites, enabled: !!status.data?.user?.is_admin });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: admin.users, enabled: !!status.data?.user?.is_admin });
  const [note, setNote] = useState("");
  const [uses, setUses] = useState(1);
  const [fresh, setFresh] = useState<{ code: string; link: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin"] });
  const me = status.data?.user;

  if (!me?.is_admin) {
    return (
      <div>
        <PageHeader title="Admin" />
        <p className="p-3 text-sm text-muted md:p-6">Admins only.</p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Admin" subtitle="Invites and accounts" />
      <div className="flex max-w-4xl flex-col gap-4 p-3 md:p-6">
        <section className="card space-y-3 p-4">
          <h2 className="font-medium">Invite someone</h2>
          <div className="flex flex-wrap items-center gap-2">
            <input className="input min-w-0 flex-1" placeholder="Note (who it's for)" value={note} onChange={(e) => setNote(e.target.value)} />
            <label className="flex items-center gap-1 text-sm">
              uses
              <input type="number" className="input w-16" min={1} max={100} value={uses} onChange={(e) => setUses(Number(e.target.value) || 1)} />
            </label>
            <button
              className="btn btn-primary"
              onClick={async () => {
                try {
                  const r = await admin.createInvite(note, uses, 14);
                  setFresh(r);
                  setNote("");
                  refresh();
                } catch (e) {
                  toast.error(err(e));
                }
              }}
            >
              <Plus size={14} /> Create invite
            </button>
          </div>
          {fresh && (
            <div className="space-y-2 rounded-md border border-accent/40 bg-accent/5 p-3 text-sm">
              <div>
                Code <b className="font-mono">{fresh.code}</b> (shown once, valid 14 days)
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-sm" onClick={() => void copy(fresh.link, "Invite link")}>
                  <Copy size={12} /> Copy link
                </button>
                <button className="btn btn-sm" onClick={() => void copy(fresh.code, "Code")}>
                  <Copy size={12} /> Copy code
                </button>
                {"share" in navigator && (
                  <button className="btn btn-sm" onClick={() => navigator.share({ title: "PhysiqueLapse invite", url: fresh.link }).catch(() => {})}>
                    Share…
                  </button>
                )}
              </div>
            </div>
          )}
          {invites.isLoading ? (
            <Spinner />
          ) : (
            <ul className="divide-y divide-border text-sm">
              {(invites.data ?? []).map((i) => (
                <li key={i.id} className="flex items-center gap-2 py-2">
                  <span className="min-w-0 flex-1 truncate">{i.note || <span className="text-muted">no note</span>}</span>
                  <span className={`text-xs ${i.usable ? "text-ok" : "text-muted"}`}>
                    {i.revoked ? "revoked" : i.usable ? `${i.uses_left} use${i.uses_left === 1 ? "" : "s"} left` : "used / expired"}
                  </span>
                  <span className="hidden text-xs text-muted sm:inline">{i.expires_at ? `until ${fmtDate(i.expires_at)}` : ""}</span>
                  {i.usable && (
                    <button className="btn btn-ghost btn-sm" title="revoke" onClick={() => void admin.revokeInvite(i.id).then(refresh)}>
                      <Trash2 size={14} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card p-4">
          <h2 className="mb-2 font-medium">Accounts</h2>
          {users.isLoading ? (
            <Spinner />
          ) : (
            <ul className="divide-y divide-border text-sm">
              {(users.data ?? []).map((u) => (
                <li key={u.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">
                      {u.name || u.email} {u.is_admin && <span className="badge bg-accent-2/20 text-accent">admin</span>}
                    </div>
                    <div className="truncate text-xs text-muted">
                      {u.email} · {u.used_mb} / {u.quota_mb} MB · since {fmtDate(u.created_at)}
                    </div>
                  </div>
                  {u.id !== me.id && (
                    <>
                      <Toggle
                        checked={!u.disabled}
                        onChange={(on) => void admin.patchUser(u.id, { disabled: !on }).then(refresh).catch((e) => toast.error(err(e)))}
                        label={u.disabled ? "disabled" : "active"}
                      />
                      <button
                        className="btn btn-sm"
                        title="password reset link"
                        onClick={async () => {
                          const r = await admin.resetLink(u.id);
                          await copy(r.link, r.emailed ? "Reset link (also emailed)" : "Reset link");
                        }}
                      >
                        <KeyRound size={12} /> Reset link
                      </button>
                      <ConfirmButton
                        className="btn btn-sm btn-danger"
                        confirmText={`Delete ${u.email} and all their photos?`}
                        onConfirm={() => void admin.deleteUser(u.id).then(refresh).catch((e) => toast.error(err(e)))}
                      >
                        <Trash2 size={12} />
                      </ConfirmButton>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
