/**
 * Outbox for photos taken on the Capture page.
 *
 * A kept photo goes into IndexedDB first, then uploads in the background. It stays until it is
 * uploaded AND you saved it to the camera roll (or dismissed that). A bad gym connection or a
 * closed tab never loses a photo: uploads retry when the page opens, comes back to the
 * foreground, or the network returns. Uploading the same photo twice is harmless (the server
 * recognises the bytes and returns the same photo).
 */
import { useSyncExternalStore } from "react";
import { redirectToLogin } from "../api/client";

export interface OutboxItem {
  id: string; // client id
  blob: Blob; // the JPEG with EXIF, exactly as saved to the camera roll
  filename: string;
  seriesId: number;
  ghostPhotoId: number | null;
  capturedAt: string; // ISO with offset
  meta: Record<string, unknown>;
  createdAt: number;
  uploaded: boolean;
  photoId: number | null;
  saved: boolean; // saved to the camera roll, or the share sheet was dismissed / saving is off
  error?: string;
}

const DB = "physiquelapse";
const STORE = "outbox";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "id" });
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise<T>((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req.result);
    t.onerror = () => reject(t.error);
  });
}

// in-memory copy too: IndexedDB can be unavailable (private mode) and the photo must survive
const memory = new Map<string, OutboxItem>();
let listeners: Array<() => void> = [];
let snapshot: OutboxItem[] = [];

function emit() {
  snapshot = [...memory.values()].sort((a, b) => a.createdAt - b.createdAt);
  listeners.forEach((l) => l());
}

let loaded: Promise<void> | null = null;
function load(): Promise<void> {
  loaded ??= tx<OutboxItem[]>("readonly", (s) => s.getAll() as IDBRequest<OutboxItem[]>)
    .then((rows) => {
      for (const r of rows) if (!memory.has(r.id)) memory.set(r.id, r);
      emit();
    })
    .catch(() => undefined);
  return loaded;
}

async function persist(item: OutboxItem) {
  memory.set(item.id, item);
  emit();
  try {
    await tx("readwrite", (s) => s.put(item));
  } catch {
    /* memory copy still holds it for this session */
  }
}

async function drop(id: string) {
  memory.delete(id);
  emit();
  try {
    await tx("readwrite", (s) => s.delete(id));
  } catch {
    /* ignore */
  }
}

async function settle(item: OutboxItem) {
  if (item.uploaded && item.saved) await drop(item.id);
  else await persist(item);
}

export async function addToOutbox(item: Omit<OutboxItem, "uploaded" | "photoId" | "saved" | "createdAt">, saved: boolean) {
  await load();
  await persist({ ...item, createdAt: Date.now(), uploaded: false, photoId: null, saved });
  void flush();
}

export async function markSaved(id: string) {
  await load();
  const it = memory.get(id);
  if (it) await settle({ ...it, saved: true });
}

async function uploadOne(it: OutboxItem): Promise<OutboxItem> {
  const fd = new FormData();
  fd.append("file", it.blob, it.filename);
  fd.append("series_id", String(it.seriesId));
  fd.append("captured_at", it.capturedAt);
  if (it.ghostPhotoId != null) fd.append("ghost_photo_id", String(it.ghostPhotoId));
  fd.append("meta", JSON.stringify(it.meta));
  const res = await fetch("/api/capture/upload", { method: "POST", body: fd });
  if (res.status === 401) {
    redirectToLogin();
    throw new Error("logged out");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    // 4xx other than timeouts won't get better by retrying: keep the photo, show the error
    if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) return { ...it, error: String(detail) };
    throw new Error(String(detail));
  }
  const r = (await res.json()) as { photo_id: number };
  return { ...it, uploaded: true, photoId: r.photo_id, error: undefined };
}

let flushing: Promise<void> | null = null;
/** Upload everything not uploaded yet (one at a time). */
export function flush(): Promise<void> {
  flushing ??= (async () => {
    await load();
    for (const it of [...memory.values()]) {
      if (it.uploaded || it.error) continue;
      try {
        await settle(await uploadOne(it));
      } catch {
        break; // offline or server down: try again on the next trigger
      }
    }
  })().finally(() => {
    flushing = null;
  });
  return flushing;
}

/** Remove a photo that the server refused (after you've seen the error). */
export const discard = (id: string) => drop(id);

let started = false;
/** Retry triggers: page load, back online, back in the foreground, and every 30 s. */
export function startOutbox() {
  if (started) return;
  started = true;
  void flush();
  window.addEventListener("online", () => void flush());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void flush();
  });
  window.setInterval(() => {
    if (snapshot.some((i) => !i.uploaded && !i.error)) void flush();
  }, 30_000);
}

export function useOutbox(): OutboxItem[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.push(cb);
      void load();
      return () => {
        listeners = listeners.filter((l) => l !== cb);
      };
    },
    () => snapshot,
  );
}
