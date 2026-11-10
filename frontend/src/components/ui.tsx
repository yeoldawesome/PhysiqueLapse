import { useEffect, useState, type ReactNode } from "react";
import { X } from "lucide-react";

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onMouseDown={onClose}>
      <div className={`card fade-in w-full ${width} max-h-[90vh] overflow-auto p-4 shadow-2xl`} onMouseDown={(e) => e.stopPropagation()}>
        {title && (
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">{title}</h2>
            <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="close">
              <X size={16} />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

export function Field({ label, hint, children, className = "" }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex items-center gap-2 text-sm ${disabled ? "opacity-50" : ""}`}
    >
      <span className={`relative inline-block h-5 w-9 rounded-full transition-colors ${checked ? "bg-accent-2" : "bg-border"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
      </span>
      {label && <span>{label}</span>}
    </button>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-muted/40 border-t-accent"
      style={{ width: size, height: size }}
    />
  );
}

export function ConfirmButton({
  children,
  onConfirm,
  confirmText = "Confirm?",
  className = "btn btn-danger",
  disabled,
  title,
}: {
  children: ReactNode;
  onConfirm: () => void;
  confirmText?: string;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      title={title}
      onClick={() => {
        if (armed) {
          setArmed(false);
          onConfirm();
        } else setArmed(true);
      }}
    >
      {armed ? confirmText : children}
    </button>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="text-base font-medium">{title}</div>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, children, subtitle }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-border bg-bg/90 px-6 py-3 backdrop-blur">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold leading-tight">{title}</h1>
        {subtitle && <div className="truncate text-xs text-muted">{subtitle}</div>}
      </div>
      <div className="ml-auto flex items-center gap-2">{children}</div>
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "warn" | "ok" | "danger" }) {
  const toneCls = tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : tone === "danger" ? "text-danger" : "";
  return (
    <div className="card px-4 py-3">
      <div className="label">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneCls}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}
