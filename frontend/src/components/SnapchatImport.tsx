import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileArchive, FolderSearch, Ghost, Play, ScanSearch, Trash2, Upload, X } from "lucide-react";
import { del, get, post } from "../api/client";
import type { ImportSession, Job, SnapScan } from "../api/types";
import { useImports } from "../api/queries";
import FolderPicker from "./FolderPicker";
import { ConfirmButton, Spinner } from "./ui";
import { toast } from "../lib/toast";
import { fmtBytes } from "../lib/format";

interface ZipEntry {
  path: string;
  name: string;
  uploaded: boolean;
  bytes: number;
  photos: number;
  videos: number;
  nested_zips: number;
  has_metadata: boolean;
  extracted: boolean;
  present: boolean;
}

interface ZipSession extends ImportSession {
  zips: ZipEntry[];
  staging_bytes: number;
  decisions: Record<string, number>;
}

interface Upload {
  name: string;
  size: number;
  sent: number;
  state: "waiting" | "uploading" | "done" | "error";
  error?: string;
}

function putFile(url: string, file: File, onProgress: (sent: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        let msg = xhr.statusText;
        try {
          msg = JSON.parse(xhr.responseText).detail ?? msg;
        } catch {
          /* ignore */
        }
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.send(file);
  });
}

const DECISION_LABEL: [string, string, string][] = [
  ["auto_accept", "imported automatically", "text-ok"],
  ["accepted", "accepted by you", "text-ok"],
  ["review", "waiting for review", "text-warn"],
  ["auto_reject", "filtered out", "text-muted"],
  ["rejected", "rejected by you", "text-muted"],
  ["duplicate", "duplicates", "text-muted"],
  ["error", "unreadable", "text-danger"],
  ["pending", "not scored yet", "text-muted"],
];

function SessionSummary({ s, onCleaned }: { s: ZipSession; onCleaned: () => void }) {
  const total = Object.values(s.decisions).reduce((a, b) => a + b, 0);
  const unpack = (s.options?.unpack_counts ?? {}) as Record<string, number>;
  return (
    <div className="rounded border border-border bg-bg p-3 text-sm">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-medium">Import #{s.id}</span>
        <span className={s.status === "done" ? "text-ok" : s.status === "failed" ? "text-danger" : "text-muted"}>{s.status}</span>
        <span className="text-xs text-muted">{s.zips.length} zip{s.zips.length === 1 ? "" : "s"}</span>
        {(s.decisions.review ?? 0) > 0 && (
          <Link to="/review" className="btn btn-sm btn-primary ml-auto">
            Review {s.decisions.review}
          </Link>
        )}
      </div>
      {total > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
          <span className="text-muted">{total} photos scored</span>
          {DECISION_LABEL.filter(([k]) => s.decisions[k]).map(([k, label, cls]) => (
            <span key={k} className={cls}>
              {s.decisions[k]} {label}
            </span>
          ))}
        </div>
      )}
      {(unpack.videos_skipped || unpack.overlays_skipped) && (
        <div className="mt-0.5 text-xs text-muted">
          skipped while unpacking: {unpack.videos_skipped ?? 0} videos, {unpack.overlays_skipped ?? 0} caption overlays
        </div>
      )}
      {s.staging_bytes > 0 && s.status !== "running" && s.status !== "queued" && (
        <div className="mt-2 flex items-center gap-2 text-xs text-muted">
          <span>Extracted photos kept for review: {fmtBytes(s.staging_bytes)}</span>
          <ConfirmButton
            className="btn btn-sm ml-auto"
            confirmText={(s.decisions.review ?? 0) > 0 ? `Discard ${s.decisions.review} unreviewed too?` : "Delete extracted files?"}
            onConfirm={() =>
              del(`/snapchat/zip-session/${s.id}/staging`, { force: true })
                .then(() => {
                  toast.success("Extracted files deleted");
                  onCleaned();
                })
                .catch((e: Error) => toast.error(e.message))
            }
          >
            <Trash2 size={12} /> Free space
          </ConfirmButton>
        </div>
      )}
    </div>
  );
}

