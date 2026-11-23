import { ApiError, get, post, redirectToLogin } from "../api/client";
import type { ImportSession, Job } from "../api/types";

export interface PickedFile {
  file: File;
  path: string; // relative path including folder parts when dropped/picked as a folder
}

const IMAGE_RE = /\.(jpe?g|png|heic|heif|webp|bmp|tiff?|gif|avif)$/i;
const VIDEO_RE = /\.(mp4|mov|m4v|avi|mkv|webm|3gp|mts)$/i;

export function isImageName(name: string) {
  return IMAGE_RE.test(name);
}
export function isVideoName(name: string) {
  return VIDEO_RE.test(name);
}

/** Collect files from a drop event, walking folders via the FileSystem Entry API when available. */
export async function filesFromDrop(dt: DataTransfer): Promise<PickedFile[]> {
  const out: PickedFile[] = [];
  const items = Array.from(dt.items ?? []);
  const entries = items
    .map((it) => (typeof it.webkitGetAsEntry === "function" ? it.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => !!e);
  if (entries.length === 0) {
    for (const f of Array.from(dt.files)) out.push({ file: f, path: f.name });
    return out;
  }
  for (const e of entries) await walkEntry(e, "", out);
  return out;
}

function walkEntry(entry: FileSystemEntry, prefix: string, out: PickedFile[]): Promise<void> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      (entry as FileSystemFileEntry).file(
        (file) => {
          out.push({ file, path: prefix + file.name });
          resolve();
        },
        () => resolve(),
      );
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const all: FileSystemEntry[] = [];
      const readBatch = () => {
        reader.readEntries(
          async (batch) => {
            if (batch.length === 0) {
              for (const child of all) await walkEntry(child, prefix + entry.name + "/", out);
              resolve();
            } else {
              all.push(...batch);
              readBatch();
            }
          },
          () => resolve(),
        );
      };
      readBatch();
    } else {
      resolve();
    }
  });
}

export function filesFromInput(list: FileList | null): PickedFile[] {
  if (!list) return [];
  return Array.from(list).map((f) => ({
    file: f,
    path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name,
  }));
}

export interface UploadProgress {
  sent: number;
  total: number;
  bytesSent: number;
  bytesTotal: number;
  phase: "staging" | "committing" | "done" | "error";
  error?: string;
}

interface UploadItem {
  id: string;
  received: number;
  size: number;
  status: "open" | "complete";
  chunk_size: number;
}

const CONCURRENCY = 2; // files in flight at once
const MAX_TRIES = 8; // per chunk, with backoff: rides out a dropped gym Wi-Fi connection
const RESUME_KEY = "pl.upload.session";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function remembered(): number | null {
  try {
    const v = localStorage.getItem(RESUME_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}
function remember(id: number | null) {
  try {
    if (id == null) localStorage.removeItem(RESUME_KEY);
    else localStorage.setItem(RESUME_KEY, String(id));
  } catch {
    /* private mode: resuming after a reload just won't work */
  }
}

/** An upload interrupted by a reload or a closed tab: picking the same files again continues it. */
async function openSession(): Promise<number> {
  const prev = remembered();
  if (prev != null) {
    try {
      const st = await get<{ session: ImportSession }>(`/imports/upload-session/${prev}`);
      if (st.session.status === "open") return prev;
    } catch {
      /* gone: start a new one */
    }
  }
  const s = await post<ImportSession>("/imports/upload-session");
  remember(s.id);
  return s.id;
}

async function putChunk(sid: number, item: UploadItem, blob: Blob, signal?: AbortSignal): Promise<UploadItem> {
  const res = await fetch(`/api/imports/upload-session/${sid}/files/${item.id}?offset=${item.received}`, {
    method: "PUT",
    body: blob,
    headers: { "Content-Type": "application/octet-stream" },
    signal,
  });
  if (res.status === 401) redirectToLogin();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, String(detail));
  }
  return (await res.json()) as UploadItem;
}

/** Upload one file in chunks, resuming from what the server already has. */
async function sendFile(sid: number, p: PickedFile, onBytes: (n: number) => void, signal?: AbortSignal) {
  let item = await post<UploadItem>(`/imports/upload-session/${sid}/files/init`, {
    rel_path: p.path,
    size: p.file.size,
    last_modified: p.file.lastModified || 0,
  });
  onBytes(item.received);
  let tries = 0;
  while (item.status !== "complete") {
    const end = Math.min(item.size, item.received + item.chunk_size);
    try {
      const before = item.received;
      item = await putChunk(sid, item, p.file.slice(item.received, end), signal);
      onBytes(item.received - before);
      tries = 0;
    } catch (e) {
      if (signal?.aborted) throw e;
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 413 || status === 415 || status === 404 || status === 401) throw e; // won't get better
      if (++tries > MAX_TRIES) throw e;
      await sleep(Math.min(30_000, 500 * 2 ** tries));
      // ask the server how far it got (a chunk may have arrived even though the reply was lost)
      try {
        const before = item.received;
        item = await get<UploadItem>(`/imports/upload-session/${sid}/files/${item.id}`);
        onBytes(item.received - before);
      } catch {
        /* still offline: retry the chunk */
      }
    }
  }
}

/** Upload files in resumable chunks into one upload session, then commit it to an import job. */
export async function uploadFiles(
  picked: PickedFile[],
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<{ session: ImportSession; job: Job }> {
  const sid = await openSession();
  const total = picked.length;
  const bytesTotal = picked.reduce((a, p) => a + p.file.size, 0);
  let sent = 0;
  let bytesSent = 0;
  const failed: string[] = [];
  const report = (phase: UploadProgress["phase"], error?: string) => onProgress({ sent, total, bytesSent, bytesTotal, phase, error });
  report("staging");
  let next = 0;
  const worker = async () => {
    while (next < total) {
      const p = picked[next++];
      try {
        await sendFile(sid, p, (n) => {
          bytesSent += n;
          report("staging");
        }, signal);
        sent++;
      } catch (e) {
        if (signal?.aborted) throw e;
        failed.push(`${p.path}: ${(e as Error).message}`);
      }
      report("staging");
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, total) }, worker));
  if (sent === 0) {
    const msg = failed[0] ?? "nothing uploaded";
    report("error", msg);
    throw new Error(msg);
  }
  report("committing");
  const r = await post<{ session: ImportSession; job: Job }>(`/imports/upload-session/${sid}/commit`);
  remember(null);
  report("done", failed.length ? `${failed.length} file(s) failed: ${failed.slice(0, 3).join("; ")}` : undefined);
  return r;
}
