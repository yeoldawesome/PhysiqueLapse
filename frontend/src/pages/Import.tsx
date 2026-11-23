import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderInput, FolderSearch } from "lucide-react";
import { post } from "../api/client";
import { useCandidates, useImports } from "../api/queries";
import type { ImportSession, Job } from "../api/types";
import Uploader from "../components/Uploader";
import SnapchatImport from "../components/SnapchatImport";
import FolderPicker from "../components/FolderPicker";
import { useAuthStatus } from "../api/auth";
import { Modal, PageHeader, Toggle } from "../components/ui";
import { toast } from "../lib/toast";
import { fmtDateTime } from "../lib/format";

const COUNT_LABELS: [string, string][] = [
  ["accepted", "imported"],
  ["duplicates", "duplicates"],
  ["near_duplicates", "near-duplicates"],
  ["skipped_ledger", "previously removed"],
  ["review", "to review"],
  ["rejected", "rejected"],
  ["overlays_skipped", "overlays skipped"],
  ["videos_skipped", "videos skipped"],
  ["other_skipped", "other files"],
  ["errors", "errors"],
];

function SessionDetail({ session, onClose }: { session: ImportSession; onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useCandidates(session.id, "duplicate,error,rejected,deleted");
  const keep = async (id: number) => {
    try {
      await post(`/imports/candidates/${id}/keep`);
      toast.info("Import queued");
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <Modal open onClose={onClose} title={`Import #${session.id} · ${session.kind}`} width="max-w-3xl">
      <div className="mb-3 text-xs text-muted">{session.root_path}</div>
      {!data || data.items.length === 0 ? (
        <div className="text-sm text-muted">Nothing was skipped in this import.</div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-2">
          {data.items.map((c) => (
            <div key={c.id} className="card overflow-hidden">
              {c.thumb_url ? (
                <img src={c.thumb_url} className="aspect-[4/5] w-full object-cover" alt="" loading="lazy" />
              ) : (
                <div className="aspect-[4/5] w-full bg-bg" />
              )}
              <div className="p-1.5 text-[11px]">
                <div className="truncate" title={c.file_name}>
                  {c.file_name}
                </div>
                <div className={c.decision === "error" ? "text-danger" : "text-muted"} title={c.error ?? ""}>
                  {c.decision}
                  {c.error ? ` · ${c.error}` : ""}
                </div>
                {c.decision !== "error" && !c.photo_id && (
                  <button className="btn btn-sm mt-1 w-full justify-center" onClick={() => keep(c.id)}>
                    keep anyway
                  </button>
                )}
                {c.decision === "duplicate" && c.photo_id && <div className="text-muted">exact copy already in library</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

export default function Import() {
  const qc = useQueryClient();
  const { data: sessions = [] } = useImports();
  const [path, setPath] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [picker, setPicker] = useState(false);
  const [detail, setDetail] = useState<ImportSession | null>(null);
  const [busy, setBusy] = useState(false);

  const importFolder = async () => {
    if (!path.trim()) return;
    setBusy(true);
    try {
      const r = await post<{ session: ImportSession; job: Job }>("/imports/folder", { path: path.trim(), recursive });
      toast.info(`Folder import started (job #${r.job.id})`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["imports"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const auth = useAuthStatus();
  const pathImport = auth.data?.path_import ?? true;
  return (
    <div>
      <PageHeader title="Import" subtitle="Originals are copied into the app's data folder. Your source files are never changed." />
      <div className="flex max-w-5xl flex-col gap-4 p-6">
        <Uploader />

        {pathImport && (
        <div className="card p-4">
          <div className="mb-3 flex items-center gap-2">
            <FolderInput size={16} className="text-accent" />
            <h2 className="font-medium">Add a folder by path</h2>
            <span className="text-xs text-muted">faster for thousands of files; the server reads the folder directly</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="input min-w-[320px] flex-1 font-mono text-xs"
              placeholder="C:\Users\you\Pictures\Progress"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") importFolder();
              }}
            />
            <button className="btn" onClick={() => setPicker(true)}>
              <FolderSearch size={14} /> Browse
            </button>
            <Toggle checked={recursive} onChange={setRecursive} label="include subfolders" />
            <button className="btn btn-primary" disabled={!path.trim() || busy} onClick={importFolder}>
              Import folder
            </button>
          </div>
        </div>
        )}

        <SnapchatImport />

        <div className="card">
          <div className="border-b border-border px-4 py-2 text-sm font-medium">Import history</div>
          {sessions.length === 0 ? (
            <div className="px-4 py-6 text-sm text-muted">No imports yet.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-4 py-2 font-medium">Kind</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Result</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className="border-t border-border/60 align-top">
                    <td className="whitespace-nowrap px-4 py-2 text-xs">{fmtDateTime(s.created_at)}</td>
                    <td className="px-4 py-2">
                      <div>{s.kind}</div>
                      {s.root_path && (
                        <div className="max-w-[260px] truncate text-[11px] text-muted" title={s.root_path}>
                          {s.root_path}
                        </div>
                      )}
                    </td>
                    <td className={`px-4 py-2 ${s.status === "failed" ? "text-danger" : s.status === "done" ? "text-ok" : "text-muted"}`}>{s.status}</td>
                    <td className="px-4 py-2 text-xs">
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {s.counts.files != null && <span className="text-muted">{s.counts.files} files</span>}
                        {COUNT_LABELS.filter(([k]) => s.counts[k]).map(([k, label]) => (
                          <span key={k} className={k === "errors" ? "text-danger" : k === "accepted" ? "text-ok" : ""}>
                            {s.counts[k]} {label}
                          </span>
                        ))}
                        {s.status === "open" && s.counts.staged != null && <span className="text-warn">{s.counts.staged} staged, not committed</span>}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button className="btn btn-sm" onClick={() => setDetail(s)}>
                        details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <FolderPicker
        open={picker}
        initial={path}
        onClose={() => setPicker(false)}
        onPick={(p) => {
          setPath(p);
          setPicker(false);
        }}
      />
      {detail && <SessionDetail session={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