export default function SnapchatImport() {
  const qc = useQueryClient();
  const [sid, setSid] = useState<number | null>(null);
  const session = useQuery({
    queryKey: ["snap-zip", sid],
    queryFn: () => get<ZipSession>(`/snapchat/zip-session/${sid}`),
    enabled: sid != null,
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "running" || q.state.data.status === "queued") ? 1500 : false),
  });
  const history = useQuery({
    queryKey: ["snap-zip", "list"],
    queryFn: () => get<ZipSession[]>("/snapchat/zip-sessions"),
    refetchInterval: 4000,
  });
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [drag, setDrag] = useState(false);
  const [path, setPath] = useState("");
  const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const ensureSession = async (): Promise<number> => {
    if (sid != null && session.data?.status === "open") return sid;
    const s = await post<ZipSession>("/snapchat/zip-session");
    setSid(s.id);
    qc.setQueryData(["snap-zip", s.id], s);
    return s.id;
  };
  useEffect(() => {
    // resume an open (not yet started) session after a page reload
    post<ZipSession>("/snapchat/zip-session").then((s) => {
      setSid(s.id);
      qc.setQueryData(["snap-zip", s.id], s);
    });
  }, [qc]);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["snap-zip"] });
    qc.invalidateQueries({ queryKey: ["imports"] });
  };

  const uploadAll = async (files: File[]) => {
    const zips = files.filter((f) => f.name.toLowerCase().endsWith(".zip"));
    if (zips.length < files.length) toast.info(`${files.length - zips.length} non-zip files ignored`);
    if (!zips.length) return;
    const id = await ensureSession();
    setUploads((u) => [...u, ...zips.map((f) => ({ name: f.name, size: f.size, sent: 0, state: "waiting" as const }))]);
    for (const f of zips) {
      setUploads((u) => u.map((x) => (x.name === f.name && x.state === "waiting" ? { ...x, state: "uploading" } : x)));
      try {
        await putFile(`/api/snapchat/zip-session/${id}/upload?name=${encodeURIComponent(f.name)}`, f, (sent) =>
          setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, sent } : x))),
        );
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, sent: f.size, state: "done" } : x)));
      } catch (e) {
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "error", error: (e as Error).message } : x)));
      }
      refresh();
    }
    setUploads((u) => u.filter((x) => x.state !== "done"));
  };

  const addPath = async (p = path) => {
    if (!p.trim()) return;
    setBusy(true);
    try {
      const id = await ensureSession();
      const r = await post<ZipSession & { errors: string[] }>(`/snapchat/zip-session/${id}/add-path`, { path: p.trim() });
      r.errors?.forEach((e) => toast.error(e));
      setPath("");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (sid == null) return;
    setBusy(true);
    try {
      const r = await post<{ job: Job }>(`/snapchat/zip-session/${sid}/start`);
      toast.info(`Unpacking and filtering (job #${r.job.id}). You can leave this page.`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const s = session.data;
  const open = s?.status === "open";
  const zips = s?.zips ?? [];
  const photos = zips.reduce((a, z) => a + z.photos, 0);
  const nested = zips.reduce((a, z) => a + z.nested_zips, 0);
  const uploading = uploads.some((u) => u.state === "uploading" || u.state === "waiting");
  const past = (history.data ?? []).filter((h) => h.status !== "open");

  return (
    <div className="card p-4">
      <div className="mb-1 flex items-center gap-2">
        <Ghost size={16} className="text-accent" />
        <h2 className="font-medium">Snapchat Memories</h2>
      </div>
      <p className="mb-3 text-xs text-muted">
        Add every zip from your Snapchat data download. Only photos are unpacked (videos and caption overlays are skipped), then each photo is
        scored by an image model plus a body-pose check. Clear physique photos are imported with their Snapchat date, clear non-physique photos are
        dropped and never copied, and anything in between goes to the{" "}
        <Link to="/review" className="underline">
          review queue
        </Link>
        .
      </p>

      {open && (
        <>
          <div
            className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
              drag ? "border-accent bg-accent/5" : "border-border"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              uploadAll(Array.from(e.dataTransfer.files));
            }}
          >
            <FileArchive size={26} className="text-muted" />
            <div className="text-sm">Drop your Snapchat .zip files here</div>
            <button className="btn btn-sm" onClick={() => fileInput.current?.click()}>
              <Upload size={13} /> Choose zip files
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".zip,application/zip"
              multiple
              className="hidden"
              onChange={(e) => {
                uploadAll(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">or, faster for big downloads, read them where they are:</span>
            <input
              className="input w-full min-w-0 flex-1 font-mono text-xs md:min-w-[260px]"
              placeholder="C:\Users\you\Downloads  (folder with the zips, or one .zip)"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addPath()}
            />
            <button className="btn btn-sm" onClick={() => setPicker(true)}>
              <FolderSearch size={13} /> Browse
            </button>
            <button className="btn btn-sm" disabled={!path.trim() || busy} onClick={() => addPath()}>
              Add
            </button>
          </div>
        </>
      )}

      {uploads.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {uploads.map((u) => (
            <li key={u.name} className="text-xs">
              <div className="flex justify-between">
                <span className="truncate">{u.name}</span>
                <span className={u.state === "error" ? "text-danger" : "text-muted"}>
                  {u.state === "error" ? u.error : u.state === "waiting" ? "waiting" : `${fmtBytes(u.sent)} / ${fmtBytes(u.size)}`}
                </span>
              </div>
              <div className="mt-0.5 h-1 overflow-hidden rounded bg-border">
                <div className={`h-full ${u.state === "error" ? "bg-danger" : "bg-accent-2"}`} style={{ width: `${u.size ? (u.sent / u.size) * 100 : 0}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {zips.length > 0 && open && (
        <div className="mt-3 rounded border border-border">
          <ul className="divide-y divide-border">
            {zips.map((z) => (
              <li key={z.path} className="flex items-center gap-2 px-3 py-1.5 text-sm">
                <FileArchive size={14} className="text-muted" />
                <span className="truncate" title={z.path}>
                  {z.name}
                </span>
                <span className="text-xs text-muted">
                  {fmtBytes(z.bytes)} · {z.photos} photos{z.nested_zips ? ` + ${z.nested_zips} nested zips` : ""} · {z.videos} videos
                </span>
                {z.has_metadata && <span className="badge bg-ok/15 text-ok">dates file</span>}
                {!z.uploaded && <span className="badge bg-panel-2 text-muted">read in place</span>}
                <button
                  className="btn btn-ghost btn-sm ml-auto"
                  title="remove"
                  onClick={() =>
                    del(`/snapchat/zip-session/${sid}/zips`, { path: z.path })
                      .then(refresh)
                      .catch((e: Error) => toast.error(e.message))
                  }
                >
                  <X size={13} />
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2 border-t border-border px-3 py-2">
            <span className="text-xs text-muted">
              {photos} photos{nested ? ` + up to ${nested} inside nested zips` : ""} to check.
              {!zips.some((z) => z.has_metadata) && " No memories_history.json yet: dates will come from file names (add the zip that contains it if you have it)."}
            </span>
            <button className="btn btn-primary ml-auto" disabled={busy || uploading} onClick={start}>
              <Play size={14} /> Unpack and filter
            </button>
          </div>
        </div>
      )}

      {s && !open && (
        <div className="mt-3">
          <SessionSummary s={s} onCleaned={refresh} />
          {(s.status === "running" || s.status === "queued") && (
            <div className="mt-2 flex items-center gap-2 text-xs text-muted">
              <Spinner size={12} /> working… progress is in the job panel (bottom right)
            </div>
          )}
          {(s.status === "failed" || s.status === "cancelled") && (
            <div className="mt-2 flex items-center gap-2 text-xs">
              <span className="text-warn">This import stopped before finishing. Resuming skips everything already done.</span>
              <button className="btn btn-sm btn-primary ml-auto" disabled={busy} onClick={start}>
                <Play size={13} /> Resume
              </button>
            </div>
          )}
          {s.status === "done" && (
            <button className="btn btn-sm mt-2" onClick={() => ensureSession().then(refresh)}>
              Start another Snapchat import
            </button>
          )}
        </div>
      )}

      {past.filter((h) => h.id !== s?.id).length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-xs text-muted">earlier Snapchat imports</summary>
          <div className="mt-2 space-y-2">
            {past
              .filter((h) => h.id !== s?.id)
              .map((h) => (
                <SessionSummary key={h.id} s={h} onCleaned={refresh} />
              ))}
          </div>
        </details>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-muted">already unzipped? import an extracted export folder instead</summary>
        <ExtractedFolderImport />
      </details>

      <FolderPicker
        open={picker}
        initial={path}
        onClose={() => setPicker(false)}
        onPick={(p) => {
          setPicker(false);
          addPath(p);
        }}
      />
    </div>
  );
}

function ExtractedFolderImport() {
  const qc = useQueryClient();
  const { data: sessions = [] } = useImports();
  const [path, setPath] = useState("");
  const [scan, setScan] = useState<SnapScan | null>(null);
  const [busy, setBusy] = useState(false);
  const existing = sessions.find((s) => s.kind === "snapchat" && s.root_path === scan?.path);
  const doScan = async () => {
    setBusy(true);
    setScan(null);
    try {
      setScan(await post<SnapScan>("/snapchat/scan", { path: path.trim() }));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    if (!scan) return;
    try {
      await post<{ job: Job }>("/snapchat/import", { path: scan.path });
      toast.info("Snapchat import started");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["imports"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <div className="mt-2">
      <div className="flex gap-2">
        <input className="input flex-1 font-mono text-xs" placeholder="extracted export folder" value={path} onChange={(e) => setPath(e.target.value)} />
        <button className="btn btn-sm" onClick={doScan} disabled={!path.trim() || busy}>
          <ScanSearch size={13} /> Scan
        </button>
      </div>
      {scan && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
          <span>
            {scan.images} photos · {scan.videos} videos · {scan.overlays} overlays · {scan.matched_metadata} exact dates
          </span>
          {scan.warnings.map((w) => (
            <span key={w} className="text-warn">
              {w}
            </span>
          ))}
          <button className="btn btn-sm btn-primary ml-auto" disabled={scan.images === 0} onClick={start}>
            {existing ? "Resume" : "Start"}
          </button>
        </div>
      )}
    </div>
  );
}
