const dateFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** Backend datetimes are naive local ISO strings ("2024-03-15T09:30:00"). */
export function parseLocal(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  return isNaN(d.getTime()) ? null : d;
}

export function fmtDate(iso: string | null | undefined, fallback = "no date"): string {
  const d = parseLocal(iso);
  return d ? dateFmt.format(d) : fallback;
}

export function fmtDateTime(iso: string | null | undefined, fallback = "no date"): string {
  const d = parseLocal(iso);
  return d ? dateTimeFmt.format(d) : fallback;
}

export function toDatetimeLocalValue(iso: string | null | undefined): string {
  const d = parseLocal(iso);
  if (!d) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function toDateValue(iso: string | null | undefined): string {
  const d = parseLocal(iso);
  if (!d) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function todayValue(): string {
  return toDateValue(new Date().toISOString().slice(0, 10) + "T00:00:00");
}

export function daysBetween(a: string | null, b: string | null): number | null {
  const da = parseLocal(a);
  const db = parseLocal(b);
  if (!da || !db) return null;
  return Math.round((db.getTime() - da.getTime()) / 86_400_000);
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function pct(x: number | null | undefined, digits = 0): string {
  if (x == null) return "–";
  return `${(x * 100).toFixed(digits)}%`;
}

export function fmtNum(x: number | null | undefined, decimals = 1): string {
  if (x == null || isNaN(x)) return "–";
  return x.toFixed(decimals);
}

export const DATE_SOURCE_LABEL: Record<string, string> = {
  exif: "EXIF",
  snapchat_metadata: "Snapchat",
  filename: "filename",
  file_mtime: "file time",
  manual: "manual",
  capture: "captured",
  none: "none",
};

export const POSE_SOURCE_LABEL: Record<string, string> = {
  capture: "captured",
  rule: "rule",
  knn: "kNN",
  manual: "manual",
  group: "automatic group",
  none: "",
};

export function relDays(n: number | null | undefined): string {
  if (n == null) return "";
  if (n === 0) return "same day";
  const abs = Math.abs(n);
  const unit = abs === 1 ? "day" : "days";
  return n < 0 ? `${abs} ${unit} earlier` : `${abs} ${unit} later`;
}
