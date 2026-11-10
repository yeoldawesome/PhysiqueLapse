import { post } from "../api/client";
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

const BATCH_FILES = 24;
const BATCH_BYTES = 48 * 1024 * 1024;

/** Stage files in batches into one upload session, then commit it to an ingest job. */
export async function uploadFiles(
  picked: PickedFile[],
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<{ session: ImportSession; job: Job }> {
  const session = await post<ImportSession>("/imports/upload-session");
  const total = picked.length;
  const bytesTotal = picked.reduce((a, p) => a + p.file.size, 0);
  let sent = 0;
  let bytesSent = 0;
  const report = (phase: UploadProgress["phase"], error?: string) => onProgress({ sent, total, bytesSent, bytesTotal, phase, error });
  report("staging");
  let i = 0;
  while (i < total) {
    const batch: PickedFile[] = [];
    let bytes = 0;
    while (i < total && batch.length < BATCH_FILES && (batch.length === 0 || bytes + picked[i].file.size < BATCH_BYTES)) {
      batch.push(picked[i]);
      bytes += picked[i].file.size;
      i++;
    }
    const fd = new FormData();
    for (const p of batch) {
      fd.append("files", p.file, p.file.name);
      fd.append("paths", p.path);
      fd.append("last_modified", String(p.file.lastModified || 0));
    }
    const res = await fetch(`/api/imports/upload-session/${session.id}/files`, { method: "POST", body: fd, signal });
    if (!res.ok) {
      const text = await res.text();
      report("error", `upload failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`upload failed: ${res.status}`);
    }
    sent += batch.length;
    bytesSent += bytes;
    report("staging");
  }
  report("committing");
  const r = await post<{ session: ImportSession; job: Job }>(`/imports/upload-session/${session.id}/commit`);
  report("done");
  return r;
}
