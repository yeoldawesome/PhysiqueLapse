import { X } from "lucide-react";
import { dismiss, useToasts } from "../lib/toast";

export default function Toasts() {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed left-1/2 top-3 z-[60] flex -translate-x-1/2 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto fade-in flex max-w-xl items-start gap-2 rounded-md border px-3 py-2 text-sm shadow-lg ${
            t.kind === "error"
              ? "border-danger/50 bg-[#2a1517] text-[#fecaca]"
              : t.kind === "success"
                ? "border-ok/40 bg-[#0f2a22] text-[#bbf7d0]"
                : "border-border bg-panel-2 text-text"
          }`}
        >
          <span className="flex-1 break-words">{t.text}</span>
          <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100" aria-label="dismiss">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
