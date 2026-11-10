import { useSyncExternalStore } from "react";

export type Toast = { id: number; kind: "info" | "success" | "error"; text: string };

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function push(kind: Toast["kind"], text: string, ttl = kind === "error" ? 8000 : 3500) {
  const id = nextId++;
  toasts = [...toasts, { id, kind, text }].slice(-6);
  emit();
  window.setTimeout(() => dismiss(id), ttl);
  return id;
}

export function dismiss(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export const toast = {
  info: (t: string) => push("info", t),
  success: (t: string) => push("success", t),
  error: (t: string) => push("error", t),
};

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => toasts,
  );
}
